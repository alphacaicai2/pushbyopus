"""管理 API 路由 - 简化版"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db import get_session
from app.models.route_rule import RouteRuleCreate
from app.models.push_log import PushLog, PushLogRead
from app.services.router import (
    list_route_rules,
    create_route_rule,
    update_route_rule,
    delete_route_rule,
)
from app.services.dispatch import dispatch_one, build_discord_payload
from app.services.miniflux import get_categories
from app.config import get_settings, clear_settings_cache

router = APIRouter(prefix="/admin", tags=["admin"])


# === 配置管理 ===

@router.get("/config")
async def get_config() -> dict:
    """获取当前配置"""
    settings = get_settings()
    return {
        "miniflux_url": settings.miniflux_url or "",
        "miniflux_token": settings.miniflux_token[:8] + "..." if settings.miniflux_token and len(settings.miniflux_token) > 8 else settings.miniflux_token or "",
        "miniflux_token_full": settings.miniflux_token or "",
    }


@router.post("/config")
async def save_config(data: dict) -> dict:
    """保存配置到 .env 文件"""
    from pathlib import Path

    env_path = Path(__file__).parent.parent.parent / ".env"

    # 读取现有配置
    env_lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()

    # 更新配置
    env_dict = {}
    for line in env_lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env_dict[key.strip()] = value.strip()

    # 应用新配置
    if "miniflux_url" in data:
        env_dict["MINIFLUX_URL"] = data["miniflux_url"]
    if "miniflux_token" in data and data["miniflux_token"]:
        env_dict["MINIFLUX_TOKEN"] = data["miniflux_token"]

    # 写回文件
    with open(env_path, "w", encoding="utf-8") as f:
        for key, value in env_dict.items():
            f.write(f"{key}={value}\n")

    # 清除配置缓存
    clear_settings_cache()

    return {"success": True, "message": "配置已保存，重启服务生效"}


# === 分类管理 ===

@router.get("/categories")
async def get_cats() -> list[dict]:
    """从 Miniflux 获取 categories 列表"""
    categories = await get_categories()
    return categories


# === 路由规则 ===

@router.get("/rules")
async def get_rules(
    db: AsyncSession = Depends(get_session),
    enabled_only: bool = False,
) -> list[dict]:
    """获取所有路由规则"""
    rules = await list_route_rules(db=db, enabled_only=enabled_only)
    return [rule.model_dump() for rule in rules]


@router.post("/rules")
async def post_rule(
    data: RouteRuleCreate,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """创建路由规则"""
    rule = await create_route_rule(db=db, data=data)
    return rule.model_dump()


@router.put("/rules/{rule_id}")
async def put_rule(
    rule_id: int,
    data: dict,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """更新路由规则"""
    from app.models.route_rule import RouteRuleUpdate
    update_data = RouteRuleUpdate(**data)
    rule = await update_route_rule(db=db, rule_id=rule_id, data=update_data)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule.model_dump()


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """删除路由规则"""
    success = await delete_route_rule(db=db, rule_id=rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"success": True}


# === 推送日志 ===

@router.get("/logs", response_model=list[PushLogRead])
async def get_logs(
    db: AsyncSession = Depends(get_session),
    limit: int = 50,
) -> list[PushLog]:
    """获取最近推送日志"""
    statement = (
        select(PushLog)
        .order_by(PushLog.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(statement)
    return list(result.scalars().all())


# === 测试推送 ===

@router.post("/test-push/{rule_id}")
async def test_push(
    rule_id: int,
    db: AsyncSession = Depends(get_session),
) -> dict:
    """测试推送"""
    rules = await list_route_rules(db=db)
    rule = next((r for r in rules if r.id == rule_id), None)

    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")

    # 构建测试消息
    test_entry = {
        "title": "测试消息 - 转发器运行正常",
        "url": "https://example.com/test",
        "feed": {"title": "测试源"},
    }

    payload = build_discord_payload(entry=test_entry)
    result = await dispatch_one(webhook_url=rule.webhook_url, payload=payload)

    return {
        "rule_id": rule_id,
        "webhook_url": rule.webhook_url,
        "result": result,
    }


# === Web UI ===

@router.get("/ui", response_class=HTMLResponse)
async def admin_ui():
    """管理界面"""
    return get_admin_html()


def get_admin_html() -> str:
    """返回简化版管理界面 HTML"""
    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Miniflux → Discord 转发器</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; padding: 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #333; margin-bottom: 20px; font-size: 24px; }
        h2 { color: #555; margin: 20px 0 10px; font-size: 16px; display: flex; align-items: center; gap: 8px; }
        .card { background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: 500; font-size: 14px; }
        .form-group input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        .form-group input:focus { outline: none; border-color: #2196f3; }
        .hint { font-size: 12px; color: #888; margin-top: 4px; }
        .btn { padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; margin-right: 8px; }
        .btn-primary { background: #2196f3; color: white; }
        .btn-success { background: #4caf50; color: white; }
        .btn-danger { background: #f44336; color: white; }
        .btn-sm { padding: 6px 12px; font-size: 12px; }
        .btn:hover { opacity: 0.9; }
        .status-ok { color: #4caf50; }
        .status-error { color: #f44336; }
        .loading { color: #666; font-style: italic; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f9f9f9; font-weight: 600; }
        .category-item { display: flex; align-items: center; padding: 12px; border: 1px solid #eee; border-radius: 4px; margin-bottom: 8px; }
        .category-info { flex: 1; }
        .category-name { font-weight: 500; }
        .category-id { font-size: 12px; color: #888; }
        .webhook-input { flex: 2; margin: 0 12px; }
        .webhook-input input { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; }
        .webhook-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
        .webhook-row input { flex: 1; }
        .log-success { color: #4caf50; }
        .log-fail { color: #f44336; }
        .log-time { font-size: 12px; color: #888; }
        .log-title { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .refresh-btn { font-size: 12px; padding: 4px 8px; background: #f0f0f0; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; }
        .refresh-btn:hover { background: #e0e0e0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📰 Miniflux → Discord 转发器</h1>

        <!-- Miniflux 配置 -->
        <div class="card">
            <h2>⚙️ Miniflux 配置</h2>
            <div class="form-group">
                <label>API 地址</label>
                <input type="text" id="miniflux_url" placeholder="https://miniflux.example.com/v1">
                <p class="hint">Miniflux API 地址，以 /v1 结尾</p>
            </div>
            <div class="form-group">
                <label>API Token</label>
                <input type="text" id="miniflux_token" placeholder="输入 API Token">
                <p class="hint">在 Miniflux 设置 → API 密钥 中获取</p>
            </div>
            <button class="btn btn-success" onclick="saveConfig()">保存配置</button>
            <button class="btn btn-primary" onclick="testConnection()">测试连接</button>
            <span id="config-status" style="margin-left: 10px;"></span>
        </div>

        <!-- 分类映射 -->
        <div class="card">
            <h2>
                📂 分类 → Webhook 映射
                <button class="refresh-btn" onclick="loadCategories()">🔄 刷新分类</button>
            </h2>
            <p class="hint" style="margin-bottom: 15px;">为每个分类添加一个或多个 Discord Webhook URL，新文章会自动推送到对应的频道。</p>
            <div id="categories-list">
                <p class="loading">加载中...</p>
            </div>
        </div>

        <!-- 推送日志 -->
        <div class="card">
            <h2>
                📋 推送日志
                <button class="refresh-btn" onclick="loadLogs()">🔄 刷新</button>
            </h2>
            <table>
                <thead>
                    <tr>
                        <th>时间</th>
                        <th>标题</th>
                        <th>分类</th>
                        <th>状态</th>
                    </tr>
                </thead>
                <tbody id="logs-table">
                    <tr><td colspan="4" class="loading">加载中...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const API = '/admin';

        // === 配置管理 ===
        async function loadConfig() {
            try {
                const res = await fetch(API + '/config');
                const config = await res.json();
                document.getElementById('miniflux_url').value = config.miniflux_url || '';
                document.getElementById('miniflux_token').value = config.miniflux_token_full || '';
            } catch (e) {
                console.error('Load config failed:', e);
            }
        }

        async function saveConfig() {
            const data = {
                miniflux_url: document.getElementById('miniflux_url').value,
                miniflux_token: document.getElementById('miniflux_token').value,
            };
            try {
                const res = await fetch(API + '/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(data)
                });
                const result = await res.json();
                if (res.ok) {
                    document.getElementById('config-status').innerHTML = '<span class="status-ok">✓ ' + result.message + '</span>';
                } else {
                    document.getElementById('config-status').innerHTML = '<span class="status-error">✗ 保存失败</span>';
                }
            } catch (e) {
                document.getElementById('config-status').innerHTML = '<span class="status-error">✗ ' + e.message + '</span>';
            }
        }

        async function testConnection() {
            document.getElementById('config-status').innerHTML = '<span class="loading">测试中...</span>';
            try {
                const res = await fetch(API + '/categories');
                const cats = await res.json();
                if (cats.length > 0) {
                    document.getElementById('config-status').innerHTML = '<span class="status-ok">✓ 连接成功，找到 ' + cats.length + ' 个分类</span>';
                    loadCategories();
                } else {
                    document.getElementById('config-status').innerHTML = '<span class="status-error">✗ 连接成功但没有分类，请先保存配置并重启服务</span>';
                }
            } catch (e) {
                document.getElementById('config-status').innerHTML = '<span class="status-error">✗ 连接失败: ' + e.message + '</span>';
            }
        }

        // === 分类映射 ===
        let categoriesData = [];
        let rulesData = [];

        async function loadCategories() {
            const div = document.getElementById('categories-list');
            div.innerHTML = '<p class="loading">加载中...</p>';
            try {
                const [catsRes, rulesRes] = await Promise.all([
                    fetch(API + '/categories'),
                    fetch(API + '/rules')
                ]);
                categoriesData = await catsRes.json();
                rulesData = await rulesRes.json();

                if (categoriesData.length === 0) {
                    div.innerHTML = '<p>没有分类，请检查 Miniflux 配置</p>';
                    return;
                }

                renderCategories();
            } catch (e) {
                div.innerHTML = '<p style="color:red;">加载失败: ' + e.message + '</p>';
            }
        }

        function renderCategories() {
            const div = document.getElementById('categories-list');
            div.innerHTML = categoriesData.map(cat => {
                const catRules = rulesData.filter(r => r.category_id === cat.id);
                return `
                    <div class="category-item" data-category-id="${cat.id}">
                        <div class="category-info">
                            <div class="category-name">${cat.title}</div>
                            <div class="category-id">ID: ${cat.id}</div>
                        </div>
                        <div class="webhook-input">
                            ${catRules.length > 0 ? catRules.map((rule, idx) => `
                                <div class="webhook-row">
                                    <input type="text" value="${rule.webhook_url}" placeholder="Discord Webhook URL"
                                        onchange="updateRule(${rule.id}, this.value)">
                                    <button class="btn btn-danger btn-sm" onclick="deleteRule(${rule.id}, ${cat.id})">删除</button>
                                </div>
                            `).join('') : '<div class="hint">暂无 Webhook</div>'}
                            <div class="webhook-row">
                                <input type="text" id="new-webhook-${cat.id}" placeholder="添加新的 Webhook URL">
                                <button class="btn btn-primary btn-sm" onclick="addWebhook(${cat.id}, '${cat.title.replace(/'/g, "\\\\'")}')">添加</button>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function addWebhook(categoryId, categoryName) {
            const input = document.getElementById('new-webhook-' + categoryId);
            const webhookUrl = input.value.trim();
            if (!webhookUrl) {
                alert('请输入 Webhook URL');
                return;
            }
            if (!webhookUrl.startsWith('https://discord.com/api/webhooks/')) {
                alert('请输入有效的 Discord Webhook URL');
                return;
            }

            try {
                const res = await fetch(API + '/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        category_id: categoryId,
                        category_name: categoryName,
                        webhook_url: webhookUrl,
                        enabled: true
                    })
                });
                if (res.ok) {
                    input.value = '';
                    await loadCategories();
                } else {
                    const err = await res.json();
                    alert('添加失败: ' + (err.detail || '未知错误'));
                }
            } catch (e) {
                alert('添加失败: ' + e.message);
            }
        }

        async function updateRule(ruleId, webhookUrl) {
            try {
                await fetch(API + '/rules/' + ruleId, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ webhook_url: webhookUrl })
                });
            } catch (e) {
                alert('更新失败: ' + e.message);
            }
        }

        async function deleteRule(ruleId, categoryId) {
            if (!confirm('确定删除此 Webhook？')) return;
            try {
                const res = await fetch(API + '/rules/' + ruleId, { method: 'DELETE' });
                if (res.ok) {
                    await loadCategories();
                } else {
                    alert('删除失败');
                }
            } catch (e) {
                alert('删除失败: ' + e.message);
            }
        }

        // === 推送日志 ===
        async function loadLogs() {
            const tbody = document.getElementById('logs-table');
            tbody.innerHTML = '<tr><td colspan="4" class="loading">加载中...</td></tr>';
            try {
                const res = await fetch(API + '/logs?limit=50');
                const logs = await res.json();
                if (logs.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4">暂无日志</td></tr>';
                    return;
                }
                tbody.innerHTML = logs.map(log => `
                    <tr>
                        <td class="log-time">${new Date(log.created_at).toLocaleString('zh-CN')}</td>
                        <td class="log-title" title="${log.entry_title}"><a href="${log.entry_url}" target="_blank">${log.entry_title}</a></td>
                        <td>${log.category_name || '-'}</td>
                        <td class="${log.success ? 'log-success' : 'log-fail'}">
                            ${log.success ? '✓ 成功' : '✗ ' + (log.error_message || '失败')}
                        </td>
                    </tr>
                `).join('');
            } catch (e) {
                tbody.innerHTML = '<tr><td colspan="4" style="color:red;">加载失败: ' + e.message + '</td></tr>';
            }
        }

        // 页面加载
        loadConfig();
        loadCategories();
        loadLogs();

        // 定时刷新日志
        setInterval(loadLogs, 30000);
    </script>
</body>
</html>'''
