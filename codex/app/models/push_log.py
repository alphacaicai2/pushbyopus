"""推送日志模型"""
from datetime import datetime

from sqlmodel import SQLModel, Field


class PushLog(SQLModel, table=True):
    """推送日志"""

    __tablename__ = "push_logs"

    id: int | None = Field(default=None, primary_key=True)
    entry_title: str = Field(description="文章标题")
    entry_url: str = Field(description="文章链接")
    category_name: str = Field(default="", description="分类名称")
    category_id: int | None = Field(default=None, description="分类ID")
    webhook_url: str = Field(description="Discord webhook URL")
    success: bool = Field(description="是否成功")
    error_message: str | None = Field(default=None, description="错误信息")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PushLogRead(SQLModel):
    """推送日志读取模型"""

    id: int
    entry_title: str
    entry_url: str
    category_name: str
    category_id: int | None
    webhook_url: str
    success: bool
    error_message: str | None
    created_at: datetime
