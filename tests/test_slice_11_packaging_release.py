"""Slice 11 — 运行时路径、启动配置、关闭与 smoke 回归测试。

覆盖：
- RuntimePaths 开发态 / 发布态路径解析
- AppRuntimeConfig 显式 db_path 绕过契约
- AppShell 延迟配置解析
- StartupCheckResult 诊断模型
- AppContext 关闭幂等与日志行为
"""

from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.application.services.settings_service import SettingsService
from app.application.services.sync_service import SyncService
from app.domain.enums.trigger_type import SyncTriggerType
from app.infrastructure.config.app_config import AppRuntimeConfig
from app.infrastructure.config.runtime_paths import RuntimePaths
from app.infrastructure.db.connection import get_connection
from app.infrastructure.logging.setup import StartupCheckResult, setup_logging
from app.infrastructure.scheduler.sync_scheduler import SyncScheduler
from app.main import AppContext, create_app_context

# ============================================================================
# Helpers
# ============================================================================


def _setup_db() -> sqlite3.Connection:
    return get_connection(":memory:")


def _mock_ctx(conn: sqlite3.Connection) -> AppContext:
    """构建一个用于测试的最小 AppContext（不启动 scheduler）。"""
    from app.application.services import (
        PaperLibraryService,
        PaperQueryService,
        SettingsService,
        StatusService,
        SubscriptionService,
        SyncService,
    )

    return AppContext(
        connection=conn,
        paper_library_service=PaperLibraryService(conn),
        paper_query_service=PaperQueryService(conn),
        settings_service=SettingsService(conn),
        status_service=StatusService(conn),
        subscription_service=MagicMock(spec=SubscriptionService),
        sync_service=MagicMock(spec=SyncService),
        scheduler=MagicMock(spec=SyncScheduler),
    )


# ============================================================================
# 1. RuntimePaths 路径解析测试
# ============================================================================


class RuntimePathsDevModeTests(unittest.TestCase):
    """开发模式下的路径解析。"""

    def test_dev_mode_uses_cwd_runtime(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=True)
        expected_base = Path.cwd() / "runtime"
        self.assertEqual(paths.data_dir, expected_base)
        self.assertEqual(paths.log_dir, expected_base / "logs")
        self.assertEqual(paths.db_dir, expected_base / "db")

    def test_dev_mode_ensure_dirs_creates_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "cwd", return_value=Path(tmp)):
                paths = RuntimePaths.for_app(is_dev_mode=True)
                paths.ensure_dirs()

                self.assertTrue(paths.data_dir.exists())
                self.assertTrue(paths.log_dir.exists())
                self.assertTrue(paths.db_dir.exists())

    def test_ensure_dirs_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "cwd", return_value=Path(tmp)):
                paths = RuntimePaths.for_app(is_dev_mode=True)
                paths.ensure_dirs()
                paths.ensure_dirs()  # 不应抛异常
                self.assertTrue(paths.data_dir.exists())

    def test_resolve_db_path_default_filename(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=True)
        resolved = paths.resolve_db_path()
        self.assertTrue(resolved.endswith("paper_research.db"))
        self.assertIn(str(paths.db_dir), resolved)

    def test_resolve_db_path_custom_filename(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=True)
        resolved = paths.resolve_db_path("test.db")
        self.assertTrue(resolved.endswith("test.db"))

    def test_resolve_log_path(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=True)
        resolved = paths.resolve_log_path()
        self.assertTrue(resolved.endswith("paper_research.log"))
        self.assertIn(str(paths.log_dir), resolved)


class RuntimePathsReleaseModeTests(unittest.TestCase):
    """发布模式下的平台路径解析。"""

    def test_release_mode_uses_platform_dir(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=False)
        # 路径不应在 cwd 下
        cwd = str(Path.cwd())
        self.assertNotIn(cwd, str(paths.data_dir))

    def test_release_mode_custom_app_name(self) -> None:
        paths = RuntimePaths.for_app("my-app", is_dev_mode=False)
        self.assertIn("my-app", str(paths.data_dir))

    def test_frozen_dataclass(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=True)
        with self.assertRaises(Exception):
            paths.data_dir = Path("/other")  # type: ignore[misc]


# ============================================================================
# 2. AppRuntimeConfig 测试
# ============================================================================


class AppRuntimeConfigCreationTests(unittest.TestCase):
    """AppRuntimeConfig.create() 路径优先级与契约测试。"""

    def setUp(self) -> None:
        # 保存并清除可能影响测试的环境变量
        self._saved = {
            "PAPER_RESEARCH_DB_PATH": os.environ.pop("PAPER_RESEARCH_DB_PATH", None),
            "PAPER_RESEARCH_DEV_MODE": os.environ.pop("PAPER_RESEARCH_DEV_MODE", None),
            "PAPER_RESEARCH_LOG_LEVEL": os.environ.pop("PAPER_RESEARCH_LOG_LEVEL", None),
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    # -- 默认路径策略 --

    def test_default_dev_mode_resolves_paths_in_cwd(self) -> None:
        """默认开发模式将路径解析到 <cwd>/runtime/ 下（纯解析，不创建目录）。"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "cwd", return_value=Path(tmp)):
                config = AppRuntimeConfig.create(is_dev_mode=True)
                # 路径已解析但目录尚未创建（纯工厂）
                self.assertIn("runtime", config.db_path)
                self.assertIn("runtime", config.data_dir)
                self.assertFalse(Path(config.data_dir).exists())

    def test_explicit_ensure_dirs_creates_directories(self) -> None:
        """调用方显式调用 config.paths.ensure_dirs() 后目录应存在。"""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, "cwd", return_value=Path(tmp)):
                config = AppRuntimeConfig.create(is_dev_mode=True)
                self.assertFalse(Path(config.data_dir).exists())
                # 显式触发 I/O
                config.paths.ensure_dirs()
                self.assertTrue(Path(config.data_dir).exists())
                self.assertTrue(Path(config.log_dir).exists())

    # -- 工厂纯度：create() 永远不调用 ensure_dirs() --

    def test_create_never_calls_ensure_dirs(self) -> None:
        """AppRuntimeConfig.create() 是纯工厂，任何参数组合都不应触发 I/O。"""
        cases = [
            {"is_dev_mode": True},
            {"is_dev_mode": False},
            {"is_dev_mode": True, "db_path": ":memory:"},
            {"is_dev_mode": False, "db_path": "/tmp/custom.db"},
        ]
        for kwargs in cases:
            with patch.object(RuntimePaths, "ensure_dirs") as mock_ensure:
                AppRuntimeConfig.create(**kwargs)
                mock_ensure.assert_not_called()

    def test_create_with_env_db_path_never_calls_ensure_dirs(self) -> None:
        """PAPER_RESEARCH_DB_PATH 设置时也不触发 I/O。"""
        with patch.object(RuntimePaths, "ensure_dirs") as mock_ensure:
            with patch.dict(os.environ, {"PAPER_RESEARCH_DB_PATH": "/env/path.db"}):
                AppRuntimeConfig.create(is_dev_mode=False)
                mock_ensure.assert_not_called()

    # -- 环境变量 db_path --

    def test_env_db_path_used_when_no_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_db = str(Path(tmp) / "env.db")
            with patch.dict(os.environ, {"PAPER_RESEARCH_DB_PATH": env_db}):
                config = AppRuntimeConfig.create(is_dev_mode=False)
                self.assertEqual(config.db_path, env_db)

    def test_explicit_db_path_wins_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit_db = str(Path(tmp) / "explicit.db")
            with patch.dict(os.environ, {"PAPER_RESEARCH_DB_PATH": "/other/path.db"}):
                config = AppRuntimeConfig.create(is_dev_mode=False, db_path=explicit_db)
                self.assertEqual(config.db_path, explicit_db)

    # -- 开发模式检测 --

    def test_dev_mode_detection_from_env_true(self) -> None:
        with patch.dict(os.environ, {"PAPER_RESEARCH_DEV_MODE": "1"}):
            config = AppRuntimeConfig.create()
            self.assertTrue(config.is_dev_mode)

    def test_dev_mode_detection_from_env_yes(self) -> None:
        with patch.dict(os.environ, {"PAPER_RESEARCH_DEV_MODE": "yes"}):
            config = AppRuntimeConfig.create()
            self.assertTrue(config.is_dev_mode)

    def test_dev_mode_detection_default_false(self) -> None:
        config = AppRuntimeConfig.create()
        self.assertFalse(config.is_dev_mode)

    def test_explicit_is_dev_mode_overrides_env(self) -> None:
        with patch.dict(os.environ, {"PAPER_RESEARCH_DEV_MODE": "1"}):
            config = AppRuntimeConfig.create(is_dev_mode=False)
            self.assertFalse(config.is_dev_mode)

    # -- 日志级别 --

    def test_log_level_default_info(self) -> None:
        config = AppRuntimeConfig.create()
        self.assertEqual(config.log_level, "INFO")

    def test_log_level_from_env(self) -> None:
        with patch.dict(os.environ, {"PAPER_RESEARCH_LOG_LEVEL": "DEBUG"}):
            config = AppRuntimeConfig.create()
            self.assertEqual(config.log_level, "DEBUG")

    def test_log_level_invalid_falls_back(self) -> None:
        with patch.dict(os.environ, {"PAPER_RESEARCH_LOG_LEVEL": "TRACE"}):
            config = AppRuntimeConfig.create()
            self.assertEqual(config.log_level, "INFO")  # fallback

    # -- 不可变 --

    def test_config_is_frozen(self) -> None:
        config = AppRuntimeConfig.create(is_dev_mode=True, db_path=":memory:")
        with self.assertRaises(Exception):
            config.db_path = "other.db"  # type: ignore[misc]

    # -- 诊断 --

    def test_as_diagnostics_contains_all_keys(self) -> None:
        config = AppRuntimeConfig.create(is_dev_mode=True, db_path=":memory:")
        diag = config.as_diagnostics()
        for key in ("data_dir", "log_dir", "db_path", "log_file", "log_level", "dev_mode"):
            self.assertIn(key, diag)


# ============================================================================
# 3. AppShell 延迟配置测试
# ============================================================================


class AppShellLazyConfigTests(unittest.TestCase):
    """AppShell 不应在构造阶段触发路径解析或 I/O。"""

    def test_construct_without_config_does_not_trigger_filesystem(self) -> None:
        """AppShell() 构造不触发 ensure_dirs()。"""
        from app.ui.app_shell import AppShell

        # 构造 AppShell 不应调用 AppRuntimeConfig.create()
        with patch(
            "app.ui.app_shell.AppRuntimeConfig.create"
        ) as mock_create:
            shell = AppShell()
            mock_create.assert_not_called()
            self.assertIsNone(shell._config)
            self.assertIsNone(shell.page)
            self.assertIsNone(shell.ctx)

    def test_construct_with_config_passes_through(self) -> None:
        """传入 config 时直接存储，不额外解析。"""
        from app.ui.app_shell import AppShell

        config = AppRuntimeConfig.create(is_dev_mode=True, db_path=":memory:")
        shell = AppShell(config=config)
        self.assertIs(shell._config, config)

    def test_call_without_config_resolves_lazily(self) -> None:
        """__call__ 时若未传入 config，延迟创建。"""
        from app.ui.app_shell import AppShell

        shell = AppShell()
        page = MagicMock()

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=_mock_ctx(_setup_db()),
        ):
            shell(page)

        # 此时 config 应已被解析
        self.assertIsNotNone(shell._config)

    def test_call_with_config_uses_provided(self) -> None:
        """__call__ 时若已传入 config，使用传入的。"""
        from app.ui.app_shell import AppShell

        config = AppRuntimeConfig.create(is_dev_mode=True, db_path=":memory:")
        shell = AppShell(config=config)
        page = MagicMock()

        with patch(
            "app.ui.app_shell.create_app_context",
            return_value=_mock_ctx(_setup_db()),
        ) as mock_create:
            shell(page)

        mock_create.assert_called_once_with(config.db_path)


# ============================================================================
# 4. StartupCheckResult 诊断模型测试
# ============================================================================


class StartupCheckResultTests(unittest.TestCase):
    """启动诊断结果模型。"""

    def test_ok_with_no_issues(self) -> None:
        result = StartupCheckResult(ok=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.warnings, [])
        self.assertIsNone(result.fatal_error)
        self.assertEqual(result.diagnostics, {})

    def test_not_ok_with_fatal_error(self) -> None:
        result = StartupCheckResult(
            ok=False,
            fatal_error="Disk full",
            diagnostics={"path": "/tmp"},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.fatal_error, "Disk full")
        self.assertEqual(result.diagnostics, {"path": "/tmp"})

    def test_ok_with_warnings(self) -> None:
        result = StartupCheckResult(
            ok=True,
            warnings=["Low disk space", "Slow I/O"],
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.warnings), 2)


# ============================================================================
# 5. setup_logging 测试
# ============================================================================


class SetupLoggingTests(unittest.TestCase):
    """日志系统初始化。"""

    def setUp(self) -> None:
        self._saved_level = os.environ.pop("PAPER_RESEARCH_LOG_LEVEL", None)
        # 清理上一次测试可能残留的 handler（防止文件句柄泄漏）
        self._clear_root_handlers()

    def tearDown(self) -> None:
        if self._saved_level is not None:
            os.environ["PAPER_RESEARCH_LOG_LEVEL"] = self._saved_level
        self._clear_root_handlers()

    @staticmethod
    def _clear_root_handlers() -> None:
        """移除并关闭根 logger 的所有 handler。"""
        root = logging.getLogger()
        for h in list(root.handlers):
            try:
                h.close()
            except Exception:
                pass
            root.removeHandler(h)

    def test_setup_logging_with_writable_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_file = str(Path(tmp) / "test.log")
            config = AppRuntimeConfig(
                db_path=":memory:",
                log_file=log_file,
                log_level="DEBUG",
                data_dir=tmp,
                log_dir=tmp,
                is_dev_mode=True,
                paths=RuntimePaths.for_app(is_dev_mode=True),
            )
            result = setup_logging(config)
            self.assertTrue(result.ok)
            self.assertIsNone(result.fatal_error)
            # 验证 root logger 有 handlers
            root = logging.getLogger()
            self.assertGreater(len(root.handlers), 0)
            # 立即清理 handler 以释放文件句柄（Windows 需要）
            self._clear_root_handlers()

    def test_setup_logging_unwritable_dir(self) -> None:
        """日志目录不可写时返回 fatal error 但不崩溃。"""
        config = AppRuntimeConfig(
            db_path=":memory:",
            log_file="Z:/nonexistent/path/that/cannot/be/created/test.log",
            log_level="INFO",
            data_dir="Z:/nonexistent",
            log_dir="Z:/nonexistent/path",
            is_dev_mode=False,
            paths=RuntimePaths.for_app(is_dev_mode=False),
        )
        result = setup_logging(config)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.fatal_error)
        self.assertIn("not writable", result.fatal_error.lower())
        # setup_logging 即使失败也会添加 console handler，需清理
        self._clear_root_handlers()


# ============================================================================
# 6. AppContext 关闭与生命周期测试
# ============================================================================


class AppContextShutdownTests(unittest.TestCase):
    """AppContext 关闭行为：幂等、顺序、日志。"""

    def setUp(self) -> None:
        self.conn = _setup_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_close_is_idempotent(self) -> None:
        ctx = create_app_context(":memory:")
        ctx.close()
        self.assertTrue(ctx.closed)
        # 第二次 close 应无副作用
        ctx.close()
        self.assertTrue(ctx.closed)

    def test_context_manager_closes(self) -> None:
        with create_app_context(":memory:") as ctx:
            self.assertFalse(ctx.closed)
        self.assertTrue(ctx.closed)

    def test_context_manager_closes_on_exception(self) -> None:
        ctx = create_app_context(":memory:")
        try:
            with ctx:
                raise RuntimeError("simulated")
        except RuntimeError:
            pass
        self.assertTrue(ctx.closed)

    def test_close_skips_teardown_when_scheduler_stop_fails(self) -> None:
        """scheduler.stop() → False 时跳过 HTTP 客户端关闭。"""
        ctx = create_app_context(":memory:")
        try:
            with patch.object(ctx.scheduler, "stop", return_value=False):
                with patch.object(ctx.sync_service, "close") as mock_close:
                    ctx.close()
            mock_close.assert_not_called()
            self.assertTrue(ctx.closed)
        finally:
            ctx._closed = False
            ctx.scheduler.stop()
            ctx.sync_service.close()
            ctx.connection.close()

    def test_close_logs_shutdown_sequence(self) -> None:
        """关闭时输出 info 级别日志。"""
        ctx = create_app_context(":memory:")
        with self.assertLogs("app.main", level="INFO") as log_cm:
            ctx.close()
        messages = "\n".join(log_cm.output)
        self.assertIn("Shutting down", messages)
        self.assertIn("shutdown complete", messages.lower())

    def test_closed_context_close_logs_debug(self) -> None:
        """已关闭的 context 重复 close 输出 debug 日志。"""
        ctx = create_app_context(":memory:")
        ctx.close()
        with self.assertLogs("app.main", level="DEBUG") as log_cm:
            ctx.close()
        messages = "\n".join(log_cm.output)
        self.assertIn("already-closed", messages)


# ============================================================================
# 7. create_app_context 启动失败测试
# ============================================================================


class CreateAppContextFailureTests(unittest.TestCase):
    """create_app_context() 在异常路径上的行为。"""

    def test_db_init_failure_raises_runtime_error(self) -> None:
        """数据库初始化失败时抛出 RuntimeError 并关闭连接。"""
        with patch(
            "app.main.get_connection",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(RuntimeError) as cm:
                create_app_context(":memory:")
            self.assertIn("Database initialization failed", str(cm.exception))

    def test_db_init_failure_logs_critical(self) -> None:
        """数据库初始化失败时记录 CRITICAL 日志。"""
        with patch(
            "app.main.get_connection",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertLogs("app.main", level="CRITICAL") as log_cm:
                try:
                    create_app_context(":memory:")
                except RuntimeError:
                    pass
            self.assertTrue(
                any("Failed to initialize database" in m for m in log_cm.output)
            )


# ============================================================================
# 8. get_connection 路径与日志测试
# ============================================================================


class GetConnectionPathTests(unittest.TestCase):
    """get_connection() 路径策略与父目录创建。"""

    def test_parent_directory_auto_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "subdir" / "nested" / "test.db")
            conn = get_connection(db_path, auto_init=True)
            # 父目录应已创建
            self.assertTrue(Path(db_path).parent.exists())
            conn.close()

    def test_memory_db_skips_parent_creation(self) -> None:
        """':memory:' 数据库不触发父目录创建逻辑。"""
        conn = get_connection(":memory:", auto_init=True)
        self.assertIsInstance(conn, sqlite3.Connection)
        conn.close()

    def test_env_var_db_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "env_test.db")
            with patch.dict(os.environ, {"PAPER_RESEARCH_DB_PATH": db_path}):
                conn = get_connection(auto_init=True)
                conn.close()
                # 数据库文件应存在（schema 初始化会创建）
                self.assertTrue(Path(db_path).exists())

    def test_unwritable_parent_raises(self) -> None:
        """父目录不可写时抛出 OSError。"""
        if os.name == "nt":
            self.skipTest("Permission model differs on Windows")
        with patch("pathlib.Path.mkdir", side_effect=OSError("Permission denied")):
            with self.assertRaises(OSError):
                get_connection("/root/forbidden/test.db", auto_init=False)


# ============================================================================
# 9. Smoke 级集成测试
# ============================================================================


class SmokeIntegrationTests(unittest.TestCase):
    """端到端 smoke：配置 → DB → context → 关闭。"""

    def test_full_lifecycle_with_temp_db(self) -> None:
        """使用临时数据库完成完整的创建→查询→关闭生命周期。"""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "smoke.db")
            ctx = create_app_context(db_path)

            # 验证 service 装配完整
            self.assertIsNotNone(ctx.paper_query_service)
            self.assertIsNotNone(ctx.settings_service)
            self.assertIsNotNone(ctx.status_service)
            self.assertIsNotNone(ctx.subscription_service)
            self.assertIsNotNone(ctx.sync_service)
            self.assertIsNotNone(ctx.scheduler)

            # 验证 schema 表存在
            tables = ctx.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            table_names = {t[0] for t in tables}
            self.assertIn("papers", table_names)
            self.assertIn("subscriptions", table_names)
            self.assertIn("paper_statuses", table_names)
            self.assertIn("sync_runs", table_names)
            self.assertIn("app_settings", table_names)
            self.assertIn("paper_versions", table_names)

            # 关闭
            ctx.close()
            self.assertTrue(ctx.closed)

    def test_config_and_context_integration(self) -> None:
        """AppRuntimeConfig → create_app_context 集成路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            config = AppRuntimeConfig.create(is_dev_mode=True, db_path=str(Path(tmp) / "int.db"))
            ctx = create_app_context(config.db_path)
            self.assertIsNotNone(ctx)
            ctx.close()


# ============================================================================
# 10. 导入与模块存在性测试
# ============================================================================


class Slice11ImportExportTests(unittest.TestCase):
    """Slice 11 新增模块的导入和基本可用性。"""

    def test_runtime_paths_importable(self) -> None:
        from app.infrastructure.config import runtime_paths

        self.assertIsNotNone(runtime_paths)

    def test_app_config_importable(self) -> None:
        from app.infrastructure.config import app_config

        self.assertIsNotNone(app_config)

    def test_logging_setup_importable(self) -> None:
        from app.infrastructure.logging import setup as logging_setup

        self.assertIsNotNone(logging_setup)

    def test_startup_check_result_creatable(self) -> None:
        result = StartupCheckResult(ok=True, diagnostics={"key": "val"})
        self.assertTrue(result.ok)
        self.assertDictEqual(result.diagnostics, {"key": "val"})

    def test_app_runtime_config_creatable(self) -> None:
        config = AppRuntimeConfig.create(is_dev_mode=True, db_path=":memory:")
        self.assertIsInstance(config, AppRuntimeConfig)
        self.assertEqual(config.db_path, ":memory:")

    def test_runtime_paths_creatable(self) -> None:
        paths = RuntimePaths.for_app(is_dev_mode=True)
        self.assertEqual(paths.data_dir, Path.cwd() / "runtime")
