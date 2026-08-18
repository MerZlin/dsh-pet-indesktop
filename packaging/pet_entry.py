# -*- coding: utf-8 -*-
"""
PyInstaller 打包入口。

不能直接用 pet/__main__.py（其中的相对导入 `from .app import main`
在 PyInstaller 冻结模式下会解析失败，导致依赖收集为空）。

构建命令（项目根目录）：
    python -m PyInstaller --noconfirm --clean --onefile --windowed ^
        --name dsh-pet-standalone-webm ^
        --collect-all imageio_ffmpeg ^
        --add-data "assets/videos;assets/videos" ^
        packaging/pet_entry.py
"""

import sys

from pet.app import main

if __name__ == "__main__":
    sys.exit(main())