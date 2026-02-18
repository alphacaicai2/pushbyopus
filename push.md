# RSS → AI Summary → Discord 推送系统需求文档

## 项目概述

构建一个系统，从 Miniflux RSS 抓取信息，用 AI 总结内容，然后推送到 Discord。

---

## 已确定的需求

### 1. 数据源
- 从 **Miniflux API** 拉取数据
- 调用 `/v1/categories` 确认分组结构

### 2. 触发机制
- **轮询频率**: 10-15 分钟一次
- **实时推送**: Entry 一出现就推（原文多长推多长）

### 3. 推送模式
| 模式 | 说明 |
|------|------|
| **实时推送** | 原文原样转发，包含标题/来源/发布时间/链接 |
| **日报汇总** | 可配置摘要长度、语言模式、周期(12h/24h) |

### 4. 分发策略
- **Discord 频道映射**: 按 Miniflux 分组 → Discord 多频道
- **重复策略**: 允许跨频道重复（同一条命中多个分组可推多次）
- **必须字段**: 标题 / 来源 / 发布时间 / 链接

---

## UI 配置界面需求

需要提供一个可调整的配置界面（网页/本地/脚本都行），包含：

### 必须配置项
1. **Miniflux 分组 → Discord 频道/Webhook 映射**
2. **实时/日报开关**: 每个分组是否启用实时、是否进入日报
3. **日报配置**:
   - 周期：12h / 24h（或自定义）
   - 摘要长度：短/中/长（滑块/下拉）
   - 语言模式：中文/英文/仅翻译标题/保留原文
4. **轮询频率**: 10-15 分钟 + "立即拉取一次"按钮
5. **去重状态展示**: 每个分组最近一次推送的 entry id / 时间

### 技术建议
- 小服务（FastAPI/Express）+ 简单前端页面
- 配置存 SQLite

---

## 待确认问题

### Q7: Discord 推送方式
- [ ] **A. Webhook（推荐）**: 每个频道一个 webhook，最简单，不需要 bot 权限
- [ ] **B. Bot**: 需要加机器人、权限管理更复杂，但可玩更多功能

### Q8: 超长内容处理
Discord 消息有长度限制，超长时如何处理？
- [ ] **A. 截断 + 原文链接**（最简单）
- [ ] **B. 分段多条发送**（可能刷屏）
- [ ] **C. 超长改成 AI 摘要 + 链接**（更稳但不完全原样）

---

## 下一步行动

1. 调用 Miniflux API 确认分组结构（Category/Tag/Folder）
2. 回答 Q7、Q8
3. 设计具体接口字段、数据结构、配置 UI 页面
4. 实现实时/日报两条 pipeline

---

## Miniflux API 验证命令

```bash
# 设置环境变量
export MINIFLUX_URL="https://你的miniflux域名或IP"
export MINIFLUX_TOKEN="你的API token"

# 验证连接
curl -sS -H "X-Auth-Token: $MINIFLUX_TOKEN" "$MINIFLUX_URL/v1/me"

# 查看分类/分组
curl -sS -H "X-Auth-Token: $MINIFLUX_TOKEN" "$MINIFLUX_URL/v1/categories"

# 查看 entry 结构
curl -sS -H "X-Auth-Token: $MINIFLUX_TOKEN" "$MINIFLUX_URL/v1/entries?limit=1&order=published_at&direction=desc"
```
