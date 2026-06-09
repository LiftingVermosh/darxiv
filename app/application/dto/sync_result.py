from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums.sync_status import SyncRunStatus
from app.domain.models._normalization import (
    _normalize_datetime,
    _normalize_optional_text, 
    _normalize_required_text
)

class SyncResultDTO(BaseModel):
    """
    同步操作完成后返回给 UI 的结果总结

    Args:
        subscription_id (str): 订阅 ID
        subscription_name (str): 订阅名称
        status (SyncRunStatus): 同步最终状态
        fetched_count (int): 抓取总数
        inserted_count (int): 新增总数
        updated_count (int): 更新总数
        started_at (datetime): 开始时间
        finished_at (datetime): 结束时间
        error_message (str | None): 错误堆栈概览
    """
    model_config = ConfigDict(extra="forbid")
    subscription_id: str
    subscription_name: str
    status: SyncRunStatus
    fetched_count: int = Field(ge=0)
    inserted_count: int = Field(ge=0)
    updated_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    error_message: str | None = None

    @field_validator("subscription_id", "subscription_name", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="value")

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def normalize_dates(cls, value: datetime) -> datetime:
        return _normalize_datetime(value)

    @field_validator("error_message", mode="before")
    @classmethod
    def normalize_error_message(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value, field_name="error_message")

    @model_validator(mode="after")
    def validate_timestamps(self) -> "SyncResultDTO":
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        if self.status == SyncRunStatus.FAILED and not self.error_message:
            raise ValueError("failed sync result must include error_message")
        return self
