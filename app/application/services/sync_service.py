from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from app.application.dto import SyncResultDTO
from app.domain.enums import SyncRunStatus, SyncTriggerType
from app.domain.models import Paper, Subscription, SyncRun
from app.infrastructure.arxiv import (
    ArxivClient,
    QueryInput,
    build_query,
    parse_feed,
)
from app.infrastructure.db.repositories import (
    PaperRepository,
    SubscriptionPaperRepository,
    SubscriptionRepository,
    SyncRunRepository,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ERROR_LENGTH = 2000


def _truncate_error(message: str) -> str:
    """截断过长错误信息以避免超出字段容量"""
    if len(message) <= _MAX_ERROR_LENGTH:
        return message
    return message[:_MAX_ERROR_LENGTH - 3] + "..."


# ---------------------------------------------------------------------------
# SyncService
# ---------------------------------------------------------------------------


class SyncService:
    """编排 订阅→arXiv抓取→解析→入库→审计记录 的完整同步流程。

    - 不直接写 SQL —— 所有持久化通过 Repository 完成
    - 不解析 Atom —— 仅通过 arXiv client/parser 获取标准化的 Paper 列表
    - 每次同步无论成功或失败都需记录 sync_runs
    - 单个订阅失败不影响批处理中其他订阅的继续执行

    Args:
        connection: 已打开的 ``sqlite3.Connection``，调用方负责连接生命周期
        arxiv_client: 可选的自定义 arXiv 客户端，默认创建标准实例
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        arxiv_client: ArxivClient | None = None,
    ) -> None:
        self._conn = connection
        self._sub_repo = SubscriptionRepository(connection)
        self._paper_repo = PaperRepository(connection)
        self._sync_run_repo = SyncRunRepository(connection)
        self._sub_paper_repo = SubscriptionPaperRepository(connection)
        self._arxiv_client = arxiv_client or ArxivClient()

    def close(self) -> None:
        """关闭底层 HTTP 客户端，释放连接资源。

        调用后本实例不可再用于同步操作。
        重复调用是幂等的。
        """
        self._arxiv_client.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_subscription(
        self,
        subscription_id: str,
        trigger_type: SyncTriggerType = SyncTriggerType.MANUAL,
    ) -> SyncResultDTO:
        """对指定订阅执行一次同步。

        Args:
            subscription_id: 要同步的订阅 ID
            trigger_type: 触发类型（默认为手动触发）

        Returns:
            包含插入/更新计数与最终状态的 :class:`SyncResultDTO`

        Raises:
            ValueError: 当 ``subscription_id`` 对应的订阅不存在时
        """
        sub = self._sub_repo.get(subscription_id)
        if sub is None:
            raise ValueError(
                f"Subscription '{subscription_id}' not found"
            )
        return self._sync_one(sub, trigger_type)

    def sync_enabled_subscriptions(
        self,
        trigger_type: SyncTriggerType = SyncTriggerType.MANUAL,
    ) -> list[SyncResultDTO]:
        """对所有已启用订阅依次执行同步。

        单个订阅失败不会中断剩余订阅的处理，失败信息将记录在对应
        ``SyncResultDTO.error_message`` 中。

        Args:
            trigger_type: 触发类型（默认为手动触发）
        """
        results: list[SyncResultDTO] = []
        for sub in self._sub_repo.list_enabled():
            try:
                result = self._sync_one(sub, trigger_type)
                results.append(result)
            except Exception as exc:
                now = datetime.now(timezone.utc)
                results.append(
                    SyncResultDTO(
                        subscription_id=sub.id,
                        subscription_name=sub.name,
                        status=SyncRunStatus.FAILED,
                        fetched_count=0,
                        inserted_count=0,
                        updated_count=0,
                        started_at=now,
                        finished_at=now,
                        error_message=_truncate_error(str(exc)),
                    )
                )
        return results

    def sync_due_subscriptions(
        self,
        trigger_type: SyncTriggerType = SyncTriggerType.SCHEDULED,
    ) -> list[SyncResultDTO]:
        """对所有已启用且到达各自同步间隔的订阅执行同步。

        与 :meth:`sync_enabled_subscriptions` 不同，本方法会逐项检查
        ``last_synced_at + sync_interval_minutes`` 是否已到期，
        未到期的订阅会被跳过。

        ``last_synced_at`` 为 ``None``（从未同步）的订阅视为立即到期。

        Args:
            trigger_type: 触发类型（默认为定时触发）
        """
        results: list[SyncResultDTO] = []
        now = datetime.now(timezone.utc)

        for sub in self._sub_repo.list_enabled():
            # -- 检查是否到达同步间隔 --
            if sub.last_synced_at is not None:
                try:
                    last = datetime.fromisoformat(sub.last_synced_at)
                    elapsed_minutes = (now - last).total_seconds() / 60.0
                    if elapsed_minutes < sub.sync_interval_minutes:
                        continue  # 未到间隔，跳过
                except (ValueError, TypeError):
                    # 时间戳损坏视为从未同步，继续执行
                    pass

            try:
                result = self._sync_one(sub, trigger_type)
                results.append(result)
            except Exception as exc:
                now_err = datetime.now(timezone.utc)
                results.append(
                    SyncResultDTO(
                        subscription_id=sub.id,
                        subscription_name=sub.name,
                        status=SyncRunStatus.FAILED,
                        fetched_count=0,
                        inserted_count=0,
                        updated_count=0,
                        started_at=now_err,
                        finished_at=now_err,
                        error_message=_truncate_error(str(exc)),
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sync_one(
        self,
        subscription: Subscription,
        trigger_type: SyncTriggerType,
    ) -> SyncResultDTO:
        """单个订阅的完整同步流程（写入 running → 抓取 → 入库 → 标记终态）。"""
        run_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)

        # 创建 running 状态的 sync_run 并立即提交，确保崩溃时审计可见
        run = SyncRun(
            id=run_id,
            subscription_id=subscription.id,
            started_at=started_at,
            status=SyncRunStatus.RUNNING,
            trigger_type=trigger_type,
        )
        self._sync_run_repo.insert(run)
        self._conn.commit()

        try:
            # 构造查询 + 请求 arXiv + 解析为 Paper 列表
            qi = QueryInput(subscription=subscription)
            query, url = build_query(qi)
            raw_xml = self._arxiv_client.fetch(url)
            fetch_result = parse_feed(raw_xml, query)

            # 增量入库（在同一事务中完成全部 paper 写入）
            inserted, updated = self._persist_papers(
                fetch_result.papers,
                subscription_id=subscription.id,
                sync_run_id=run_id,
            )

            # 标记 sync_run 为成功
            finished_at = datetime.now(timezone.utc)
            run = SyncRun(
                id=run_id,
                subscription_id=subscription.id,
                started_at=started_at,
                finished_at=finished_at,
                status=SyncRunStatus.SUCCESS,
                trigger_type=trigger_type,
                fetched_count=len(fetch_result.papers),
                inserted_count=inserted,
                updated_count=updated,
            )
            self._sync_run_repo.update(run)

            # 回写订阅的 last_synced_at，使 UI/调度层可查询上次同步时间
            self._sub_repo.set_last_synced_at(
                subscription.id,
                finished_at.isoformat(),
            )

            self._conn.commit()

            return SyncResultDTO(
                subscription_id=subscription.id,
                subscription_name=subscription.name,
                status=SyncRunStatus.SUCCESS,
                fetched_count=len(fetch_result.papers),
                inserted_count=inserted,
                updated_count=updated,
                started_at=started_at,
                finished_at=finished_at,
            )

        except Exception as exc:
            # 先回滚持久化阶段已经写入但尚未提交的论文数据，
            # 确保半写入不会随失败状态一起落库，保持原子性。
            self._conn.rollback()

            # 标记失败 —— 错误信息截断以防止字段溢出
            finished_at = datetime.now(timezone.utc)
            error_msg = _truncate_error(str(exc))
            run = SyncRun(
                id=run_id,
                subscription_id=subscription.id,
                started_at=started_at,
                finished_at=finished_at,
                status=SyncRunStatus.FAILED,
                trigger_type=trigger_type,
                error_message=error_msg,
            )
            self._sync_run_repo.update(run)
            self._conn.commit()

            return SyncResultDTO(
                subscription_id=subscription.id,
                subscription_name=subscription.name,
                status=SyncRunStatus.FAILED,
                fetched_count=0,
                inserted_count=0,
                updated_count=0,
                started_at=started_at,
                finished_at=finished_at,
                error_message=error_msg,
            )

    def _persist_papers(
        self, papers: list[Paper],
        *,
        subscription_id: str,
        sync_run_id: str,
    ) -> tuple[int, int]:
        """将论文列表逐条写入本地数据库。

        去重规则：
        - 以 ``arxiv_id`` 识别同一篇论文
        - 以 ``(arxiv_id, version)`` 识别版本级唯一记录
        - 数据库不存在的论文计为 **inserted**
        - 已存在且当前快照发生变化（版本号提升，或同版本下标题、
          摘要、更新时间变化）计为 **updated**

        每条论文入库后同步写入 ``subscription_papers`` 归属表，
        使系统记录该论文由哪个订阅引入。

        Args:
            papers: 待持久化的论文列表
            subscription_id: 触发本次同步的订阅 ID
            sync_run_id: 本次同步运行的 ID

        Returns:
            ``(inserted_count, updated_count)``
        """
        inserted = 0
        updated = 0

        for paper in papers:
            existing = self._paper_repo.get(paper.arxiv_id)
            is_new = existing is None

            self._paper_repo.upsert(paper)
            self._paper_repo.upsert_version(paper, None)

            # 记录订阅-论文归属关系
            # 注意：不在此处升级 provenance_state。legacy_unattributed
            # 论文即使通过重同步建立了某个订阅的链接，也不等于"所有
            # 历史归属都已恢复"——它可能还属于其他尚未重同步的旧订阅。
            # 因此保留 legacy_unattributed 标记，防止部分重建的归属被
            # 误判为完整归属后遭到孤儿删除。
            self._sub_paper_repo.upsert(
                subscription_id=subscription_id,
                arxiv_id=paper.arxiv_id,
                last_sync_run_id=sync_run_id,
            )

            if is_new:
                inserted += 1
            elif self._snapshot_changed(paper, existing):
                updated += 1

        return inserted, updated

    @staticmethod
    def _snapshot_changed(incoming: Paper, existing: Paper) -> bool:
        """判断 *incoming* 相对 *existing* 是否导致当前快照变化。

        触发条件：
        - 版本号不同
        - 标题、摘要或更新时间发生变化
        """
        if incoming.version != existing.version:
            return True
        return (
            incoming.title != existing.title
            or incoming.abstract != existing.abstract
            or incoming.updated_at != existing.updated_at
        )
