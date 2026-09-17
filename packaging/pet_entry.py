# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口。

不能直接用 pet/__main__.py（其中的相对导入 `from .app import main`
在 PyInstaller 冻结模式下会解析失败，导致依赖收集为空）。

构建命令（项目根目录）：
    python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx ^
        --name dsh-pet-standalone-webm ^
        --collect-all imageio_ffmpeg ^
        --add-data "assets/characters;assets/characters" ^
        packaging/pet_entry.py

注意：`--runtime-tmpdir "."` 是按“进程当前工作目录”解析的，不是 exe 所在目录。
因此开机自启（pet/autostart.py）会先用 `start /D` 切到 exe 目录再启动；直接双击
exe 时资源管理器默认工作目录就是 exe 所在目录，行为一致。
"""

import sys

if "--settings" in sys.argv:
    # exe 自启动参数分流：设置页独立进程必须在 import pet.app 之前分流，否则子进程
    # 会把整个桌宠（素材库/ffmpeg/托盘/灵动岛）再拉一份，独立进程省内存的前提就没了。
    # 用绝对导入：pet/__main__.py 的相对导入在 PyInstaller 顶层入口会解析失败
    #（见本文件模块 docstring），但作为包内模块 import 进来时相对导入正常。
    from pet.__main__ import _run_settings

    sys.exit(_run_settings())

from pet.app import main

if __name__ == "__main__":
    sys.exit(main())