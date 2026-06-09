from __future__ import annotations

import re
from typing import Any
from datetime import datetime, timezone


ARXIV_ID_PATTERN = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?$",
    re.IGNORECASE,
)


def _normalize_whitespace(value: str) -> str:
    """ 规范化字符串中的空白字符 """
    return re.sub(r"\s+", " ", value).strip()


def _normalize_required_text(value: str, *, field_name: str) -> str:
    """ 规范化指定文本，如果文本为空则抛出异常 """
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = _normalize_whitespace(value)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    """ 规范化可选文本，如果文本为空则返回 None """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    normalized = _normalize_whitespace(value)
    return normalized or None


def _normalize_arxiv_id(value: str) -> str:
    """ 规范化 arxiv ID """
    if not isinstance(value, str):
        raise ValueError("arxiv_id must be a string")

    match = ARXIV_ID_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("Invalid arxiv_id format")
    return match.group("base")


def _normalize_string_list(
    value: str | list[str] | None,
    *,
    field_name: str,
    lowercase: bool = False,
    require_non_empty: bool = False,
) -> list[str]:
    """ 规范化字符串列表 """
    if value is None:
        items: list[str] = []
    elif isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError(f"{field_name} must be a string or a list of strings")

    normalized: list[str] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} items must be strings")
        cleaned = _normalize_whitespace(item)
        
        if not cleaned:
            continue
        candidate = cleaned.lower() if lowercase else cleaned

        # Check if already seen
        if candidate in seen:
            continue
        
        seen.add(candidate)
        normalized.append(candidate)

    if require_non_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty")

    return normalized

def _normalize_datetime(v: Any) -> datetime:
    """ 规范化日期时间 """
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))

    if not isinstance(v, datetime):
        return v    # 错误回传交给 pydantic 处理

    if v.tzinfo is None:
        # 强制将 naive datetime 视为 UTC
        return v.replace(tzinfo=timezone.utc)
    # 强制转换 aware datetime 到 UTC
    return v.astimezone(timezone.utc)