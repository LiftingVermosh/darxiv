"""运行期设置数据传输对象。

封装所有可持久化的用户偏好，携带合理默认值，
用于 UI 层与 :class:`~app.application.services.settings_service.SettingsService` 之间传递。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Defaults (single source of truth)
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_sync_enabled": False,
    "global_sync_interval_minutes": None,
    "show_hidden_by_default": False,
    "default_list_filters": {},
    "last_open_page": None,
}


def default_settings() -> dict[str, Any]:
    """返回默认设置字典的浅拷贝（避免调用方意外修改模块级常量）。"""
    return dict(_DEFAULT_SETTINGS)


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


class AppSettingsDTO(BaseModel):
    """运行期设置对象。

    所有字段均有默认值，因此构造一个全默认实例只需 ``AppSettingsDTO()``。

    Attributes:
        auto_sync_enabled: 是否启用自动同步
        global_sync_interval_minutes: 全局自动同步间隔（分钟）；``None`` 时回退到 60 分钟
        show_hidden_by_default: Dashboard 是否默认展示已隐藏论文
        default_list_filters: 默认的列表筛选条件字典
        last_open_page: 上次关闭应用时所在的页面路由
    """

    model_config = ConfigDict(extra="forbid")

    auto_sync_enabled: bool = Field(default=False)
    global_sync_interval_minutes: int | None = Field(default=None)
    show_hidden_by_default: bool = Field(default=False)
    default_list_filters: dict[str, object] = Field(default_factory=dict)
    last_open_page: str | None = Field(default=None)

    @classmethod
    def with_defaults(cls) -> AppSettingsDTO:
        """创建一个全默认值的设置对象。"""
        return cls(**default_settings())
