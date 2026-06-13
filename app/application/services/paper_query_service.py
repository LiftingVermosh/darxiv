from __future__ import annotations

import sqlite3

from app.application.dto.paper_detail import PaperDetailDTO
from app.application.dto.paper_list_filters import PaperListFilters
from app.application.dto.paper_list_item import PaperListItemDTO
from app.domain.models.paper import Paper
from app.domain.models.paper_status import PaperStatus
from app.infrastructure.db.repositories.paper_repository import PaperRepository
from app.infrastructure.db.repositories.paper_status_repository import (
    PaperStatusRepository,
)


class PaperQueryService:
    """面向 UI 的论文查询聚合服务。

    负责将 ``papers`` 与 ``paper_statuses`` 表的数据聚合为稳定的 DTO，
    避免 UI 层直接拼装 repository 结果。

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

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def list_papers(
        self, filters: PaperListFilters | None = None
    ) -> list[PaperListItemDTO]:
        """论文列表主页入口，支持按条件组合过滤。

        默认按 ``updated_at DESC`` 排序；空库时返回空列表。

        Args:
            filters: 可选的组合过滤条件；``None`` 时返回全部论文

        Returns:
            匹配过滤条件的 :class:`~PaperListItemDTO` 列表
        """
        if filters is None:
            filters = PaperListFilters()

        # 从仓储层获取基础数据集
        if filters.category is not None:
            papers = self._paper_repo.list_by_category(filters.category)
        else:
            papers = self._paper_repo.list_all()

        # 逐条聚合状态并应用 Python 侧过滤
        results: list[PaperListItemDTO] = []
        for paper in papers:
            list_item = self._merge_to_list_item(paper)
            if self._matches_filters(list_item, paper, filters):
                results.append(list_item)

        # 应用 limit（结果已按 updated_at DESC 排序，来自 SQL）
        if filters.limit is not None and len(results) > filters.limit:
            results = results[: filters.limit]

        return results

    def get_paper_detail(self, arxiv_id: str) -> PaperDetailDTO | None:
        """论文详情页主入口，返回论文完整元数据与用户状态聚合视图。

        Args:
            arxiv_id: 论文 ID

        Returns:
            聚合后的 :class:`~PaperDetailDTO`；若论文不存在则返回 ``None``
        """
        paper = self._paper_repo.get(arxiv_id)
        if paper is None:
            return None

        status = self._status_repo.get(arxiv_id)
        return self._merge_to_detail(paper, status)

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

    # ------------------------------------------------------------------
    # Internal helpers: merge paper + status → DTO
    # ------------------------------------------------------------------

    def _merge_to_list_item(self, paper: Paper) -> PaperListItemDTO:
        """将 :class:`Paper` 与对应的用户状态合并为列表 DTO。

        若状态记录缺失，自动补全默认状态视图。
        """
        status = self._status_repo.get(paper.arxiv_id)
        if status is None:
            status = self._make_default_status(paper.arxiv_id)

        return PaperListItemDTO(
            arxiv_id=paper.arxiv_id,
            title=paper.title,
            authors_preview=self._build_authors_preview(paper.authors),
            primary_category=paper.primary_category,
            categories=paper.categories,
            published_at=paper.published_at,
            updated_at=paper.updated_at,
            is_starred=status.is_starred,
            is_read=status.is_read,
            is_hidden=status.is_hidden,
        )

    def _merge_to_detail(
        self, paper: Paper, status: PaperStatus | None
    ) -> PaperDetailDTO:
        """将 :class:`Paper` 与可选的 :class:`PaperStatus` 合并为详情 DTO。

        若状态记录缺失，自动补全默认状态视图。
        """
        if status is None:
            status = self._make_default_status(paper.arxiv_id)

        return PaperDetailDTO(
            arxiv_id=paper.arxiv_id,
            latest_version=paper.version,
            title=paper.title,
            abstract=paper.abstract,
            authors=paper.authors,
            primary_category=paper.primary_category,
            categories=paper.categories,
            published_at=paper.published_at,
            updated_at=paper.updated_at,
            pdf_url=paper.pdf_url,
            abs_url=paper.abs_url,
            comment=paper.comment,
            journal_ref=paper.journal_ref,
            doi=paper.doi,
            is_starred=status.is_starred,
            is_read=status.is_read,
            is_hidden=status.is_hidden,
            rating=status.rating,
            note=status.note,
            tags=status.tags,
        )

    # ------------------------------------------------------------------
    # Internal helpers: filter predicates
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_filters(
        list_item: PaperListItemDTO,
        paper: Paper,
        filters: PaperListFilters,
    ) -> bool:
        """检查由 *paper* 与 *list_item* 构成的聚合行是否通过所有筛选条件。

        返回 ``False`` 表示该行不满足过滤条件，应从结果中排除。
        """
        # 关键词匹配：标题或摘要大小写不敏感包含
        if filters.keyword is not None:
            kw = filters.keyword.lower()
            if (
                kw not in paper.title.lower()
                and kw not in paper.abstract.lower()
            ):
                return False

        # 作者匹配：大小写不敏感子串
        if filters.author is not None:
            author_lower = filters.author.lower()
            if not any(author_lower in a.lower() for a in paper.authors):
                return False

        # 状态过滤
        if filters.is_starred is not None and list_item.is_starred != filters.is_starred:
            return False

        if filters.is_read is not None and list_item.is_read != filters.is_read:
            return False

        if filters.is_hidden is not None and list_item.is_hidden != filters.is_hidden:
            return False

        # 日期范围过滤
        if filters.published_from is not None and paper.published_at < filters.published_from:
            return False

        if filters.published_to is not None and paper.published_at > filters.published_to:
            return False

        if filters.updated_from is not None and paper.updated_at < filters.updated_from:
            return False

        if filters.updated_to is not None and paper.updated_at > filters.updated_to:
            return False

        return True

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
