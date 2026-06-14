"""调度器 Tick 事件对象。

定义 SyncScheduler 与 UI 层之间的稳定契约：
每次调度循环完成后，调度器产出本事件供 UI 消费。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.application.dto.sync_result import SyncResultDTO


class SchedulerTickEvent(BaseModel):
    """调度器单次同步循环的结果事件。

    Attributes:
        started_at: 循环开始时间
        finished_at: 循环结束时间（进行中时为 ``None``）
        status: 状态标签 —— ``"idle"`` | ``"running"`` | ``"completed"`` | ``"failed"``
        results: 本次循环中每个订阅的同步结果列表
        error_message: 循环级错误信息（单个订阅失败不影响该字段为空）
    """

    model_config = ConfigDict(extra="forbid")

    started_at: datetime
    finished_at: datetime | None = None
    status: str = "idle"
    results: list[SyncResultDTO] = Field(default_factory=list)
    error_message: str | None = None
