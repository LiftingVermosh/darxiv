"""平台感知的运行时路径解析。

在开发态（``is_dev_mode=True``）下，数据、日志和数据库均落于项目根下的
``runtime/`` 目录，方便开发者随时检查。

在发布态下，路径遵循平台惯例：Windows → ``%APPDATA%``，
macOS → ``~/Library/Application Support``，Linux → ``$XDG_DATA_HOME``。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

_APP_NAME = "paper-research"


@dataclass(frozen=True)
class RuntimePaths:
    """平台感知的运行时目录布局。

    Attributes:
        data_dir: 应用数据根目录（包含 db、日志等子目录）
        log_dir: 日志文件目录
        db_dir: 数据库文件目录
    """

    data_dir: Path
    log_dir: Path
    db_dir: Path

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def for_app(
        cls,
        app_name: str = _APP_NAME,
        *,
        is_dev_mode: bool = False,
    ) -> RuntimePaths:
        """根据运行模式与平台推导运行时目录。

        Args:
            app_name: 应用名（用于平台目录命名）。
            is_dev_mode: ``True`` 时使用 ``<cwd>/runtime/`` 作为根目录。

        Returns:
            解析完成的 :class:`RuntimePaths`。
        """
        if is_dev_mode:
            base = Path.cwd() / "runtime"
        else:
            base = cls._default_data_dir(app_name)

        data_dir = base
        log_dir = base / "logs"
        db_dir = base / "db"

        return cls(
            data_dir=data_dir,
            log_dir=log_dir,
            db_dir=db_dir,
        )

    # ------------------------------------------------------------------
    # Platform detection
    # ------------------------------------------------------------------

    @staticmethod
    def _default_data_dir(app_name: str) -> Path:
        """根据当前操作系统返回推荐的用户数据目录。

        - Windows: ``%APPDATA%/<app_name>``，回退到 ``%LOCALAPPDATA%``
        - macOS: ``~/Library/Application Support/<app_name>``
        - Linux / 其他: ``$XDG_DATA_HOME/<app_name>``，回退到
          ``~/.local/share/<app_name>``
        """
        if sys.platform == "win32":
            base = os.environ.get("APPDATA") or os.environ.get(
                "LOCALAPPDATA"
            ) or str(Path.home())
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = os.environ.get("XDG_DATA_HOME") or str(
                Path.home() / ".local" / "share"
            )

        return Path(base) / app_name

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def ensure_dirs(self) -> None:
        """递归创建所有运行时目录（幂等）。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.db_dir.mkdir(parents=True, exist_ok=True)

    def resolve_db_path(self, filename: str = "paper_research.db") -> str:
        """返回数据库文件的完整路径。

        Args:
            filename: 数据库文件名，默认 ``paper_research.db``。

        Returns:
            位于 ``db_dir`` 下的数据库文件路径字符串。
        """
        return str(self.db_dir / filename)

    def resolve_log_path(self, filename: str = "paper_research.log") -> str:
        """返回日志文件的完整路径。

        Args:
            filename: 日志文件名，默认 ``paper_research.log``。

        Returns:
            位于 ``log_dir`` 下的日志文件路径字符串。
        """
        return str(self.log_dir / filename)
