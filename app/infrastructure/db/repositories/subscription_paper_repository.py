"""``subscription_papers`` 表的仓储层。

管理订阅与论文之间的多对多归属关系，
支持关联创建、查询与清理操作。
"""

from __future__ import annotations

import sqlite3

from app.infrastructure.db.repositories.paper_repository import _utcnow


_UPSERT_SQL = """
    INSERT INTO subscription_papers (
        subscription_id, arxiv_id, first_seen_at, last_seen_at, last_sync_run_id
    ) VALUES (
        :subscription_id, :arxiv_id, :first_seen_at, :last_seen_at, :last_sync_run_id
    )
    ON CONFLICT(subscription_id, arxiv_id) DO UPDATE SET
        last_seen_at = :last_seen_at,
        last_sync_run_id = COALESCE(:last_sync_run_id, subscription_papers.last_sync_run_id)
"""

_GET_ARXIV_IDS_SQL = """
    SELECT arxiv_id FROM subscription_papers
    WHERE subscription_id = ?
    ORDER BY last_seen_at DESC
"""

_GET_SUBSCRIPTION_IDS_SQL = """
    SELECT subscription_id FROM subscription_papers
    WHERE arxiv_id = ?
"""

_COUNT_SUBSCRIPTIONS_SQL = """
    SELECT COUNT(*) FROM subscription_papers WHERE arxiv_id = ?
"""

_DELETE_FOR_SUB_SQL = """
    DELETE FROM subscription_papers WHERE subscription_id = ?
"""

_DELETE_PAIR_SQL = """
    DELETE FROM subscription_papers
    WHERE subscription_id = ? AND arxiv_id = ?
"""

_FIND_ORPHANS_SQL = """
    SELECT sp.arxiv_id
    FROM (
        SELECT DISTINCT arxiv_id FROM subscription_papers
        WHERE arxiv_id IN ({})
    ) AS candidate
    LEFT JOIN subscription_papers sp2 ON candidate.arxiv_id = sp2.arxiv_id
    GROUP BY candidate.arxiv_id
    HAVING COUNT(sp2.subscription_id) = 0
"""


class SubscriptionPaperRepository:
    """``subscription_papers`` 表的仓储层。

    负责订阅-论文归属记录的持久化与查询。
    不承担业务逻辑；所有语义由调用方（service 层）决定。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        subscription_id: str,
        arxiv_id: str,
        *,
        first_seen_at: str | None = None,
        last_sync_run_id: str | None = None,
    ) -> None:
        """插入或更新一条订阅-论文归属记录。

        如果记录已存在，仅更新 ``last_seen_at`` 和可选的
        ``last_sync_run_id``；``first_seen_at`` 保持不变。

        Args:
            subscription_id: 订阅 ID
            arxiv_id: 论文 arXiv ID
            first_seen_at: 首次发现时间（仅新记录时写入；已存在记录则忽略）
            last_sync_run_id: 最近一次同步运行的 ID（可选）
        """
        now = _utcnow()
        params = {
            "subscription_id": subscription_id,
            "arxiv_id": arxiv_id,
            "first_seen_at": first_seen_at if first_seen_at is not None else now,
            "last_seen_at": now,
            "last_sync_run_id": last_sync_run_id,
        }
        self._conn.execute(_UPSERT_SQL, params)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_arxiv_ids_for_subscription(
        self, subscription_id: str
    ) -> list[str]:
        """返回指定订阅关联的所有论文 arXiv ID 列表。

        按 ``last_seen_at DESC`` 排序（最近同步的论文排在前面）。

        Args:
            subscription_id: 订阅 ID

        Returns:
            arxiv_id 字符串列表；若无关联论文则返回空列表
        """
        rows = self._conn.execute(
            _GET_ARXIV_IDS_SQL, (subscription_id,)
        ).fetchall()
        return [r["arxiv_id"] for r in rows]

    def get_subscription_ids_for_arxiv(
        self, arxiv_id: str
    ) -> list[str]:
        """返回引用指定论文的所有订阅 ID 列表。

        Args:
            arxiv_id: 论文 arXiv ID

        Returns:
            subscription_id 字符串列表
        """
        rows = self._conn.execute(
            _GET_SUBSCRIPTION_IDS_SQL, (arxiv_id,)
        ).fetchall()
        return [r["subscription_id"] for r in rows]

    def count_subscriptions_for_arxiv(self, arxiv_id: str) -> int:
        """返回引用指定论文的订阅数量。

        Args:
            arxiv_id: 论文 arXiv ID

        Returns:
            引用该论文的订阅计数
        """
        row = self._conn.execute(
            _COUNT_SUBSCRIPTIONS_SQL, (arxiv_id,)
        ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_for_subscription(self, subscription_id: str) -> None:
        """删除指定订阅的所有归属记录。

        调用方负责提交事务。

        Args:
            subscription_id: 订阅 ID
        """
        self._conn.execute(_DELETE_FOR_SUB_SQL, (subscription_id,))

    def delete_pair(
        self, subscription_id: str, arxiv_id: str
    ) -> None:
        """删除一条特定的订阅-论文归属记录。

        调用方负责提交事务。

        Args:
            subscription_id: 订阅 ID
            arxiv_id: 论文 arXiv ID
        """
        self._conn.execute(_DELETE_PAIR_SQL, (subscription_id, arxiv_id))

    def find_orphan_arxiv_ids(
        self, candidate_arxiv_ids: list[str]
    ) -> list[str]:
        """从候选列表中找出已无任何订阅归属的论文 ID。

        用于在删除订阅后判断哪些论文已成为孤儿。

        Args:
            candidate_arxiv_ids: 待检查的论文 arXiv ID 列表

        Returns:
            孤立论文的 arxiv_id 列表（无任何订阅归属的论文）
        """
        if not candidate_arxiv_ids:
            return []

        # 动态生成 IN 子句占位符
        placeholders = ", ".join("?" for _ in candidate_arxiv_ids)
        sql = (
            "SELECT arxiv_id FROM subscription_papers "
            "WHERE arxiv_id IN (" + placeholders + ")"
        )
        still_referenced = set(
            r["arxiv_id"]
            for r in self._conn.execute(sql, candidate_arxiv_ids).fetchall()
        )
        return [
            aid for aid in candidate_arxiv_ids
            if aid not in still_referenced
        ]
