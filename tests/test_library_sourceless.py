# -*- coding: utf-8 -*-
"""无源视频角色包（只有 ``character.json`` + 帧序列）的建库回归。

回归背景：``_load_all`` 扫不到 ``*.webm``/``*.gif`` 就抛 ``FileNotFoundError``，
``rescan_frameseq`` 又要求「源 webm 的 sha256」当采纳身份 —— 于是**不带源视频的
角色包根本起不来**。契约见 ``.scratch/asset-arch/RESOURCE-CONTRACT.md``。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from pet import character_pack as cp
from pet import frameseq_provision
from pet.frameseq_clip import FrameSeqClip
from pet.library import MovieLibrary

SHA = "b" * 64


def _make_frames(dir_path: Path, count: int = 4, size: tuple[int, int] = (32, 24)) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    w, h = size
    for i in range(count):
        img = QImage(w, h, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        img.setPixel(0, 0, (0xFF << 24) | ((i * 40) << 8) | 0x33)
        assert img.save(str(dir_path / f"f_{i + 1:04d}.webp"), "webp", 100)
    from pet import pack_download as _pd
    (dir_path / "meta.json").write_text(
        json.dumps({"fps": 24.0, "frames": count,
                    "encoder": frameseq_provision.ENCODER_DESC,
                    # 采纳闸门要求内容摘要（失败关闭）；口径与安装器写进来的一致
                    "content_sha": _pd.frame_digest(dir_path)}),
        encoding="utf-8")


def _sourceless_pack(tmp_path: Path, *, frames: int = 4) -> Path:
    """``<char>/character.json`` + ``<char>/anim/idle/待机/``，**没有 videos 目录**。"""
    root = tmp_path / "chars" / "shenshen"
    clip_dir = root / "anim" / "idle" / "待机"
    _make_frames(clip_dir, count=frames)
    index = {
        "schema": 1, "id": "shenshen", "name": "深深",
        "canvas": [640, 360], "fps": 24.0,
        "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["core", "extra"],
        "clips": [{
            "name": "待机", "category": "idle", "dir": "anim/idle/待机",
            "frames": frames, "key_every": 12, "part": "core",
            "content_sha": __import__("pet.pack_download", fromlist=["x"]).frame_digest(clip_dir),
            "bytes": 100,
        }],
    }
    (root / cp.INDEX_NAME).write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return root


def _close_all(lib: MovieLibrary) -> None:
    for m in lib.movies().values():
        for name in ("close", "cleanup", "stop"):
            fn = getattr(m, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break


def test_library_builds_from_sourceless_pack(tmp_path):
    root = _sourceless_pack(tmp_path)
    lib = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                       prewarm_enabled=False)
    try:
        assert lib.media_type == "frameseq"
        assert list(lib._manifest) == ["待机"]          # 名字来自索引
        assert lib.folder_map["待机"] == "idle"          # 分类来自索引，不从路径首段推
        assert lib._paths == {}                          # 没有任何源文件
        # 回归（终审 B1）：names() 曾返回 list(self._paths) → 无源包拿到零个动画，
        # 应用侧动作池为空。口径必须是"声明了 **且** 素材可用"。
        assert lib.names() == ["待机"], "无源包必须把自己的 clip 报成可播放"
        assert lib._frameseq_dirs["待机"] == root / "anim" / "idle" / "待机"
        clip = lib.movie("待机")
        assert isinstance(clip, FrameSeqClip)
        assert clip.frameCount() == 4
    finally:
        _close_all(lib)


def test_sourceless_clip_with_wrong_frame_count_is_not_adopted(tmp_path):
    """索引写 4 帧、磁盘只有 2 帧 ⇒ 不采纳（半成品/被替换），取 clip 报明确状态。"""
    root = _sourceless_pack(tmp_path, frames=4)
    d = root / "anim" / "idle" / "待机"
    for p in sorted(d.glob("f_*.webp"))[2:]:
        p.unlink()
    lib = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                       prewarm_enabled=False)
    try:
        assert "待机" not in lib._frameseq_dirs
        assert lib.names() == [], "未通过校验的 clip 不得进入动作池"
        with pytest.raises(KeyError):
            lib.movie("待机")
    finally:
        _close_all(lib)


def test_illegal_index_raises_clear_error(tmp_path):
    root = _sourceless_pack(tmp_path)
    (root / cp.INDEX_NAME).write_text('{"schema": 99}', encoding="utf-8")
    with pytest.raises(FileNotFoundError) as excinfo:
        MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                     prewarm_enabled=False)
    assert "索引非法" in str(excinfo.value)


def test_index_not_picked_up_from_shared_parent(tmp_path):
    """素材目录不叫 videos 时，不得到父目录里捡别人的索引（防串角色）。"""
    shared = tmp_path / "assets"
    shared.mkdir()
    (shared / cp.INDEX_NAME).write_text(json.dumps({
        "schema": 1, "id": "other", "name": "x", "canvas": [1, 1], "fps": 24,
        "pipeline": "p", "format": "f", "parts": ["core"],
        "clips": [{"name": "c", "category": "idle", "dir": "anim/c",
                   "frames": 1, "key_every": 12, "part": "core", "content_sha": SHA}],
    }), encoding="utf-8")
    mine = shared / "mine"
    (mine / "idle").mkdir(parents=True)
    (mine / "idle" / "x.webm").write_bytes(b"placeholder")
    lib = MovieLibrary(character_id="mine", asset_dir=mine, prewarm_enabled=False)
    try:
        assert lib._pack_index is None       # 没被别人的索引污染
    finally:
        _close_all(lib)


def test_sibling_library_keeps_pack_index_after_shared_adopt(tmp_path):
    """兄弟库（多宠同角色）必须继承包索引。

    终审 B3：``SharedCharacterMedia`` 原来不带包索引，于是第二个库的
    ``rescan_frameseq()`` 先清空**共享**映射、又因为没有索引无法重新采纳
    → 无源包在多宠场景下动作集被清空。
    """
    root = _sourceless_pack(tmp_path)
    a = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                     prewarm_enabled=False)
    try:
        b = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                         prewarm_enabled=False)
        try:
            assert b._pack_index is not None, "兄弟库必须拿到包索引"
            # 任一库重扫之后，两个库都还能看到这段无源 clip
            b.rescan_frameseq()
            assert "待机" in b._frameseq_dirs
            assert "待机" in a._frameseq_dirs, "重扫不得把共享映射清空后不重建"
            assert a.names() == ["待机"] and b.names() == ["待机"]
        finally:
            _close_all(b)
    finally:
        _close_all(a)


def test_action_pool_excludes_unplayable_index_entries(tmp_path):
    """终审 B1：动作分类必须与「可播放」取交集。

    `folder_files` 直接来自索引声明会包含不可播放条目（分件未装 / 校验不过），
    而 `catalog.build_categories` 会把它原样搬进动作池 → 调度器可能选中一个
    播不动的动画。这里用「一条好 idle + 一条帧数不符的 random」验证。
    """
    root = _sourceless_pack(tmp_path)
    import json as _json
    idx_path = root / cp.INDEX_NAME
    idx = _json.loads(idx_path.read_text(encoding="utf-8"))
    bad_dir = root / "anim" / "random" / "坏段"
    _make_frames(bad_dir, count=2)          # 只有 2 帧，索引却声明 99 帧
    idx["clips"].append({
        "name": "坏段", "category": "random", "dir": "anim/random/坏段",
        "frames": 99, "key_every": 12, "part": "extra",
        "content_sha": "c" * 64, "bytes": 1,
    })
    idx_path.write_text(_json.dumps(idx, ensure_ascii=False), encoding="utf-8")

    lib = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                       prewarm_enabled=False)
    try:
        assert lib.names() == ["待机"], "坏段不可播放"
        assert lib.folder_files == {"idle": ["待机"]}, "动作分类不得含不可播放条目"
        assert "坏段" not in lib.folder_map
    finally:
        _close_all(lib)


def test_startup_recovery_commits_crashed_install(tmp_path):
    """崩在「帧已落地、索引还没合并」→ 建库时前滚，该段立刻可播。

    这是「无启动恢复」那一条的端到端证据：不靠下一次安装，也不靠重启第二次。
    """
    import json as _json

    from pet import pack_download as _pd

    root = _sourceless_pack(tmp_path)
    extra = root / "anim" / "random" / "新段"
    _make_frames(extra, count=4)
    sha = _json.loads((extra / "meta.json").read_text(encoding="utf-8"))["content_sha"]
    _pd._write_journal(root, {
        "schema": 1, "archive": "extra", "phase": "merge",
        "part_doc": {"clips": [{
            "name": "新段", "category": "random", "dir": "anim/random/新段",
            "frames": 4, "key_every": 12, "part": "extra",
            "content_sha": sha, "bytes": 1}]},
    })
    lib = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                       prewarm_enabled=False)
    try:
        assert "新段" in lib.names(), "启动前滚后该段必须可播"
        assert "新段" in lib.folder_files.get("random", [])
        assert not (root / _pd.JOURNAL_NAME).exists(), "收口后日志必须删掉"
    finally:
        _close_all(lib)


def test_first_install_crash_is_recovered_before_discovery(tmp_path, monkeypatch):
    """**首次装包**崩在索引合并前 → 启动时也能收口（终审 B3）。

    回归背景：那时版本目录里没有 ``character.json``，常规发现链看不见它，
    `recover_installs` 也就永远不会被调用——事务永久卡死。
    """
    import json as _json

    from pet import catalog as _catalog
    from pet import pack_download as _pd

    root = _sourceless_pack(tmp_path)
    extra = root / "anim" / "random" / "新段"
    _make_frames(extra, count=4)
    sha = _json.loads((extra / "meta.json").read_text(encoding="utf-8"))["content_sha"]
    # 模拟首次安装：帧已落地、日志在、但索引还没写
    (root / _catalog.character_pack_dirs.__name__ and root / "character.json").unlink()
    _pd._write_journal(root, {
        "schema": 1, "archive": "extra", "phase": "merge",
        "part_doc": {"schema": 1, "id": "shenshen", "name": "深深",
                     "canvas": [640, 360], "fps": 24.0,
                     "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
                     "clips": [{"name": "新段", "category": "random",
                                "dir": "anim/random/新段", "frames": 4,
                                "key_every": 12, "part": "extra",
                                "content_sha": sha, "bytes": 1}]},
    })
    # 发现链指向临时根（真实实现读 exe/cwd 下的 characters/）
    monkeypatch.setattr(_catalog, "external_character_dirs", lambda: [tmp_path / "chars"])

    lib = MovieLibrary(character_id="shenshen", asset_dir=root / "videos",
                       prewarm_enabled=False)
    try:
        assert "新段" in lib.names(), "首次安装的事务必须在起库时被前滚进来"
        assert (root / "character.json").is_file()
        assert not (root / _pd.JOURNAL_NAME).exists()
    finally:
        _close_all(lib)



