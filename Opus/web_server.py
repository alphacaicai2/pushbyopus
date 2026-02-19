"""
Opus Relay - Web 配置管理 API

基于 FastAPI 提供 REST API，用于：
- 读取和保存 config.json
- 测试 Miniflux / 翻译 / Discord 连接
- 获取 Feed 分组列表
"""

import json
import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx

logger = logging.getLogger("opus.web")

# 配置文件路径
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
STATIC_DIR = os.path.join(CONFIG_DIR, "static")

app = FastAPI(title="Opus Relay 配置管理", version="1.0")


# ========== 数据模型 ==========

class TranslationConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o-mini"


class AppConfig(BaseModel):
    miniflux_url: str = ""
    miniflux_token: str = ""
    poll_interval_minutes: int = 5
    batch_interval_seconds: int = 120
    batch_max_items: int = 15
    translation: TranslationConfig = TranslationConfig()
    routes: dict[str, str] = {}


class TestMinifluxRequest(BaseModel):
    url: str
    token: str


class TestTranslationRequest(BaseModel):
    base_url: str
    api_key: str
    model: str = "gpt-4o-mini"


class TestWebhookRequest(BaseModel):
    webhook_url: str


# ========== 配置读写 API ==========

@app.get("/api/config")
async def get_config():
    """读取当前配置"""
    if not os.path.exists(CONFIG_PATH):
        return AppConfig().model_dump()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取配置失败: {e}")


@app.post("/api/config")
async def save_config(config: AppConfig):
    """保存配置到 config.json"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, indent=2, ensure_ascii=False)
        return {"success": True, "message": "配置已保存"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置失败: {e}")


# ========== 测试连接 API ==========

@app.post("/api/test/miniflux")
async def test_miniflux(req: TestMinifluxRequest):
    """测试 Miniflux 连接"""
    url = req.url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{url}/v1/me",
                headers={"X-Auth-Token": req.token},
            )
            if resp.status_code == 200:
                user = resp.json().get("username", "未知")
                return {"success": True, "message": f"连接成功，用户: {user}"}
            else:
                return {"success": False, "message": f"连接失败: HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {e}"}


@app.post("/api/test/translation")
async def test_translation(req: TestTranslationRequest):
    """测试翻译 API"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{req.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {req.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": req.model,
                    "messages": [
                        {"role": "system", "content": "将文章标题翻译为中文，只返回翻译结果。"},
                        {"role": "user", "content": "OpenAI Releases GPT-5 with Breakthrough Capabilities"},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                translated = data["choices"][0]["message"]["content"].strip().strip("\"'")
                return {
                    "success": True,
                    "message": "翻译成功",
                    "original": "OpenAI Releases GPT-5 with Breakthrough Capabilities",
                    "translated": translated,
                    "model": req.model,
                }
            else:
                return {"success": False, "message": f"API 错误: HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"翻译测试失败: {e}"}


@app.post("/api/test/webhook")
async def test_webhook(req: TestWebhookRequest):
    """测试 Discord Webhook（发送一条测试消息）"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                req.webhook_url,
                json={
                    "embeds": [{
                        "title": "🔔 Opus Relay 测试消息",
                        "description": "如果你看到这条消息，说明 Webhook 配置正确！",
                        "color": 5763719,  # 绿色
                    }],
                },
            )
            if resp.status_code == 204:
                return {"success": True, "message": "Webhook 测试成功，请检查 Discord 频道"}
            elif resp.status_code == 404:
                return {"success": False, "message": "Webhook URL 无效（404）"}
            else:
                return {"success": False, "message": f"发送失败: HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"Webhook 测试失败: {e}"}


# ========== Feed 列表 API ==========

@app.get("/api/feeds")
async def get_feeds():
    """获取 Miniflux 的 Feed 分组列表"""
    # 先读取当前配置获取 Miniflux 凭证
    if not os.path.exists(CONFIG_PATH):
        raise HTTPException(status_code=400, detail="请先配置 Miniflux 连接信息")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    url = config.get("miniflux_url", "").rstrip("/")
    token = config.get("miniflux_token", "")

    if not url or not token:
        raise HTTPException(status_code=400, detail="Miniflux URL 或 Token 未配置")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{url}/v1/feeds",
                headers={"X-Auth-Token": token},
            )
            resp.raise_for_status()
            feeds = resp.json()

            # 按分组聚合
            categories = {}
            for feed in feeds:
                cat = feed.get("category", {})
                cat_id = str(cat.get("id", "0"))
                cat_name = cat.get("title", "未分组")

                if cat_id not in categories:
                    categories[cat_id] = {
                        "id": cat_id,
                        "name": cat_name,
                        "feeds": [],
                    }
                categories[cat_id]["feeds"].append({
                    "id": feed.get("id"),
                    "title": feed.get("title", "无标题"),
                })

            # 按分组名称排序
            result = sorted(categories.values(), key=lambda c: c["name"])
            return {"categories": result, "total_feeds": len(feeds)}

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"获取 Feed 列表失败: {e}")


# ========== 静态文件 & 首页 ==========

# 挂载静态文件目录
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    """返回配置页面"""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
