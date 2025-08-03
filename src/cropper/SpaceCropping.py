"""
HDF5气象数据空间裁剪模块

该模块提供了对HDF5格式气象数据文件进行空间裁剪的功能。
支持按经纬度范围裁剪数据，保持所有属性和组结构。

主要特性:
- 支持1D和2D经纬度网格
- 支持多维2D网格数据处理
- 保留所有HDF5属性和组结构
- 支持跨越180度经线的裁剪
- 智能识别经纬度维度
- 压缩输出以节省空间

作者: 陈嘉尚、胡俊耀
版本: 3.0
"""

import h5py
import numpy as np
import argparse
from pathlib import Path
import sys
import logging
from collections import defaultdict
from typing import Optional, List, Tuple, Union, Any


class HDF5CropperError(Exception):
    """HDF5裁剪器专用异常类"""
    pass


class HDF5Inspector:
    """HDF5文件结构检查器"""

    @staticmethod
    def inspect_structure(file_path: Union[str, Path], group_path: Optional[str] = None) -> None:
        """
        检查HDF5文件结构

        Args:
            file_path: HDF5文件路径
            group_path: 要检查的特定组路径，None表示检查整个文件
        """

        def print_structure(name: str, obj: Any) -> None:
            indent = "  " * name.count('/')
            if isinstance(obj, h5py.Group):
                attr_count = len(obj.attrs)
                print(f"{indent}{name}/ (Group) - {attr_count} attributes")
                if attr_count > 0:
                    for attr_name in obj.attrs.keys():
                        print(f"{indent}  @{attr_name}")
            elif isinstance(obj, h5py.Dataset):
                attr_count = len(obj.attrs)
                print(f"{indent}{name} (Dataset): shape={obj.shape}, dtype={obj.dtype} - {attr_count} attributes")

        try:
            with h5py.File(file_path, 'r') as f:
                # 显示根级别属性
                if len(f.attrs) > 0:
                    print(f"Root attributes ({len(f.attrs)}):")
                    for attr_name in f.attrs.keys():
                        print(f"  @{attr_name}")
                    print()

                if group_path:
                    if group_path in f:
                        print(f"Structure of group '{group_path}':")
                        target_group = f[group_path]
                        if len(target_group.attrs) > 0:
                            print(f"Group '{group_path}' attributes ({len(target_group.attrs)}):")
                            for attr_name in target_group.attrs.keys():
                                print(f"  @{attr_name}")
                            print()
                        target_group.visititems(print_structure)
                    else:
                        print(f"Group '{group_path}' not found")
                else:
                    print("HDF5 file structure:")
                    f.visititems(print_structure)
        except Exception as e:
            raise HDF5CropperError(f"检查文件结构时出错: {e}")


class AttributeCopier:
    """HDF5属性复制器"""

    @staticmethod
    def copy_group_attributes(source_group: h5py.Group, target_group: h5py.Group,
                              logger: Optional[logging.Logger] = None) -> int:
        """
        复制组的所有属性

        Args:
            source_group: 源组
            target_group: 目标组
            logger: 日志记录器

        Returns:
            复制的属性数量
        """
        copied_count = 0
        for attr_name, attr_value in source_group.attrs.items():
            try:
                target_group.attrs[attr_name] = attr_value
                copied_count += 1
            except Exception as e:
                if logger:
                    logger.warning(f"无法复制组属性 '{attr_name}': {e}")
                else:
                    print(f"警告: 无法复制组属性 '{attr_name}': {e}")

        if logger and copied_count > 0:
            logger.info(f"已复制组的 {copied_count} 个属性")

        return copied_count

    @staticmethod
    def copy_dataset_attributes(source_dataset: h5py.Dataset, target_dataset: h5py.Dataset) -> None:
        """
        复制数据集的所有属性

        Args:
            source_dataset: 源数据集
            target_dataset: 目标数据集
        """
        for attr_name, attr_value in source_dataset.attrs.items():
            try:
                target_dataset.attrs[attr_name] = attr_value
            except Exception as e:
                print(f"警告: 无法复制属性 '{attr_name}': {e}")


class GroupManager:
    """HDF5组管理器"""

    @staticmethod
    def create_hierarchy(file_handle: h5py.File, group_path: Optional[str],
                         source_file: Optional[h5py.File] = None,
                         logger: Optional[logging.Logger] = None) -> h5py.Group:
        """
        创建组层次结构并复制所有中间组的属性

        Args:
            file_handle: 目标HDF5文件句柄
            group_path: 要创建的组路径
            source_file: 源HDF5文件句柄（用于复制属性）
            logger: 日志记录器

        Returns:
            创建的组对象
        """
        if group_path is None or group_path == '':
            return file_handle

        # 分解路径并逐层创建
        path_parts = group_path.strip('/').split('/')
        current_path = ''
        current_group = file_handle

        for part in path_parts:
            if part:  # 跳过空字符串
                current_path = current_path + '/' + part if current_path else part

                if current_path not in current_group:
                    # 创建新组
                    new_group = current_group.create_group(part)
                    if logger:
                        logger.info(f"创建组: {current_path}")

                    # 如果源文件存在，复制组属性
                    if source_file and current_path in source_file:
                        AttributeCopier.copy_group_attributes(source_file[current_path], new_group, logger)

                    current_group = new_group
                else:
                    # 组已存在，移动到该组
                    current_group = current_group[part]
                    if logger:
                        logger.info(f"使用现有组: {current_path}")

        return current_group


class DimensionAnalyzer:
    """经纬度维度分析器"""

    @staticmethod
    def find_lat_lon_dimensions(lat_data: np.ndarray, lon_data: np.ndarray,
                                data_shape: Tuple[int, ...]) -> Tuple[
        Optional[int], Optional[int], bool, Optional[List[int]]]:
        """
        智能识别经纬度在数据集中的维度位置
        支持多维2D网格数据的处理

        Args:
            lat_data: 纬度数据
            lon_data: 经度数据
            data_shape: 数据形状

        Returns:
            (lat_dim, lon_dim, is_2d_grid, extra_dims)
        """
        lat_shape = lat_data.shape
        lon_shape = lon_data.shape

        # 如果经纬度是1D数组
        if len(lat_shape) == 1 and len(lon_shape) == 1:
            # 寻找与经纬度长度匹配的维度
            lat_dim = None
            lon_dim = None

            for i, dim_size in enumerate(data_shape):
                if dim_size == lat_shape[0]:
                    lat_dim = i
                if dim_size == lon_shape[0]:
                    lon_dim = i

            return lat_dim, lon_dim, False, None  # False表示不是2D网格, None表示没有额外维度

        # 如果经纬度是2D数组
        elif len(lat_shape) == 2 and len(lon_shape) == 2:
            # 检查哪些维度与经纬度网格匹配
            matching_dims = []
            extra_dims = []  # 存储额外的维度（如时间、质量级别等）

            # 对于多维数据，寻找与经纬度网格匹配的连续维度
            for i in range(len(data_shape) - 1):
                if (data_shape[i] == lat_shape[0] and
                        data_shape[i + 1] == lat_shape[1]):
                    matching_dims.append((i, i + 1))
                    # 找出其他维度作为额外维度
                    for j, dim_size in enumerate(data_shape):
                        if j != i and j != i + 1:
                            extra_dims.append(j)
                    break

            if matching_dims:
                lat_dim, lon_dim = matching_dims[0]
                return lat_dim, lon_dim, True, extra_dims  # True表示是2D网格，extra_dims是额外维度

            # 如果没有找到连续的匹配维度，尝试分别匹配
            lat_dim = None
            lon_dim = None
            for i, dim_size in enumerate(data_shape):
                if dim_size == lat_shape[0] and lat_dim is None:
                    lat_dim = i
                elif dim_size == lat_shape[1] and lon_dim is None:
                    lon_dim = i

            if lat_dim is not None and lon_dim is not None:
                extra_dims = [i for i in range(len(data_shape)) if i not in [lat_dim, lon_dim]]
                return lat_dim, lon_dim, True, extra_dims

        return None, None, False, None


class DataCropper:
    """数据裁剪器"""

    @staticmethod
    def crop_multidim_2d_grid(data: np.ndarray, lat_indices: np.ndarray, lon_indices: np.ndarray,
                              lat_dim: int, lon_dim: int, extra_dims: List[int],
                              logger: logging.Logger) -> np.ndarray:
        """
        对多维2D网格数据进行裁剪

        Args:
            data: 原始数据数组
            lat_indices: 纬度索引
            lon_indices: 经度索引
            lat_dim: 纬度维度位置
            lon_dim: 经度维度位置
            extra_dims: 额外维度列表
            logger: 日志记录器

        Returns:
            裁剪后的数据
        """
        data_shape = data.shape
        logger.info(f"处理多维2D网格数据，原始形状: {data_shape}")
        logger.info(f"纬度维度: {lat_dim}, 经度维度: {lon_dim}, 额外维度: {extra_dims}")
        logger.info(f"纬度索引数量: {len(lat_indices)}, 经度索引数量: {len(lon_indices)}")

        # 对于多维2D网格数据，需要特别处理索引
        if len(data_shape) == 3 and lat_dim == 0 and lon_dim == 1:
            # 最常见的情况：(lat, lon, other_dim)
            cropped_data = data[np.ix_(lat_indices, lon_indices, range(data_shape[2]))]
        elif len(data_shape) == 3:
            # 其他三维情况，根据维度位置构建索引
            indices = [slice(None)] * len(data_shape)
            indices[lat_dim] = lat_indices
            indices[lon_dim] = lon_indices

            # 对于额外维度，保持完整
            for dim in extra_dims:
                indices[dim] = slice(None)

            cropped_data = data[tuple(indices)]
        else:
            # 通用多维情况
            indices = [slice(None)] * len(data_shape)
            indices[lat_dim] = lat_indices
            indices[lon_dim] = lon_indices

            # 对于额外维度，保持完整
            for dim in extra_dims:
                indices[dim] = slice(None)

            cropped_data = data[tuple(indices)]

        logger.info(f"裁剪后形状: {cropped_data.shape}")
        return cropped_data


class HDF5Cropper:
    """HDF5文件裁剪器主类"""

    def __init__(self, verbose: bool = False):
        """
        初始化HDF5裁剪器

        Args:
            verbose: 是否显示详细日志
        """
        self.verbose = verbose
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        """设置日志记录器"""
        logger = logging.getLogger(__name__)
        if self.verbose:
            logger.setLevel(logging.INFO)
            if not logger.handlers:
                formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
                ch = logging.StreamHandler()
                ch.setFormatter(formatter)
                logger.addHandler(ch)
        return logger

    def validate_input_file(self, input_path: Union[str, Path]) -> None:
        """
        验证输入文件

        Args:
            input_path: 输入文件路径

        Raises:
            HDF5CropperError: 文件不存在或不是有效的HDF5文件
        """
        input_path = Path(input_path)
        if not input_path.exists():
            raise HDF5CropperError(f"输入文件 '{input_path}' 不存在")

        try:
            with h5py.File(input_path, 'r'):
                pass
        except Exception:
            raise HDF5CropperError(f"文件 '{input_path}' 不是有效的HDF5格式")

    def normalize_coordinates(self, lat_min: float, lat_max: float,
                              lon_min: float, lon_max: float) -> Tuple[float, float, float, float]:
        """
        标准化坐标范围

        Args:
            lat_min, lat_max: 纬度范围
            lon_min, lon_max: 经度范围

        Returns:
            标准化后的坐标范围
        """
        lat_min = max(-90.0, lat_min)
        lat_max = min(90.0, lat_max)
        lon_min = ((lon_min + 180) % 360) - 180
        lon_max = ((lon_max + 180) % 360) - 180

        return lat_min, lat_max, lon_min, lon_max

    def crop_file(self, input_hdf: Union[str, Path], output_hdf: Union[str, Path],
                  lat_min: float, lat_max: float, lon_min: float, lon_max: float,
                  lat_var: str, lon_var: str, data_vars: Optional[List[str]] = None,
                  data_group: Optional[str] = None, latlon_group: Optional[str] = None) -> str:
        """
        裁剪HDF5文件到指定的经纬度范围

        Args:
            input_hdf: 输入HDF5文件路径
            output_hdf: 输出HDF5文件路径
            lat_min, lat_max: 纬度范围
            lon_min, lon_max: 经度范围
            lat_var: 纬度变量名
            lon_var: 经度变量名
            data_vars: 需要裁剪的数据集名称列表
            data_group: 数据所在的组路径
            latlon_group: 经纬度所在的组路径

        Returns:
            输出文件路径

        Raises:
            HDF5CropperError: 裁剪过程中的各种错误
        """
        # 验证输入文件
        self.validate_input_file(input_hdf)

        # 标准化坐标
        lat_min, lat_max, lon_min, lon_max = self.normalize_coordinates(
            lat_min, lat_max, lon_min, lon_max
        )

        self.logger.info(f"裁剪范围: 纬度 {lat_min}°N-{lat_max}°N, 经度 {lon_min}°E-{lon_max}°E")

        # 处理跨越180度经线的情况
        if lon_min > lon_max:
            lon_ranges = [(lon_min, 180.0), (-180.0, lon_max)]
            self.logger.info("检测到跨越180度经线的裁剪范围，将分两段处理")
        else:
            lon_ranges = [(lon_min, lon_max)]

        # 创建输出文件路径
        Path(output_hdf).parent.mkdir(parents=True, exist_ok=True)

        try:
            with h5py.File(input_hdf, 'r') as fin, h5py.File(output_hdf, 'w') as fout:
                self._process_file(fin, fout, lat_min, lat_max, lon_ranges,
                                   lat_var, lon_var, data_vars, data_group, latlon_group)

        except Exception as e:
            # 如果出错，删除可能创建的输出文件
            if Path(output_hdf).exists():
                Path(output_hdf).unlink()
            raise HDF5CropperError(f"处理文件时出错: {e}")

        return str(output_hdf)

    def _process_file(self, fin: h5py.File, fout: h5py.File, lat_min: float, lat_max: float,
                      lon_ranges: List[Tuple[float, float]], lat_var: str, lon_var: str,
                      data_vars: Optional[List[str]], data_group: Optional[str],
                      latlon_group: Optional[str]) -> None:
        """处理文件的内部方法"""
        # 复制全局属性
        root_attrs_count = AttributeCopier.copy_group_attributes(fin, fout, self.logger)
        self.logger.info(f"已复制根级别的 {root_attrs_count} 个属性")

        # 设置组
        if data_group is None:
            output_data_group = fout
            input_data_group = fin
        else:
            output_data_group = GroupManager.create_hierarchy(fout, data_group, fin, self.logger)
            input_data_group = fin[data_group]

        if latlon_group is None or latlon_group == data_group:
            output_latlon_group = output_data_group
            input_latlon_group = input_data_group
        else:
            output_latlon_group = GroupManager.create_hierarchy(fout, latlon_group, fin, self.logger)
            input_latlon_group = fin[latlon_group]

        # 检查经纬度变量
        if lat_var not in input_latlon_group or lon_var not in input_latlon_group:
            available_vars = list(input_latlon_group.keys())
            raise HDF5CropperError(f"找不到经纬度变量: {lat_var} 或 {lon_var}. 可用变量: {available_vars}")

        # 读取经纬度数据
        lats = input_latlon_group[lat_var][:]
        lons = input_latlon_group[lon_var][:]

        self.logger.info(f"经纬度数据形状: lat={lats.shape}, lon={lons.shape}")

        # 获取索引
        lat_indices, all_lon_indices, is_2d_grid = self._get_indices(
            lats, lons, lat_min, lat_max, lon_ranges
        )

        # 处理经纬度数据
        self._process_coordinates(
            lats, lons, lat_indices, all_lon_indices, is_2d_grid,
            lat_var, lon_var, input_latlon_group, output_latlon_group
        )

        # 处理数据集
        self._process_datasets(
            input_data_group, output_data_group, data_vars, lats, lons,
            lat_indices, all_lon_indices, is_2d_grid, lat_var, lon_var, data_group
        )

    def _get_indices(self, lats: np.ndarray, lons: np.ndarray, lat_min: float, lat_max: float,
                     lon_ranges: List[Tuple[float, float]]) -> Tuple[np.ndarray, List[int], bool]:
        """获取经纬度索引"""
        # 过滤填充值
        fill_value = -9999.9
        valid_lats = lats[lats > fill_value]
        valid_lons = lons[lons > fill_value]

        if valid_lats.size > 0 and valid_lons.size > 0:
            self.logger.info(f"文件的有效纬度范围: {np.min(valid_lats)}°N - {np.max(valid_lats)}°N")
            self.logger.info(f"文件的有效经度范围: {np.min(valid_lons)}°E - {np.max(valid_lons)}°E")

        # 找到符合条件的索引
        if lats.ndim == 1 and lons.ndim == 1:
            # 1D经纬度数组
            lat_indices = np.where((lats >= lat_min) & (lats <= lat_max))[0]

            all_lon_indices = []
            for lon_min_range, lon_max_range in lon_ranges:
                lon_indices = np.where((lons >= lon_min_range) & (lons <= lon_max_range))[0]
                if len(lon_indices) > 0:
                    all_lon_indices.extend(lon_indices)
            all_lon_indices = sorted(set(all_lon_indices))
            is_2d_grid = False

        elif lats.ndim == 2 and lons.ndim == 2:
            # 2D经纬度网格
            lat_mask = (lats >= lat_min) & (lats <= lat_max)

            lon_mask = np.zeros_like(lats, dtype=bool)
            for lon_min_range, lon_max_range in lon_ranges:
                lon_mask |= (lons >= lon_min_range) & (lons <= lon_max_range)

            combined_mask = lat_mask & lon_mask

            if not np.any(combined_mask):
                raise HDF5CropperError("在指定范围内没有找到符合条件的点")

            valid_rows, valid_cols = np.where(combined_mask)
            lat_indices = np.unique(valid_rows)
            all_lon_indices = np.unique(valid_cols)
            is_2d_grid = True

        else:
            raise HDF5CropperError(f"不支持的经纬度维度: lat={lats.ndim}D, lon={lons.ndim}D")

        if len(lat_indices) == 0:
            raise HDF5CropperError("在指定范围内没有找到纬度点")
        if len(all_lon_indices) == 0:
            raise HDF5CropperError("在指定范围内没有找到经度点")

        self.logger.info(f"裁剪后纬度点数: {len(lat_indices)}, 经度点数: {len(all_lon_indices)}")

        return lat_indices, all_lon_indices, is_2d_grid

    def _process_coordinates(self, lats: np.ndarray, lons: np.ndarray,
                             lat_indices: np.ndarray, all_lon_indices: List[int], is_2d_grid: bool,
                             lat_var: str, lon_var: str, input_latlon_group: h5py.Group,
                             output_latlon_group: h5py.Group) -> None:
        """处理经纬度坐标"""
        # 裁剪经纬度数据
        if is_2d_grid:
            cropped_lats = lats[np.ix_(lat_indices, all_lon_indices)]
            cropped_lons = lons[np.ix_(lat_indices, all_lon_indices)]
        else:
            cropped_lats = lats[lat_indices]
            cropped_lons = lons[all_lon_indices]

        # 创建数据集
        if lat_var in output_latlon_group:
            del output_latlon_group[lat_var]
        if lon_var in output_latlon_group:
            del output_latlon_group[lon_var]

        lat_dataset = output_latlon_group.create_dataset(
            lat_var, data=cropped_lats, compression='gzip', compression_opts=4
        )
        lon_dataset = output_latlon_group.create_dataset(
            lon_var, data=cropped_lons, compression='gzip', compression_opts=4
        )

        # 复制属性
        AttributeCopier.copy_dataset_attributes(input_latlon_group[lat_var], lat_dataset)
        AttributeCopier.copy_dataset_attributes(input_latlon_group[lon_var], lon_dataset)

        self.logger.info(f"已复制纬度变量 '{lat_var}' 的 {len(input_latlon_group[lat_var].attrs)} 个属性")
        self.logger.info(f"已复制经度变量 '{lon_var}' 的 {len(input_latlon_group[lon_var].attrs)} 个属性")

    def _process_datasets(self, input_data_group: h5py.Group, output_data_group: h5py.Group,
                          data_vars: Optional[List[str]], lats: np.ndarray, lons: np.ndarray,
                          lat_indices: np.ndarray, all_lon_indices: List[int], is_2d_grid: bool,
                          lat_var: str, lon_var: str, data_group: Optional[str]) -> None:
        """处理数据集"""
        processed_datasets = 0
        skipped_datasets = 0

        # 如果未指定数据集，则处理所有可能的数据集
        if data_vars is None:
            data_vars = []
            for name in input_data_group:
                if name in [lat_var, lon_var]:
                    continue
                obj = input_data_group[name]
                if isinstance(obj, h5py.Dataset):
                    data_vars.append(name)

        for var_name in data_vars:
            try:
                dataset = self._get_dataset(input_data_group, var_name, data_group)
                if dataset is None:
                    self.logger.warning(f"数据集 '{var_name}' 不存在，跳过")
                    skipped_datasets += 1
                    continue

                cropped_data = self._crop_dataset(dataset, lats, lons, lat_indices, all_lon_indices, is_2d_grid,
                                                  var_name)
                if cropped_data is None:
                    skipped_datasets += 1
                    continue

                self._save_dataset(output_data_group, var_name, cropped_data, dataset, data_group)
                processed_datasets += 1

            except Exception as e:
                self.logger.error(f"处理数据集 '{var_name}' 时出错: {e}")
                skipped_datasets += 1

        self.logger.info(f"处理完成! 总数据集数: {processed_datasets + skipped_datasets}, "
                         f"成功: {processed_datasets}, 跳过: {skipped_datasets}")

    def _get_dataset(self, input_data_group: h5py.Group, var_name: str,
                     data_group: Optional[str]) -> Optional[h5py.Dataset]:
        """获取数据集"""
        # 处理绝对路径和相对路径
        if data_group is not None and not var_name.startswith(data_group):
            full_var_name = f"{data_group}/{var_name}"
        else:
            full_var_name = var_name

        if var_name in input_data_group:
            return input_data_group[var_name]
        return None

    def _crop_dataset(self, dataset: h5py.Dataset, lats: np.ndarray, lons: np.ndarray,
                      lat_indices: np.ndarray, all_lon_indices: List[int], is_2d_grid: bool,
                      var_name: str) -> Optional[np.ndarray]:
        """裁剪数据集"""
        data_shape = dataset.shape
        self.logger.info(f"处理数据集 '{var_name}', 形状: {data_shape}")

        # 智能识别经纬度维度
        lat_dim, lon_dim, is_data_2d_grid, extra_dims = DimensionAnalyzer.find_lat_lon_dimensions(
            lats, lons, data_shape
        )

        if lat_dim is None or lon_dim is None:
            self.logger.warning(f"无法识别数据集 '{var_name}' 中的经纬度维度，跳过")
            return None

        # 根据数据类型进行裁剪
        if is_data_2d_grid:
            if extra_dims is not None and len(extra_dims) > 0:
                # 多维2D网格数据
                self.logger.info(f"检测到多维2D网格数据，额外维度数量: {len(extra_dims)}")
                cropped_data = DataCropper.crop_multidim_2d_grid(
                    dataset[:], lat_indices, all_lon_indices,
                    lat_dim, lon_dim, extra_dims, self.logger
                )
            else:
                # 纯2D网格数据
                if len(data_shape) == 2:
                    cropped_data = dataset[:][np.ix_(lat_indices, all_lon_indices)]
                else:
                    self.logger.warning(f"数据集 '{var_name}' 的维度结构不支持，跳过")
                    return None
        else:
            # 1D数组情况
            indices = [slice(None)] * len(data_shape)
            indices[lat_dim] = lat_indices
            indices[lon_dim] = all_lon_indices
            cropped_data = dataset[tuple(indices)]

        self.logger.info(f"成功处理数据集 '{var_name}', 裁剪后形状: {cropped_data.shape}")

        if extra_dims is not None and len(extra_dims) > 0:
            self.logger.info(f"多维数据集 '{var_name}' 的额外维度已保持完整，对每个2D切片都应用了相同的空间裁剪")

        return cropped_data

    def _save_dataset(self, output_data_group: h5py.Group, var_name: str,
                      cropped_data: np.ndarray, original_dataset: h5py.Dataset,
                      data_group: Optional[str]) -> None:
        """保存数据集"""
        rel_var_name = var_name if data_group is None else var_name.replace(f"{data_group}/", "")

        # 安全创建数据集：如果数据集已存在则先删除再创建
        if rel_var_name in output_data_group:
            self.logger.info(f"数据集 '{rel_var_name}' 已存在，将覆盖")
            del output_data_group[rel_var_name]

        new_dataset = output_data_group.create_dataset(
            rel_var_name, data=cropped_data,
            compression='gzip', compression_opts=4
        )

        # 复制属性
        AttributeCopier.copy_dataset_attributes(original_dataset, new_dataset)
        self.logger.info(f"已复制数据集 '{var_name}' 的 {len(original_dataset.attrs)} 个属性")


def crop_hdf5_file(input_hdf: Union[str, Path], output_hdf: Union[str, Path],
                   lat_min: float, lat_max: float, lon_min: float, lon_max: float,
                   lat_var: str, lon_var: str, data_vars: Optional[List[str]] = None,
                   data_group: Optional[str] = None, latlon_group: Optional[str] = None,
                   verbose: bool = False) -> str:
    """
    裁剪HDF5文件到指定的经纬度范围

    这是一个便捷函数，封装了HDF5Cropper类的功能。

    Args:
        input_hdf: 输入HDF5文件路径
        output_hdf: 输出HDF5文件路径
        lat_min, lat_max: 纬度范围 (度)
        lon_min, lon_max: 经度范围 (度)
        lat_var: 纬度变量名
        lon_var: 经度变量名
        data_vars: 需要裁剪的数据集名称列表，None表示处理所有符合条件的数据集
        data_group: 数据所在的组路径，None表示根目录
        latlon_group: 经纬度所在的组路径，None表示根目录
        verbose: 是否显示详细处理信息

    Returns:
        输出文件路径

    Raises:
        HDF5CropperError: 裁剪过程中的各种错误

    Example:
        >>> crop_hdf5_file(
        ...     'input.h5', 'output.h5',
        ...     lat_min=30.0, lat_max=40.0,
        ...     lon_min=110.0, lon_max=120.0,
        ...     lat_var='latitude', lon_var='longitude',
        ...     verbose=True
        ... )
        'output.h5'
    """
    cropper = HDF5Cropper(verbose=verbose)
    return cropper.crop_file(
        input_hdf, output_hdf, lat_min, lat_max, lon_min, lon_max,
        lat_var, lon_var, data_vars, data_group, latlon_group
    )


def inspect_hdf5_structure(file_path: Union[str, Path], group_path: Optional[str] = None) -> None:
    """
    检查HDF5文件结构

    这是一个便捷函数，用于检查HDF5文件的结构。

    Args:
        file_path: HDF5文件路径
        group_path: 要检查的特定组路径，None表示检查整个文件

    Raises:
        HDF5CropperError: 检查文件时出错

    Example:
        >>> inspect_hdf5_structure('data.h5')
        HDF5 file structure:
          /group1/ (Group) - 2 attributes
            @description
            @units
          ...
    """
    HDF5Inspector.inspect_structure(file_path, group_path)


def main():
    """
    命令行接口主函数

    提供命令行方式使用HDF5裁剪功能。
    """
    parser = argparse.ArgumentParser(
        description='裁剪HDF5格式文件到指定的经纬度范围，支持多维2D网格数据和组属性复制',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 检查文件结构
  %(prog)s -i data.h5 --inspect

  # 裁剪文件
  %(prog)s -i input.h5 -o output.h5 \\
    --lat-min 30 --lat-max 40 --lon-min 110 --lon-max 120 \\
    --lat-var latitude --lon-var longitude -v

  # 指定数据组和变量
  %(prog)s -i input.h5 -o output.h5 \\
    --lat-min 30 --lat-max 40 --lon-min 110 --lon-max 120 \\
    --lat-var lat --lon-var lon \\
    -g /HDFEOS/GRIDS/MODIS_Grid_8Day_1km_LST \\
    -d LST_Day_1km LST_Night_1km -v
        """
    )

    parser.add_argument('-i', '--input', required=True,
                        help='输入HDF5文件路径')
    parser.add_argument('--inspect', action='store_true',
                        help='仅检查文件结构，不执行裁剪')
    parser.add_argument('-o', '--output',
                        help='输出HDF5文件路径')
    parser.add_argument('--lat-min', type=float,
                        help='最小纬度 (°N)')
    parser.add_argument('--lat-max', type=float,
                        help='最大纬度 (°N)')
    parser.add_argument('--lon-min', type=float,
                        help='最小经度 (°E)')
    parser.add_argument('--lon-max', type=float,
                        help='最大经度 (°E)')
    parser.add_argument('--lat-var',
                        help='纬度变量名')
    parser.add_argument('--lon-var',
                        help='经度变量名')
    parser.add_argument('-d', '--data-vars', nargs='+',
                        help='需要裁剪的数据集名称列表，默认处理所有符合条件的数据集')
    parser.add_argument('-g', '--data-group',
                        help='数据所在的组路径，默认为根目录')
    parser.add_argument('--latlon-group',
                        help='经纬度所在的组路径，默认为根目录')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='显示详细处理信息')

    args = parser.parse_args()

    try:
        # 如果只是检查结构
        if args.inspect:
            inspect_hdf5_structure(args.input, args.data_group)
            return

        # 检查裁剪所需的参数
        required_for_crop = ['output', 'lat_min', 'lat_max', 'lon_min', 'lon_max', 'lat_var', 'lon_var']
        missing_args = [arg for arg in required_for_crop if getattr(args, arg) is None]
        if missing_args:
            print(f"错误: 执行裁剪需要以下参数: {missing_args}")
            sys.exit(1)

        # 执行裁剪
        output_file = crop_hdf5_file(
            args.input,
            args.output,
            args.lat_min,
            args.lat_max,
            args.lon_min,
            args.lon_max,
            args.lat_var,
            args.lon_var,
            args.data_vars,
            args.data_group,
            args.latlon_group,
            args.verbose
        )
        print(f"成功! 裁剪后的HDF5文件已保存至: {output_file}")

    except HDF5CropperError as e:
        print(f"HDF5裁剪错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"执行裁剪时出错: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()