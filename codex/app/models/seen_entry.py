"""去重记录模型"""
from datetime import datetime

from sqlmodel import SQLModel, Field


class SeenEntry(SQLModel, table=True):
    """已见条目（去重用）"""

    __tablename__ = "seen_entries"

    entry_url: str = Field(primary_key=True)
    seen_at: datetime = Field(default_factory=datetime.utcnow)
