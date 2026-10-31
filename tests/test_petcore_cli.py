from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


def _run(args: list[str], tmp_path: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["APPDATA"] = str(tmp_path)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return subprocess.run(
        [PYTHON, "-m", "petcore", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def test_check_reports_legacy_character(tmp_path) -> None:
    result = _run(["--check"], tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["version"]
    assert report["characters"]
    character = report["characters"][0]
    assert character["id"] == "shenshen"
    assert character["animations"] > 0
    assert character["backends"]["webm"] > 0
    assert isinstance(report["diagnostics"], list)
    assert report["menu_template"] in {"json", "builtin"}
    assert isinstance(report["menu_layout_diagnostics"], list)
    assert report["self_talk_images"]["count"] >= 0
    assert report["self_talk_images"]["builtin_dir"]


def test_check_reports_the_builtin_daily_pool(tmp_path) -> None:
    result = _run(["--check", "--strict"], tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    daily = report["self_talk_daily"]
    assert daily["schema"] == 1
    assert daily["generated"]
    assert daily["unique"] >= 1000
    assert daily["count"] == daily["unique"]
    assert Path(daily["path"]).is_file()
    assert report["strict"]["problems"] == []


def test_strict_check_fails_when_the_daily_pool_is_missing(tmp_path, monkeypatch, capsys) -> None:
    """池文件缺失时运行时只会静默回落普通自语：`--strict` 必须把它变成红的。"""
    from petcore import cli, paths

    monkeypatch.setattr(paths, "builtin_features_dir", lambda: tmp_path / "features")
    code = cli.main(["--check", "--strict", "--root", str(tmp_path / "data")])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["self_talk_daily"]["unique"] == 0
    assert any(
        "daily lines missing" in item for item in report["strict"]["problems"]
    )


def test_build_script_gates_the_daily_pool() -> None:
    text = (ROOT / "scripts" / "build_petcore.ps1").read_text(encoding="utf-8")
    assert "$report.self_talk_daily.unique" in text
    assert "self_talk_daily" in text.split("ConvertFrom-Json", 1)[1]


def test_pack_legacy_and_check_mod_character(tmp_path) -> None:
    pack = tmp_path / "packs" / "character.shenshen"
    result = _run(["pack-legacy", "shenshen", "--out", str(pack)], tmp_path)
    assert result.returncode == 0, result.stderr
    assert (pack / "mod.json").exists()
    manifest = json.loads((pack / "mod.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "content"
    assert manifest["character"]["media"] == "videos"

    checked = _run(["--check", "--mods-dir", str(pack.parent)], tmp_path)
    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    ids = {character["id"] for character in report["characters"]}
    assert "shenshen" in ids
    assert any(mod["kind"] == "content" for mod in report["mods"])


def test_pack_character_cli(tmp_path) -> None:
    source = tmp_path / "source"
    (source / "videos").mkdir(parents=True)
    (source / "videos" / "idle.webm").write_bytes(b"")
    (source / "manifest.json").write_text(
        json.dumps({"name": "CLI Pet", "body_box": [212, 60, 428, 330]}),
        encoding="utf-8",
    )
    out = tmp_path / "mods" / "character.cli"
    result = _run(
        ["pack-character", str(source), "--out", str(out), "--id", "cli", "--copy"],
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((out / "mod.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "character.cli"
    assert manifest["character"]["body_box"] == [212, 60, 428, 330]


def test_mods_doctor_runs(tmp_path) -> None:
    result = _run(["mods", "doctor"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "mod dir" in result.stdout


def test_mods_subcommand_accepts_global_root(tmp_path) -> None:
    data_root = tmp_path / "data"
    result = _run(["--root", str(data_root), "mods", "doctor"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"root: {data_root}" in result.stdout


def test_update_status_is_headless_and_does_not_download(tmp_path) -> None:
    result = _run(["update", "status"], tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["state"] == "idle"
    assert report["current_version"] == report["version"]
    assert report["asset_available"] is False
    assert not (tmp_path / "updates" / "manifest.json").exists()
