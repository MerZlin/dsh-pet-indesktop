# -*- coding: utf-8 -*-
"""python -m pet 入口。"""

import sys
from pathlib import Path
from typing import Any


def _chat_available() -> bool:
    """打包变体是否带 AI 聊天（no-chat 变体 excludes=['pet.chat']）。

    只做 find_spec 探测，不 import pet.chat——设置进程启动不该白付聊天模块的
    导入成本；真的缺模块时 include_ai 传 False，避免设置页在构造期炸掉。
    """
    import importlib.util

    try:
        return importlib.util.find_spec("pet.chat") is not None
    except (ImportError, ValueError):
        return False


def _exec_settings(app, config, *, include_ai: bool = True, initial_page: str = "", feature_lease=None) -> int:
    """独立设置进程主体：锁 + 独立对话框 + 事件循环。

    单独拆一层是为了让测试能注入最小 QApplication/临时 Config，不必真的跑
    一个阻塞的 app.exec() 或用真实用户配置目录。
    """
    import logging

    from PySide6.QtCore import QLockFile

    # 单实例：设置进程自己持有 settings.lock 直到退出（QLockFile 在解锁/析构时
    # 删除锁文件；崩溃残留的锁由 QLockFile 按 pid 存活判定为陈旧后接管）。
    # 主进程经同一把锁判断"是否已有设置页开着"，已持有则不再拉起。
    lock = QLockFile(str(config.dir / "settings.lock"))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(0):
        logging.getLogger(__name__).info("已有设置进程持有 settings.lock，本次退出")
        return 0
    from .modern_settings_dialog import ModernSettingsDialog

    # parent=None + standalone=True：没有桌宠窗口可依附，试听/避让由
    # pet.settings_standalone 提供进程内最小宿主。
    dialog_kwargs: dict[str, Any] = {"include_ai": include_ai, "standalone": True}
    if initial_page:
        dialog_kwargs["initial_page"] = initial_page
    dialog = ModernSettingsDialog(config, parent=None, **dialog_kwargs)
    dialog.finished.connect(lambda _result: app.quit())
    dialog.show()
    try:
        return app.exec()
    finally:
        if feature_lease is not None:
            feature_lease.close()
        lock.unlock()


def _settings_instance_id(argv) -> str:
    """从 --settings 附加参数里取 instance_id（缺省空 = 主配置）。

    进程内多窗（experimental_single_process_spawn）时第二窗的 config 是
    config-slot-N.json，而进程级 DSH_PET_INSTANCE 仍是主窗的；主进程会显式
    追加 --instance slot-N 把子进程指到正确的那份配置。
    """
    try:
        index = argv.index("--instance")
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return str(argv[index + 1] or "").strip()


def _settings_page(argv) -> str:
    try:
        index = argv.index("--settings-page")
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return str(argv[index + 1] or "").strip()


def _run_settings(config=None, *, feature_lease=None) -> int:
    """--settings：设置页独立进程（不导入 pet.app）。

    参照 --uninstall-cleanup 的免 GUI 分流范式，但设置页自身要 GUI：只拉起最小
    QApplication + Config + ModernSettingsDialog。**严禁**导入 pet.app——那会连带
    载入素材库/ffmpeg/托盘/灵动岛，独立进程省内存的前提（也省启动时间）就没了。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    # 设置进程只有设置窗一个窗口：关窗即退出，OS 立刻回收全部内存
    # （进程内设置页首开留下的字体/样式/模块高水位没有卸载 API，只能靠进程退出）。
    app.setQuitOnLastWindowClosed(True)
    if config is None:
        from .config import Config

        # DSH_PET_INSTANCE 由主进程经环境继承下来：子肥鱼的设置进程编辑的是
        # 它自己的 config-slot-N.json，与主进程同一份文件；多窗场景由主进程
        # 追加 --instance 显式指明。
        config = Config(instance_id=_settings_instance_id(sys.argv) or None)
    try:
        config.dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return _exec_settings(app, config, include_ai=_chat_available(), initial_page=_settings_page(sys.argv), feature_lease=feature_lease)


def _run_worker(worker_id: str | None = None) -> int:
    """Run an allow-listed worker without importing the desktop UI."""
    from .workers.lease_bootstrap import claim_worker_lease_from_environment

    try:
        claim_worker_lease_from_environment()
    except Exception:
        # Do not import the Worker implementation when the parent/child
        # handoff cannot be proven.  The parent will observe no lease-claimed
        # hello and stop the process without guessing from a timeout.
        return 78
    from .workers.worker_entry import main as worker_main

    return worker_main(worker_id)


def _worker_id(argv) -> str:
    try:
        index = argv.index("--worker")
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return str(argv[index + 1] or "").strip()


def _is_unified_frozen_core() -> bool:
    from pathlib import Path

    if not getattr(sys, "frozen", False):
        return False
    try:
        from build_variant import VARIANT
    except ImportError:
        return False
    return VARIANT == "core-webm" and Path(sys.executable).name.casefold() == "dsh-pet-core-webm.exe"


def _core_maintenance_entry() -> int:
    # Installer package activation and Core removal are both closed maintenance
    # routes. Neither starts the normal host/settings/Worker path.
    args = sys.argv[1:]
    if not _is_unified_frozen_core():
        return 64
    if args == ["--core-maintenance", "uninstall"]:
        from .core_maintenance import run_uninstall

        return run_uninstall()
    if len(args) >= 4 and args[:2] == ["--core-maintenance", "install-packages"]:
        from .core_maintenance import run_install_packages

        return run_install_packages(Path(args[2]), tuple(args[3:]))
    return 64


def _main(*, enable_chat: bool = True) -> int:
    if "--core-maintenance" in sys.argv:
        return _core_maintenance_entry()
    from .core_code_gate import CoreCodeGateError, hold_current_core_code

    try:
        hold_current_core_code()
    except CoreCodeGateError as exc:
        print("CoreCodeGate: " + str(exc), file=sys.stderr)
        return 3
    if "--import-local-data" in sys.argv:
        if sys.argv[1:] != ["--import-local-data"] or not _is_unified_frozen_core():
            return 64
        from .runtime_data_import_entry import run_data_import

        # An ordinary RuntimeLayout session would pin the very files being
        # imported. This closed UI instead retains only the removal/code gates.
        return run_data_import()
    local_intent = None
    if "--install-local-packages" in sys.argv:
        if sys.argv[1] != "--install-local-packages" or not _is_unified_frozen_core():
            return 64
        from .local_package_intents import parse_intents

        try:
            local_intent = parse_intents(sys.argv[2:])
        except ValueError:
            return 64
    from .runtime_layout import RuntimeLayoutError, initialize_for_current_build

    try:
        layout = initialize_for_current_build()
        if _is_unified_frozen_core() and layout is None:
            print("RuntimeLayout: required_layout_missing", file=sys.stderr)
            return 2
    except RuntimeLayoutError as exc:
        print("RuntimeLayout: " + str(exc), file=sys.stderr)
        return 2
    if local_intent is not None:
        from .local_package_intents import run_local_packages

        return run_local_packages(*local_intent)
    # Worker 分流必须早于 pet.app：事件采集进程不得初始化 QApplication、窗口
    # 或 Chat UI，只加载 allowlist 内的 worker 实现。
    if "--worker" in sys.argv:
        return _run_worker(_worker_id(sys.argv))
    # 卸载清理走无 GUI 路径：不导入 pet.app（避免拉起 QApplication/事件循环）。
    if "--uninstall-cleanup" in sys.argv:
        from .uninstall_cleanup import run_uninstall_cleanup

        results = run_uninstall_cleanup()
        # 关键步骤失败（值为 False）返回非零，跳过（"skipped"）或成功（True）为 0
        failed = any(v is False for v in results.values())
        return 1 if failed else 0
    # 设置页独立进程同样不导入 pet.app（模块集合是主进程的子集，见模块 docstring）。
    if "--settings" in sys.argv:
        return _run_settings()
    from .app import main as app_main

    return app_main(enable_chat=enable_chat)


if __name__ == "__main__":
    sys.exit(_main())
