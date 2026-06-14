"""应用壳层。

负责 Flet 页面初始化、路由派发与 AppContext 生命周期管理。
所有页面通过本模块注册路由并获取共享服务容器。
"""

from __future__ import annotations

import flet as ft

from app.main import AppContext, create_app_context
from app.ui.pages.dashboard_page import build_dashboard_view
from app.ui.pages.paper_detail_page import build_paper_detail_view
from app.ui.pages.settings_page import build_settings_view
from app.ui.pages.subscriptions_page import build_subscriptions_view


class AppShell:
    """Flet 应用主壳。

    通过 ``ft.app(target=AppShell())`` 启动。
    内部完成页面配置、路由注册与 AppContext 装配。

    Attributes:
        page: 当前 Flet :class:`ft.Page` 实例
        ctx: 共享的 :class:`AppContext` 服务容器
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        self.page: ft.Page | None = None
        self.ctx: AppContext | None = None

    def __call__(self, page: ft.Page) -> None:
        """Flet 入口回调。

        在 Flet 启动时被调用一次，传入由框架创建的 Page 实例。
        """
        self.page = page
        self.ctx = create_app_context(self._db_path)

        # -- 页面级配置 --
        page.title = "Paper Research"
        page.theme_mode = ft.ThemeMode.LIGHT
        page.window.width = 1200
        page.window.height = 800
        page.window.min_width = 800
        page.window.min_height = 600

        # -- 主题配色 --
        page.theme = ft.Theme(
            color_scheme_seed=ft.Colors.BLUE,
        )

        # -- 路由 --
        page.on_route_change = self._on_route_change

        # -- 底部导航栏 --
        page.navigation_bar = ft.NavigationBar(
            destinations=[
                ft.NavigationBarDestination(
                    icon=ft.Icons.DASHBOARD,
                    label="Dashboard",
                ),
                ft.NavigationBarDestination(
                    icon=ft.Icons.SUBSCRIPTIONS,
                    label="Subscriptions",
                ),
                ft.NavigationBarDestination(
                    icon=ft.Icons.SETTINGS,
                    label="Settings",
                ),
            ],
            on_change=self._on_nav_change,
        )

        # -- 调度器自动启动 --
        self._start_scheduler_if_enabled()

        # -- 初始路由（恢复上次打开的页面） --
        last_page = self.ctx.settings_service.get("last_open_page")
        if last_page and isinstance(last_page, str) and last_page.startswith("/"):
            page.go(last_page)
        else:
            page.go("/dashboard")

    # ------------------------------------------------------------------
    # Scheduler lifecycle
    # ------------------------------------------------------------------

    def _start_scheduler_if_enabled(self) -> None:
        """若设置中启用自动同步，则启动后台调度器。"""
        if self.ctx is None:
            return
        settings = self.ctx.settings_service.get_all()
        if settings.auto_sync_enabled:
            self.ctx.scheduler.start()

    # ------------------------------------------------------------------
    # Route handling
    # ------------------------------------------------------------------

    def _on_route_change(self, e: ft.RouteChangeEvent) -> None:
        """根据当前路由构建并切换视图。"""
        if self.page is None or self.ctx is None:
            return

        route = self.page.route
        self.page.views.clear()

        # -- 清理上一页面的调度器监听器 --
        self._cleanup_settings_listener()

        # 解析路由并构建对应视图
        if route == "/dashboard" or route == "/":
            view = build_dashboard_view(self.ctx, self.page)
            self.page.navigation_bar.selected_index = 0
            self._save_last_page(route)
        elif route == "/subscriptions":
            view = build_subscriptions_view(self.ctx, self.page)
            self.page.navigation_bar.selected_index = 1
            self._save_last_page(route)
        elif route == "/settings":
            view = build_settings_view(self.ctx, self.page)
            self.page.navigation_bar.selected_index = 2
            self._save_last_page(route)
        elif route.startswith("/paper/"):
            arxiv_id = route.split("/paper/", 1)[1]
            view = build_paper_detail_view(self.ctx, self.page, arxiv_id)
            # 论文详情页是瞬时页面，不作为恢复目标
        else:
            # 未知路由 → 重定向到 Dashboard
            self.page.go("/dashboard")
            return

        self.page.views.append(view)
        self.page.update()

    def _cleanup_settings_listener(self) -> None:
        """移除上一次设置页挂在调度器上的 tick listener。

        导航离开 /settings 时调用，避免后台 tick 继续驱动已销毁页面的
        旧控件引用。
        """
        if self.page is None or self.ctx is None:
            return
        prev = getattr(self.page, "_settings_tick_listener", None)
        if prev is not None:
            self.ctx.scheduler.remove_tick_listener(prev)
            self.page._settings_tick_listener = None  # type: ignore[attr-defined]

    def _save_last_page(self, route: str) -> None:
        """将当前路由持久化为 ``last_open_page`` 设置项。"""
        if self.ctx is None:
            return
        try:
            self.ctx.settings_service.set("last_open_page", route)
        except Exception:
            pass  # 持久化失败不应影响导航

    def _on_nav_change(self, e: ft.ControlEvent) -> None:
        """底部导航栏点击事件。"""
        if self.page is None:
            return
        index = e.control.selected_index
        if index == 0:
            self.page.go("/dashboard")
        elif index == 1:
            self.page.go("/subscriptions")
        elif index == 2:
            self.page.go("/settings")
