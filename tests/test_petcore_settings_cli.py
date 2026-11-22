# -*- coding: utf-8 -*-
"""`petcore settings`：全键 schema、只读状态键、受控写入与槽位路由。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from petcore.config import DEFAULTS, GLOBAL_CONFIG_KEYS
from petcore.settings_cli import main
from petcore.settings_schema import SETTINGS, spec_for


def _run(capsys, *args: str) -> tuple[int, dict]:
    code = main(list(args))
    text = capsys.readouterr().out.strip()
    return code, json.loads(text) if text else {}


def test_schema_covers_every_default_and_scope() -> None:
    assert set(SETTINGS) == set(DEFAULTS)
    for key, spec in SETTINGS.items():
        assert spec.scope == ("global" if key in GLOBAL_CONFIG_KEYS else "pet")
        assert spec.type
        assert spec.key == key
    assert spec_for("drag_lean_strength").label_zh == "倾斜强度"
    assert spec_for("chat_layout").choices == ("desktop", "phone")


def test_numeric_ranges_match_the_config_normalizer(tmp_path: Path) -> None:
    from petcore.config import Config as _Config

    config = _Config(tmp_path)
    for key, spec in SETTINGS.items():
        if (
            spec.kind != "preference"
            or spec.type not in {"int", "float"}
            or spec.min is None
            or spec.max is None
        ):
            continue
        config.set(key, spec.min - 1)
        assert float(config.get(key)) == pytest.approx(float(spec.min)), key
        config.set(key, spec.max + 1)
        assert float(config.get(key)) == pytest.approx(float(spec.max)), key


def test_list_is_machine_readable_and_marks_state_keys(capsys, tmp_path: Path) -> None:
    code, payload = _run(
        capsys, "list", "--root", str(tmp_path), "--writable", "--query", "倾斜"
    )
    assert code == 0
    keys = {item["key"] for item in payload["settings"]}
    assert "drag_lean_strength" in keys
    assert "active_pets" not in keys, "状态键不出现在可写列表"


def test_list_accepts_the_documented_json_flag(capsys, tmp_path: Path) -> None:
    code, payload = _run(capsys, "list", "--json", "--root", str(tmp_path))
    assert code == 0
    assert payload["count"] == len(DEFAULTS)


def test_describe_known_and_unknown(capsys, tmp_path: Path) -> None:
    code, payload = _run(
        capsys, "describe", "drag_lean_strength", "--root", str(tmp_path)
    )
    assert code == 0
    assert payload["setting"]["min"] == 0.0
    assert payload["setting"]["max"] == 100.0

    code, payload = _run(capsys, "describe", "no-such-key", "--root", str(tmp_path))
    assert code == 1
    assert payload["ok"] is False


def test_set_rejects_out_of_range_without_force(capsys, tmp_path: Path) -> None:
    code, payload = _run(capsys, "set", "scale", "9", "--root", str(tmp_path))
    assert code == 1
    assert payload["ok"] is False
    assert "最大值" in payload["error"]


def test_set_force_clamps_and_get_reads_back(capsys, tmp_path: Path) -> None:
    code, payload = _run(
        capsys, "set", "scale", "9", "--force", "--root", str(tmp_path)
    )
    assert code == 0
    assert payload["applied"] == 3.0
    assert payload["clamped"] is True

    code, payload = _run(capsys, "get", "scale", "--root", str(tmp_path))
    assert code == 0
    assert payload["value"] == 3.0


def test_state_keys_are_read_only(capsys, tmp_path: Path) -> None:
    code, payload = _run(
        capsys, "set", "active_pets", "[1]", "--root", str(tmp_path)
    )
    assert code == 1
    assert "只读" in payload["error"]


def test_pet_keys_route_to_slot_files(capsys, tmp_path: Path) -> None:
    code, payload = _run(
        capsys,
        "set",
        "drag_lean_strength",
        "55",
        "--slot",
        "1",
        "--root",
        str(tmp_path),
    )
    assert code == 0
    assert payload["file"].endswith("config-pet-1.json")
    assert (tmp_path / "config-pet-1.json").exists()
    raw = json.loads((tmp_path / "config-pet-1.json").read_text(encoding="utf-8"))
    assert raw["drag_lean_strength"] == 55


def test_global_keys_ignore_the_slot(capsys, tmp_path: Path) -> None:
    code, payload = _run(
        capsys,
        "set",
        "chat_layout",
        "phone",
        "--slot",
        "2",
        "--root",
        str(tmp_path),
    )
    assert code == 0
    assert payload["file"].endswith("config.json")
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert raw["chat_layout"] == "phone"


def test_export_writes_a_schema_file(capsys, tmp_path: Path) -> None:
    target = tmp_path / "schema.json"
    code, payload = _run(
        capsys, "export", str(target), "--root", str(tmp_path)
    )
    assert code == 0
    assert payload["ok"] is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["count"] == len(DEFAULTS)
    assert any(item["key"] == "drag_lean_strength" for item in data["settings"])


def test_mirror_policy_and_custom_prefix_schema_and_cli_round_trip(capsys, tmp_path: Path) -> None:
    from petcore.net.github_mirrors import ALL_MIRROR_POLICIES

    policy_spec = spec_for("pack_mirror_policy")
    custom_spec = spec_for("pack_mirror_custom_url")
    assert policy_spec is not None and policy_spec.choices == ALL_MIRROR_POLICIES
    assert custom_spec is not None and custom_spec.scope == "global" and custom_spec.type == "str"

    code, payload = _run(
        capsys, "set", "pack_mirror_policy", "gh-proxy", "--root", str(tmp_path)
    )
    assert code == 0 and payload["applied"] == "gh-proxy"

    code, payload = _run(
        capsys,
        "set",
        "pack_mirror_custom_url",
        "https://mirror.example.com/proxy",
        "--root",
        str(tmp_path),
    )
    assert code == 0 and payload["applied"] == "https://mirror.example.com/proxy/"
