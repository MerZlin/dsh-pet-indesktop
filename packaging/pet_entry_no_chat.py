# -*- coding: utf-8 -*-
"""PyInstaller entry for the desktop-pet build without the AI chat feature."""
import sys

if "--uninstall-cleanup" in sys.argv:
    # 卸载清理走无 GUI 路径，不导入 pet.app / 不拉起 QApplication。
    from pet.uninstall_cleanup import run_uninstall_cleanup
    run_uninstall_cleanup()
    sys.exit(0)

from pet.app import main

if __name__ == "__main__":
    sys.exit(main(enable_chat=False))
