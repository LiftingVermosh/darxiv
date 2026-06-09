from enum import Enum


class SyncTriggerType(str, Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
