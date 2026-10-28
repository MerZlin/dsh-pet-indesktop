# -*- coding: utf-8 -*-
"""PyInstaller entry for the desktop-pet build without the AI chat feature."""
import sys

from pet.runtime_cleanup import mark_frozen_runtime_dir

# 同 pet_entry.py：onefile 运行目录归属标记 + 卸载清理分流都必须在
# import pet.app 之前完成（否则清理参数会当成普通启动，清理不执行）。
mark_frozen_runtime_dir()

if "--uninstall-cleanup" in sys.argv:
    from pet.uninstall_cleanup import main as uninstall_cleanup_main

    sys.exit(uninstall_cleanup_main())

if "--agent-hook" in sys.argv:
    from pet.__main__ import _run_agent_hook
    sys.exit(_run_agent_hook())

if "--settings" in sys.argv:
    # 同 pet_entry.py：--settings 必须在 import pet.app 之前分流到独立设置进程。
    # no-chat 变体里 pet.chat 被 excludes 剔除，_run_settings 会经 find_spec 探测
    # 到并传 include_ai=False（不构造 AI 设置页）。
    from pet.__main__ import _run_settings

    sys.exit(_run_settings())

if "--ocr-worker" in sys.argv:
    # 同 pet_entry.py：本地 OCR 的识别子进程，必须在 import pet.app 之前分流。
    from pet.__main__ import _run_ocr_worker

    sys.exit(_run_ocr_worker())

from pet.app import main

if __name__ == "__main__":
    sys.exit(main(enable_chat=False))
