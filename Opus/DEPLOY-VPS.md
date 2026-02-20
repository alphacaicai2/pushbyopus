# Opus Relay · VPS 24 小时部署指南

在已有 Docker 的 VPS 上部署 Opus Relay，与现有容器隔离、互不影响。

---

## 1. 原则：独立运行、不碰老服务

- 使用 **独立 compose 项目名**（`-p opus`），与现有 `docker compose` 栈分开。
- 只占用 **一个端口**（默认 8090），可改成其他端口避免冲突。
- 数据与配置放在 **单独目录**，不挂载到已有容器。

---

## 2. 在 VPS 上操作

### 2.1 准备目录与代码

```bash
# 选一个目录，例如（与现有项目分开）
mkdir -p /opt/opus-relay
cd /opt/opus-relay

# 方式 A：从 Git 拉取（推荐）
git clone https://github.com/alphacaicai2/pushbyopus.git .
cd Opus

# 方式 B：本地上传
# 在本地打包 Opus 目录后 scp 到 VPS，再解压到 /opt/opus-relay/Opus
```

### 2.2 配置文件

```bash
cd /opt/opus-relay/Opus

cp config.example.json config.json
# 编辑 config.json，填入 miniflux_url、miniflux_token、translation、routes 等
nano config.json   # 或 vim
```

若 8090 与现有服务冲突，先改端口再启动（见 2.4）。

### 2.3 构建并后台运行（独立项目名）

```bash
cd /opt/opus-relay/Opus

# 构建镜像（仅首次或代码更新后）
docker compose -p opus build

# 后台启动，restart: always 保证 24 小时持续运行
docker compose -p opus up -d
```

- `-p opus`：项目名为 `opus`，与默认的 `compose` 项目、其他 `-p xxx` 的栈互不干扰。
- 容器名固定为 `opus-relay`，重启/更新不会和别的服务混在一起。

### 2.4 若端口 8090 已被占用

编辑 `docker-compose.yml`，把端口改成未占用的主机端口，例如 18090：

```yaml
ports:
  - "18090:8090"   # 主机 18090 → 容器内 8090
```

然后：

```bash
docker compose -p opus up -d
```

访问时用：`http://你的VPS IP:18090`。

---

## 3. 常用命令

| 操作           | 命令 |
|----------------|------|
| 查看状态       | `docker compose -p opus ps` |
| 查看日志       | `docker compose -p opus logs -f opus-relay` |
| 停止           | `docker compose -p opus down` |
| 更新代码后重建 | `git pull`（若在 repo 里）→ `docker compose -p opus build --no-cache` → `docker compose -p opus up -d` |

---

## 4. 与现有 Docker 的关系

- **网络**：未在 compose 里指定 `network_mode` 或 `networks` 时，Compose 会为 `opus` 项目创建独立网络（如 `opus_default`），**不会**加入你现有服务的网络。
- **卷**：只挂载当前目录下的 `config.json` 和 `./data`，**不会**挂载到已有容器的卷。
- **端口**：只映射 8090（或你改的端口），**不会**动其它服务已占用的端口。

因此现有通过 Docker 跑的程序不会受影响。

---

## 5. 访问与检查

- Web 管理界面：`http://VPS的IP:8090`（若改了端口则用对应端口）。
- 确认 24 小时运行：`docker compose -p opus ps` 中 `opus-relay` 状态为 `Up`，且 `restart: always` 已生效（VPS 重启后会自动拉起）。

---

## 6. 可选：用 systemd 在非 Docker 下 24 小时运行

若你更希望用 systemd 直接跑 Python（不用 Docker），可参考：

```bash
# 安装依赖
cd /opt/opus-relay/Opus
pip install -r requirements.txt   # 或 uv pip install -r requirements.txt

# 创建 systemd 服务（示例）
sudo tee /etc/systemd/system/opus-relay.service << 'EOF'
[Unit]
Description=Opus Relay
After=network.target

[Service]
Type=simple
User=你的用户
WorkingDirectory=/opt/opus-relay/Opus
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10
Environment=TZ=Asia/Shanghai

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable opus-relay
sudo systemctl start opus-relay
```

一般推荐用 **Docker + `-p opus`** 部署，与现有 Docker 环境一致且隔离更好。
