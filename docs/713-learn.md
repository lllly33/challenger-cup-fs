HDF5 文件本身支持分块（chunked）、压缩等存储方式。虽然 chunked/compressed 数据没有物理 offset，但 HDF5 库（如 h5py）会自动定位和解压所需的数据块，只读取你请求的部分。

## 读某个字段

你可以用 h5py 直接访问某个字段（如 `/FS/SLV/precipRate`），甚至只读取其中的某一行、某一列或某个区间。例如：

```python
import h5py
with h5py.File('yourfile.h5', 'r') as f:
    data = f['FS/SLV/precipRate'][0:10, :, :]  # 只读前10个scan
```

这样只会读取你需要的数据块，速度很快，内存占用低。

## 读某个范围

如果你要“筛选”符合某条件的数据（如降水率>0），可以先按需读取部分数据，再用 numpy 过滤

```py
import numpy as np
mask = data > 0
filtered = data[mask]
```

# h5pyd（HSDS）

> HSDS 是 HDF Group 官方的分布式 HDF5 服务，支持 HDFS 后端

## 流程（粗略）

1. 上传 HDF5 文件到 HDFS。
2. 生成并保存元数据索引（如 .index.json）。
3. 部署 HSDS 服务，配置 HDFS 作为后端存储。
4. 用户用 h5pyd 客户端，按需远程读取指定字段/切片，无需全量下载。
5. 可结合元数据索引，实现字段检索、权限控制等高级功能。

针对三台 Linux 虚拟机（HDFS集群）环境，基于 HSDS 的 HDF5 分布式存储与按需读取的详细部署方案、代码、文件结构建议：

一、环境假设

- 主节点：namenode（如 hdfs-master）
- 两个工作节点：datanode（如 hdfs-worker1, hdfs-worker2）
- HDFS 已部署并运行，且各节点网络互通
- 你有 HDF5 文件和 .index.json 元数据

二、文件结构建议（HDFS）
建议统一存放路径，便于管理和服务：

```
/data/hdf5/
    yourfile1.h5
    yourfile2.h5
    yourfile1.h5.index.json
    yourfile2.h5.index.json
```

三、HSDS 部署步骤

1. 安装 HSDS
   在主节点或任意一台节点上安装 HSDS（推荐主节点），可用 pip 安装：

```sh
capip install hsds
```

2. 配置 HSDS 使用 HDFS
   编辑 HSDS 配置文件（如 ~/.hsds/config.yaml），关键参数如下：

```yaml
root_dir: /data/hdf5/           # HDFS上的数据目录
hdfs_host: hdfs-master          # HDFS namenode主机名或IP
hdfs_port: 9000                 # HDFS端口（默认9000）
hdfs_user: your_hdfs_user       # HDFS访问用户名
hdfs_driver: hdfs               # 指定后端为hdfs
```

更多参数可参考[官方文档](https://github.com/HDFGroup/hsds/blob/master/docs/ConfigOptions.md)。

3. 启动 HSDS 服务
   在主节点上运行：

```sh
hsds --config ~/.hsds/config.yaml
```

默认监听 5100 端口（可用 --port 参数修改）。

4. 测试 RESTful API
   用 curl 或浏览器访问 http://hdfs-master:5100/ ，确认服务正常。

四、客户端按需读取代码（Python）

安装 h5pyd：

```sh
pip install h5pyd
```

示例代码（远程按需读取）：

```python
import h5pyd

# 连接到 HSDS 服务
f = h5pyd.File("/data/hdf5/yourfile1.h5", 'r', endpoint="http://hdfs-master:5100")

# 按需读取部分数据
data = f['FS/SLV/precipRate'][0:10, :, :]  # 只读前10个scan

# 进一步筛选
import numpy as np
filtered = data[data > 0]
print(filtered)
```

五、自动化字段筛选脚本（结合 .index.json）

假设你有 .index.json 文件，自动筛选常用字段并读取：

```python
import json
import h5pyd

# 读取元数据
with open('yourfile1.h5.index.json', 'r') as f:
    index = json.load(f)

fields = [
    "FS/SLV/precipRate", "FS/Latitude", "FS/Longitude", "FS/ScanTime/Year", "FS/FLG/qualityFlag"
    # 可根据 index 自动筛选
]

f = h5pyd.File("/data/hdf5/yourfile1.h5", 'r', endpoint="http://hdfs-master:5100")
for field in fields:
    if field in f:
        data = f[field][:]
        print(field, data.shape)
```

六、分布式/并发访问说明

- HSDS 支持多用户并发访问，自动调度 chunk，适合大数据场景。
- 你可以在多台客户端同时用 h5pyd 访问同一个 HDF5 文件，系统自动分块读取。

七、补充建议

- 建议将 .index.json 元数据同步上传到 HDFS，或存入数据库，便于检索和服务。
- HSDS 支持 S3、POSIX 等多种后端，后续可扩展。
- 可用 nginx 做反向代理，提升安全性和可用性。

八、常见问题排查

- 确认 HDFS 端口和防火墙设置，HSDS 能正常访问 HDFS。
- HSDS 配置文件路径和参数需与实际环境一致。
- Python 客户端需能访问 HSDS 服务端口。

如需更详细的脚本、配置文件模板或集群架构图，请继续说明！






# 计划

+ 从hdfs上直接读hdf5文件并解析出关键信息，存回hdfs
+ 计划结合hdfs以及hsds，实现精确查找和按需查找
