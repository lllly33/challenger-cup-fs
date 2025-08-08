import time
import h5py
import os
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import psycopg2
import numpy as np
from datetime import datetime

# 假设 config.py 存在且包含 DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, LOCAL_MOUNT_POINT
from config import DB_HOST, DB_NAME, DB_USER, DB_PASSWORD, LOCAL_MOUNT_POINT, DB_PATH_PREFIX

# 导入我们的提取函数
# 假设 extract_hdf5.py 在 src/read/ 目录下
sys.path.append(os.path.join(os.path.dirname(__file__), '../src/read'))
from extract_hdf5 import extract_hdf5_subset

# --- 配置测试参数 ---
# 请替换为您的实际文件ID和目标路径
TEST_FILE_ID = 14 # 替换为数据库中一个大HDF5文件的ID
TEST_TARGET_PATH = "/FS/Longitude" # 替换为该文件内部一个数据集的路径

OUTPUT_DIR = "performance_test_output"
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def get_original_file_path(file_id):
    """从数据库获取原始HDF5文件的物理路径"""
    conn = None
    try:
        conn = psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
        cur = conn.cursor()
        cur.execute("SELECT file_path FROM hdf5_files WHERE id = %s;", (file_id,))
        file_record = cur.fetchone()
        if not file_record:
            raise ValueError(f"未找到文件ID为 {file_id} 的记录")
        # 转换数据库路径到本地可访问路径
        return file_record[0].replace(DB_PATH_PREFIX, LOCAL_MOUNT_POINT)
    finally:
        if conn:
            cur.close()
            conn.close()

def test_traditional_read(file_path, dataset_path):
    """
    传统方式：直接读取整个HDF5文件中的数据集。
    """
    print(f"\n--- 传统方式读取: {dataset_path} ---")
    start_time = time.time()
    try:
        with h5py.File(file_path, 'r') as hf:
            if dataset_path not in hf:
                raise ValueError(f"数据集 {dataset_path} 不存在于文件 {file_path} 中。")
            data = hf[dataset_path][:]
            print(f"  成功读取数据集，形状: {data.shape}, 数据类型: {data.dtype}")
    except Exception as e:
        print(f"  传统方式读取失败: {e}")
        return None
    finally:
        end_time = time.time()
        print(f"  耗时: {end_time - start_time:.4f} 秒")
    return data

def test_our_method_read(file_id, target_path):
    """
    我们的方式：使用 extract_hdf5_subset 提取子集，然后读取新文件。
    """
    print(f"\n--- 我们的方式读取: {target_path} ---")
    start_time = time.time()
    output_file_name = f"extracted_subset_{datetime.now().strftime('%Y%m%d%H%M%S')}.h5"
    output_file_path = os.path.join(OUTPUT_DIR, output_file_name)

    try:
        # 1. 提取子集
        print(f"  正在提取子集到: {output_file_path}")
        success = extract_hdf5_subset(file_id, target_path, output_file_path)
        if not success:
            raise Exception("子集提取失败。")
        print(f"  子集提取成功。")

        # 2. 读取新文件中的数据集
        with h5py.File(output_file_path, 'r') as hf_extracted:
            # 提取后的文件，数据集路径可能与原始文件相同，也可能在根目录
            # 假设提取后，目标路径的数据集直接在根目录或原路径下
            extracted_data_path = target_path # 尝试原始路径
            if extracted_data_path not in hf_extracted:
                # 如果不在原路径，尝试直接在根目录查找，因为 extract_hdf5_subset 复制时会保持结构
                # 但如果只提取一个Dataset，它可能直接成为新文件的根Dataset
                if len(hf_extracted.keys()) == 1 and isinstance(hf_extracted[list(hf_extracted.keys())[0]], h5py.Dataset):
                    extracted_data_path = list(hf_extracted.keys())[0]
                else:
                    raise ValueError(f"无法在新文件中找到数据集 {target_path}")

            data = hf_extracted[extracted_data_path][:]
            print(f"  成功读取提取后的数据集，形状: {data.shape}, 数据类型: {data.dtype}")
    except Exception as e:
        print(f"  我们的方式读取失败: {e}")
        return None
    finally:
        end_time = time.time()
        print(f"  总耗时: {end_time - start_time:.4f} 秒")
        # 清理生成的临时文件
        if os.path.exists(output_file_path):
            os.remove(output_file_path)
            print(f"  已清理临时文件: {output_file_path}")
    return data

def main():
    print("--- HDF5 数据读取性能对比测试 ---")
    print(f"测试文件ID: {TEST_FILE_ID}")
    print(f"目标数据集路径: {TEST_TARGET_PATH}")

    original_file_path = get_original_file_path(TEST_FILE_ID)
    if not original_file_path or not os.path.exists(original_file_path):
        print(f"错误: 原始文件 {original_file_path} 不存在或无法获取。请检查 TEST_FILE_ID 和数据库配置。")
        return

    print(f"原始文件物理路径: {original_file_path}")

    # 运行传统方式测试
    traditional_data = test_traditional_read(original_file_path, TEST_TARGET_PATH)

    # 运行我们的方式测试
    our_method_data = test_our_method_read(TEST_FILE_ID, TEST_TARGET_PATH)

    # 验证数据是否一致 (如果都成功读取)
    if traditional_data is not None and our_method_data is not None:
        if np.array_equal(traditional_data, our_method_data):
            print("\n--- 验证结果: 两种方式读取的数据内容一致。---")
        else:
            print("\n--- 验证结果: 警告！两种方式读取的数据内容不一致。---")
    else:
        print("\n--- 验证结果: 无法进行数据内容一致性验证 (部分读取失败)。---")

    print("\n--- 测试完成 ---")

if __name__ == "__main__":
    main()
