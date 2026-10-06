"""Public contracts of the optional Codex companion edition."""

import importlib
import json
import re
import time
from pathlib import Path

import pytest


def test_extension_settings_persist_and_recover(tmp_path):
    from pet.config import Config

    cfg = Config(base=tmp_path)
    assert cfg.get("ui_language") == "zh_CN"
    assert cfg.get("codex_usage_enabled") is False
    for key in ("settings_ui_style", "menu_ui_style", "bubble_ui_style", "quota_ui_style", "quick_chat_ui_style", "chat_window_ui_style"):
        cfg.set(key, "glass")
    cfg.set("ui_language", "en")
    assert cfg.save()
    loaded = Config(base=tmp_path)
    assert loaded.get("ui_language") == "en"
    assert loaded.get("chat_window_ui_style") == "glass"
    data = json.loads(loaded.path.read_text(encoding="utf-8"))
    data.update(ui_language="invalid", codex_usage_enabled="false", chat_window_ui_style=[])
    loaded.path.write_text(json.dumps(data), encoding="utf-8")
    loaded.reload()
    assert loaded.get("ui_language") == "zh_CN"
    assert loaded.get("codex_usage_enabled") is False
    assert loaded.get("chat_window_ui_style") == "dark"


@pytest.mark.parametrize("used,remaining", [(5, 95), (105, 0), (-5, 100), (None, None), (True, None)])
def test_remaining_quota_is_not_generation(used, remaining):
    from pet.codex_bridge.usage import normalize_usage

    value = normalize_usage({"rateLimits": {"primary": {"usedPercent": used, "windowDurationMins": 300}}}, now=100)
    assert value["windows"][0]["remainingPercent"] == remaining
    assert value["updatedAt"] == 100


def test_public_modules_are_portable():
    for name in ("codex_quota_state", "codex_usage_history", "codex_work_reader", "codex_companion", "codex_bridge.server"):
        importlib.import_module("pet." + name)


def test_languages_preserve_paths_and_editable_content():
    from pet.language_ui import text

    assert text("保存", "zh_TW") == "儲存"
    assert text("C:/用户/保存.json", "zh_TW") == "C:/用户/保存.json"
    assert text("Codex 剩餘用量", "en") == "Codex remaining quota"


def test_expired_visual_preview_does_not_apply(tmp_path):
    from pet.ui_preview import active_values

    path = tmp_path / "preview.json"
    path.write_text(json.dumps({"updatedAt": 0, "values": {"chat_window_ui_style": "glass"}}), encoding="utf-8")
    assert active_values(path) == {}


def test_extension_has_no_binary_or_user_profile_dependencies():
    root = Path(__file__).resolve().parents[1]
    sources = [root / "pet" / "codex_companion.py", *(root / "pet" / "codex_bridge").glob("*.py")]
    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert "marshal.loads" not in source
        assert not re.search(r"[A-Za-z]:[\\/]+Users[\\/]+", source)
        assert not re.search(r'''["'][A-Za-z]:[\\/]''', source)


def _snapshot(stamp, used, reset=None, minutes=300):
    return {
        "ok": True,
        "updatedAt": stamp,
        "windows": [{"limitId": "codex", "window": "primary", "remainingPercent": 100 - used, "windowDurationMins": minutes, "resetsAt": reset}],
    }


def test_history_starts_at_consumption_and_preserves_gaps(tmp_path):
    from pet.codex_usage_history import UsageHistory

    now = time.time()
    history = UsageHistory(tmp_path / "history.json")
    history.observe(_snapshot(now - 360, 0), now=now)
    history.observe(_snapshot(now - 300, 5), now=now)
    history.observe(_snapshot(now - 240, 10), now=now)
    history.observe(_snapshot(now, 20), now=now)
    result = history.five_hour({"limitId": "codex", "window": "primary"}, now=now)
    assert result["start"] == now - 300
    assert result["end"] == now - 300 + 18000
    assert len(result["segments"]) == 2
    assert UsageHistory(history.path).samples == history.samples
    assert not history.observe(_snapshot(now, 20), now=now)


def test_missing_daily_history_is_unknown(tmp_path):
    from pet.codex_usage_history import UsageHistory

    history = UsageHistory(tmp_path / "history.json")
    rows = history.daily({"limitId": "codex", "window": "primary"})
    assert len(rows) == 7
    assert all(row["value"] is None for row in rows)


def test_quota_thresholds_survive_restart_and_reset(tmp_path):
    from pet.codex_quota_state import QuotaThresholdState

    path = tmp_path / "thresholds.json"
    now = time.time()
    state = QuotaThresholdState(path)
    state.observe(_snapshot(now, 30, now + 100))
    assert state.pending[0]["thresholds"] == [75]
    state.acknowledge(1)
    state = QuotaThresholdState(path)
    state.observe(_snapshot(now + 1, 40, now + 100))
    assert not state.pending
    state.observe(_snapshot(now + 2, 80, now + 100))
    assert state.pending[0]["thresholds"] == [50, 25]
    state.acknowledge(1)
    state.observe(_snapshot(now + 101, 30, now + 18100))
    assert state.pending[0]["thresholds"] == [75]


def test_viewed_notice_is_removed_but_new_state_reappears(tmp_path):
    from pet.codex_work_reader import NoticeLedger

    row = {"threadId": "example", "state": "completed", "signature": "turn1:completed"}
    ledger = NoticeLedger(tmp_path / "seen.json")
    assert ledger.visible([row]) == [row]
    assert ledger.mark([row])
    assert not NoticeLedger(ledger.path).visible([row])
    row = dict(row, signature="turn2:completed")
    assert ledger.visible([row]) == [row]


def test_hook_setup_preserves_handlers_and_private_content(tmp_path):
    from pet.codex_bridge.hooks import merge, groups
    from pet.codex_bridge.hook import record

    existing = {"unrelatedSetting": 1, "hooks": {"Stop": [{"hooks": [{"command": "existing", "type": "command"}]}]}}
    additions = groups(tmp_path / "events")
    result = merge(existing, additions)
    assert result["hooks"]["Stop"][0] == existing["hooks"]["Stop"][0]
    assert merge(result, additions) == result
    assert existing["hooks"]["Stop"] != result["hooks"]["Stop"]
    record({"hook_event_name": "PermissionRequest", "session_id": "example", "prompt": "private user text", "tool_input": "private tool arguments"}, tmp_path)
    saved = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert set(saved) == {"state", "ts", "event", "sessionId"}
    assert saved["state"] == "attention"


def test_model_selection_uses_catalog_and_rejects_unknown():
    from pet.codex_bridge.__main__ import select_model

    catalog = [{"model": "a"}, {"model": "b", "isDefault": True}]
    assert select_model(catalog) == "b"
    assert select_model(catalog, "a") == "a"
    with pytest.raises(ValueError):
        select_model(catalog, "unknown")


def test_chat_translation_rejects_remote_images_and_tools():
    from pet.codex_bridge.server import translate
    from pet.codex_bridge.rpc import prepare

    body = translate({"messages": [{"role": "user", "content": "hello"}]})
    assert prepare(body)[1][0]["type"] == "text"
    with pytest.raises(ValueError):
        translate({"messages": [{"role": "user", "content": "hello"}], "tools": [{}]})
    with pytest.raises(ValueError):
        prepare({"input": [{"role": "user", "content": [{"type": "input_image", "image_url": "https://example.com/image.png"}]}]})


def test_source_ui_profile_in_separate_process(tmp_path):
    """Do not modify the class surfaces used by the rest of the native suite."""
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    result = subprocess.run(
        [sys.executable, "-X", "utf8", str(root / "scripts/probe_codex_companion.py"), "--output", str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr
    value = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert value["sourceQtSmoke"] and value["modelTurns"] == 0
    assert len(value["settingsStates"]) == 18
