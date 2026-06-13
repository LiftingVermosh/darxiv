from __future__ import annotations


class ArxivError(Exception):
    """arXiv 集成层所有异常的基类"""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        base = self.message
        if self.cause is not None:
            base = f"{base} (caused by: {self.cause!r})"
        return base


class ArxivRequestError(ArxivError):
    """网络请求、超时或非 200 响应时抛出"""


class ArxivParseError(ArxivError):
    """Atom XML 结构异常或 feedparser 解析失败时抛出"""


class ArxivDataContractError(ArxivError):
    """条目字段校验失败、无法构造 Paper 模型时抛出"""
