"""查询诊断信息 DTO。

提供内部诊断结构，用于验证 SQL 下推效果与性能调优。
MVP 阶段不直接暴露给 UI，但对测试和性能回归验证很有价值。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class QueryDebugInfo(BaseModel):
    """单次 ``list_papers`` 查询的诊断摘要。

    Args:
        sql_row_count: SQL 查询返回的行数（LIMIT 截断前）
        filter_applied_in_sql: 已下推到 SQL WHERE 的过滤字段名列表
        filter_applied_in_python: 仍在 Python 侧执行的过滤字段名列表
        total_matches: 无 LIMIT 时的总匹配行数（通过 COUNT 查询获取）
    """

    model_config = ConfigDict(extra="forbid")

    sql_row_count: int
    filter_applied_in_sql: list[str]
    filter_applied_in_python: list[str]
    total_matches: int | None = None
