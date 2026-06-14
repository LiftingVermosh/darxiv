"""应用日志配置。

在所有其他模块之前调用 :func:`setup_logging`，确保启动阶段的关键信息
（数据库初始化、路径解析、调度器生命周期）均可落盘。
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.infrastructure.config.app_config import AppRuntimeConfig

# ---------------------------------------------------------------------------
# 诊断模型
# ---------------------------------------------------------------------------


@dataclass
class StartupCheckResult:
    """启动阶段诊断结果。

    Attributes:
        ok: 启动是否成功（无致命错误）
        warnings: 非致命告警列表
        fatal_error: 致命错误描述（``None`` 表示无致命错误）
        diagnostics: 启动诊断信息摘要
    """

    ok: bool
    warnings: list[str] = field(default_factory=list)
    fatal_error: str | None = None
    diagnostics: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 日志格式
# ---------------------------------------------------------------------------

_LOG_FORMAT = (
    "%(asctime)s [%(levelname)-8s] %(name)s:%(lineno)d — %(message)s"
)
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SIMPLE_FORMAT = "[%(levelname)-5s] %(name)s — %(message)s"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_logging(config: AppRuntimeConfig) -> StartupCheckResult:
    """配置全局日志基础设施。

    初始化后：
    - 根 logger 统一接收所有消息
    - 日志文件写入 ``config.log_file``（包含 DEBUG 及以上级别）
    - 控制台输出 WARNING 及以上级别（简洁格式，避免 UI 噪声）
    - 模块级 ``logging.getLogger(__name__)`` 正常可用

    Args:
        config: 已解析的运行时配置

    Returns:
        :class:`StartupCheckResult`，包含日志目录可写性等诊断信息
    """
    warnings: list[str] = []
    diagnostics: dict[str, str] = {
        "log_file": config.log_file,
        "log_level": config.log_level,
    }

    # -- 确保日志目录可写 --
    log_path = Path(config.log_file)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # 通过创建空文件测试可写性
        log_path.touch(exist_ok=True)
    except OSError as exc:
        fatal = (
            f"Log directory is not writable: {log_path.parent}\n"
            f"  {exc}"
        )
        # 即使目录不可写，仍然配置控制台输出用于诊断
        _configure_root_logger(
            log_level=config.log_level,
            log_file=None,  # 跳过文件 handler
        )
        logger = logging.getLogger(__name__)
        logger.critical("FATAL: %s", fatal)
        return StartupCheckResult(
            ok=False,
            warnings=warnings,
            fatal_error=fatal,
            diagnostics=diagnostics,
        )

    # -- 配置根 logger --
    level = getattr(logging, config.log_level, logging.INFO)
    _configure_root_logger(log_level=level, log_file=config.log_file)

    logger = logging.getLogger(__name__)
    logger.info("Logging initialized — file=%s, level=%s", config.log_file, config.log_level)

    # -- 诊断信息 --
    diagnostics.update(config.as_diagnostics())
    for key, value in diagnostics.items():
        logger.debug("  %s = %s", key, value)

    return StartupCheckResult(
        ok=True,
        warnings=warnings,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _configure_root_logger(
    *,
    log_level: int,
    log_file: str | None,
) -> None:
    """配置 Python 根 logger 的 handlers 与 formatter。

    Args:
        log_level: 日志级别（如 ``logging.INFO``）
        log_file: 日志文件路径；``None`` 时仅配置控制台 handler
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # 根接收所有消息，由 handler 过滤等级

    # 清除已有的 handlers（幂等）
    root.handlers.clear()

    # -- 控制台 handler（WARNING+，简洁格式） --
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(logging.Formatter(_SIMPLE_FORMAT))
    root.addHandler(console_handler)

    # -- 文件 handler（DEBUG+，完整格式） --
    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)
        )
        root.addHandler(file_handler)

    # 抑制过于嘈杂的第三方 logger
    for noisy in ("httpx", "httpcore", "feedparser", "flet", "flet_core"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
