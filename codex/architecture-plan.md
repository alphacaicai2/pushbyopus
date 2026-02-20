# Miniflux → Discord 转发器

> 从零设计的简洁架构

---

## 1. 项目定位

一个轻量的 RSS 转发器：从 Miniflux 接收新文章，推送到 Discord。

**核心功能**：
- 去重（避免重复推送）
- 路由（feed/category → Discord webhook，支持一对多）
- 翻译（标题翻成中文，快扫一眼）
- 限流（429 自动退避）

**后续增强**：
- 聚合（120秒窗口，减少消息数）

---

## 2. 技术栈

| 组件 | 选择 | 理由 |
|-----|------|------|
| 框架 | **FastAPI** | 异步、开发快 |
| HTTP | **httpx** | 异步请求 |
| 数据库 | **SQLite** | 零配置 |
| ORM | **SQLModel** | 类型安全 |

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
│   ├── translate.py     # 翻译标题
│   ├── router.py        # 路由规则
│   └── dispatch.py      # 发送到 Discord
│
├── routers/             # API 路由
│   ├── webhook.py       # Miniflux webhook
│   └── admin.py         # 管理接口
│
└── templates/           # 配置 UI
    └── index.html
```

---

## 4. 数据模型

```sql
-- 路由规则
CREATE TABLE route_rules (
    id INTEGER PRIMARY KEY,
    match_type TEXT NOT NULL,      -- 'feed' | 'category'
    match_value TEXT NOT NULL,     -- feed_id 或 category_id
    webhook_url TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 去重
CREATE TABLE seen_entries (
    entry_url TEXT PRIMARY KEY,
    seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 轮询游标
CREATE TABLE sync_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. 核心流程

```
Miniflux Webhook ──┐
                   ├──► Ingest ──► Dedup ──► Translate ──► Router ──► Dispatch ──► Discord
Poll (10min) ──────┘
```

---

## 6. 实现计划

### 最小转发器

**目标**：能收能发，不重复

- [x] Ingest - webhook 接收 + 轮询
- [x] Dedup - URL 去重
- [x] Router - feed/category → webhook
- [x] Dispatch - 直推 Discord，429 退避

### 完整转发器

**目标**：加上翻译和 UI

- [ ] Translate - 标题翻译
- [ ] Admin UI - 路由规则管理界面

### 后续增强

- [ ] Aggregate - 120秒聚合窗口

---

## 7. API 设计

```
POST /webhooks/miniflux     # 接收 Miniflux webhook
GET  /admin/rules           # 获取路由规则
POST /admin/rules           # 新增规则
PUT  /admin/rules/{id}      # 修改规则
DELETE /admin/rules/{id}    # 删除规则
POST /admin/sync-feeds      # 同步 feed 列表
POST /admin/test-push/{id}  # 测试推送
GET  /                      # 配置 UI
```

---

## 8. 部署

```yaml
services:
  relay:
    build: .
    ports:
      - "8080:8080"
    environment:
      - MINIFLUX_URL=${MINIFLUX_URL}
      - MINIFLUX_TOKEN=${MINIFLUX_TOKEN}
      - TRANSLATE_PROVIDER=openai
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    volumes:
      - ./data:/app/data
```

---

## 9. 待确认

- [ ] 翻译 API 选择？
- [ ] UI 形式？

---

*更新: 2026-02*
