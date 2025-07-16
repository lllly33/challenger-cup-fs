import h5py
import json
import os

# 替换成你的 HDF5 文件路径
hdf5_path = "HDF格式示例文件1/2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5"
index_output_path = "HDF格式示例文件1/2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5.index.json"

index_data = {}

def extract_info(name, obj):
    if isinstance(obj, h5py.Dataset):
        info = {
            "shape": obj.shape,
            "dtype": str(obj.dtype),
            "attrs": {k: str(obj.attrs[k]) for k in obj.attrs},
        }

        # 获取文件偏移量：通过低层接口（注意：不适用于压缩数据）
        try:
            offset = obj.id.get_offset()
            info["file_offset"] = offset
        except Exception as e:
            info["file_offset"] = None  # 如果失败则设为 None

        index_data[name] = info

# 打开 HDF5 文件并递归遍历数据集
with h5py.File(hdf5_path, "r") as f:
    f.visititems(extract_info)

# 保存为 JSON 文件
with open(index_output_path, "w") as f:
    json.dump(index_data, f, indent=2)

print(f"[✓] 索引已保存到: {index_output_path}")
