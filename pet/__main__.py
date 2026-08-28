# -*- coding: utf-8 -*-
"""python -m pet 入口。"""

import sys


def _main() -> int:
    # 卸载清理走无 GUI 路径：不导入 pet.app（避免拉起 QApplication/事件循环）。
    if "--uninstall-cleanup" in sys.argv:
        from .uninstall_cleanup import run_uninstall_cleanup
        run_uninstall_cleanup()
        return 0
    from .app import main as app_main
    return app_main()


if __name__ == "__main__":
    sys.exit(_main())
