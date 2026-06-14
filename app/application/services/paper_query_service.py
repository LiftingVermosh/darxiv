from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.application.dto.paper_detail import PaperDetailDTO
from app.application.dto.paper_list_filters import PaperListFilters
from app.application.dto.paper_list_item import PaperListItemDTO
from app.application.dto.query_debug_info import QueryDebugInfo
from app.domain.models.paper import Paper
from app.domain.models.paper_status import PaperStatus
from app.infrastructure.db.repositories.paper_query_repository import (
    PaperQueryRepository,
)
from app.infrastructure.db.repositories.paper_repository import (
    PaperRepository,
    _parse_dt,
)
from app.infrastructure.db.repositories.paper_status_repository import (
    PaperStatusRepository,
)


class PaperQueryService:
    """面向 UI 的论文查询聚合服务。

    负责将 ``papers`` 与 ``paper_statuses`` 表的数据聚合为稳定的 DTO，
    避免 UI 层直接拼装 repository 结果。

    列表查询通过 :class:`PaperQueryRepository` 以单次 LEFT JOIN 完成，
    所有过滤条件下推到 SQL 层，消除 N+1 状态查询。

    仅执行查询与聚合，不承担写操作；所有写操作由
    :class:`~app.application.services.sync_service.SyncService` 和
    :class:`~app.application.services.subscription_service.SubscriptionService` 负责。

    Args:
        connection: 已打开的 ``sqlite3.Connection``，调用方负责连接生命周期
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._paper_repo = PaperRepository(connection)
        self._status_repo = PaperStatusRepository(connection)
        self._query_repo = PaperQueryRepository(connection)
        self._last_debug_info: QueryDebugInfo | None = None

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def list_papers(
        self,
        filters: PaperListFilters | None = None,
        *,
        sort_by: str = "updated_at",
        sort_order: str = "DESC",
        offset: int | None = None,
    ) -> list[PaperListItemDTO]:
        """论文列表主页入口，支持按条件组合过滤。

        默认按 ``updated_at DESC`` 排序；空库时返回空列表。

        所有过滤条件均下推到 SQL（零 Python 侧二次过滤），
        状态通过 LEFT JOIN 批量预取，消除 N+1 查询。

        Args:
            filters: 可选的组合过滤条件；``None`` 时返回全部论文
            sort_by: 排序字段（``updated_at`` / ``published_at`` / ``title``）
            sort_order: ``ASC`` 或 ``DESC``
            offset: 可选的偏移量，配合 ``filters.limit`` 实现分页

        Returns:
            匹配过滤条件的 :class:`~PaperListItemDTO` 列表
        """
        if filters is None:
            filters = PaperListFilters()

        # 通过只读仓储执行单次 JOIN 查询
        rows = self._query_repo.query_papers(
            filters,
            sort_by=sort_by,
            sort_order=sort_order,
            offset=offset,
        )

        # 组装 DTO 列表
        results = [self._row_to_list_item(r) for r in rows]

        # 记录诊断信息（所有过滤均已下推 SQL，Python 侧无二次过滤）
        self._last_debug_info = QueryDebugInfo(
            sql_row_count=len(rows),
            filter_applied_in_sql=self._collect_applied_filters(filters),
            filter_applied_in_python=[],
            total_matches=self._query_repo.count_papers(filters),
        )

        return results

    def get_paper_detail(self, arxiv_id: str) -> PaperDetailDTO | None:
        """论文详情页主入口，返回论文完整元数据与用户状态聚合视图。

        通过 LEFT JOIN 单次查询获取论文与状态，消除逐条状态查询。

        Args:
            arxiv_id: 论文 ID

        Returns:
            聚合后的 :class:`~PaperDetailDTO`；若论文不存在则返回 ``None``
        """
        row = self._query_repo.get_paper_with_status(arxiv_id)
        if row is None:
            return None
        return self._row_to_detail(row)

    def list_starred_papers(
        self, limit: int | None = None
    ) -> list[PaperListItemDTO]:
        """便利方法：获取已收藏论文列表。

        内部委托给 :meth:`list_papers`，等价于
        ``list_papers(PaperListFilters(is_starred=True, limit=limit))``。

        Args:
            limit: 可选的返回记录上限
        """
        return self.list_papers(
            PaperListFilters(is_starred=True, limit=limit)
        )

    @property
    def last_query_debug_info(self) -> QueryDebugInfo | None:
        """最近一次 :meth:`list_papers` 调用的诊断信息。

        可用于测试断言与性能调优；若尚未执行过查询则返回 ``None``。
        """
        return self._last_debug_info

    # ------------------------------------------------------------------
    # Internal helpers: row → DTO
    # ------------------------------------------------------------------

    def _row_to_list_item(self, row: dict[str, Any]) -> PaperListItemDTO:
        """将 JOIN 查询行（dict）转换为列表 DTO。

        状态字段已通过 COALESCE 在 SQL 层处理默认值，
        无需额外补全。
        """
        authors = json.loads(row["authors_json"])

        return PaperListItemDTO(
            arxiv_id=row["arxiv_id"],
            title=row["title"],
            authors_preview=self._build_authors_preview(authors),
            primary_category=row["primary_category"],
            categories=json.loads(row["categories_json"]),
            published_at=_parse_dt(row["published_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            is_starred=bool(row["is_starred"]),
            is_read=bool(row["is_read"]),
            is_hidden=bool(row["is_hidden"]),
        )

    def _row_to_detail(self, row: dict[str, Any]) -> PaperDetailDTO:
        """将 JOIN 查询行（dict）转换为详情 DTO。

        可空字段（rating / note / tags_json / status_updated_at）在无
        paper_statuses 记录时为 NULL，需按默认值处理。
        """
        tags: list[str] = []
        if row.get("tags_json"):
            try:
                tags = json.loads(row["tags_json"])
            except (json.JSONDecodeError, TypeError):
                tags = []

        return PaperDetailDTO(
            arxiv_id=row["arxiv_id"],
            latest_version=row["latest_version"],
            title=row["title"],
            abstract=row["abstract"],
            authors=json.loads(row["authors_json"]),
            primary_category=row["primary_category"],
            categories=json.loads(row["categories_json"]),
            published_at=_parse_dt(row["published_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            pdf_url=row.get("pdf_url"),
            abs_url=row["abs_url"],
            comment=row.get("comment"),
            journal_ref=row.get("journal_ref"),
            doi=row.get("doi"),
            is_starred=bool(row["is_starred"]),
            is_read=bool(row["is_read"]),
            is_hidden=bool(row["is_hidden"]),
            rating=row.get("rating"),
            note=row.get("note"),
            tags=tags,
        )

    # ------------------------------------------------------------------
    # Internal helpers: diagnostics
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_applied_filters(filters: PaperListFilters) -> list[str]:
        """收集所有在 SQL 层生效的过滤字段名。"""
        applied: list[str] = []
        if filters.category is not None:
            applied.append("category")
        if filters.keyword is not None:
            applied.append("keyword")
        if filters.author is not None:
            applied.append("author")
        if filters.is_starred is not None:
            applied.append("is_starred")
        if filters.is_read is not None:
            applied.append("is_read")
        if filters.is_hidden is not None:
            applied.append("is_hidden")
        if filters.published_from is not None:
            applied.append("published_from")
        if filters.published_to is not None:
            applied.append("published_to")
        if filters.updated_from is not None:
            applied.append("updated_from")
        if filters.updated_to is not None:
            applied.append("updated_to")
        if filters.limit is not None:
            applied.append("limit")
        return applied

    # ------------------------------------------------------------------
    # Internal helpers: presentation
    # ------------------------------------------------------------------

    @staticmethod
    def _build_authors_preview(authors: list[str]) -> str:
        """根据作者数量生成标准化的预览字符串。

        - 1 位作者：``Alice``
        - 2 位作者：``Alice, Bob``
        - 3 位及以上：``Alice, Bob et al.``
        """
        if not authors:
            return ""
        if len(authors) == 1:
            return authors[0]
        if len(authors) == 2:
            return f"{authors[0]}, {authors[1]}"
        return f"{authors[0]}, {authors[1]} et al."

    @staticmethod
    def _make_default_status(arxiv_id: str) -> PaperStatus:
        """为尚无 ``paper_statuses`` 记录的论文生成默认状态视图。

        默认值约定：
        - ``is_starred = False``
        - ``is_read = False``
        - ``is_hidden = False``
        - ``rating = None``
        - ``note = None``
        - ``tags = []``
        """
        return PaperStatus(arxiv_id=arxiv_id)
