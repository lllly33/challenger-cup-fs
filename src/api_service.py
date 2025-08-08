

import psycopg2
import os
from datetime import datetime
import traceback
import h5py
import numpy as np
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, DB_PATH_PREFIX, LOCAL_MOUNT_POINT

# 假设 cropper 模块在 src/cropper/ 路径下
from .cropper.SpaceCropping import HDF5Cropper, HDF5CropperError
from .interpolation.main_new import run_interpolation




def get_hdf5_files_from_db():
    """
    从数据库中获取所有已入库的HDF5文件信息。
    返回一个列表，每个元素是一个字典，包含 'id' 和 'file_name'。
    """
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()
        cur.execute("SELECT id, file_name FROM hdf5_files ORDER BY file_name;")
        files = []
        for row in cur.fetchall():
            files.append({"id": row[0], "file_name": row[1]})
        return files
    except (Exception, psycopg2.Error) as error:
        print(f"获取HDF5文件列表失败: {error}")
        traceback.print_exc()
        return []
    finally:
        if conn:
            cur.close()
            conn.close()


def get_hdf5_latlon_data(file_id: int):
    """
    根据文件ID从数据库获取HDF5文件路径，并读取其经纬度数据的范围。

    Args:
        file_id (int): HDF5文件的数据库ID。

    Returns:
        dict: 包含经纬度范围的字典，例如 {'lat_min': -90.0, 'lat_max': 90.0, 'lon_min': -180.0, 'lon_max': 180.0}。
              如果发生错误或找不到数据，返回 None。
    """
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()

        # 1. 根据 file_id 查询文件路径
        cur.execute("SELECT file_path FROM hdf5_files WHERE id = %s;", (file_id,))
        file_record = cur.fetchone()
        if not file_record:
            print(f"[ERROR] 未找到文件ID为 {file_id} 的HDF5文件记录。")
            return None
        input_hdf_path = file_record[0]

        # 2. 智能推断经纬度变量名和组路径 (复用 find_and_crop_hdf5 中的逻辑)
        lat_var = None
        lon_var = None
        latlon_group = None

        cur.execute(
            "SELECT name, parent_path FROM hdf5_datasets WHERE file_id = %s AND (name ILIKE '%%lat%%' OR name ILIKE '%%latitude%%') LIMIT 1;",
            (file_id,))
        lat_record = cur.fetchone()
        if lat_record:
            lat_var, latlon_group = lat_record

        if latlon_group:
            cur.execute(
                "SELECT name FROM hdf5_datasets WHERE file_id = %s AND parent_path = %s AND (name ILIKE '%%lon%%' OR name ILIKE '%%longitude%%') LIMIT 1;",
                (file_id, latlon_group))
            lon_record = cur.fetchone()
            if lon_record:
                lon_var = lon_record[0]

        if not lat_var or not lon_var:
            print(f"[ERROR] 无法为文件 {input_hdf_path} 推断经纬度变量。")
            return None

        # 3. 读取HDF5文件并获取经纬度数据
        with h5py.File(input_hdf_path, 'r') as hf:
            if latlon_group and latlon_group != '/':
                group = hf[latlon_group]
            else:
                group = hf

            if lat_var not in group or lon_var not in group:
                print(f"[ERROR] HDF5文件中找不到经纬度变量: {lat_var} 或 {lon_var} 在组 {latlon_group} 中。")
                return None

            lats = group[lat_var][:]
            lons = group[lon_var][:]

            # 过滤填充值 (假设 -9999.9 是填充值)
            valid_lats = lats[lats > -9999.0] # 稍微放宽一点，避免浮点数精度问题
            valid_lons = lons[lons > -9999.0]

            if valid_lats.size == 0 or valid_lons.size == 0:
                print(f"[WARNING] 文件 {input_hdf_path} 中没有有效的经纬度数据。")
                return None

            lat_min = float(np.min(valid_lats))
            lat_max = float(np.max(valid_lats))
            lon_min = float(np.min(valid_lons))
            lon_max = float(np.max(valid_lons))

            return {
                'lat_min': lat_min,
                'lat_max': lat_max,
                'lon_min': lon_min,
                'lon_max': lon_max
            }

    except Exception as e:
        print(f"[ERROR] 获取HDF5文件经纬度数据失败: {e}")
        traceback.print_exc()
        return None
    finally:
        if conn:
            cur.close()
            conn.close()


def get_hdf5_groups_from_db(file_id: int):
    """
    从数据库中获取指定HDF5文件的所有组路径。

    Args:
        file_id (int): HDF5文件的数据库ID。
    Returns:
        list: 包含组路径的列表，例如 ['/', '/FS', '/FS/Swath']。
    """
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT full_path FROM hdf5_groups WHERE file_id = %s ORDER BY full_path;", (file_id,))
        groups = [row[0] for row in cur.fetchall()]
        # 确保根路径存在
        if '/' not in groups:
            groups.insert(0, '/')
        return groups
    except (Exception, psycopg2.Error) as error:
        print(f"获取HDF5组列表失败: {error}")
        traceback.print_exc()
        return []
    finally:
        if conn:
            cur.close()
            conn.close()


def get_hdf5_variables_from_db(file_id: int, group_path: str = None):
    """
    从数据库中获取指定HDF5文件的所有数据集变量名。
    这个实现是通用的，会返回文件内所有可识别的数据集。

    Args:
        file_id (int): HDF5文件的数据库ID。
    Returns:
        list: 包含变量名的列表，例如 ['airTemperature', 'pressure', 'Latitude', 'Longitude']。
              如果发生错误或找不到数据，返回空列表。
    """
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()
        # 动态构建查询语句
        base_query = "SELECT name FROM hdf5_datasets WHERE file_id = %s"
        params = [file_id]

        if group_path:
            base_query += " AND parent_path = %s"
            params.append(group_path)
        
        base_query += " ORDER BY name;"
        
        cur.execute(base_query, tuple(params))

        # 使用 set 来自动去重，然后转为 list 并排序
        variables = sorted(list(set([row[0] for row in cur.fetchall()])))
        print(f"[DEBUG] get_hdf5_variables_from_db (通用): file_id={file_id}, group='{group_path}', variables={variables}")
        return variables
    except (Exception, psycopg2.Error) as error:
        print(f"获取HDF5变量列表失败: {error}")
        traceback.print_exc()
        return []
    finally:
        if conn:
            cur.close()
            conn.close()


def find_and_crop_hdf5(file_name: str, lat_min: float, lat_max: float, lon_min: float, lon_max: float,
                       output_dir: str = 'out') -> str:
    """
    根据文件名和经纬度范围，自动从数据库查找元数据并执行HDF5文件的空间裁剪。

    Args:
        file_name (str): 要裁剪的HDF5文件名 (例如 '2A.GPM.Ka...HDF5')。
        lat_min (float): 最小纬度。
        lat_max (float): 最大纬度。
        lon_min (float): 最小经度。
        lon_max (float): 最大经度。
        output_dir (str, optional): 输出目录。默认为 'out'。

    Returns:
        str: 成功裁剪后生成的文件的绝对路径。

    Raises:
        ValueError: 如果在数据库中找不到文件或必要的元数据。
        HDF5CropperError: 如果裁剪过程中发生错误。
        Exception: 其他数据库连接或未知错误。
    """
    conn = None
    try:
        # 1. 连接数据库
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()
        print(f"成功连接到数据库 '{DB_NAME}'。")

        # 2. 查询文件元数据
        print(f"正在数据库中查找文件: {file_name}...")
        cur.execute("SELECT id, file_path FROM hdf5_files WHERE file_name = %s;", (file_name,))
        file_record = cur.fetchone()
        if not file_record:
            raise ValueError(f"在数据库中未找到文件名为 '{file_name}' 的记录。")

        file_id, input_hdf_path = file_record
        # 将数据库中的路径转换为本地可访问的路径
        input_hdf_path = input_hdf_path.replace(DB_PATH_PREFIX, LOCAL_MOUNT_POINT)
        print(f"找到文件记录: ID={file_id}, Path='{input_hdf_path}'")

        # 3. 智能推断经纬度变量名和组路径
        # 推断纬度
        cur.execute(
            "SELECT name, parent_path FROM hdf5_datasets WHERE file_id = %s AND (name ILIKE '%%lat%%' OR name ILIKE '%%latitude%%') LIMIT 1;",
            (file_id,))
        lat_record = cur.fetchone()
        if not lat_record:
            raise ValueError("在数据库中未能自动推断出纬度变量 (lat/latitude)。")
        lat_var, latlon_group = lat_record
        print(f"推断出纬度变量: '{lat_var}', 组: '{latlon_group}'")

        # 推断经度
        cur.execute(
            "SELECT name FROM hdf5_datasets WHERE file_id = %s AND parent_path = %s AND (name ILIKE '%%lon%%' OR name ILIKE '%%longitude%%') LIMIT 1;",
            (file_id, latlon_group))
        lon_record = cur.fetchone()
        if not lon_record:
            raise ValueError(f"在组 '{latlon_group}' 中未能自动推断出经度变量 (lon/longitude)。")
        lon_var = lon_record[0]
        print(f"推断出经度变量: '{lon_var}'")

        # 假设数据组和经纬度组是同一个
        data_group = latlon_group
        print(f"假设数据组与经纬度组相同: '{data_group}'")

    except (Exception, psycopg2.Error) as error:
        print(f"数据库操作失败: {error}")
        traceback.print_exc()
        raise
    finally:
        if conn:
            cur.close()
            conn.close()

    # 4. 准备并执行裁剪
    try:
        # 生成唯一的输出文件名
        base_name = os.path.splitext(file_name)[0]
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        output_filename = f"{base_name}_cropped_{timestamp}.h5"
        output_path = os.path.join(output_dir, output_filename)

        print(f"准备执行裁剪...")
        print(f"  输入: {input_hdf_path}")
        print(f"  输出: {output_path}")
        print(f"  范围: Lat({lat_min}, {lat_max}), Lon({lon_min}, {lon_max})")

        # 实例化裁剪器并执行
        cropper = HDF5Cropper(verbose=True)

        # data_vars 设置为 None，让 cropper 自动处理组内所有符合条件的数据集
        final_output_path = cropper.crop_file(
            input_hdf=input_hdf_path,
            output_hdf=output_path,
            lat_min=lat_min,
            lat_max=lat_max,
            lon_min=lon_min,
            lon_max=lon_max,
            lat_var=lat_var,
            lon_var=lon_var,
            data_vars=None,
            data_group=data_group,
            latlon_group=latlon_group
        )

        print(f"裁剪成功完成！")
        return os.path.abspath(final_output_path)

    except HDF5CropperError as e:
        print(f"HDF5 裁剪过程中发生错误: {e}")
        raise
    except Exception as e:
        print(f"执行裁剪时发生未知错误: {e}")
        traceback.print_exc()
        raise


def perform_interpolation(file_id: int, var_name: str, resolution: float,
                          lon_min: float = None, lon_max: float = None,
                          lat_min: float = None, lat_max: float = None,
                          layer_min: int = None, layer_max: int = None,
                          output_dir: str = 'out') -> str:
    """
    根据文件ID和插值参数，执行HDF5文件的空间插值。

    Args:
        file_id (int): HDF5文件的数据库ID。
        var_name (str): 要插值的变量名。
        resolution (float): 目标网格分辨率。
        lon_min (float, optional): 最小经度。
        lon_max (float, optional): 最大经度。
        lat_min (float, optional): 最小纬度。
        lat_max (float, optional): 最大纬度。
        layer_min (int, optional): 起始层索引。
        layer_max (int, optional): 结束层索引。
        output_dir (str, optional): 结果保存目录。默认为 'out'。

    Returns:
        str: 成功插值后生成的文件的绝对路径。
    """
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()

        cur.execute("SELECT file_path FROM hdf5_files WHERE id = %s;", (file_id,))
        file_record = cur.fetchone()
        if not file_record:
            raise ValueError(f"未找到文件ID为 {file_id} 的HDF5文件记录。")
        input_hdf_path = file_record[0]

        print(f"准备执行插值任务...")
        print(f"  输入文件: {input_hdf_path}")
        print(f"  变量名: {var_name}")
        print(f"  分辨率: {resolution}")
        print(f"  经纬度范围: Lon({lon_min}, {lon_max}), Lat({lat_min}, {lat_max})")
        print(f"  层范围: Layer({layer_min}, {layer_max})")

        output_file_path = run_interpolation(
            file_id=file_id, # 传递 file_id 而不是文件路径
            var_name=var_name,
            resolution=resolution,
            output_dir=output_dir,
            lon_min=lon_min,
            lon_max=lon_max,
            lat_min=lat_min,
            lat_max=lat_max,
            layer_min=layer_min,
            layer_max=layer_max
        )

        if output_file_path:
            print(f"插值成功完成！结果保存到: {output_file_path}")
            return os.path.abspath(output_file_path)
        else:
            raise Exception("插值失败，未生成输出文件。")

    except Exception as e:
        print(f"执行插值时发生错误: {e}")
        traceback.print_exc()
        raise
    finally:
        if conn:
            cur.close()
            conn.close()


if __name__ == '__main__':
    # --- 这是一个使用示例 ---
    # 确保在运行此示例前，你已经运行过 writehdf5.py 将元数据存入数据库

    # 1. 指定要裁剪的文件名和范围
    target_file = '2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5'
    latitude_range = (-58, -48)
    longitude_range = (102, 142)

    print("--- 开始执行裁剪任务 ---")

    try:
        # 2. 调用主函数
        result_path = find_and_crop_hdf5(
            file_name=target_file,
            lat_min=latitude_range[0],
            lat_max=latitude_range[1],
            lon_min=longitude_range[0],
            lon_max=longitude_range[1],
            output_dir='out' # 指定输出目录
        )
        print(f"\n--- 任务成功 ---")
        print(f"裁剪后的文件已保存到: {result_path}")

    except (ValueError, HDF5CropperError) as e:
        print(f"\n--- 任务失败 ---")
        print(f"错误: {e}")
    except Exception as e:
        print(f"\n--- 发生意外错误 ---")
        print(f"错误: {e}")


