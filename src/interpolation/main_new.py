#!/usr/bin/env python3
import numpy as np
import h5py
import os
import time
import psycopg2
import traceback
from scipy.spatial import cKDTree
from tqdm import tqdm
from joblib import Parallel, delayed

# --- 数据库连接参数 ---
DB_HOST = "localhost"
DB_NAME = "juicefs"
DB_USER = "juiceuser"
DB_PASSWORD = "0333"

# --- 插值算法参数 ---
CUSTOM_MISSING = -9999.9
MAX_NEIGHBORS = 10
MIN_NEIGHBORS = 3
POWER = 2
MAX_DISTANCE = 0.5
BLOCK_SIZE = 128
MAX_POINTS_PER_BLOCK = 50000
PARALLEL = True
NUM_CORES = -1


DB_PATH_PREFIX = '/mnt/jfs/'
LOCAL_MOUNT_POINT = '/mnt/myjfs/'


def _get_paths_from_db(file_id: int, var_name: str):
    """从数据库查询文件路径、变量路径和经纬度路径"""
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()

        # 1. 获取文件物理路径
        cur.execute("SELECT file_path FROM hdf5_files WHERE id = %s;", (file_id,))
        file_record = cur.fetchone()
        if not file_record:
            raise ValueError(f"未找到文件ID为 {file_id} 的记录")
        file_path = file_record[0]

        # 将数据库中的路径转换为本地可访问的路径
        file_path = file_path.replace(DB_PATH_PREFIX, LOCAL_MOUNT_POINT)

        # 2. 获取目标变量的完整路径
        cur.execute("SELECT full_path, parent_path FROM hdf5_datasets WHERE file_id = %s AND name = %s LIMIT 1;", (file_id, var_name))
        var_record = cur.fetchone()
        if not var_record:
            raise ValueError(f"在文件ID {file_id} 中未找到变量名 '{var_name}'")
        data_full_path, data_parent_path = var_record

        # 3. 智能推断经纬度路径
        lat_path, lon_path = None, None
        # 优先在同级目录下查找
        cur.execute("""
            SELECT name, full_path FROM hdf5_datasets
            WHERE file_id = %s AND parent_path = %s AND (name ILIKE '%%lat%%' OR name ILIKE '%%latitude%%')
            LIMIT 1;
        """, (file_id, data_parent_path))
        lat_record = cur.fetchone()
        if lat_record:
            lat_path = lat_record[1]

        cur.execute("""
            SELECT name, full_path FROM hdf5_datasets
            WHERE file_id = %s AND parent_path = %s AND (name ILIKE '%%lon%%' OR name ILIKE '%%longitude%%')
            LIMIT 1;
        """, (file_id, data_parent_path))
        lon_record = cur.fetchone()
        if lon_record:
            lon_path = lon_record[1]

        # 如果同级找不到，则在全文件中查找
        if not lat_path:
            cur.execute("SELECT full_path FROM hdf5_datasets WHERE file_id = %s AND (name ILIKE '%%lat%%' OR name ILIKE '%%latitude%%') LIMIT 1;", (file_id,))
            lat_record = cur.fetchone()
            if lat_record: lat_path = lat_record[0]

        if not lon_path:
            cur.execute("SELECT full_path FROM hdf5_datasets WHERE file_id = %s AND (name ILIKE '%%lon%%' OR name ILIKE '%%longitude%%') LIMIT 1;", (file_id,))
            lon_record = cur.fetchone()
            if lon_record: lon_path = lon_record[0]

        if not lat_path or not lon_path:
            raise ValueError(f"无法为文件ID {file_id} 自动推断经纬度变量路径")

        print(f"[INFO] DB Paths: file='{file_path}', data='{data_full_path}', lat='{lat_path}', lon='{lon_path}'")
        return file_path, data_full_path, lat_path, lon_path

    finally:
        if conn:
            cur.close()
            conn.close()


def read_hdf5_data(file_path, data_path, lat_path, lon_path):
    """使用动态路径读取HDF5数据"""
    print(f"读取文件: {file_path}")
    with h5py.File(file_path, 'r') as f:
        longitude = f[lon_path][:].astype(np.float32)
        latitude = f[lat_path][:].astype(np.float32)
        data = f[data_path][:].astype(np.float32)

    # 维度校验
    assert longitude.shape == latitude.shape, "经纬度维度不匹配"
    assert len(data.shape) in (2, 3), f"变量必须为二维或三维，实际形状: {data.shape}"
    
    # 对于GPM等数据，数据的前两维通常与经纬度匹配
    if data.shape[:2] != longitude.shape:
        print(f"[WARNING] 变量维度 {data.shape} 与经纬度维度 {longitude.shape} 前两维不匹配。请检查数据结构。")
        # 这里可以根据需要添加更复杂的维度匹配逻辑
        
    # 二维变量转为三维单一层格式
    if len(data.shape) == 2:
        print(f"检测到二维变量，自动转换为单一层格式 (添加维度)")
        data = data.reshape(data.shape[0], data.shape[1], 1)

    return longitude, latitude, data


def preprocess_data(longitude, latitude, data, lon_min_arg=None, lon_max_arg=None, lat_min_arg=None, lat_max_arg=None, layer_min=None, layer_max=None):
    print("预处理数据...")
    start = time.time()
    filled_data = data.copy()
    total_layers = filled_data.shape[2]

    # 三维变量的层范围过滤
    if total_layers > 1:  # 仅对三维变量生效
        if layer_min is not None:
            layer_min = max(0, min(layer_min, total_layers - 1))
        else:
            layer_min = 0
        if layer_max is not None:
            layer_max = max(layer_min, min(layer_max, total_layers - 1))
        else:
            layer_max = total_layers - 1

        filled_data = filled_data[:, :, layer_min:layer_max + 1]
        total_layers = filled_data.shape[2]
        print(f"三维变量层范围过滤：保留第{layer_min}至{layer_max}层，共{total_layers}层")

    # 经纬度有效性校验
    lon_valid = ~np.isnan(longitude) & (longitude >= -180) & (longitude <= 180)
    lat_valid = ~np.isnan(latitude) & (latitude >= -90) & (latitude <= 90)
    coord_valid = lon_valid & lat_valid

    global_valid = coord_valid.copy()
    
    # 逐层处理缺失值
    for layer in range(total_layers):
        data_layer = filled_data[:, :, layer]
        missing_mask = (data_layer == CUSTOM_MISSING) | np.isnan(data_layer)
        missing_mask &= coord_valid
        
        if np.sum(missing_mask) > 0:
            valid_mask = ~missing_mask & coord_valid
            valid_lon_layer = longitude[valid_mask]
            valid_lat_layer = latitude[valid_mask]
            valid_data_layer = data_layer[valid_mask]

            if len(valid_data_layer) >= MIN_NEIGHBORS:
                tree = cKDTree(np.column_stack((valid_lon_layer, valid_lat_layer)))
                missing_points = np.column_stack((longitude[missing_mask], latitude[missing_mask]))
                distances, indices = tree.query(missing_points, k=min(MAX_NEIGHBORS, len(valid_data_layer)), distance_upper_bound=MAX_DISTANCE)
                
                interpolated_values = np.full(missing_points.shape[0], CUSTOM_MISSING, dtype=np.float32)
                for i in range(len(missing_points)):
                    valid_nb = distances[i] < MAX_DISTANCE
                    if np.isscalar(distances[i]): # k=1
                        if valid_nb:
                            interpolated_values[i] = valid_data_layer[indices[i]]
                    elif np.sum(valid_nb) >= MIN_NEIGHBORS:
                        weights = 1.0 / (distances[i][valid_nb] ** POWER)
                        weights /= weights.sum()
                        interpolated_values[i] = np.sum(valid_data_layer[indices[i][valid_nb]] * weights)
                
                data_layer[missing_mask] = interpolated_values
                filled_data[:, :, layer] = data_layer
                global_valid &= ~(missing_mask & (interpolated_values == CUSTOM_MISSING))

    # 提取有效数据与范围
    valid_lon = longitude[global_valid]
    valid_lat = latitude[global_valid]
    if len(valid_lon) == 0:
        raise ValueError("无有效数据点")

    valid_data_layers = [filled_data[:, :, l][global_valid] for l in range(total_layers)]
    lon_min, lon_max = valid_lon.min(), valid_lon.max()
    lat_min, lat_max = valid_lat.min(), valid_lat.max()

    # 应用用户指定经纬度范围
    if lon_min_arg is not None: lon_min = max(lon_min_arg, -180.0)
    if lon_max_arg is not None: lon_max = min(lon_max_arg, 180.0)
    if lat_min_arg is not None: lat_min = max(lat_min_arg, -90.0)
    if lat_max_arg is not None: lat_max = min(lat_max_arg, 90.0)

    final_mask = (valid_lon >= lon_min) & (valid_lon <= lon_max) & (valid_lat >= lat_min) & (valid_lat <= lat_max)
    valid_lon, valid_lat = valid_lon[final_mask], valid_lat[final_mask]
    valid_data_layers = [l[final_mask] for l in valid_data_layers]

    if len(valid_lon) == 0:
        raise ValueError(f"指定的经纬度范围 (lon: [{lon_min_arg}, {lon_max_arg}], lat: [{lat_min_arg}, {lat_max_arg}]) 内没有找到任何有效的数据点。")

    print(f"预处理耗时: {time.time() - start:.2f}秒")
    return valid_lon, valid_lat, valid_data_layers, lon_min, lon_max, lat_min, lat_max, total_layers


def create_interpolation_grid(lon_min, lon_max, lat_min, lat_max, resolution):
    grid_lon = np.arange(lon_min, lon_max, resolution)
    grid_lat = np.arange(lat_min, lat_max, resolution)
    print(f"插值网格规模: {len(grid_lon)}x{len(grid_lat)} = {len(grid_lon) * len(grid_lat):,}点")
    return grid_lon, grid_lat


def idw_interpolation(lon_valid, lat_valid, data_valid, query_points):
    if len(lon_valid) < MIN_NEIGHBORS:
        return np.full(len(query_points), CUSTOM_MISSING, dtype=np.float32)

    tree = cKDTree(np.column_stack((lon_valid, lat_valid)))
    distances, indices = tree.query(query_points, k=min(MAX_NEIGHBORS, len(lon_valid)), distance_upper_bound=MAX_DISTANCE)
    result = np.full(len(query_points), CUSTOM_MISSING, dtype=np.float32)

    for i in range(len(query_points)):
        if np.isscalar(distances[i]):
            if distances[i] < MAX_DISTANCE: result[i] = data_valid[indices[i]]
            continue

        valid_nb = distances[i] < MAX_DISTANCE
        if np.sum(valid_nb) >= MIN_NEIGHBORS:
            weights = 1.0 / (distances[i][valid_nb] ** POWER)
            weights /= weights.sum()
            result[i] = np.sum(data_valid[indices[i][valid_nb]] * weights)
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
        return np.full(block_shape, CUSTOM_MISSING, dtype=np.float32), i_start, i_end, j_start, j_end

    result_flat = idw_interpolation(lon_valid[in_range], lat_valid[in_range], data_valid[in_range], query_points)
    return result_flat.reshape(block_shape), i_start, i_end, j_start, j_end


def batch_idw(lon_valid, lat_valid, data_valid, grid_lon, grid_lat, layer_idx):
    print(f"\\n===== 插值层 {layer_idx + 1} =====")
    start = time.time()
    blocks = []
    for i in range(0, len(grid_lat), BLOCK_SIZE):
        i_end = min(i + BLOCK_SIZE, len(grid_lat))
        for j in range(0, len(grid_lon), BLOCK_SIZE):
            j_end = min(j + BLOCK_SIZE, len(grid_lon))
            blocks.append((f"block_{i//BLOCK_SIZE}_{j//BLOCK_SIZE}", lon_valid, lat_valid, data_valid, grid_lon, grid_lat, i, i_end, j, j_end))

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


def save_to_hdf5(output_path, grid_lon, grid_lat, all_results, total_layers, var_name, original_dim, resolution, layer_min_arg=None, layer_max_arg=None):
    print(f"保存结果到: {output_path}")
    with h5py.File(output_path, 'w') as f:
        grp = f.create_group('idw_interpolation')
        grp.create_dataset('longitude', data=grid_lon)
        grp.create_dataset('latitude', data=grid_lat)

        if original_dim == 2 and total_layers == 1:
            dset = grp.create_dataset(var_name, data=all_results[0], compression='gzip', compression_opts=3)
        else:
            dset = grp.create_dataset(var_name, data=np.array(all_results), compression='gzip', compression_opts=3)
        
        dset.attrs['missing_value'] = CUSTOM_MISSING
        grp.attrs['grid_resolution'] = resolution
        grp.attrs['variable_name'] = var_name
        grp.attrs['original_dimension'] = original_dim
        if original_dim == 3:
            grp.attrs['layers_processed'] = f"{layer_min_arg or 0}-{layer_max_arg or (total_layers-1)}"


def run_interpolation(
    file_id: int,
    var_name: str,
    resolution: float = 0.1,
    output_dir: str = None,
    lon_min: float = None,
    lon_max: float = None,
    lat_min: float = None,
    lat_max: float = None,
    layer_min: int = None,
    layer_max: int = None
):
    try:
        start = time.time()
        # 从数据库获取路径
        file_path, data_path, lat_path, lon_path = _get_paths_from_db(file_id, var_name)

        # 输出路径设置
        output_dir = output_dir if output_dir else os.path.dirname(file_path)
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(
            output_dir,
            f"{os.path.splitext(os.path.basename(file_path))[0]}_{var_name}_interpolated.h5"
        )

        # 读取数据
        lon, lat, data = read_hdf5_data(file_path, data_path, lat_path, lon_path)
        original_dim = 2 if data.shape[2] == 1 else 3

        # 预处理
        valid_lon, valid_lat, data_layers, lon_min_res, lon_max_res, lat_min_res, lat_max_res, total_layers = preprocess_data(
            lon, lat, data, lon_min, lon_max, lat_min, lat_max, layer_min, layer_max
        )

        # 创建插值网格
        grid_lon, grid_lat = create_interpolation_grid(lon_min_res, lon_max_res, lat_min_res, lat_max_res, resolution)

        # 批量插值
        all_results = []
        for i, layer in enumerate(tqdm(data_layers, desc="总进度")):
            res, _, _ = batch_idw(valid_lon, valid_lat, layer, grid_lon, grid_lat, i)
            all_results.append(res)

        # 保存结果
        save_to_hdf5(output_file, grid_lon, grid_lat, all_results, total_layers, var_name, original_dim, resolution, layer_min, layer_max)

        print(f"\\n总耗时: {time.time() - start:.2f}秒")
        return output_file

    except Exception as e:
        print(f"错误: {e}")
        traceback.print_exc()
        return None