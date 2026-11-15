# -*- coding: utf-8 -*-
"""外部改 config.json：应用内可重读；设置窗打开时延后，不冲掉编辑中的值。"""

from __future__ import annotations

import json
from pathlib import Path

from petcore.config import Config
from petcore.ui.app import CoreHost


class _FakeHost:
    _config_signature = CoreHost._config_signature
    _check_external_config = CoreHost._check_external_config

    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = Config(root)
        self.pets = None
        self.window = None
        self.settings_dialog = None
        self.applied = 0

    def apply_live_settings(self) -> None:
        self.applied += 1


def _external_write(path: Path, **values) -> None:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(values)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")


def test_config_reload_reads_the_file(tmp_path: Path) -> None:
    config = Config(tmp_path)
    config.set("scale", 0.72)
    config.save()
    _external_write(config.path, scale=1.5)
    assert config.reload() is True
    assert config.get("scale") == 1.5


def test_host_reloads_when_no_settings_dialog_is_open(tmp_path: Path) -> None:
    host = _FakeHost(tmp_path)
    host.config.set("scale", 0.72)
    host.config.save()
    host._config_watch_sig = host._config_signature()

    _external_write(host.config.path, scale=1.5)
    host._check_external_config()

    assert host.config.get("scale") == 1.5
    assert host.applied == 1


def test_host_defers_while_the_settings_dialog_is_open(tmp_path: Path) -> None:
    host = _FakeHost(tmp_path)
    host.config.set("scale", 0.72)
    host.config.save()
    host._config_watch_sig = host._config_signature()
    host.settings_dialog = object()

    _external_write(host.config.path, scale=1.5)
    host._check_external_config()

    assert host.config.get("scale") == 0.72
    assert host.applied == 0, "设置窗打开时不应用外部修改，避免覆盖用户编辑"


def test_corrupt_external_write_is_retried_on_the_next_tick(tmp_path: Path) -> None:
    host = _FakeHost(tmp_path)
    host.config.set("scale", 0.72)
    host.config.save()
    host._config_watch_sig = host._config_signature()

    host.config.path.write_text("{", encoding="utf-8")
    host._check_external_config()
    assert host.config.get("scale") == 0.72
    assert host._config_watch_sig != host._config_signature(), "失败不推进签名"

    host.config.path.write_text('{"scale": 1.5}', encoding="utf-8")
    host._check_external_config()
    assert host.config.get("scale") == 1.5


def test_host_ignores_its_own_unchanged_file(tmp_path: Path) -> None:
    host = _FakeHost(tmp_path)
    host.config.set("scale", 0.72)
    host.config.save()
    host._config_watch_sig = host._config_signature()

    host._check_external_config()

    assert host.applied == 0
