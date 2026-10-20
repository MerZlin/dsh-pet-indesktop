# -*- coding: utf-8 -*-
"""``pet/character_pack.py``（角色包索引）的回归测试。"""
from __future__ import annotations

import json

import pytest

from pet import character_pack as cp

SHA = "a" * 64


def _clip(**over):
    base = {
        "name": "待机呼吸休闲",
        "category": "idle",
        "dir": "anim/idle/待机呼吸休闲",
        "frames": 241,
        "key_every": 12,
        "part": "core",
        "content_sha": SHA,
        "bytes": 1234,
    }
    base.update(over)
    return base


def _index(**over):
    base = {
        "schema": 1,
        "id": "shenshen",
        "name": "深深",
        "canvas": [640, 360],
        "fps": 24.0,
        "pipeline": "unblend-white-v6",
        "format": "lossless-frame-v1",
        "parts": ["core", "extra"],
        "clips": [_clip()],
    }
    base.update(over)
    return base


def test_parse_minimal_index():
    idx = cp.parse_index(_index())
    assert idx.id == "shenshen"
    assert idx.canvas == (640, 360)
    assert idx.fps == 24.0
    assert len(idx.clips) == 1
    assert idx.clips[0].name == "待机呼吸休闲"
    assert idx.categories() == {"idle": ["待机呼吸休闲"]}


def test_rejects_unknown_schema():
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(schema=99))


def test_rejects_empty_clips():
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(clips=[]))


def test_rejects_duplicate_clip_names():
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(clips=[_clip(), _clip()]))


@pytest.mark.parametrize("bad_dir", [
    "../../etc/passwd",
    "/abs/path",
    "C:/windows/system32",
    "",
])
def test_rejects_path_traversal_and_absolute_dirs(bad_dir):
    """下载来的包必须防路径穿越——索引里的 dir 只能指向角色目录内部。"""
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(clips=[_clip(dir=bad_dir)]))


def test_normalises_backslashes_in_dir():
    idx = cp.parse_index(_index(clips=[_clip(dir="anim\\idle\\待机")]))
    assert idx.clips[0].dir == "anim/idle/待机"


def test_rejects_bad_content_sha():
    for bad in ("", "zz" * 32, "a" * 63, "A" * 64 + "b"):
        with pytest.raises(cp.IndexError_):
            cp.parse_index(_index(clips=[_clip(content_sha=bad)]))


def test_accepts_uppercase_sha_normalised_to_lower():
    idx = cp.parse_index(_index(clips=[_clip(content_sha="A" * 64)]))
    assert idx.clips[0].content_sha == "a" * 64


def test_rejects_part_not_declared():
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(clips=[_clip(part="bonus")]))


def test_rejects_zero_frames_and_bad_key_every():
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(clips=[_clip(frames=0)]))
    with pytest.raises(cp.IndexError_):
        cp.parse_index(_index(clips=[_clip(key_every=0)]))


def test_load_index_returns_none_when_absent(tmp_path):
    assert cp.load_index(tmp_path) is None


def test_load_index_reads_file(tmp_path):
    (tmp_path / cp.INDEX_NAME).write_text(
        json.dumps(_index(), ensure_ascii=False), encoding="utf-8")
    idx = cp.load_index(tmp_path)
    assert idx is not None and idx.id == "shenshen"


def test_load_index_raises_on_broken_json(tmp_path):
    (tmp_path / cp.INDEX_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(cp.IndexError_):
        cp.load_index(tmp_path)


def test_uninstalled_part_excluded_from_pool():
    """未安装分件的 clip 不得进入候选池（否则切过去静默不动）。"""
    idx = cp.parse_index(_index(clips=[
        _clip(name="a", part="core"),
        _clip(name="b", part="extra", dir="anim/random/b"),
    ]))
    present = {"core"}
    assert [c.name for c in cp.clips_in_parts(idx, present)] == ["a"]
    assert cp.missing_parts(idx, present) == {"extra"}


# ------------------------------------------------------------------ 采纳闸门（B2）
def _entry(**over):
    e = cp.ClipEntry(name="c", category="idle", dir="anim/c", frames=3,
                     key_every=12, part="core", content_sha=SHA, bytes=10)
    return cp.ClipEntry(**{**e.__dict__, **over}) if over else e


FRAMES = ["f_0001.webp", "f_0002.webp", "f_0003.webp"]
GOOD_META = {"frames": 3, "encoder": "ENC", "fps": 24.0, "canvas": [640, 360],
             "content_sha": SHA}


def _ok(meta=None, names=None):
    return cp.admissible(_entry(), meta if meta is not None else GOOD_META,
                         names if names is not None else FRAMES,
                         encoder="ENC", fps=24.0, canvas=(640, 360))


def test_gate_accepts_conforming_clip():
    assert _ok() == (True, "")


def test_gate_rejects_wrong_encoder():
    """旧 Q70 档（或任何非当前档）不许被当成无损产物采纳——这是 B2 的核心。"""
    ok, why = _ok({**GOOD_META, "encoder": "libwebp lossy q70 (…white-v5)"})
    assert not ok and "编码档不符" in why


def test_gate_rejects_non_integer_frames():
    ok, why = _ok({**GOOD_META, "frames": "3"})
    assert not ok and "不是整数" in why
    ok, why = _ok({**GOOD_META, "frames": True})
    assert not ok


def test_gate_rejects_frame_count_mismatch():
    ok, why = _ok({**GOOD_META, "frames": 5})
    assert not ok and "帧数不符" in why


def test_gate_rejects_fps_mismatch():
    ok, why = _ok({**GOOD_META, "fps": 30.0})
    assert not ok and "帧率不符" in why


def test_gate_rejects_canvas_mismatch():
    ok, why = _ok({**GOOD_META, "canvas": [1280, 720]})
    assert not ok and "画布不符" in why


def test_gate_rejects_content_sha_mismatch():
    ok, why = _ok({**GOOD_META, "content_sha": "e" * 64})
    assert not ok and "内容摘要" in why


def test_gate_rejects_non_contiguous_or_missing_frames():
    """只比数量不够：帧文件必须严格连续（中间缺一个也是半成品）。"""
    ok, why = _ok(names=["f_0001.webp", "f_0002.webp"])
    assert not ok and "不连续或不完整" in why
    ok, why = _ok(names=["f_0001.webp", "f_0003.webp", "f_0004.webp"])
    assert not ok and "不连续或不完整" in why


def test_gate_rejects_non_dict_meta():
    ok, _why = _ok(meta=[])
    assert not ok



def test_core_gaps_rejects_nan_fps(tmp_path):
    """meta.fps = NaN 不许穿过闭包（运行时 admissible 会拒，准入不能更松）。"""
    import json as _json

    from pet import frameseq_provision as _fp

    root = tmp_path / "pack"
    clips = []
    for name, cat in (("待机", "idle"), ("走路", "move"), ("转身", "turn"),
                      ("点击", "click"), ("拖拽", "drag")):
        d = root / "anim" / cat / name
        d.mkdir(parents=True)
        (d / "f_0001.webp").write_bytes(b"x")
        fps_value = float("nan") if cat == "idle" else 24.0
        (d / "meta.json").write_text(_json.dumps({
            "fps": fps_value, "frames": 1, "content_sha": "a" * 64,
            "encoder": _fp.ENCODER_DESC}), encoding="utf-8")
        clips.append({"name": name, "category": cat, "dir": f"anim/{cat}/{name}",
                      "frames": 1, "key_every": 12, "part": "core",
                      "content_sha": "a" * 64, "bytes": 1})
    index = cp.parse_index({
        "schema": 1, "id": "shenshen", "canvas": [640, 360], "fps": 24.0,
        "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["core"], "clips": clips})
    gaps = cp.core_gaps(root, index)
    assert "待机" in gaps, f"NaN fps 必须判缺口: {gaps}"
