"""运行期设置服务。

对 :class:`~app.infrastructure.db.repositories.app_settings_repository.AppSettingsRepository`
进行薄封装，为 UI 与调度器层提供带默认值保护的设置读写能力。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from app.application.dto.app_settings_dto import AppSettingsDTO, default_settings
from app.infrastructure.db.repositories.app_settings_repository import (
    AppSettingsRepository,
)

logger = logging.getLogger(__name__)


class SettingsService:
    """运行期设置的应用层服务。

    负责：
    - 读取单个设置（缺失时回退到默认值）
    - 批量写入设置
    - 返回带默认值的完整配置对象
    - 删除 / 重置设置项

    反序列化失败时回退到默认值并记录日志，不会向上层抛出异常。

    Args:
        connection: 共享的 ``sqlite3.Connection``，调用方负责生命周期
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._repo = AppSettingsRepository(connection)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_all(self) -> AppSettingsDTO:
        """返回带默认值的完整设置对象。

        对每一个已知设置键逐项读取；若键不存在或反序列化失败，
        使用模块级默认值，确保调用方总得到稳定的对象。
        """
        defaults = default_settings()
        kwargs: dict[str, Any] = {}
        for key, default in defaults.items():
            try:
                value = self._repo.get(key)
                kwargs[key] = value if value is not None else default
            except Exception:
                logger.warning(
                    "Failed to read setting '%s', falling back to default.", key
                )
                kwargs[key] = default
        return AppSettingsDTO(**kwargs)

    def get(self, key: str) -> Any:
        """读取单个设置。

        Args:
            key: 设置键名

        Returns:
            反序列化后的值；键不存在时返回该键的默认值
        """
        defaults = default_settings()
        default = defaults.get(key)
        try:
            value = self._repo.get(key)
            return value if value is not None else default
        except Exception:
            logger.warning(
                "Failed to read setting '%s', falling back to default.", key
            )
            return default

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def update(self, settings: AppSettingsDTO) -> None:
        """批量写入设置。

        仅写入调用方显式设置的字段，随后提交事务。
        单个字段写入失败时记录日志并继续处理剩余字段。

        Args:
            settings: 携带新值的设置 DTO
        """
        data = settings.model_dump(exclude_unset=True)
        for key, value in data.items():
            try:
                self._repo.set(key, value)
            except Exception:
                logger.exception("Failed to persist setting '%s'.", key)
        self._conn.commit()

    def set(self, key: str, value: Any) -> None:
        """写入单个设置并提交。

        Args:
            key: 设置键名
            value: 待序列化的值（必须可 JSON 序列化）
        """
        self._repo.set(key, value)
        self._conn.commit()

    def reset(self, key: str) -> None:
        """删除指定设置项，使其回退到默认值。

        键不存在时静默成功（幂等）。

        Args:
            key: 要重置的设置键名
        """
        self._repo.delete(key)
        self._conn.commit()
