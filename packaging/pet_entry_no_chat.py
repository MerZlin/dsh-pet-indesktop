# -*- coding: utf-8 -*-
"""PyInstaller entry for the desktop-pet build without the AI chat feature."""
import sys

if "--settings" in sys.argv:
    # 同 pet_entry.py：--settings 必须在 import pet.app 之前分流到独立设置进程。
    # no-chat 变体里 pet.chat 被 excludes 剔除，_run_settings 会经 find_spec 探测
    # 到并传 include_ai=False（不构造 AI 设置页）。
    from pet.__main__ import _run_settings

    sys.exit(_run_settings())

from pet.app import main

if __name__ == "__main__":
    sys.exit(main(enable_chat=False))
