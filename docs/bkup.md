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
