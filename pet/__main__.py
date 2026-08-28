# -*- coding: utf-8 -*-
"""python -m pet 入口。"""

import sys


def _main() -> int:
    # 卸载清理走无 GUI 路径：不导入 pet.app（避免拉起 QApplication/事件循环）。
    if "--uninstall-cleanup" in sys.argv:
        from .uninstall_cleanup import run_uninstall_cleanup
        results = run_uninstall_cleanup()
        # 关键步骤失败（值为 False）返回非零，跳过（"skipped"）或成功（True）为 0
        failed = any(v is False for v in results.values())
        return 1 if failed else 0
    from .app import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(_main())
