# Miniflux → Discord 推送系统实施计划

## 项目概述

构建一个从 Miniflux RSS 抓取内容并推送到 Discord 的系统。

- **数据源**: Miniflux API (`https://miniflux.vibexcap.com`)
- **推送方式**: Discord Webhook
- **部署方式**: 本地 Windows + Docker（后续可迁云端）

---

## Miniflux 分组结构

| ID | 分组名称 |
|----|----------|
| 2 | AI 与深度学习 |
| 3 | Web 与前端 |
| 4 | 创业与风险投资 |
| 5 | 日报 |
| 6 | 海外大厂 |
| 7 | 科技商业与分析 |
| 8 | 综合分组 |
| 9 | 知名媒体 |

---

## 技术栈

| 组件 | 技术选型 |
|------|----------|
| 后端框架 | Python 3.12 + FastAPI |
| 调度器 | APScheduler |
| HTTP 客户端 | httpx |
| 数据库 | SQLite |
| 前端 | Jinja2 + HTMX（服务端渲染） |
| 容器化 | Docker + Docker Compose |

---

## 核心功能

### 1. 实时推送
- 轮询间隔：10-15 分钟（可配置）
- 推送内容：原文（超长分段发送）
- 必须字段：标题 / 来源 / 发布时间 / 链接

### 2. 日报汇总
- 周期：12h / 24h（可配置）
- AI 摘要长度：短/中/长（可配置）
- 语言模式：中文/英文/仅翻译标题/保留原文

### 3. 配置界面
- 自动拉取 Miniflux 分组
- 每个分组可配置对应的 Discord Webhook URL
- 实时/日报开关（每个分组独立）
- 日报配置（周期、摘要长度、语言）
- 轮询频率配置
- "立即拉取一次"按钮
- 去重状态展示

---

## 数据库设计

### category_binding（分组绑定）
```sql
CREATE TABLE category_binding (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL,        -- Miniflux Category ID
    category_name TEXT NOT NULL,         -- Category 名称（冗余存储）
    webhook_url TEXT,                    -- Discord Webhook URL
    realtime_enabled BOOLEAN DEFAULT 1,  -- 是否启用实时推送
    digest_enabled BOOLEAN DEFAULT 0,    -- 是否纳入日报
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### digest_setting（日报配置）
```sql
CREATE TABLE digest_setting (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- 单例
    period_hours INTEGER DEFAULT 24,        -- 周期（小时）
    summary_length TEXT DEFAULT 'medium',   -- short/medium/long
    language_mode TEXT DEFAULT 'chinese',   -- chinese/english/title_only/original
    digest_time TEXT DEFAULT '08:00',       -- 日报发送时间
    timezone TEXT DEFAULT 'Asia/Shanghai'
);
```

### poll_setting（轮询配置）
```sql
CREATE TABLE poll_setting (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- 单例
    interval_minutes INTEGER DEFAULT 15,    -- 轮询间隔（分钟）
    last_poll_at TIMESTAMP,                 -- 上次轮询时间
    miniflux_url TEXT,
    miniflux_token TEXT                     -- 加密存储
);
```

### delivery_state（推送状态）
```sql
CREATE TABLE delivery_state (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL,
    last_entry_id INTEGER NOT NULL,         -- 该分组最后推送的 entry ID
    last_push_at TIMESTAMP,
    UNIQUE(category_id)
);
```

### delivery_log（推送日志）
```sql
CREATE TABLE delivery_log (
    id INTEGER PRIMARY KEY,
    entry_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    feed_id INTEGER,
    title TEXT,
    url TEXT,
    webhook_url TEXT,
    mode TEXT,                              -- realtime / digest
    status TEXT,                            -- success / failed
    error_message TEXT,
    pushed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## API 设计

### 配置管理
- `GET /api/categories` - 获取 Miniflux 分组并同步到本地
- `GET /api/bindings` - 获取所有分组绑定配置
- `PUT /api/bindings/{category_id}` - 更新某个分组的绑定配置
- `GET /api/digest-settings` - 获取日报配置
- `PUT /api/digest-settings` - 更新日报配置
- `GET /api/poll-settings` - 获取轮询配置
- `PUT /api/poll-settings` - 更新轮询配置

### 手动触发
- `POST /api/poll/now` - 立即拉取一次
- `POST /api/digest/now` - 立即生成并发送日报

### 状态查询
- `GET /api/status` - 获取系统状态（上次轮询时间、各分组推送状态）
- `GET /api/logs` - 获取推送日志

### 前端页面
- `GET /` - 配置界面主页

---

## 项目结构

```
push/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置管理
│   ├── database.py             # SQLite 连接
│   ├── models.py               # 数据模型
│   ├── schemas.py              # Pydantic schemas
│   ├── miniflux.py             # Miniflux API 客户端
│   ├── discord.py              # Discord Webhook 客户端
│   ├── scheduler.py            # APScheduler 调度
│   ├── poller.py               # 轮询逻辑
│   ├── dispatcher.py           # 推送分发逻辑
│   ├── digest.py               # 日报生成逻辑
│   └── routers/
│       ├── config.py           # 配置相关 API
│       ├── poll.py             # 轮询相关 API
│       └── status.py           # 状态查询 API
├── templates/
│   └── index.html              # 配置界面
├── static/
│   └── style.css               # 样式
├── data/
│   └── push.db                 # SQLite 数据库
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── plan.md                     # 本文件
└── push.md                     # 原始需求
```

---

## 实施阶段

### Phase 1: 基础框架 + 配置界面
**目标**: 能看到配置界面，能保存 Webhook 映射

- [ ] 项目初始化（FastAPI + SQLite）
- [ ] 数据库模型创建
- [ ] Miniflux API 客户端（获取分组）
- [ ] 配置 API（CRUD）
- [ ] 配置界面（分组列表 + Webhook 输入框）
- [ ] Docker 配置

### Phase 2: 实时推送
**目标**: 能自动拉取新内容并推送到 Discord

- [ ] 轮询调度器（APScheduler）
- [ ] 获取新条目逻辑（增量拉取）
- [ ] 去重机制
- [ ] Discord Webhook 推送
- [ ] 超长内容分段
- [ ] 推送日志

### Phase 3: 日报功能
**目标**: 能生成并发送日报

- [ ] 日报调度器
- [ ] 条目聚合逻辑
- [ ] AI 摘要集成（可选 LLM API）
- [ ] 日报格式化
- [ ] 日报推送

### Phase 4: 完善
**目标**: 提升稳定性和易用性

- [ ] 错误重试机制
- [ ] 配置导入/导出
- [ ] 更好的日志展示
- [ ] 使用文档

---

## 界面设计稿

```
┌─────────────────────────────────────────────────────────────────┐
│  📰 Miniflux → Discord 推送系统                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─ 系统状态 ─────────────────────────────────────────────────┐ │
│  │ 上次轮询: 2026-02-18 16:30:00  │ 状态: ✅ 运行中            │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ Miniflux 配置 ────────────────────────────────────────────┐ │
│  │ URL:    [https://miniflux.vibexcap.com            ]        │ │
│  │ Token:  [•••••••••••••••••••••••••••••••••••      ] [显示] │ │
│  │                                                              │ │
│  │ 轮询间隔: [15 分钟 ▼]    [🔄 立即拉取一次]                    │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ 分组映射 ─────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  分组名称          Webhook URL                    实时  日报│ │
│  │  ──────────────────────────────────────────────────────────│ │
│  │  AI 与深度学习     [https://discord.com/api/...    ] [✓] [ ]│ │
│  │  Web 与前端        [                              ] [ ] [✓]│ │
│  │  创业与风险投资    [https://discord.com/api/...    ] [✓] [ ]│ │
│  │  日报             [https://discord.com/api/...    ] [ ] [✓]│ │
│  │  海外大厂          [                              ] [ ] [ ]│ │
│  │  科技商业与分析    [                              ] [ ] [ ]│ │
│  │  综合分组          [                              ] [ ] [ ]│ │
│  │  知名媒体          [https://discord.com/api/...    ] [✓] [ ]│ │
│  │                                                              │ │
│  │  [🔄 同步 Miniflux 分组]                                     │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  ┌─ 日报配置 ────────────────────────────────────────────────┐ │
│  │ 发送时间: [08:00 ]  周期: [24 小时 ▼]                        │ │
│  │ 摘要长度: [中等 ▼]   语言: [中文 ▼]                          │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│  [💾 保存所有配置]                                                │
│                                                                 │
│  ┌─ 推送日志（最近 10 条）────────────────────────────────────┐ │
│  │ 时间         分组          标题                 状态        │ │
│  │ ───────────────────────────────────────────────────────────│ │
│  │ 16:30:00    AI与深度学习   OpenAI 发布 GPT-5    ✅ 成功     │ │
│  │ 16:15:00    知名媒体       UK inflation...      ✅ 成功     │ │
│  │ 16:00:00    创业与风险投资  Series A 融资...    ✅ 成功     │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 待办事项

- [ ] Phase 1: 基础框架 + 配置界面
- [ ] Phase 2: 实时推送
- [ ] Phase 3: 日报功能
- [ ] Phase 4: 完善

---

## 环境要求

- Windows 10/11
- Docker Desktop
- Python 3.12（开发时）
