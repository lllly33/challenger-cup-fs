

import psycopg2
import os
from datetime import datetime
import traceback

# 假设 cropper 模块在 src/cropper/ 路径下
from cropper.SpaceCropping import HDF5Cropper, HDF5CropperError

# --- 数据库连接参数 (与 writehdf5.py 保持一致) ---
DB_HOST = "localhost"
DB_NAME = "test1"
DB_USER = "postgres"
DB_PASSWORD = "123456"


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


