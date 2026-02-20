"""FastAPI 应用入口"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.db import init_db
from app.routers import webhook_router, admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    await init_db()
    yield


app = FastAPI(
    title="Miniflux -> Discord 转发器",
    description="将 Miniflux RSS 新文章转发到 Discord",
    version="0.1.0",
    lifespan=lifespan,
)

# 注册路由
app.include_router(webhook_router)
app.include_router(admin_router)


@app.get("/", response_class=HTMLResponse)
async def index():
    """首页 - 简单的状态页面"""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Miniflux -> Discord 转发器</title>
        <style>
            body { font-family: system-ui; max-width: 800px; margin: 50px auto; padding: 20px; }
            h1 { color: #333; }
            .status { background: #f0f0f0; padding: 20px; border-radius: 8px; }
            .admin-link { display: inline-block; background: #2196f3; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin: 10px 0; }
            .admin-link:hover { background: #1976d2; }
            code { background: #e0e0e0; padding: 2px 6px; border-radius: 4px; }
            a { color: #0066cc; }
        </style>
    </head>
    <body>
        <h1>📰 Miniflux -> Discord 转发器</h1>
        <div class="status">
            <p>✅ 服务运行中</p>
            <a href="/admin/ui" class="admin-link">🔧 打开管理界面</a>
            <h3>API 端点</h3>
            <ul>
                <li><code>POST /webhooks/miniflux</code> - Miniflux webhook 入口</li>
                <li><code>GET /admin/categories</code> - 获取分类列表</li>
                <li><code>GET /admin/rules</code> - 获取路由规则</li>
                <li><code>POST /admin/rules</code> - 创建路由规则</li>
                <li><code>GET /admin/logs</code> - 获取推送日志</li>
            </ul>
            <h3>文档</h3>
            <p><a href="/docs">Swagger UI</a> | <a href="/redoc">ReDoc</a></p>
        </div>
    </body>
    </html>
    """


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}
