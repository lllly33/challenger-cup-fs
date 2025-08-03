import h5py
import os
import time
import shutil  # 用于高效文件复制
import tempfile

def traverse_hdf5(group, indent=0):
    """
    递归遍历HDF5文件结构并打印。
    """
    for name, obj in group.items():
        prefix = "  " * indent
        if isinstance(obj, h5py.Group):
            print(f"{prefix}Group: {name}")
            traverse_hdf5(obj, indent + 1)
        elif isinstance(obj, h5py.Dataset):
            print(f"{prefix}Dataset: {name}")
            for attr_name, attr_value in obj.attrs.items():
                print(f"{prefix}  Attribute: {attr_name}, Value: {attr_value}")
        else:
            print(f"{prefix}Other object: {name} (type: {type(obj)})")

if __name__ == "__main__":
    start_time = time.time()

    # 1. JuiceFS上的HDF5文件路径（根据实际情况修改）
    juicefs_file_path = "/mnt/myjfs/2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5"

    # 2. 创建临时目录和文件，模拟从远程存储“下载”
    temp_dir = tempfile.mkdtemp()
    local_temp_file = os.path.join(temp_dir, os.path.basename(juicefs_file_path))

    try:
        if not os.path.exists(juicefs_file_path):
            raise FileNotFoundError(f"源文件未找到: {juicefs_file_path}")

        print(f"开始复制文件从 {juicefs_file_path} 到 {local_temp_file} ...")
        shutil.copy(juicefs_file_path, local_temp_file)
        print("文件复制完成。\n")

        print("HDF5文件结构如下：")
        with h5py.File(local_temp_file, 'r') as hdf_file:
            traverse_hdf5(hdf_file)

    except Exception as e:
        print(f"处理文件时出错：{e}")

    finally:
        print("\n正在清理临时文件...")
        if os.path.exists(local_temp_file):
            os.remove(local_temp_file)
        if os.path.exists(temp_dir):
            os.rmdir(temp_dir)
        print("清理完成。")

        end_time = time.time()
        print(f"\n程序运行总时间：{end_time - start_time:.2f} 秒")
