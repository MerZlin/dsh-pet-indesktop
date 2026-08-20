# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口。

不能直接用 pet/__main__.py（其中的相对导入 `from .app import main`
在 PyInstaller 冻结模式下会解析失败，导致依赖收集为空）。

构建命令（项目根目录）：
    python -m PyInstaller --noconfirm --clean --onefile --windowed ^
        --name dsh-pet-standalone-webm ^
        --collect-all imageio_ffmpeg ^
        --add-data "assets/characters;assets/characters" ^
        packaging/pet_entry.py

注意：`--runtime-tmpdir "."` 是按“进程当前工作目录”解析的，不是 exe 所在目录。
因此开机自启（pet/autostart.py）会先用 `start /D` 切到 exe 目录再启动；直接双击
exe 时资源管理器默认工作目录就是 exe 所在目录，行为一致。
"""

import sys

from pet.app import main

if __name__ == "__main__":
    sys.exit(main())