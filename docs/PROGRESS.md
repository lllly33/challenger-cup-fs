- **环境搭建**: 成功使用 OrbStack 搭建了 3 节点虚拟机集群。
- **组件部署**: 部署并配置了所有必需的组件：
  - 分布式 MinIO 集群作为对象存储。
  - PostgreSQL 作为元数据存储。
  - 所有节点上的 JuiceFS 客户端。
- **文件系统创建**: 创建了名为 `myjfs` 的 JuiceFS 卷。
- **文件系统挂载**: 将 `myjfs` 卷挂载到所有三个节点的 `/mnt/myjfs`。
- **S3 网关启动与验证**:
  - 成功在 `node1` 上启动 JuiceFS S3 网关，并解决了环境变量配置问题。
  - 验证了 S3 网关可以从宿主机（Mac）通过 `http://localhost:8080` 成功访问，并返回了预期的 S3 错误响应，证明服务已正常运行且可达。
- **OrbStack 虚拟机交互**: 明确了与 OrbStack 虚拟机交互的正确方式为 `orb -m <machine_name> <command>`，而非传统 SSH。

| 组件                 | 技术/工具  | 状态          | 备注                                                                    |
| -------------------- | ---------- | ------------- | ----------------------------------------------------------------------- |
| **虚拟化环境** | OrbStack   | `✅ 已部署` | 3 台 Ubuntu Linux 虚拟机 (`node1`, `node2`, `node3`) 正在运行。   |
| **文件系统层** | JuiceFS    | `✅ 已部署` | 已创建名为 `myjfs` 的 JuiceFS 卷，并挂载到所有节点的 `/mnt/myjfs`。 |
| **数据存储层** | MinIO      | `✅ 已部署` | 跨 3 个节点运行的分布式 MinIO 集群，作为 JuiceFS 的后端存储。           |
| **元数据引擎** | PostgreSQL | `✅ 已部署` | PostgreSQL 实例运行在 `node1` 上，用于存储 JuiceFS 元数据。           |

## 项目进展：JuiceFS 文件服务部署 + Cloudflare 内网穿透

### 目标

构建一个可以通过公网访问的分布式文件服务，基于 JuiceFS（挂载 MinIO 对象存储），并使用 Filebrowser 提供 Web UI，再通过 Cloudflare Tunnel 实现安全的公网访问。

### ✅ 环境说明

- 平台：ORB 提供的 Ubuntu 虚拟机（ARM64 架构）
- 文件系统：JuiceFS（挂载 MinIO）
- 可视化工具：Filebrowser
- 内网穿透：Cloudflare Tunnel

### 步骤详解

#### 2. 安装并启动 Filebrowser（Web 文件浏览器）

```bash
curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash
filebrowser -r /mnt/myjfs -p 8081
```

- 默认用户名：admin
- 随机初始密码会在第一次启动时显示
- 登录后可以通过命令修改密码：
  `filebrowser users update admin --password <新密码>888888888888`
- 若权限报错，可将 `/mnt/myjfs` 目录授权给当前用户或使用 `sudo` 启动 Filebrowser。

#### 3. 安装 Cloudflared 并配置隧道

```bash
sudo apt install cloudflared
cloudflared tunnel login     # 跳转网页认证
cloudflared tunnel create juicefs-tunnel
```

创建配置文件 `/etc/cloudflared/config.yml`，内容如下：

```yaml
tunnel: <自动生成的隧道 ID>
credentials-file: /root/.cloudflared/<隧道 ID>.json

ingress:
  - hostname: <your-subdomain>.trycloudflare.com
    service: http://localhost:8081
  - service: http_status:404
```

启动服务：

```bash
cloudflared tunnel run juicefs-tunnel
```

#### 4. 设置 systemd 自启动服务

创建文件 `/etc/systemd/system/cloudflared.service`：

```ini
[Unit]
Description=cloudflared
After=network-online.target
Wants=network-online.target

[Service]
TimeoutStartSec=0
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

#### 5. 后续维护事项

- **系统重启**：需要手动重新挂载 JuiceFS，并手动启动 Filebrowser
- **Cloudflared 启动**：如果配置 systemd，Cloudflared 会自动恢复连接
- **查看公网地址**：登录 Cloudflare Zero Trust 控制台查看 tunnel 域名

### 目前成果

- 本地挂载的 JuiceFS 成功通过 Filebrowser Web 页面公开
- 手机等公网设备可通过 Cloudflare Tunnel 访问
- 构建出完整的“对象存储 → 文件系统挂载 → Web 可视化 → 公网访问”链路
- 所有工具可通过命令行控制，适合集成自动化或封装服务容器

# 公网访问

🌐 项目阶段性成果总结：通过 Cloudflare 实现虚拟机 Filebrowser 的公网访问

🧩 背景与目标

本次任务目标是：在 ORB 平台的 Ubuntu 虚拟机中部署 Filebrowser 并通过 Cloudflare Tunnel 实现公网访问，便于远程共享和管理本地挂载的 JuiceFS 文件系统内容。

🛠️ 配置与操作步骤

✅ 1. Filebrowser 安装与配置

安装 Filebrowser（使用官方二进制版本）：

wget -O filebrowser https://github.com/filebrowser/filebrowser/releases/latest/download/linux-arm64-filebrowser

chmod +x filebrowser

sudo mv filebrowser /usr/local/bin/

启动 Filebrowser：

filebrowser -r /mnt/myjfs --port 8081

修改 admin 密码：

filebrowser users update admin --password 888888888888

Filebrowser 默认监听 127.0.0.1:8081，配合 Cloudflare tunnel 即可暴露出去。

✅ 2. Cloudflare Tunnel 配置

安装 cloudflared：

wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb

sudo dpkg -i cloudflared-linux-arm64.deb

登录 Cloudflare，创建 Tunnel：

cloudflared tunnel login

cloudflared tunnel create myfilebrowser

编写配置文件 /etc/cloudflared/config.yml：

tunnel: 5a66d920-bdf8-4759-a617-51e79f579d81

credentials-file: /home/crocotear/.cloudflared/5a66d920-bdf8-4759-a617-51e79f579d81.json

ingress:

- hostname: crocotear.icu

service: http://localhost:8081

- service: http_status:404

在 Cloudflare 网站添加一条 DNS CNAME 或 A 记录，指向你的 Tunnel。

运行 tunnel：

cloudflared tunnel run myfilebrowser

## 🔄 虚拟机重启后的恢复流程

每次开机后需要：

### 手动启动 Filebrowser 服务：

filebrowser -r /mnt/myjfs --port 8081 &

### 启动 Cloudflare Tunnel：

cloudflared tunnel --config /etc/cloudflared/config.yml run juicefs-tunnel

运行成功后，你在手机上即可通过 Cloudflare 分配的域名访问 FileBrowser。

## 🧾 涉及的主要配置文件汇总

路径 文件用途

/etc/cloudflared/config.yml Cloudflare tunnel 的主配置文件

/home/crocotear/.cloudflared/*.json Tunnel 凭证文件

/Users/crocotear/filebrowser.db Filebrowser 的 SQLite 用户配置和设置数据库

## postgresql简化工作流程示意

### ✅ 创建文件：

1. `jfs_node` 记录文件 inode、名字、大小、权限等。
2. `jfs_edge` 建立其与目录的父子关系。
3. `jfs_dir_stats` 更新该目录的文件数。

### ✅ 写入文件内容：

1. 分块写入对象存储（如 MinIO），每块计算 hash。
2. `jfs_chunk` 登记每个块的信息（hash、大小）。
3. `jfs_chunk_ref` 登记块和文件之间的映射。

### ✅ 删除文件：

1. `jfs_delfile` 记录该文件被删除。
2. `jfs_delslices` 标记其使用的块为“待删除”。
3. 回收流程会在后台定期清理。

### ✅ 文件锁机制：

* `jfs_flock`/`jfs_plock` 用于支持多个客户端挂载时的并发读写控制。

---

## 📌 设计特点

* ✅** ** **高可扩展性** ：每个表设计都非常「扁平」，适合高并发查询。
* ✅** ** **事务一致性好** ：得益于 PostgreSQL 的事务能力，多个元数据操作天然原子性。
* ✅** ** **强可观测性** ：你可以直接在 SQL 层查看所有文件系统行为。

```bash





juicefs-# \dt
               List of relations
 Schema |       Name        | Type  |   Owner
--------+-------------------+-------+-----------
 public | jfs_acl           | table | juiceuser
 public | jfs_chunk         | table | juiceuser
 public | jfs_chunk_ref     | table | juiceuser
 public | jfs_counter       | table | juiceuser
 public | jfs_delfile       | table | juiceuser
 public | jfs_delslices     | table | juiceuser
 public | jfs_detached_node | table | juiceuser
 public | jfs_dir_quota     | table | juiceuser
 public | jfs_dir_stats     | table | juiceuser
 public | jfs_edge          | table | juiceuser
 public | jfs_flock         | table | juiceuser
 public | jfs_node          | table | juiceuser
 public | jfs_plock         | table | juiceuser
 public | jfs_session2      | table | juiceuser
 public | jfs_setting       | table | juiceuser
 public | jfs_sustained     | table | juiceuser
 public | jfs_symlink       | table | juiceuser
 public | jfs_xattr         | table | juiceuser
(18 rows)


```

## ssh登录

祝：ssh root@182.92.108.81

程：ssh teammember1@123.57.128.59

冒：**ssh root@116.62.59.113**

## postgresql登录

✅ DBeaver 中的配置示例：
Host: 182.92.108.81
Port: 5432
Database: juicefs
Username: juicefs
Password: zwhhhhh
SSL Mode: Disable

1.验证write和read在服务器上

2. 搭好juicefs，测试
3. 放几个大的文件到服务器本地
4. 报告




## 8/3


writehdf5.py 提取并存入数据库的元数据，几乎完全包含了 SpaceCropping.py 进行裁剪时所需的部分关键参数。

  我们来具体分析一下 SpaceCropping.py 的 crop_file 函数需要的参数，以及 writehdf5.py 能否提供它们：

  ┌────────────────┬──────────────┬───────────────────────────────────────────────────────────────────────────────┐
  │ `SpaceCropp... │ `writehdf... │ 如何从数据库获取？                                                            │
  ├────────────────┼──────────────┼───────────────────────────────────────────────────────────────────────────────┤
  │ input_hdf (... │ 能           │ 从 hdf5_files 表的 file_path 字段查询得到。                                     │
  │ output_hdf ... │ 不能         │ 这是用户在裁剪时才决定的，元数据里不会有。                                    │
  │ lat_min, `l... │ 不能         │ 这是用户指定的裁剪区域，是操作参数，不是文件自带的元数据。                    │   │ lon_min, `l... │ 不能         │ 同上，这是用户指定的裁剪区域。                                                │
  │ lat_var (纬... │ 能           │ 可以推断。通过查询 hdf5_datasets 表，找到那些名字里含有 "lat" 或 "Latitude... │
  │ lon_var (经... │ 能           │ 可以推断。同理，查询 hdf5_datasets 表，找到名字含 "lon" 或 "Longitude" 且...  │
  │ data_vars (... │ 能           │ 可以推断。查询 hdf5_datasets 表，找出所有与经纬度数据维度相匹配的数据集。     │
  │ data_group ... │ 能           │ 可以推断。通过查询 hdf5_datasets 表，找到目标数据集的 parent_path 字段即可。    │
  │ `latlon_gro... │ 能           │ 可以推断。通过查询 hdf5_datasets 表，找到经纬度变量的 parent_path 字段即可。    │
  └────────────────┴──────────────┴───────────────────────────────────────────────────────────────────────────────┘

  潜在工作流的具体实现

  基于上面的分析，一个自动化的工作流可以这样设计：

1. 用户输入: 用户提供两样东西：

   * 要处理的原始 HDF5 文件名 (e.g., my_data.h5)。
   * 想要裁剪的地理范围 (e.g., lat: 10-20, lon: 150-160)。
2. 系统后台操作:

   * 查询数据库:
     * SELECT file_path FROM hdf5_files WHERE file_name = 'my_data.h5'; -> 得到 input_hdf。
     * SELECT name, parent_path FROM hdf5_datasets WHERE file_id = ... AND (name ILIKE '%lat%' OR name ILIKE
       '%latitude%'); -> 推断出 lat_var 和 latlon_group。
     * SELECT name, parent_path FROM hdf5_datasets WHERE file_id = ... AND (name ILIKE '%lon%' OR name ILIKE
       '%longitude%'); -> 推断出 lon_var。
     * SELECT name, parent_path FROM hdf5_datasets WHERE file_id = ... AND shape LIKE ...; -> 找到所有需要裁剪的
       data_vars 和它们的 data_group。
   * 调用裁剪函数:
     * 将从数据库查到的 input_hdf, lat_var, lon_var, data_vars, data_group,
       latlon_group，以及用户输入的经纬度范围，一起传给 SpaceCropping.crop_file() 函数。
     * 动态生成一个 output_hdf 路径。
   * 执行裁剪: SpaceCropping.py 完成实际的裁剪工作。

  结论就是：

  是的，writehdf5.py 所构建的元数据数据库，正是实现自动化裁剪流程的关键第一步。它将原本需要手动查看文件才能确定的参数（如变
  量名、组路径），变成了可以通过 SQL 快速、准确查询到的信息，为后续的 SpaceCropping.py 提供了必要的“导航数据”。

╭──────────────────────────────────────────────────────────────────────────────────────╮
│  > 那你给我可以完成对应人物的函数，后面我要把它暴露出服务器上可以对外提供服务的东西  │
╰──────────────────────────────────────────────────────────────────────────────────────╯

✦ 好的，遵照您的要求，我将创建一个函数，该函数封装了我们讨论过的“潜在工作流”。这个函数将成为您未来API服务的核心业务逻辑。

  我将创建一个新的文件 src/api_service.py 来存放这个函数。这样做可以保持逻辑的清晰分离：

* writehdf5.py: 负责将元数据写入数据库。
* cropper/SpaceCropping.py: 提供通用的裁剪工具。
* api_service.py: 业务逻辑层，连接数据库和工具，对外提供具体服务。

  这个新函数 find_and_crop_hdf5 的工作流程如下：

1. 接收输入: 接收 文件名 和 经纬度范围 作为参数。
2. 连接数据库: 连接到 PostgreSQL 数据库。
3. 查询元数据:
   * 根据 文件名 在 hdf5_files 表中找到文件的完整路径 (input_hdf) 和 file_id。
   * 使用 file_id 在 hdf5_datasets 表中智能推断出经纬度变量名 (lat_var, lon_var) 和它们所在的组路径
     (latlon_group)。这是通过搜索名称中包含 "lat" 或 "lon" 的数据集来实现的。
   * 假设数据所在的组 (data_group) 与经纬度所在的组 (latlon_group) 相同。这是一个常见的约定，可以简化逻辑。
4. 准备裁剪:
   * 动态生成一个唯一的输出文件名，以避免覆盖，格式为 原文件名_cropped_时间戳.h5。
   * 实例化 HDF5Cropper。
5. 执行裁剪:
   * 调用 cropper.crop_file 方法，将从数据库中查到的和用户传入的所有参数都传递进去。
   * SpaceCropping 模块将自动处理所有相关数据集的裁剪。
6. 返回结果: 返回成功裁剪后的文件路径。

  以下是 src/api_service.py 的完整代码：

 ╭──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
 │ ✔  WriteFile Writing to src/api_service.py                                                                               │
 │                                                                                                                          │
 │    ... first 66 lines hidden ...                                                                                         │
 │     67         # 推断经度                                                                                                │
 │     68         cur.execute(                                                                                              │
 │     69             "SELECT name FROM hdf5_datasets WHERE file_id = %s AND parent_path = %s AND (name ILIKE               │
 │        '%%lon%%' OR name ILIKE '%%longitude%%') LIMIT 1;",                                                               │
 │     70             (file_id, latlon_group))                                                                              │
 │     71         lon_record = cur.fetchone()                                                                               │
 │     72         if not lon_record:                                                                                        │
 │     73             raise ValueError(f"在组 '{latlon_group}' 中未能自动推断出经度变量 (lon/longitude)。")                 │
 │     74         lon_var = lon_record[0]                                                                                   │
 │     75         print(f"推断出经度变量: '{lon_var}'")                                                                     │
 │     76                                                                                                                   │
 │     77         # 假设数据组和经纬度组是同一个                                                                            │
 │     78         data_group = latlon_group                                                                                 │
 │     79         print(f"假设数据组与经纬度组相同: '{data_group}'")                                                        │
 │     80                                                                                                                   │
 │     81     except (Exception, psycopg2.Error) as error:                                                                  │
 │     82         print(f"数据库操作失败: {error}")                                                                         │
 │     83         traceback.print_exc()                                                                                     │
 │     84         raise                                                                                                     │
 │     85     finally:                                                                                                      │
 │     86         if conn:                                                                                                  │
 │     87             cur.close()                                                                                           │
 │     88             conn.close()                                                                                          │
 │     89                                                                                                                   │
 │     90     # 4. 准备并执行裁剪                                                                                           │
 │     91     try:                                                                                                          │
 │     92         # 生成唯一的输出文件名                                                                                    │
 │     93         base_name = os.path.splitext(file_name)[0]                                                                │
 │     94         timestamp = datetime.now().strftime("%Y%m%d%H%M%S")                                                       │
 │     95         output_filename = f"{base_name}_cropped_{timestamp}.h5"                                                   │
 │     96         output_path = os.path.join(output_dir, output_filename)                                                   │
 │     97                                                                                                                   │
 │     98         print(f"准备执行裁剪...")                                                                                 │
 │     99         print(f"  输入: {input_hdf_path}")                                                                        │
 │    100         print(f"  输出: {output_path}")                                                                           │
 │    101         print(f"  范围: Lat({lat_min}, {lat_max}), Lon({lon_min}, {lon_max})")                                    │
 │    102                                                                                                                   │
 │    103         # 实例化裁剪器并执行                                                                                      │
 │    104         cropper = HDF5Cropper(verbose=True)                                                                       │
 │    105                                                                                                                   │
 │    106         # data_vars 设置为 None，让 cropper 自动处理组内所有符合条件的数据集                                      │
 │    107         final_output_path = cropper.crop_file(                                                                    │
 │    108             input_hdf=input_hdf_path,                                                                             │
 │    109             output_hdf=output_path,                                                                               │
 │    110             lat_min=lat_min,                                                                                      │
 │    111             lat_max=lat_max,                                                                                      │
 │    112             lon_min=lon_min,                                                                                      │
 │    113             lon_max=lon_max,                                                                                      │
 │    114             lat_var=lat_var,                                                                                      │
 │    115             lon_var=lon_var,                                                                                      │
 │    116             data_vars=None,                                                                                       │
 │    117             data_group=data_group,                                                                                │
 │    118             latlon_group=latlon_group                                                                             │
 │    119         )                                                                                                         │
 │    120                                                                                                                   │
 │    121         print(f"裁剪成功完成！")                                                                                  │
 │    122         return os.path.abspath(final_output_path)                                                                 │
 │    123                                                                                                                   │
 │    124     except HDF5CropperError as e:                                                                                 │
 │    125         print(f"HDF5 裁剪过程中发生错误: {e}")                                                                    │
 │    126         raise                                                                                                     │
 │    127     except Exception as e:                                                                                        │
 │    128         print(f"执行裁剪时发生未知错误: {e}")                                                                     │
 │    129         traceback.print_exc()                                                                                     │
 │    130         raise                                                                                                     │
 │    131                                                                                                                   │
 │    132                                                                                                                   │
 │    133 if __name__ == '__main__':                                                                                        │
 │    134     # --- 这是一个使用示例 ---                                                                                    │
 │    135     # 确保在运行此示例前，你已经运行过 writehdf5.py 将元数据存入数据库                                            │
 │    136                                                                                                                   │
 │    137     # 1. 指定要裁剪的文件名和范围                                                                                 │
 │    138     target_file = '2A.GPM.Ka.V9-20211125.20230101-S231026-E004258.050253.V07A.HDF5'                               │
 │    139     latitude_range = (-58, -48)                                                                                   │
 │    140     longitude_range = (102, 142)                                                                                  │
 │    141                                                                                                                   │
 │    142     print("--- 开始执行裁剪任务 ---")                                                                             │
 │    143                                                                                                                   │
 │    144     try:                                                                                                          │
 │    145         # 2. 调用主函数                                                                                           │
 │    146         result_path = find_and_crop_hdf5(                                                                         │
 │    147             file_name=target_file,                                                                                │
 │    148             lat_min=latitude_range[0],                                                                            │
 │    149             lat_max=latitude_range[1],                                                                            │
 │    150             lon_min=longitude_range[0],                                                                           │
 │    151             lon_max=longitude_range[1],                                                                           │
 │    152             output_dir='out' # 指定输出目录                                                                       │
 │    153         )                                                                                                         │
 │    154         print(f"\n--- 任务成功 ---")                                                                              │
 │    155         print(f"裁剪后的文件已保存到: {result_path}")                                                             │
 │    156                                                                                                                   │
 │    157     except (ValueError, HDF5CropperError) as e:                                                                   │
 │    158         print(f"\n--- 任务失败 ---")                                                                              │
 │    159         print(f"错误: {e}")                                                                                       │
 │    160     except Exception as e:                                                                                        │
 │    161         print(f"\n--- 发生意外错误 ---")                                                                          │
 │    162         print(f"错误: {e}")                                                                                       │
 │    163                                                                                                                   │
 ╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
✦ 我已经成功创建了 src/api_service.py 文件，并将您需要的 find_and_crop_hdf5 函数写入其中。

  这个函数现在是您项目的核心业务逻辑，可以直接被您的Web服务器（如使用Flask或FastAPI）调用。

  如何使用这个函数

1. 确保元数据已在数据库中：在调用此函数前，必须先运行 writehdf5.py，将目标HDF5文件的元数据存入PostgreSQL数据库。
2. 从您的服务器代码中调用:

   例如，如果您使用 FastAPI，您的服务器代码可能看起来像这样：

    1     # main_server.py
    2     from fastapi import FastAPI, HTTPException
    3     from pydantic import BaseModel
    4     import sys
    5
    6     # 将 src 目录添加到 python 路径
    7     sys.path.append('./src')
    8     from api_service import find_and_crop_hdf5, HDF5CropperError
    9
   10     app = FastAPI()
   11
   12     class CropRequest(BaseModel):
   13         file_name: str
   14         lat_min: float
   15         lat_max: float
   16         lon_min: float
   17         lon_max: float
   18
   19     @app.post("/crop")
   20     async def crop_endpoint(request: CropRequest):
   21         try:
   22             result_path = find_and_crop_hdf5(
   23                 file_name=request.file_name,
   24                 lat_min=request.lat_min,
   25                 lat_max=request.lat_max,
   26                 lon_min=request.lon_min,
   27                 lon_max=request.lon_max
   28             )
   29             return {"message": "Cropping successful", "output_file": result_path}
   30         except (ValueError, HDF5CropperError) as e:
   31             raise HTTPException(status_code=404, detail=str(e))
   32         except Exception as e:
   33             raise HTTPException(status_code=500, detail=f"An internal error occurred: {e}")

3. 直接运行测试:
   您也可以直接运行 python src/api_service.py
   来测试这个函数的功能。它会使用文件中预设的文件名和经纬度范围进行一次裁剪测试。

  这个 api_service.py 脚本为您后续的服务器开发奠定了坚实的基础。接下来您有什么计划？是准备编写服务器代码，还是有其他任务？

╭────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│  > 这些东西现在都是在我的本地进行的，那么怎么放到我的服务器上，我希望能给用户一个web界面这种好看的可视化的东西，他在web里 │
│    可以输入他需要输入的内容，可以下拉查看能处理的文件                                                                     │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

✦ 好的，这是一个非常棒的想法！将您的工具部署为带有Web界面的服务，是使其变得易于使用和分享的最佳方式。

  我们将分几步来完成这个任务。我会为您创建一个完整的、可以运行的Web应用。

  技术选型

* Web框架: 我们将使用 Flask。它是一个轻量级的 Python Web 框架，非常适合将现有的 Python
  脚本快速封装成服务，而且学习曲线平缓。
* 前端样式: 我们将使用
  Bootstrap。这是一个流行的CSS框架，可以让我们快速构建出美观、响应式的Web界面，无需从零开始编写复杂的CSS。

  行动计划

1. 创建新的项目结构: 我会在您的项目根目录下创建一个新的主程序 app.py 用于运行Flask服务，以及一个 templates
   文件夹来存放HTML页面。
2. 编写后端代码 (`app.py`):
   * 创建一个Flask应用。
   * 编写一个路由 (/) 来显示主页。这个路由会连接数据库，获取所有可供处理的HDF5文件名列表，并将其传递给HTML页面。
   * 编写一个路由 (/crop) 来接收前端表单提交的数据（文件名、经纬度等）。
   * 在这个路由中，调用我们之前创建的 api_service.find_and_crop_hdf5 函数来执行裁剪。
   * 根据裁剪结果，向用户显示成功或失败页面。
   * 编写一个路由 (/download/`<filename>`) 以便用户可以下载生成的裁剪文件。
3. 编写前端页面 (`templates/index.html` 和 `templates/result.html`):
   * index.html:
     这是应用的主页。它将包含一个表单，其中有一个下拉菜单（用于选择文件）和四个输入框（用于输入经纬度范围）。
   * result.html: 这个页面用于向用户显示操作结果。如果成功，它会提供一个下载链接；如果失败，它会显示一个清晰的错误信息。
4. 指导您如何运行: 最后，我会告诉您需要安装什么依赖以及如何启动这个Web服务。

  您准备好让我开始创建这些文件了吗？




   **psql postgres://juiceuser:0333@localhost:15432/juicefs**
