from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums.sync_status import SyncRunStatus
from app.domain.enums.trigger_type import SyncTriggerType
from app.domain.models._normalization import (
    _normalize_optional_text, 
    _normalize_required_text,
    _normalize_datetime
)


class SyncRun(BaseModel):
    """
    数据同步任务的执行审计记录，记录每一次爬取与入库的结果

    Args:
        id (str): 同步记录的唯一标识符
        subscription_id (str): 所属订阅规则的 ID
        started_at (datetime): 同步开始时间
        finished_at (datetime | None): 同步结束时间
        status (SyncRunStatus): 任务状态（运行中、成功、失败）
        trigger_type (SyncTriggerType): 触发类型（手动触发或定时任务触发）
        fetched_count (int): 从 API 获取到的论文总数
        inserted_count (int): 新增入库的论文数量
        updated_count (int): 检测到版本更新并更新了信息的论文数量
        error_message (str | None): 任务失败时的错误堆栈或异常描述
    """
    model_config = ConfigDict(extra="forbid")
    id: str
    subscription_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: SyncRunStatus = SyncRunStatus.RUNNING
    trigger_type: SyncTriggerType
    fetched_count: int = Field(default=0, ge=0)
    inserted_count: int = Field(default=0, ge=0)
    updated_count: int = Field(default=0, ge=0)
    error_message: str | None = None

    @field_validator("id", "subscription_id", mode="before")
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
    def validate_status_fields(self) -> "SyncRun":
        if self.status == SyncRunStatus.FAILED and not self.error_message:
            raise ValueError("failed sync run must include error_message")
        if self.status != SyncRunStatus.RUNNING and self.finished_at is None:
            raise ValueError("completed sync run must include finished_at")
        if self.status == SyncRunStatus.RUNNING and self.finished_at is not None:
            raise ValueError("running sync run must not include finished_at")
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        return self
