#!/usr/bin/env python3
import numpy as np
import h5py
import os
import time
import argparse
from scipy.spatial import cKDTree
from tqdm import tqdm
from joblib import Parallel, delayed


def setup_input_interfaces():
    parser = argparse.ArgumentParser(description='气象数据插值工具（支持三维范围指定）')
    parser.add_argument('--input-file', required=True,
                        help='JuiceFS中的HDF5文件路径（如：/mnt/juicefs/data/file.h5）')
    parser.add_argument('--var-name', required=True,
                        help='要插值的变量名（如：airTemperature、pressure）')
    # 经纬度范围参数（空间范围）
    parser.add_argument('--lon-min', type=float, help='经度最小值（可选）')
    parser.add_argument('--lon-max', type=float, help='经度最大值（可选）')
    parser.add_argument('--lat-min', type=float, help='纬度最小值（可选）')
    parser.add_argument('--lat-max', type=float, help='纬度最大值（可选）')
    # 新增：垂直层范围参数（三维变量专用）
    parser.add_argument('--layer-min', type=int, help='起始层索引（从0开始，可选）')
    parser.add_argument('--layer-max', type=int, help='结束层索引（从0开始，可选）')
    # 其他参数
    parser.add_argument('--resolution', type=float, default=0.1,
                        help='目标网格分辨率（单位：度，默认0.1度）')
    parser.add_argument('--output-dir', type=str, help='结果保存目录（默认与输入同目录）')
    return parser.parse_args()


args = setup_input_interfaces()
INPUT_FILE = args.input_file
VAR_NAME = args.var_name
GRID_RESOLUTION = args.resolution  # 插值密度

# 输出路径设置
output_dir = args.output_dir if args.output_dir else os.path.dirname(INPUT_FILE)
os.makedirs(output_dir, exist_ok=True)
OUTPUT_FILE = os.path.join(
    output_dir,
    f"{os.path.splitext(os.path.basename(INPUT_FILE))[0]}_{VAR_NAME}_processed.h5"
)

# 常量与参数配置
CUSTOM_MISSING = -9999.9
MAX_NEIGHBORS = 20
POWER = 2
MAX_DISTANCE = 5.0
MIN_NEIGHBORS = 1
BLOCK_SIZE = 100
MAX_POINTS_PER_BLOCK = 5000
PARALLEL = True
NUM_CORES = -1  # 使用所有可用核心


def read_hdf5_data(file_path, var_name):
    print(f"读取文件: {file_path}，变量: {var_name}")
    with h5py.File(file_path, 'r') as f:
        longitude = f['FS/Longitude'][:].astype(np.float32)
        latitude = f['FS/Latitude'][:].astype(np.float32)
        data = f[f'FS/VER/{var_name}'][:].astype(np.float32)

    # 维度校验（支持二维和三维）
    assert longitude.shape == latitude.shape, "经纬度维度不匹配"
    assert len(data.shape) in (2, 3), f"变量必须为二维或三维，实际形状: {data.shape}"
    assert data.shape[0:2] == longitude.shape, "变量前两维与经纬度不匹配"

    # 二维变量转为三维单一层格式
    if len(data.shape) == 2:
        print(f"检测到二维变量，自动转换为单一层格式 (添加维度)")
        data = data.reshape(data.shape[0], data.shape[1], 1)

    return longitude, latitude, data


def preprocess_data(longitude, latitude, data, layer_min=None, layer_max=None):
    print("预处理数据...")
    start = time.time()
    filled_data = data.copy()
    total_layers = filled_data.shape[2]

    # 三维变量的层范围过滤
    if total_layers > 1:  # 仅对三维变量生效
        # 处理层索引边界
        if layer_min is not None:
            layer_min = max(0, min(layer_min, total_layers - 1))
        else:
            layer_min = 0
        if layer_max is not None:
            layer_max = max(layer_min, min(layer_max, total_layers - 1))
        else:
            layer_max = total_layers - 1

        # 截取指定范围的层
        filled_data = filled_data[:, :, layer_min:layer_max + 1]
        total_layers = filled_data.shape[2]  # 更新层数
        print(f"三维变量层范围过滤：保留第{layer_min}至{layer_max}层，共{total_layers}层")

    rows, cols = longitude.shape

    # 经纬度有效性校验
    lon_valid = ~np.isnan(longitude) & ~np.isinf(longitude) & \
                (longitude >= -180) & (longitude <= 180)
    lat_valid = ~np.isnan(latitude) & ~np.isinf(latitude) & \
                (latitude >= -90) & (latitude <= 90)
    coord_valid = lon_valid & lat_valid
    print(f"经纬度有效点数量: {np.sum(coord_valid)}")

    # 样本层缺失值统计
    sample_layer = filled_data[:, :, 0]
    valid_sample = sample_layer[coord_valid]
    print(f"样本层缺失值数量: {np.sum(valid_sample == CUSTOM_MISSING)}")

    global_valid = coord_valid.copy()
    total_missing = 0

    # 逐层处理缺失值（仅处理过滤后的层）
    for layer in range(total_layers):
        data_layer = filled_data[:, :, layer]
        missing_mask = (data_layer == CUSTOM_MISSING) | np.isnan(data_layer) | np.isinf(data_layer)
        missing_mask &= coord_valid
        missing_count = np.sum(missing_mask)

        if layer % 10 == 0:
            print(f"层 {layer + 1}/{total_layers} 缺失值: {missing_count}")

        if missing_count > 0:
            total_missing += missing_count
            valid_mask = ~missing_mask & coord_valid
            valid_lon = longitude[valid_mask]
            valid_lat = latitude[valid_mask]
            valid_data = data_layer[valid_mask]

            if len(valid_data) < 5:
                print(f"警告：层 {layer} 有效点不足，使用均值填补")
                layer_mean = np.nanmean(data_layer[coord_valid])
                data_layer[missing_mask] = layer_mean
                filled_data[:, :, layer] = data_layer
                global_valid &= ~missing_mask
                continue

            # IDW插值填补（不变）
            tree = cKDTree(np.column_stack((valid_lon, valid_lat)))
            missing_points = np.column_stack((longitude[missing_mask], latitude[missing_mask]))
            distances, indices = tree.query(missing_points, k=min(MAX_NEIGHBORS, len(valid_data)),
                                            distance_upper_bound=MAX_DISTANCE)

            for i, (row, col) in enumerate(np.argwhere(missing_mask)):
                if np.isscalar(distances[i]):
                    valid_nb = distances[i] < MAX_DISTANCE
                else:
                    valid_nb = distances[i] < MAX_DISTANCE

                if np.sum(valid_nb) >= MIN_NEIGHBORS:
                    if np.isscalar(distances[i]):
                        data_layer[row, col] = valid_data[indices[i]]
                    else:
                        weights = 1.0 / (distances[i][valid_nb] ** POWER)
                        weights /= weights.sum()
                        data_layer[row, col] = np.sum(valid_data[indices[i][valid_nb]] * weights)
                else:
                    data_layer[row, col] = np.nanmean(valid_data)

            filled_data[:, :, layer] = data_layer
            global_valid &= ~missing_mask

    # 提取有效数据与范围
    valid_lon = longitude[global_valid]
    valid_lat = latitude[global_valid]
    if len(valid_lon) == 0:
        raise ValueError("无有效数据点")

    valid_data_layers = [filled_data[:, :, l][global_valid] for l in range(total_layers)]
    lon_min, lon_max = valid_lon.min(), valid_lon.max()
    lat_min, lat_max = valid_lat.min(), valid_lat.max()

    # 应用用户指定经纬度范围
    if args.lon_min is not None:
        lon_min = max(args.lon_min, -180.0)
        mask = valid_lon >= lon_min
        valid_lon, valid_lat = valid_lon[mask], valid_lat[mask]
        valid_data_layers = [l[mask] for l in valid_data_layers]
    if args.lon_max is not None:
        lon_max = min(args.lon_max, 180.0)
        mask = valid_lon <= lon_max
        valid_lon, valid_lat = valid_lon[mask], valid_lat[mask]
        valid_data_layers = [l[mask] for l in valid_data_layers]
    if args.lat_min is not None:
        lat_min = max(args.lat_min, -90.0)
        mask = valid_lat >= lat_min
        valid_lon, valid_lat = valid_lon[mask], valid_lat[mask]
        valid_data_layers = [l[mask] for l in valid_data_layers]
    if args.lat_max is not None:
        lat_max = min(args.lat_max, 90.0)
        mask = valid_lat <= lat_max
        valid_lon, valid_lat = valid_lon[mask], valid_lat[mask]
        valid_data_layers = [l[mask] for l in valid_data_layers]

    print(f"预处理耗时: {time.time() - start:.2f}秒，总缺失值: {total_missing}")
    return valid_lon, valid_lat, valid_data_layers, lon_min, lon_max, lat_min, lat_max, total_layers


def create_interpolation_grid(lon_min, lon_max, lat_min, lat_max, resolution):
    lon_min = max(lon_min, -180.0)
    lon_max = min(lon_max, 180.0)
    lat_min = max(lat_min, -90.0)
    lat_max = min(lat_max, 90.0)
    margin = max(resolution * 2, 0.1)
    grid_lon = np.arange(lon_min - margin, lon_max + margin, resolution)
    grid_lat = np.arange(lat_min - margin, lat_max + margin, resolution)
    print(f"插值网格规模: {len(grid_lon)}x{len(grid_lat)} = {len(grid_lon) * len(grid_lat):,}点")
    return grid_lon, grid_lat


def idw_interpolation(lon_valid, lat_valid, data_valid, query_points):
    if len(lon_valid) == 0:
        return np.full(len(query_points), CUSTOM_MISSING, dtype=np.float32)

    tree = cKDTree(np.column_stack((lon_valid, lat_valid)))
    distances, indices = tree.query(query_points, k=min(MAX_NEIGHBORS, len(lon_valid)),
                                    distance_upper_bound=MAX_DISTANCE)
    result = np.full(len(query_points), CUSTOM_MISSING, dtype=np.float32)

    for i in range(len(query_points)):
        if np.isscalar(distances[i]):
            if distances[i] < MAX_DISTANCE:
                result[i] = data_valid[indices[i]]
            continue

        valid_nb = distances[i] < MAX_DISTANCE
        if np.sum(valid_nb) >= MIN_NEIGHBORS:
            weights = 1.0 / (distances[i][valid_nb] ** POWER)
            weights /= weights.sum()
            result[i] = np.sum(data_valid[indices[i][valid_nb]] * weights)
        else:
            result[i] = np.nanmean(data_valid)

    return result


def process_block(args):
    block_id, lon_valid, lat_valid, data_valid, grid_lon, grid_lat, i_start, i_end, j_start, j_end = args
    lon_block = grid_lon[j_start:j_end]
    lat_block = grid_lat[i_start:i_end]
    lon_grid, lat_grid = np.meshgrid(lon_block, lat_block)
    query_points = np.column_stack((lon_grid.flatten(), lat_grid.flatten()))
    block_shape = (i_end - i_start, j_end - j_start)

    in_range = (lon_valid >= lon_block.min() - MAX_DISTANCE) & (lon_valid <= lon_block.max() + MAX_DISTANCE) & \
               (lat_valid >= lat_block.min() - MAX_DISTANCE) & (lat_valid <= lat_block.max() + MAX_DISTANCE)

    if np.sum(in_range) < MIN_NEIGHBORS:
        tree = cKDTree(np.column_stack((lon_valid, lat_valid)))
        _, indices = tree.query(query_points, k=1)
        return data_valid[indices].reshape(block_shape), i_start, i_end, j_start, j_end

    lon_in_range = lon_valid[in_range]
    lat_in_range = lat_valid[in_range]
    data_in_range = data_valid[in_range]

    if len(query_points) > MAX_POINTS_PER_BLOCK:
        sub_results = []
        for q_start in range(0, len(query_points), MAX_POINTS_PER_BLOCK):
            q_end = min(q_start + MAX_POINTS_PER_BLOCK, len(query_points))
            sub_results.append(
                idw_interpolation(lon_in_range, lat_in_range, data_in_range, query_points[q_start:q_end]))
        result_flat = np.concatenate(sub_results)
    else:
        result_flat = idw_interpolation(lon_in_range, lat_in_range, data_in_range, query_points)

    return result_flat.reshape(block_shape), i_start, i_end, j_start, j_end


def batch_idw(lon_valid, lat_valid, data_valid, grid_lon, grid_lat, layer_idx):
    print(f"\n===== 插值层 {layer_idx + 1} =====")
    start = time.time()
    blocks = []
    for i in range(0, len(grid_lat), BLOCK_SIZE):
        i_end = min(i + BLOCK_SIZE, len(grid_lat))
        for j in range(0, len(grid_lon), BLOCK_SIZE):
            j_end = min(j + BLOCK_SIZE, len(grid_lon))
            blocks.append((f"block_{i // BLOCK_SIZE}_{j // BLOCK_SIZE}",
                           lon_valid, lat_valid, data_valid, grid_lon, grid_lat, i, i_end, j, j_end))

    result = np.full((len(grid_lat), len(grid_lon)), CUSTOM_MISSING, dtype=np.float32)
    if PARALLEL and len(blocks) > 1:
        results = Parallel(n_jobs=NUM_CORES, verbose=5)(delayed(process_block)(b) for b in blocks)
        for block_result, i_s, i_e, j_s, j_e in results:
            result[i_s:i_e, j_s:j_e] = block_result
    else:
        for block_args in tqdm(blocks, desc="分块处理"):
            block_result, i_s, i_e, j_s, j_e = process_block(block_args)
            result[i_s:i_e, j_s:j_e] = block_result

    valid_count = np.sum(result != CUSTOM_MISSING)
    coverage = 100 * valid_count / result.size if result.size else 0
    print(f"层 {layer_idx + 1} 完成，耗时: {time.time() - start:.2f}秒，覆盖率: {coverage:.2f}%")
    return result, valid_count, result.size


def save_to_hdf5(output_path, grid_lon, grid_lat, all_results, total_layers, var_name, original_dim):
    print(f"保存结果到: {output_path}")
    with h5py.File(output_path, 'w') as f:
        grp = f.create_group('idw_interpolation')
        grp.create_dataset('longitude', data=grid_lon)
        grp.create_dataset('latitude', data=grid_lat)

        if original_dim == 2 and total_layers == 1:
            dset = grp.create_dataset(
                var_name,
                shape=(len(grid_lat), len(grid_lon)),
                dtype=np.float32,
                chunks=(BLOCK_SIZE, BLOCK_SIZE),
                compression='gzip',
                compression_opts=3
            )
            dset[:] = all_results[0]
        else:
            dset = grp.create_dataset(
                var_name,
                shape=(total_layers, len(grid_lat), len(grid_lon)),
                dtype=np.float32,
                chunks=(1, BLOCK_SIZE, BLOCK_SIZE),
                compression='gzip',
                compression_opts=3
            )
            for i, res in enumerate(tqdm(all_results, desc="写入数据")):
                dset[i] = res

        grp.attrs['missing_value'] = CUSTOM_MISSING
        grp.attrs['grid_resolution'] = GRID_RESOLUTION
        grp.attrs['variable_name'] = var_name
        grp.attrs['original_dimension'] = original_dim
        if original_dim == 3:
            # 记录实际处理的层范围
            grp.attrs['layers_processed'] = f"{args.layer_min or 0}-{args.layer_max or (total_layers - 1)}"


def generate_report(total_valid, total_points, total_layers, var_name, original_dim):
    coverage = 100 * total_valid / total_points if total_points else 0
    print("\n" + "=" * 60)
    print("                  插值完成报告")
    print("=" * 60)
    print(f"变量名: {var_name}")
    print(f"原始维度: {original_dim}D，处理层数: {total_layers}")
    if original_dim == 3 and (args.layer_min is not None or args.layer_max is not None):
        print(f"层范围: {args.layer_min or 0}至{args.layer_max or (total_layers - 1)}")
    print(f"总覆盖率: {coverage:.2f}%")
    print(f"结果文件: {OUTPUT_FILE}")
    print("=" * 60)


def main():
    try:
        start = time.time()
        # 读取数据
        lon, lat, data = read_hdf5_data(INPUT_FILE, VAR_NAME)
        original_dim = 2 if data.shape[2] == 1 else 3

        # 预处理（传入层范围参数）
        valid_lon, valid_lat, data_layers, lon_min, lon_max, lat_min, lat_max, total_layers = preprocess_data(
            lon, lat, data,
            layer_min=args.layer_min,  # 传入起始层
            layer_max=args.layer_max  # 传入结束层
        )

        # 创建插值网格
        grid_lon, grid_lat = create_interpolation_grid(lon_min, lon_max, lat_min, lat_max, GRID_RESOLUTION)

        # 批量插值
        all_results = []
        total_valid, total_points = 0, 0
        for i, layer in enumerate(tqdm(data_layers, desc="总进度")):
            res, valid, total = batch_idw(valid_lon, valid_lat, layer, grid_lon, grid_lat, i)
            all_results.append(res)
            total_valid += valid
            total_points += total
            del res

        # 保存结果
        save_to_hdf5(OUTPUT_FILE, grid_lon, grid_lat, all_results, total_layers, VAR_NAME, original_dim)

        # 生成报告
        generate_report(total_valid, total_points, total_layers, VAR_NAME, original_dim)
        print(f"总耗时: {time.time() - start:.2f}秒")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
