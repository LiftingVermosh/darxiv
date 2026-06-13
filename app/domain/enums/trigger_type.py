from enum import Enum


class SyncTriggerType(str, Enum):
    """
    同步任务触发类型枚举

    Attributes:
        MANUAL: 手动触发
        SCHEDULED: 定时任务触发
    """
    MANUAL = "manual"
    SCHEDULED = "scheduled"
