"""路由规则模型 - 简化版：分类ID到Webhook的映射"""
from datetime import datetime

from sqlmodel import SQLModel, Field


class RouteRule(SQLModel, table=True):
    """路由规则 - 一个分类可以对应多个 webhook"""

    __tablename__ = "route_rules"

    id: int | None = Field(default=None, primary_key=True)
    category_id: int = Field(description="Miniflux 分类 ID")
    category_name: str = Field(default="", description="分类名称（仅用于显示）")
    webhook_url: str = Field(description="Discord webhook URL")
    enabled: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RouteRuleCreate(SQLModel):
    """创建路由规则"""

    category_id: int
    category_name: str = ""
    webhook_url: str
    enabled: bool = True


class RouteRuleUpdate(SQLModel):
    """更新路由规则"""

    category_id: int | None = None
    category_name: str | None = None
    webhook_url: str | None = None
    enabled: bool | None = None
