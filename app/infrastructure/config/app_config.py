"""统一的运行时配置模型。

通过 :class:`AppRuntimeConfig` 收口数据库路径、日志级别、数据目录等
所有启动期决策，避免各模块分散读取环境变量或硬编码路径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.infrastructure.config.runtime_paths import RuntimePaths


def _detect_dev_mode() -> bool:
    """通过环境变量判断是否为开发模式。

    ``PAPER_RESEARCH_DEV_MODE=1``（或 ``true``/``yes``，不区分大小写）
    时返回 ``True``；否则返回 ``False``。
    """
    val = os.environ.get("PAPER_RESEARCH_DEV_MODE", "").lower()
    return val in ("1", "true", "yes")


def _detect_log_level() -> str:
    """从环境变量读取日志级别，默认 ``INFO``。

    ``PAPER_RESEARCH_LOG_LEVEL`` 支持 ``DEBUG``、``INFO``、``WARNING``、
    ``ERROR``、``CRITICAL``（不区分大小写）。
    """
    raw = os.environ.get("PAPER_RESEARCH_LOG_LEVEL", "INFO").upper()
    valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    return raw if raw in valid else "INFO"


@dataclass(frozen=True)
class AppRuntimeConfig:
    """应用运行时配置。

    所有路径均为已解析的绝对路径字符串，构造时不执行 I/O——
    调用方根据需要在启动阶段显式调用 ``RuntimePaths.ensure_dirs()``。

    Attributes:
        db_path: SQLite 数据库文件的完整路径
        log_file: 日志文件的完整路径
        log_level: 日志级别字符串（``DEBUG``/``INFO``/``WARNING``/``ERROR``/``CRITICAL``）
        data_dir: 应用数据根目录
        log_dir: 日志目录
        is_dev_mode: 是否为开发模式
        paths: 底层的 :class:`RuntimePaths` 实例
    """

    db_path: str
    log_file: str
    log_level: str
    data_dir: str
    log_dir: str
    is_dev_mode: bool
    paths: RuntimePaths

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        is_dev_mode: bool | None = None,
        db_path: str | None = None,
    ) -> AppRuntimeConfig:
        """按优先级解析运行时配置。

        优先级（从高到低）：
        1. 显式传入的 *db_path*（绕过路径策略）
        2. 环境变量 ``PAPER_RESEARCH_DB_PATH``
        3. 开发模式：``<cwd>/runtime/db/paper_research.db``
        4. 发布模式：平台用户数据目录下的 ``db/paper_research.db``

        Args:
            is_dev_mode: 显式指定开发模式；``None`` 时从环境变量检测
            db_path: 显式数据库路径；``None`` 时按优先级推导

        Returns:
            解析完成的 :class:`AppRuntimeConfig`
        """
        if is_dev_mode is None:
            is_dev_mode = _detect_dev_mode()

        paths = RuntimePaths.for_app(is_dev_mode=is_dev_mode)

        # 数据库路径解析（按优先级）
        if db_path is not None:
            resolved_db = db_path
        elif env_db := os.environ.get("PAPER_RESEARCH_DB_PATH"):
            resolved_db = env_db
        else:
            resolved_db = paths.resolve_db_path()

        log_level = _detect_log_level()
        log_file = paths.resolve_log_path()

        return cls(
            db_path=resolved_db,
            log_file=log_file,
            log_level=log_level,
            data_dir=str(paths.data_dir),
            log_dir=str(paths.log_dir),
            is_dev_mode=is_dev_mode,
            paths=paths,
        )

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def as_diagnostics(self) -> dict[str, str]:
        """返回诊断信息字典，供启动日志或运维排查使用。"""
        return {
            "data_dir": self.data_dir,
            "log_dir": self.log_dir,
            "db_path": self.db_path,
            "log_file": self.log_file,
            "log_level": self.log_level,
            "dev_mode": str(self.is_dev_mode),
        }
