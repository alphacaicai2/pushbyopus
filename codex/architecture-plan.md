# Miniflux → Discord 推送服务

> 从零设计的简洁架构

---

## 1. 项目定位

把 Miniflux 的新文章推送到 Discord，支持：
- 翻译标题（快扫一眼）
- 去重（避免重复）
- 多频道路由（一对多）
- ~~聚合发送~~ → **后续功能**
- 自动限流（429 退避）

---

## 2. 技术栈

| 组件 | 选择 | 理由 |
|-----|------|------|
| 框架 | **FastAPI** | 异步、开发快、代码少 |
| HTTP 客户端 | **httpx** | 异步、支持重试 |
| 数据库 | **SQLite** | 零配置、够用 |
| ORM | **SQLModel** | SQLAlchemy + Pydantic，类型安全 |
| 调度 | **asyncio** | 后台任务，无需额外组件 |

**结论**：单体服务，最小依赖，一行命令启动。

---

## 3. 模块划分

```
codex/app/
├── main.py              # FastAPI 入口
├── config.py            # 配置管理
├── db.py                # 数据库连接
│
├── models/              # 数据模型
│   ├── route_rule.py    # 路由规则
│   ├── seen_entry.py    # 去重记录
│   └── sync_state.py    # 轮询游标
│
├── services/            # 核心服务
│   ├── ingest.py        # 接收 webhook / 轮询
│   ├── dedup.py         # 去重
│   ├── translate.py     # 翻译标题（后续）
│   ├── router.py        # 路由规则
│   └── dispatch.py      # 发送到 Discord
│
├── routers/             # API 路由
│   ├── webhook.py       # Miniflux webhook
│   └── admin.py         # 管理接口
│
└── templates/           # 配置 UI（后续）
    └── index.html
```

### 模块职责

| 模块 | 职责 | 阶段 |
|-----|------|------|
| `ingest` | 接收事件：webhook 入口 + 轮询拉取 | MVP |
| `dedup` | 去重：URL 归一化，DB 唯一约束 | MVP |
| `router` | 路由：feed/category → webhook 列表 | MVP |
| `dispatch` | 发送：直推 Discord，429 退避 | MVP |
| `translate` | 翻译：超时 1.5s，失败用原标题 | V1.0 |
| `aggregate` | 聚合：120秒窗口，最多15条/消息 | 后续 |

---

## 4. 数据模型（最小化）

```sql
-- 路由规则（UI可编辑）
CREATE TABLE route_rules (
    id INTEGER PRIMARY KEY,
    match_type TEXT NOT NULL,      -- 'feed' | 'category'
    match_value TEXT NOT NULL,     -- feed_id 或 category_id
    webhook_url TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uq_route ON route_rules(match_type, match_value, webhook_url);

-- 去重（7天过期，定时清理）
CREATE TABLE seen_entries (
    entry_url TEXT PRIMARY KEY,    -- 归一化后的 URL
    seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 轮询游标
CREATE TABLE sync_state (
    key TEXT PRIMARY KEY,          -- 'miniflux_last_entry_id'
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. 核心流程

### MVP 流程（实时推送）

```
┌─────────────────────────────────────────────────────────┐
│  Miniflux Webhook  ──┐                                 │
│                      ├──► Ingest ──► Dedup ──► Router   │
│  Poll (10min) ───────┘              │                  │
└─────────────────────────────────────┼───────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────┐
│                       Dispatch                          │
│              直推 Discord，429 自动退避                  │
└─────────────────────────────┬───────────────────────────┘
                              ▼
                         Discord
```

### 后续流程（加聚合）

```
... Router → Aggregate (120秒) → Dispatch → Discord
```

---

## 6. 实现计划

### Phase 1: MVP（最小可用）

**目标**：能收能发，不重复

| 模块 | 功能 |
|-----|------|
| `ingest` | webhook 接收 + 10分钟轮询 |
| `dedup` | URL 去重，7天过期 |
| `router` | feed/category → webhook（支持一对多） |
| `dispatch` | 直推 Discord，429 退避重试 |

### Phase 2: V1.0

| 模块 | 功能 |
|-----|------|
| `translate` | 标题翻译（OpenAI），1.5s 超时 |
| `admin UI` | 路由规则管理界面 |

### Phase 3: 后续

| 模块 | 功能 |
|-----|------|
| `aggregate` | 120秒聚合，解决高频推送 |

---

## 7. API 设计

### Webhook 入口
```
POST /webhooks/miniflux
X-Miniflux-Signature: sha256=xxx

{
  "id": 123,
  "title": "Article Title",
  "url": "https://...",
  "feed": {"id": 1, "title": "Feed Name"},
  "category": {"id": 2, "title": "Category Name"}
}
```

### 管理接口
```
GET    /admin/rules           # 获取所有路由规则
POST   /admin/rules           # 新增规则
PUT    /admin/rules/{id}      # 修改规则
DELETE /admin/rules/{id}      # 删除规则
POST   /admin/sync-feeds      # 从 Miniflux 同步 feed 列表
POST   /admin/test-push/{id}  # 测试推送
```

---

## 8. 部署方案

### Docker Compose

```yaml
services:
  push-relay:
    build: .
    ports:
      - "8080:8080"
    environment:
      - MINIFLUX_URL=${MINIFLUX_URL}
      - MINIFLUX_TOKEN=${MINIFLUX_TOKEN}
      - INGEST_MODE=webhook,poll
      - POLL_INTERVAL_SEC=600
      - TRANSLATE_PROVIDER=none
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

### 环境变量

```bash
# 必填
MINIFLUX_URL=https://miniflux.example.com
MINIFLUX_TOKEN=your_token

# 可选（V1.0）
TRANSLATE_PROVIDER=openai
OPENAI_API_KEY=sk-xxx
```

---

## 9. 待确认

- [ ] 翻译用 OpenAI 还是其他？
- [ ] UI 用 HTML 还是其他形式？

---

*更新时间: 2026-02*
