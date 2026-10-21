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

# 同 pet/__main__：Qt 插件路径消毒必须最早（issue #323）
from pet.qt_plugin_sanitizer import sanitize_plugin_path as _sanitize_plugin_path

_sanitize_plugin_path()

from pet.runtime_cleanup import mark_frozen_runtime_dir

# onefile 运行目录的归属标记：本次启动标记自己，下一次启动才敢把这个目录当遗留
# 清理（没有本产品标记的 `_MEI*` 一律不删，见 pet/runtime_cleanup.py）。onedir
# 的 _MEIPASS 是安装目录里的 _internal，这里不会写任何东西。
mark_frozen_runtime_dir()

if "--uninstall-cleanup" in sys.argv:
    # 卸载清理同样必须在 import pet.app 之前分流：否则参数会当成普通启动
    #（拉起素材库/ffmpeg/托盘），清理根本不执行。入口只透传退出码。
    from pet.uninstall_cleanup import main as uninstall_cleanup_main

    sys.exit(uninstall_cleanup_main())

if "--settings" in sys.argv:
    # exe 自启动参数分流：设置页独立进程必须在 import pet.app 之前分流，否则子进程
    # 会把整个桌宠（素材库/ffmpeg/托盘/灵动岛）再拉一份，独立进程省内存的前提就没了。
    # 用绝对导入：pet/__main__.py 的相对导入在 PyInstaller 顶层入口会解析失败
    #（见本文件模块 docstring），但作为包内模块 import 进来时相对导入正常。
    from pet.__main__ import _run_settings

    sys.exit(_run_settings())

if "--ocr-worker" in sys.argv:
    # 本地 OCR 的识别子进程（切片 5）。冻结包里 exe 自己就是解释器，所以识别子进程
    # 是"本 exe 加一个内部子命令"，路由**必须**登记在这里——冻结启动走的是本文件，
    # pet/__main__.py 不参与。同样必须在 import pet.app 之前分流：这条路径要的正是
    # "别把 Qt / 素材库 / 托盘拖进来"。
    from pet.__main__ import _run_ocr_worker

    sys.exit(_run_ocr_worker())

from pet.app import main

if __name__ == "__main__":
    sys.exit(main())