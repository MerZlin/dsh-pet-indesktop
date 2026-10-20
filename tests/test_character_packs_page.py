# -*- coding: utf-8 -*-
"""「角色与素材」页（`pet/character_packs_page.py`）的回归测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pet import character_packs_page as page


def _write_pack(root: Path, cid: str, folder: str, *, clips=3, broken=False) -> Path:
    d = root / cid / folder
    d.mkdir(parents=True)
    if broken:
        (d / "character.json").write_text("{ not json", encoding="utf-8")
        return d
    doc = {
        "schema": 1, "id": cid, "name": "深深", "canvas": [640, 360], "fps": 24.0,
        "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["core"],
        "clips": [{"name": f"c{i}", "category": "idle", "dir": f"anim/idle/c{i}",
                   "frames": 4, "key_every": 12, "part": "core",
                   "content_sha": "a" * 64, "bytes": 1000} for i in range(clips)],
    }
    (d / "character.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return d


def test_installed_packs_empty(tmp_path):
    assert page.installed_packs(tmp_path) == []


def test_installed_packs_reads_summary(tmp_path):
    _write_pack(tmp_path, "shenshen", "2.0.0-base-640x360", clips=3)
    rows = page.installed_packs(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "shenshen"
    assert r["name"] == "深深"
    assert r["canvas"] == [640, 360]
    assert r["clips"] == 3
    assert r["parts"] == {"core": 3}
    assert r["bytes"] == 3000


def test_installed_packs_skips_broken_index(tmp_path):
    _write_pack(tmp_path, "shenshen", "1.0.0-base-640x360", broken=True)
    _write_pack(tmp_path, "other", "1.0.0-base-640x360", clips=2)
    rows = page.installed_packs(tmp_path)
    assert [r["id"] for r in rows] == ["other"], "坏包只跳过自己，不掀翻列表"


def test_installed_packs_sorts_by_id_then_folder(tmp_path):
    _write_pack(tmp_path, "b", "2.0.0-base-640x360")
    _write_pack(tmp_path, "a", "3.0.0-base-640x360")
    _write_pack(tmp_path, "a", "1.0.0-base-640x360")
    assert [(r["id"], r["folder"]) for r in page.installed_packs(tmp_path)] == [
        ("a", "1.0.0-base-640x360"), ("a", "3.0.0-base-640x360"),
        ("b", "2.0.0-base-640x360")]


def test_characters_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PET_PACK_DIR", str(tmp_path))
    assert page.characters_root() == tmp_path


@pytest.fixture()
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    return app


def test_build_content_without_registry_shows_hint(tmp_path, monkeypatch, qapp):
    """默认清单地址始终存在（jsdelivr 镜像组），页面先显示后台拉取占位，不许空白。"""
    monkeypatch.setenv("PET_PACK_DIR", str(tmp_path))
    monkeypatch.delenv("PET_PACK_REGISTRY", raising=False)
    _write_pack(tmp_path, "shenshen", "2.0.0-base-640x360")
    from PySide6.QtWidgets import QLabel

    w = page.build_content(None)
    try:
        texts = [lb.text() for lb in w.findChildren(QLabel)]
        # 文案按用户视角写（不含 JSON/环境变量名这类实现术语，见设置页文案检查）
        assert any("正在拉取角色包列表" in t for t in texts), texts[:10]
    finally:
        w.deleteLater()
        qapp.processEvents()


def test_registry_fetch_is_size_capped(monkeypatch):
    """注册表拉取必须有体积上限（终审 B12：无上限 read 会被坏源拖死）。"""
    import io

    from pet import character_packs_page as page
    from pet import pack_download as pd

    ok_url = "https://cdn.jsdelivr.net/gh/x/y@main/packs-index.json"

    class _Resp:
        def __init__(self, body):
            self._body = body

        def read(self, n=None):
            return self._body if n is None else self._body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    big = b"{" + b"0" * (page.MAX_REGISTRY_BYTES + 10)
    monkeypatch.setattr(pd, "trusted_urlopen", lambda *a, **k: _Resp(big))
    try:
        page._fetch_registry_text(ok_url)
    except ValueError as exc:
        assert "上限" in str(exc)
    else:
        raise AssertionError("超限的注册表必须被拒")

    monkeypatch.setattr(pd, "trusted_urlopen", lambda *a, **k: _Resp(b'{"schema": 1, "packs": []}'))
    assert page._fetch_registry_text(ok_url).startswith("{")


def test_registry_fetch_rejects_untrusted_url():
    """清单是整个信任链的根：非 https / 非白名单主机的地址不许 urlopen（终审）。"""
    from pet import character_packs_page as page

    for bad in ("https://example.invalid/reg.json", "http://cdn.jsdelivr.net/x"):
        try:
            page._fetch_registry_text(bad)
        except ValueError as exc:
            assert "白名单" in str(exc)
        else:
            raise AssertionError(f"不可信地址必须被拒: {bad}")


def test_build_content_never_blocks_gui_on_registry(monkeypatch):
    """构造设置页不许同步联网（终审 B12）：慢源只能影响"可下载"区块。"""
    import time

    from pet import character_packs_page as page

    monkeypatch.setenv(page.ENV_REGISTRY, "https://example.invalid/reg.json")

    def slow(url):
        time.sleep(1.5)
        return []

    monkeypatch.setattr(page, "_registry_parts", slow)
    started = time.perf_counter()
    widget = page.build_content(None)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.5, f"构造时被网络阻塞了 {elapsed:.2f}s"
    widget.deleteLater()


def test_use_pack_activates_restart_scoped(tmp_path, monkeypatch):
    """「使用这套」= 写 active.json（重启后生效，无跨进程通知）；core 不闭环的版本被拒。"""
    from pet import character_pack as cp
    from pet import character_packs_page as page

    chars = tmp_path / "chars"
    good = chars / "shenshen" / "1.0.0-base-640x360"
    # core 闭包五类各一段（激活闸门要求必需动作闭包）
    clips = []
    for name, cat in (("待机", "idle"), ("走路", "move"), ("转身", "turn"),
                      ("点击", "click"), ("拖拽", "drag")):
        d = good / "anim" / cat / name
        d.mkdir(parents=True)
        (d / "f_0001.webp").write_bytes(b"x")
        (d / "meta.json").write_text(
            __import__('json').dumps({'fps': 24.0, 'frames': 1, 'content_sha': 'a' * 64,
                                      'encoder': __import__('pet.frameseq_provision', fromlist=['x']).ENCODER_DESC}),
            encoding="utf-8")
        clips.append({"name": name, "category": cat, "dir": f"anim/{cat}/{name}",
                      "frames": 1, "key_every": 12, "part": "core",
                      "content_sha": "a" * 64, "bytes": 1})
    (good / "character.json").write_text(json.dumps({
        "schema": 1, "id": "shenshen", "name": "深深", "canvas": [640, 360],
        "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["core"], "clips": clips}, ensure_ascii=False), encoding="utf-8")

    assert page.use_pack(good) is True, "完整包应可激活"
    assert (good.parent / "active.json").is_file(), "指针必须写出来"

    bad = chars / "shenshen" / "2.0.0-base-640x360"
    bd = bad / "anim" / "random" / "新段"
    bd.mkdir(parents=True)
    (bd / "f_0001.webp").write_bytes(b"x")
    (bd / "meta.json").write_text('{"fps": 24.0, "frames": 1}', encoding="utf-8")
    (bad / "character.json").write_text(json.dumps({
        "schema": 1, "id": "shenshen", "name": "深深", "canvas": [640, 360],
        "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["extra"],
        "clips": [{"name": "新段", "category": "random", "dir": "anim/random/新段",
                   "frames": 1, "key_every": 12, "part": "extra",
                   "content_sha": "e" * 64, "bytes": 1}]}, ensure_ascii=False), encoding="utf-8")
    assert page.use_pack(bad) is False, "只装 extra 的版本不许被激活"


def test_installed_packs_marks_active_one(tmp_path):
    """列表要能指出"当前使用"的那套——由 active.json 指针决定，不是版本号。"""
    import json as _json

    from pet import character_pack as cp
    from pet import character_packs_page as page

    root = tmp_path / "characters"
    for ver, name in (("1.0.0-base-640x360", "旧"), ("2.0.0-base-640x360", "新")):
        # core 闭包五类各一段（激活闸门要求必需动作闭包）
        clips = []
        for cname, cat in (("待机", "idle"), ("走路", "move"), ("转身", "turn"),
                           ("点击", "click"), ("拖拽", "drag")):
            d = root / "shenshen" / ver / "anim" / cat / cname
            d.mkdir(parents=True)
            (d / "f_0001.webp").write_bytes(b"x")
            (d / "meta.json").write_text(_json.dumps(
                {"fps": 24.0, "frames": 1, "content_sha": "a" * 64,
                 "encoder": __import__('pet.frameseq_provision', fromlist=['x']).ENCODER_DESC}),
                encoding="utf-8")
            clips.append({"name": cname, "category": cat, "dir": f"anim/{cat}/{cname}",
                          "frames": 1, "key_every": 12, "part": "core",
                          "content_sha": "a" * 64, "bytes": 1})
        (root / "shenshen" / ver / "character.json").write_text(_json.dumps({
            "schema": 1, "id": "shenshen", "name": name, "canvas": [640, 360],
            "fps": 24.0, "pipeline": "unblend-white-v6",
            "format": "lossless-frame-v1", "parts": ["core"],
            "clips": clips}), encoding="utf-8")

    rows = page.installed_packs(root)
    assert all(r["active"] is False for r in rows), "没有指针时谁都不是活动包"
    assert cp.set_active(root / "shenshen" / "1.0.0-base-640x360") is True
    rows = page.installed_packs(root)
    active = [r["folder"] for r in rows if r["active"]]
    assert active == ["1.0.0-base-640x360"], "活动包应是指针指向的旧版本，不是最高版"


def test_download_section_loaded_builds_rows_without_crash(tmp_path, monkeypatch, qapp):
    """清单拉取成功后的回调必须真的铺出行（六轮抓到的 QObject→QWidget parent 崩溃）。"""
    from pet import character_packs_page as page
    from pet import pack_download as pd

    monkeypatch.setenv("PET_PACK_DIR", str(tmp_path))
    pack = pd.Pack(id="shenshen", name="深深", variant="base", version="2.0.0",
                   canvas=(640, 360), pipeline="unblend-white-v6",
                   format="lossless-frame-v1", app_min="0",
                   parts=(pd.Part(name="core", urls=("https://cdn.jsdelivr.net/x",),
                                  bytes=1024, sha256="a" * 64),))
    from PySide6.QtCore import QObject

    w = page.build_content(None)
    try:
        section = next(c for c in w.findChildren(QObject) if isinstance(c, page._DownloadSection))
        section._on_loaded([(pack, pack.parts[0])])
        qapp.processEvents()
        from PySide6.QtWidgets import QLabel, QPushButton
        texts = [lb.text() for lb in w.findChildren(QLabel)]
        # 一包一行：画质档名 + 折叠分件明细（用户拍板形态）
        assert any("标清角色包" in t for t in texts), texts[:8]
        from pet.settings_widgets import CollapsibleGroup
        groups = w.findChildren(CollapsibleGroup)
        assert any(g.toggle.text().startswith("分件明细") for g in groups), [
            g.toggle.text() for g in groups]
        buttons = [b.text() for b in w.findChildren(QPushButton)]
        assert any("一键下载全部" in b for b in buttons), buttons[:8]
        # 离线分支同样不许崩
        section._on_failed("boom")
        qapp.processEvents()
        texts = [lb.text() for lb in w.findChildren(QLabel)]
        assert any("暂时拉不到素材清单" in t for t in texts), texts[:8]
    finally:
        w.deleteLater()
        qapp.processEvents()


def test_installed_part_names_requires_clips_on_disk(tmp_path):
    """索引 parts 还在、素材目录被删 → 按未安装处理（重下入口不许被封死，六轮）。"""
    import json as _json

    from pet import character_packs_page as page

    target = tmp_path / "pack"
    d = target / "anim" / "idle" / "c"
    d.mkdir(parents=True)
    (target / "character.json").write_text(_json.dumps({
        "schema": 1, "id": "shenshen", "canvas": [640, 360], "fps": 24.0,
        "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["core"],
        "clips": [{"name": "c", "category": "idle", "dir": "anim/idle/c",
                   "frames": 1, "part": "core", "content_sha": "a" * 64}]}),
        encoding="utf-8")
    assert page._installed_part_names(target) == {"core"}
    import shutil
    shutil.rmtree(d)
    assert page._installed_part_names(target) == set(), "缺素材不许再显示已安装"


def test_parse_part_filename():
    from pet.character_packs_page import _parse_part_filename
    from pathlib import Path as _P

    assert _parse_part_filename(_P("shenshen-2.0.0-base-640x360.extra3.zip")) == \
        ("shenshen", "2.0.0-base-640x360")
    assert _parse_part_filename(_P("shenshen-3.1.0-winter-1280x720.core.zip")) == \
        ("shenshen", "3.1.0-winter-1280x720")
    for bad in ("random.zip", "shenshen.zip", "a-b-c.zip.part",
                "shenshen-2.0.0-base-640x360.extra3.zip.bak"):
        assert _parse_part_filename(_P(bad)) is None, bad


def test_registry_falls_through_on_structurally_invalid_json(monkeypatch):
    """第一个源返回合法 JSON 但清单结构不合法 → 必须继续试下一个源（P2）。"""
    from pet import character_packs_page as page

    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "bad" in url:
            return '{"schema": 99, "packs": []}'          # 合法 JSON，结构不合法
        return json.dumps({"schema": 1, "packs": [{
            "id": "shenshen", "variant": "base", "version": "2.0.0",
            "canvas": [640, 360], "pipeline": "unblend-white-v6",
            "format": "lossless-frame-v1",
            "parts": [{"name": "core", "urls": ["https://cdn.jsdelivr.net/x"],
                       "bytes": 10, "sha256": "a" * 64}]}]})

    monkeypatch.setattr(page, "_fetch_registry_text", fake_fetch)
    out = page._registry_parts(["https://cdn.jsdelivr.net/bad",
                                "https://cdn.jsdelivr.net/good"])
    assert len(out) == 1, "好源必须被用到"
    assert len(calls) == 2, "结构不合法不许就此放弃镜像组"
