"""只读查询仓储：论文与状态的联合查询、SQL 层过滤与分页。

将原本在 Python 侧执行的 N+1 状态查询、关键词/作者/状态/日期过滤
全部下推到 SQLite，通过单次 LEFT JOIN 查询完成。

所有 SQL 构造均使用参数化查询，避免字符串拼接注入风险。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.application.dto.paper_list_filters import PaperListFilters
from app.infrastructure.db.repositories.paper_repository import _format_dt


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 sqlite3.Row 转换为普通 dict，便于服务层消费。"""
    return dict(row)


# ---------------------------------------------------------------------------
# Base query fragments
# ---------------------------------------------------------------------------

_BASE_SELECT = """
    SELECT
        p.arxiv_id,
        p.latest_version,
        p.title,
        p.abstract,
        p.authors_json,
        p.primary_category,
        p.categories_json,
        p.published_at,
        p.updated_at,
        p.pdf_url,
        p.abs_url,
        p.comment,
        p.journal_ref,
        p.doi,
        p.created_at,
        p.synced_at,
        COALESCE(ps.is_starred, 0)  AS is_starred,
        COALESCE(ps.is_read, 0)     AS is_read,
        COALESCE(ps.is_hidden, 0)   AS is_hidden,
        ps.rating,
        ps.note,
        ps.tags_json,
        ps.updated_at               AS status_updated_at
    FROM papers p
    LEFT JOIN paper_statuses ps ON p.arxiv_id = ps.arxiv_id
"""

_BASE_COUNT_SELECT = """
    SELECT COUNT(*)
    FROM papers p
    LEFT JOIN paper_statuses ps ON p.arxiv_id = ps.arxiv_id
"""


class PaperQueryRepository:
    """只读查询仓储。

    负责：
    - 论文与状态的 LEFT JOIN 联合查询
    - 列表筛选 SQL 动态生成（参数化）
    - LIMIT / OFFSET 分页支持
    - 行计数辅助

    不承担任何写操作；所有写操作由
    :class:`~PaperRepository` 与 :class:`~PaperStatusRepository` 负责。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # Public: filtered list query
    # ------------------------------------------------------------------

    def query_papers(
        self,
        filters: PaperListFilters,
        *,
        sort_by: str = "updated_at",
        sort_order: str = "DESC",
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """执行带过滤条件的联合查询，返回 paper + status 合并行。

        所有过滤条件均下推到 SQL WHERE 子句，零 Python 侧二次过滤。

        Args:
            filters: 组合过滤条件对象
            sort_by: 排序字段（仅允许白名单内的列名）
            sort_order: ``ASC`` 或 ``DESC``
            offset: 可选的偏移量（配合 ``filters.limit`` 实现分页）

        Returns:
            匹配行的 dict 列表，按指定排序排列
        """
        sql, params = self._build_query(
            filters,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
        )
        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_paper_with_status(self, arxiv_id: str) -> dict[str, Any] | None:
        """按 ``arxiv_id`` 获取单篇论文与状态的 JOIN 结果。

        Args:
            arxiv_id: 论文 ID

        Returns:
            包含 paper + status 字段的 dict；若论文不存在则返回 ``None``
        """
        sql = _BASE_SELECT + " WHERE p.arxiv_id = ?"
        row = self._conn.execute(sql, (arxiv_id,)).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def count_papers(self, filters: PaperListFilters) -> int:
        """返回匹配 *filters* 的论文总数。

        用于分页计算总页数等场景。

        Args:
            filters: 组合过滤条件对象

        Returns:
            匹配行数
        """
        where_clause, params = self._build_where(filters)
        if filters.subscription_id is not None:
            sql = (
                "SELECT COUNT(*) "
                "FROM subscription_papers sp "
                "JOIN papers p ON sp.arxiv_id = p.arxiv_id "
                "LEFT JOIN paper_statuses ps ON p.arxiv_id = ps.arxiv_id"
            )
        else:
            sql = _BASE_COUNT_SELECT
        if where_clause:
            sql += " WHERE " + where_clause
        row = self._conn.execute(sql, params).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Internal: SQL builder
    # ------------------------------------------------------------------

    # -- 白名单列名，防止 ORDER BY 注入 --
    _ALLOWED_SORT_COLUMNS = frozenset({
        "updated_at",
        "published_at",
        "title",
        "primary_category",
        "arxiv_id",
    })

    def _build_query(
        self,
        filters: PaperListFilters,
        *,
        sort_by: str = "updated_at",
        sort_order: str = "DESC",
        offset: int | None = None,
    ) -> tuple[str, list[Any]]:
        """构建完整的 SELECT + WHERE + ORDER BY + LIMIT/OFFSET 语句与参数列表。"""
        where_clause, params = self._build_where(filters)

        # 当按 subscription_id 过滤时，需要 JOIN subscription_papers
        if filters.subscription_id is not None:
            sql = (
                "SELECT "
                "    p.arxiv_id,"
                "    p.latest_version,"
                "    p.title,"
                "    p.abstract,"
                "    p.authors_json,"
                "    p.primary_category,"
                "    p.categories_json,"
                "    p.published_at,"
                "    p.updated_at,"
                "    p.pdf_url,"
                "    p.abs_url,"
                "    p.comment,"
                "    p.journal_ref,"
                "    p.doi,"
                "    p.created_at,"
                "    p.synced_at,"
                "    COALESCE(ps.is_starred, 0)  AS is_starred,"
                "    COALESCE(ps.is_read, 0)     AS is_read,"
                "    COALESCE(ps.is_hidden, 0)   AS is_hidden,"
                "    ps.rating,"
                "    ps.note,"
                "    ps.tags_json,"
                "    ps.updated_at               AS status_updated_at"
                " FROM subscription_papers sp"
                " JOIN papers p ON sp.arxiv_id = p.arxiv_id"
                " LEFT JOIN paper_statuses ps ON p.arxiv_id = ps.arxiv_id"
            )
        else:
            sql = _BASE_SELECT

        if where_clause:
            sql += " WHERE " + where_clause

        # 排序（白名单校验）
        col = sort_by if sort_by in self._ALLOWED_SORT_COLUMNS else "updated_at"
        order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        sql += f" ORDER BY p.{col} {order}"

        # LIMIT（SQLite 要求 LIMIT 在 OFFSET 之前；offset 存在但 limit 未设置时
        # 用 -1 表示"无上限"，避免生成裸 OFFSET 导致语法错误）
        limit = filters.limit
        if limit is None and offset is not None:
            limit = -1

        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        # OFFSET
        if offset is not None:
            sql += " OFFSET ?"
            params.append(offset)

        return sql, params

    def _build_where(
        self, filters: PaperListFilters
    ) -> tuple[str, list[Any]]:
        """根据 *filters* 构造 WHERE 子句与对应的参数列表。

        返回 ``(where_clause_without_WHERE_keyword, params)``。
        若无需过滤则返回 ``("", [])``。
        """
        clauses: list[str] = []
        params: list[Any] = []

        # -- 订阅过滤（精确匹配 subscription_papers.subscription_id） --
        if filters.subscription_id is not None:
            clauses.append("sp.subscription_id = ?")
            params.append(filters.subscription_id)

        # -- 分类（精确匹配） --
        if filters.category is not None:
            clauses.append("p.primary_category = ?")
            params.append(filters.category)

        # -- 关键词（标题 + 摘要 LIKE 搜索，SQLite LIKE 默认 ASCII 大小写不敏感） --
        if filters.keyword is not None:
            pattern = f"%{filters.keyword}%"
            clauses.append("(p.title LIKE ? OR p.abstract LIKE ?)")
            params.extend([pattern, pattern])

        # -- 作者（JSON 文本 LIKE 搜索） --
        if filters.author is not None:
            pattern = f"%{filters.author}%"
            clauses.append("p.authors_json LIKE ?")
            params.append(pattern)

        # -- 状态布尔过滤（COALESCE 处理无状态记录 → 默认 False） --
        if filters.is_starred is not None:
            clauses.append("COALESCE(ps.is_starred, 0) = ?")
            params.append(int(filters.is_starred))

        if filters.is_read is not None:
            clauses.append("COALESCE(ps.is_read, 0) = ?")
            params.append(int(filters.is_read))

        if filters.is_hidden is not None:
            clauses.append("COALESCE(ps.is_hidden, 0) = ?")
            params.append(int(filters.is_hidden))

        # -- 日期范围过滤 --
        if filters.published_from is not None:
            clauses.append("p.published_at >= ?")
            params.append(_format_dt(filters.published_from))

        if filters.published_to is not None:
            clauses.append("p.published_at <= ?")
            params.append(_format_dt(filters.published_to))

        if filters.updated_from is not None:
            clauses.append("p.updated_at >= ?")
            params.append(_format_dt(filters.updated_from))

        if filters.updated_to is not None:
            clauses.append("p.updated_at <= ?")
            params.append(_format_dt(filters.updated_to))

        if not clauses:
            return "", []

        return " AND ".join(clauses), params
