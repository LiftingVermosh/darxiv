from __future__ import annotations


class SubscriptionServiceError(Exception):
    """订阅服务层异常的基类"""


class SubscriptionNotFoundError(SubscriptionServiceError):
    """请求的订阅不存在"""

    def __init__(self, subscription_id: str) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"Subscription '{subscription_id}' not found")


class DuplicateSubscriptionNameError(SubscriptionServiceError):
    """同名订阅已存在"""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"A subscription named '{name}' already exists")


class InvalidSubscriptionRuleError(SubscriptionServiceError):
    """订阅规则不满足业务约束"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# StatusService exceptions
# ---------------------------------------------------------------------------


class StatusServiceError(Exception):
    """状态服务层异常的基类"""


class PaperNotFoundError(StatusServiceError):
    """目标论文在 ``papers`` 表中不存在"""

    def __init__(self, arxiv_id: str) -> None:
        self.arxiv_id = arxiv_id
        super().__init__(f"Paper '{arxiv_id}' not found. Cannot update status.")


class InvalidPaperStatusError(StatusServiceError):
    """状态更新违反业务约束"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)
