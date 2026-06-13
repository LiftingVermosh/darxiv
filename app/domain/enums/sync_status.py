from enum import Enum


class SyncRunStatus(str, Enum):
    """
    同步任务运行状态枚举

    Attributes:
        RUNNING: 运行中
        SUCCESS: 成功
        FAILED: 失败
    """
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
