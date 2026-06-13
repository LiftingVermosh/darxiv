from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models._normalization import (
    _normalize_optional_text,
    _normalize_string_list,
)


class PaperStatusPatchInput(BaseModel):
    """论文用户状态的批量写入 DTO。

    所有字段均可选；``None`` 表示"不修改该字段"。
    若需要清空评分或备注，应显式传入对应字段（见 :meth:`StatusService.update_rating`
    和 :meth:`StatusService.update_note`）。

    Args:
        is_starred: 是否收藏（``None`` = 不修改）
        is_read: 是否已读（``None`` = 不修改）
        is_hidden: 是否忽略/隐藏（``None`` = 不修改）
        rating: 用户评分 1-5（``None`` = 不修改）
        note: 用户备注（``None`` = 不修改）
        tags: 用户标签集合（``None`` = 不修改）
    """

    model_config = ConfigDict(extra="forbid")

    is_starred: bool | None = None
    is_read: bool | None = None
    is_hidden: bool | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    note: str | None = None
    tags: list[str] | None = None

    # -- text normalization -------------------------------------------------

    @field_validator("note", mode="before")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="note")

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value: str | list[str] | None) -> list[str] | None:
        """去重、去空白；``None`` 直接透传（表示不修改）"""
        if value is None:
            return None
        return _normalize_string_list(value, field_name="tags")

    # -- cross-field validation ---------------------------------------------

    @model_validator(mode="after")
    def validate_hidden_starred_conflict(self) -> "PaperStatusPatchInput":
        """is_hidden=True 与 is_starred=True 冲突"""
        if self.is_hidden is True and self.is_starred is True:
            raise ValueError("hidden paper cannot be starred")
        return self
