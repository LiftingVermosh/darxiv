"""论文库管理服务。

提供论文物理删除与订阅解绑能力，
是面向 UI 的论文删除统一入口。
"""

from __future__ import annotations

import sqlite3

from app.application.services.exceptions import (
    PaperNotFoundError,
    SubscriptionNotFoundError,
)
from app.infrastructure.db.repositories.paper_repository import PaperRepository
from app.infrastructure.db.repositories.paper_status_repository import (
    PaperStatusRepository,
)
from app.infrastructure.db.repositories.subscription_paper_repository import (
    SubscriptionPaperRepository,
)
from app.infrastructure.db.repositories.subscription_repository import (
    SubscriptionRepository,
)


class PaperLibraryService:
    """论文库管理服务。

    负责论文的删除与订阅解绑操作。
    所有删除语义区分"全局物理删除"和"仅从当前订阅解绑"。

    Args:
        connection: 已打开的 ``sqlite3.Connection``，调用方负责连接生命周期
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._paper_repo = PaperRepository(connection)
        self._status_repo = PaperStatusRepository(connection)
        self._sub_paper_repo = SubscriptionPaperRepository(connection)
        self._sub_repo = SubscriptionRepository(connection)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def delete_paper(
        self,
        arxiv_id: str,
        *,
        subscription_id: str | None = None,
    ) -> str:
        """删除一篇论文。

        语义：
        - **subscription_id is None**（全局删除）：
          从本地库物理删除论文。所有关联的 ``paper_versions``、
          ``paper_statuses`` 和 ``subscription_papers`` 通过 ON DELETE
          CASCADE 自动清理。
        - **subscription_id is not None**（订阅内删除）：
          仅解除论文与指定订阅的关联；若解绑后论文不再属于任何订阅，
          则物理删除。

        Args:
            arxiv_id: 论文 arXiv ID
            subscription_id: 可选的订阅 ID；提供时仅从该订阅解绑

        Returns:
            删除结果描述字符串（用于 UI 通知）

        Raises:
            PaperNotFoundError: 目标论文不存在
            SubscriptionNotFoundError: 提供的 ``subscription_id`` 不存在
        """
        # -- 论文存在性校验 --
        paper = self._paper_repo.get(arxiv_id)
        if paper is None:
            raise PaperNotFoundError(arxiv_id)

        if subscription_id is not None:
            # -- 订阅存在性校验 --
            sub = self._sub_repo.get(subscription_id)
            if sub is None:
                raise SubscriptionNotFoundError(subscription_id)

            # -- 归属关系校验：论文必须确实属于该订阅 --
            sub_ids = self._sub_paper_repo.get_subscription_ids_for_arxiv(
                arxiv_id
            )
            if subscription_id not in sub_ids:
                raise ValueError(
                    f"Paper '{arxiv_id}' does not belong to "
                    f"subscription '{subscription_id}'."
                )

            # 解除归属
            self._sub_paper_repo.delete_pair(subscription_id, arxiv_id)

            # 检查是否仍被其他订阅引用
            remaining = (
                self._sub_paper_repo.count_subscriptions_for_arxiv(
                    arxiv_id
                )
            )
            if remaining > 0:
                self._conn.commit()
                return (
                    f"Paper '{paper.title[:40]}...' "
                    f"unlinked from subscription '{sub.name}'."
                )

            # 无其他归属 → 检查是否应保护
            row = self._conn.execute(
                "SELECT provenance_state FROM papers WHERE arxiv_id = ?",
                (arxiv_id,),
            ).fetchone()
            if row and row["provenance_state"] == "legacy_unattributed":
                # 升级前的历史论文，真实归属已不可考；
                # 即使当前无 subscription_papers 链接也保留
                self._conn.commit()
                return (
                    f"Paper '{paper.title[:40]}...' "
                    f"unlinked from subscription '{sub.name}' "
                    f"(kept — predates provenance tracking)."
                )

            # 确认为迁移后论文且已成孤儿，物理删除
            self._conn.execute(
                "DELETE FROM papers WHERE arxiv_id = ?",
                (arxiv_id,),
            )
            self._conn.commit()
            return (
                f"Paper '{paper.title[:40]}...' "
                f"removed (no longer belongs to any subscription)."
            )

        # -- 全局删除 --
        # paper_versions / paper_statuses / subscription_papers 通过 CASCADE 自动清理
        self._conn.execute(
            "DELETE FROM papers WHERE arxiv_id = ?",
            (arxiv_id,),
        )
        self._conn.commit()
        return f"Paper '{paper.title[:40]}...' permanently deleted."

    def delete_papers_batch(
        self,
        arxiv_ids: list[str],
        *,
        subscription_id: str | None = None,
    ) -> int:
        """批量删除论文。

        委托给 :meth:`delete_paper`，对每个 ``arxiv_id`` 逐一执行。
        不存在的论文被静默跳过；仅解绑或保留的结果不计入删除数。

        Args:
            arxiv_ids: 论文 arXiv ID 列表
            subscription_id: 可选的订阅 ID

        Returns:
            实际物理删除的论文数量
        """
        deleted = 0
        for aid in arxiv_ids:
            try:
                result = self.delete_paper(
                    aid, subscription_id=subscription_id
                )
                if "deleted" in result or "removed" in result:
                    deleted += 1
            except PaperNotFoundError:
                continue
        return deleted
