from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict

from app.domain.models._normalization import (
    _normalize_arxiv_id,
    _normalize_datetime,
    _normalize_optional_text,
    _normalize_string_list,
)


class PaperStatus(BaseModel):
    """
    用户对特定论文的个性化状态、交互记录与评价

    Args:
        arxiv_id (str): 对应论文的唯一标识符
        is_starred (bool): 是否已收藏
        is_read (bool): 是否已阅读
        is_hidden (bool): 是否已忽略/隐藏（不再出现在推荐列表中）
        rating (int | None): 用户评分（1-5星）
        note (str | None): 用户个人笔记/备注
        tags (list[str]): 用户为论文打上的自定义标签
        updated_at (datetime): 状态最后一次更新的时间
    """
    model_config = ConfigDict(extra="forbid")
    arxiv_id: str
    is_starred: bool = False
    is_read: bool = False
    is_hidden: bool = False
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None
    tags: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def normalize_arxiv_id(cls, value: str) -> str:
        return _normalize_arxiv_id(value)

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="note")

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: str | list[str] | None) -> list[str]:
        return _normalize_string_list(value, field_name="tags")

    @model_validator(mode="after")
    def validate_status_consistency(self) -> "PaperStatus":
        if self.is_hidden and self.is_starred:
            raise ValueError("hidden paper cannot be starred")
        return self
