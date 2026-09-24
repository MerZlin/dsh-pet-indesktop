"""资源型 DLC 的路径约定。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def bundled_content_root() -> Path:
    """仓库或冻结包内的 bundled content 根目录。"""
    return Path(__file__).resolve().parent.parent.parent / "content"


def _platform_base() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home())
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))


def app_data_root(app_dir_name: str | None = None) -> Path:
    if app_dir_name is None:
        from .. import config

        app_dir_name = config.APP_DIR_NAME
    return _platform_base() / app_dir_name


def installed_content_root(app_dir_name: str | None = None) -> Path:
    return app_data_root(app_dir_name) / "content"


def installed_characters_root(app_dir_name: str | None = None) -> Path:
    return installed_content_root(app_dir_name) / "characters"


def staging_root(app_dir_name: str | None = None) -> Path:
    return installed_content_root(app_dir_name) / "staging"


def cache_root(app_dir_name: str | None = None) -> Path:
    return installed_content_root(app_dir_name) / "cache"


def logs_root(app_dir_name: str | None = None) -> Path:
    return installed_content_root(app_dir_name) / "logs"
