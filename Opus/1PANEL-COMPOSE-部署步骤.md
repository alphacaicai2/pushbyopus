# 方式 A：在 1Panel 里用 Compose 部署 Opus Relay（详细步骤）

按顺序做即可，完成后再访问 `http://你的VPS的IP:8090` 使用 Web 管理界面。

---

## 第一步：把代码放到 VPS 上

任选一种方式。

### 做法 1：用 1Panel 终端 + Git（推荐）

1. 打开 1Panel 左侧 **「终端」**。
2. 输入并执行（一行一行来）：

```bash
# 进入一个目录，例如 /opt（没有就建一个）
cd /opt

# 克隆仓库（会得到 pushbyopus 文件夹，里面包含 Opus）
git clone https://github.com/alphacaicai2/pushbyopus.git

# 确认 Opus 目录存在
ls pushbyopus/Opus
```

看到有 `main.py`、`docker-compose.yml`、`Dockerfile`、`static` 等就对了。  
之后所有步骤里说的「Opus 目录」都是：**`/opt/pushbyopus/Opus`**（如果你克隆到了别处，就换成你的路径）。

### 做法 2：本地上传

1. 在 1Panel 打开 **「文件」**。
2. 进入 `/opt`（或你想放的目录），新建文件夹，例如 `opus-relay`。
3. 把你电脑上 **Push 仓库里的整个 `Opus` 文件夹**（含 `main.py`、`docker-compose.yml`、`Dockerfile`、`static`、`requirements.txt` 等）打包成 zip，上传到这个目录并解压。  
   确保最终结构是：`/opt/opus-relay/Opus/docker-compose.yml` 存在。  
   下文中「Opus 目录」就指：**`/opt/opus-relay/Opus`**（按你实际路径改）。

---

## 第二步：准备配置文件 config.json

1. 在 1Panel **「文件」** 里进入 **Opus 目录**（即包含 `docker-compose.yml` 的那一层）。
2. 找到 `config.example.json`，**复制一份**，重命名为 **`config.json`**。
3. 右键 `config.json` 选 **「编辑」**，**至少填写**：
   - `miniflux_url`：你的 Miniflux 地址，如 `https://miniflux.example.com`
   - `miniflux_token`：Miniflux API Token  
   其余可先不填或留空：
   - `translation`：若用硅基流动等再填；不用就留空对象 `{}`
   - **`routes` 可不设**：启动后可在 Web 管理界面里按「分组 → Webhook」添加并保存，会写回 `config.json`。
4. 保存后关闭编辑器。

---

## 第三步：在 1Panel 里用 Compose 创建栈并构建镜像

1. 在 1Panel 左侧进入 **「容器」**（或 **「Docker」**）。
2. 找到 **「Compose」** / **「编排」** / **「项目」** 这类入口，点进去。
3. 点击 **「创建 Compose 项目」** / **「新建」** / **「添加」**。
4. 填写：
   - **项目名称**：填 **`opus`**（方便和别的服务区分）。
   - **Compose 文件** / **编排文件**：
     - 若有 **「路径」** 或 **「选择目录」**：选 **Opus 目录**（如 `/opt/pushbyopus/Opus` 或 `/opt/opus-relay/Opus`），让 1Panel 使用该目录下的 `docker-compose.yml`。
     - 若是 **「填写 YAML」**：把下面整段复制进去（注意把路径改成你的 Opus 目录）：

```yaml
services:
  opus-relay:
    build: .
    image: opus-relay:latest
    container_name: opus-relay
    restart: always
    ports:
      - "8090:8090"
    volumes:
      - ./config.json:/app/config.json:ro
      - ./data:/app/data
    environment:
      - TZ=Asia/Shanghai
```

   若用「路径」方式，1Panel 一般会自动用该目录下的 `docker-compose.yml`，无需手贴 YAML。
5. **构建并启动**：
   - 若有 **「构建」** 按钮：先点 **「构建」**，等构建完成（会按 Dockerfile 生成镜像）。
   - 再点 **「启动」** / **「部署」**。
   - 若只有 **「部署」** / **「运行」**：直接点，1Panel 通常会先构建再启动。
6. 等状态变为 **运行中** / **Up**。在 **「容器」** 列表里应能看到 **`opus-relay`**，在 **「镜像」** 里能看到新构建的镜像（如 `opus-relay`）。

---

## 第四步：端口冲突时改端口（可选）

若 8090 已被占用（1Panel 或其它服务）：

1. 在 1Panel 的 **「文件」** 里打开 **Opus 目录** 下的 `docker-compose.yml`。
2. 把 `ports` 改成例如：

```yaml
ports:
  - "18090:8090"
```

3. 保存后，回到 **Compose 项目 `opus`**，点 **「重新部署」** / **「重建并启动」**（或停止后重新启动）。
4. 访问时用：**`http://你的VPS的IP:18090`**。

---

## 第五步：验证与访问

1. 在 1Panel **「容器」** 里确认 **`opus-relay`** 状态为 **运行中**。
2. 浏览器打开：**`http://你的VPS的IP:8090`**（若改了端口则用 18090）。
3. 能看到 **「Opus Relay · 配置管理」** 页面即部署成功；可在该页面改配置、看实时日志。

---

## 之后常用操作（在 1Panel）

| 操作       | 在 1Panel 里怎么做 |
|------------|--------------------|
| 看日志     | 容器 → 点 `opus-relay` → 日志 |
| 重启       | 容器 → 选 `opus-relay` → 重启 |
| 停止       | Compose 项目 `opus` → 停止 |
| 更新代码   | 终端里 `cd /opt/pushbyopus && git pull`，然后 Compose 项目 `opus` → 重新构建并启动 |

---

**总结**：方式 A 就是在 1Panel 里用 **Compose** 指向 **Opus 目录**，让 1Panel 按 Dockerfile **自动构建镜像**并**创建容器**；你只要把代码放到服务器、配好 `config.json`、在 Compose 里选对目录并构建启动即可。
