# -*- coding: utf-8 -*-
"""角色包版本目录排序（`catalog._pack_version_key`）的类型安全回归。"""
from __future__ import annotations

from pathlib import Path

from pet import catalog


def test_version_key_is_type_stable_across_segments():
    """`1.0` 与 `1.0.0` 段数不同：键必须类型统一，不能把 str 与 int 拿去比较。"""
    a = catalog._pack_version_key(Path("1.0-base-640x360"))
    b = catalog._pack_version_key(Path("1.0.0-base-640x360"))
    assert isinstance(a[0], tuple) and isinstance(b[0], tuple)
    assert sorted([a, b])          # 不抛 TypeError


def test_version_key_orders_numerically():
    folders = [Path(n) for n in ("1.0.0-a-1x1", "1.10.0-a-1x1", "1.2.0-a-1x1")]
    ordered = sorted(folders, key=catalog._pack_version_key, reverse=True)
    assert [f.name for f in ordered] == ["1.10.0-a-1x1", "1.2.0-a-1x1", "1.0.0-a-1x1"]


def test_version_key_handles_garbage_prefix():
    assert catalog._pack_version_key(Path("weird-name"))


def test_extra_only_version_never_becomes_active(tmp_path, monkeypatch):
    """只装了 extra 的新版本不许顶掉完整包（终审第四轮 B11）。

    页面允许单独下 extra；装完会生成本地索引。发现链原来只认"最高版本目录有
    character.json"，于是重启后 extra-only 的版本成了活动包 —— 用户丢掉 core 的
    点击/拖拽/走路闭包，而且看不出原因。
    """
    import json as _json

    from pet import catalog as _catalog
    from pet import pack_download as _pd

    chars = tmp_path / "chars"

    def _write(root, clips):
        root.mkdir(parents=True, exist_ok=True)
        for c in clips:
            d = root / c["dir"]
            d.mkdir(parents=True, exist_ok=True)
            (d / "f_0001.webp").write_bytes(b"x")
            meta = {"fps": 24.0, "frames": c["frames"],
                    "encoder": __import__("pet.frameseq_provision", fromlist=["x"]).ENCODER_DESC}
            if c.get("content_sha"):
                meta["content_sha"] = c["content_sha"]
            (d / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
        idx = {"schema": 1, "id": "shenshen", "name": "深深", "canvas": [640, 360],
               "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
               "parts": ["core", "extra"], "clips": clips}
        (root / "character.json").write_text(_json.dumps(idx, ensure_ascii=False),
                                             encoding="utf-8")

    complete = chars / "shenshen" / "1.0.0-base-640x360"
    _write(complete, [{"name": name, "category": cat, "dir": f"anim/{cat}/{name}",
                       "frames": 1, "key_every": 12, "part": "core",
                       "content_sha": _pd.frame_digest(complete / "anim" / cat / name),
                       "bytes": 1}
                      for name, cat in (("待机", "idle"), ("走路", "move"), ("转身", "turn"),
                                        ("点击", "click"), ("拖拽", "drag"))])
    extra_only = chars / "shenshen" / "2.0.0-base-640x360"
    _write(extra_only, [{"name": "新动作", "category": "random",
                         "dir": "anim/random/新动作", "frames": 1, "key_every": 12,
                         "part": "extra", "content_sha": "e" * 64, "bytes": 1}])

    monkeypatch.setattr(_catalog, "external_character_dirs", lambda: [chars])
    dirs = _catalog.character_pack_dirs("shenshen")
    assert extra_only not in dirs, "core 不闭环的版本不许参与发现"
    assert dirs == [complete]

    # 新语义：**装了什么不等于用哪个** —— 没有指针就不激活任何包
    assert _catalog.resolve_character_video_dir("shenshen") != complete,         "没有 active.json 时不许按版本号自动激活"
    # 指针也不许指到 core 不闭环的版本
    from pet import character_pack as _cp
    assert _cp.set_active(extra_only) is False
    assert _catalog.resolve_character_video_dir("shenshen") != extra_only
    # 显式激活完整的那套 → 才生效
    assert _cp.set_active(complete) is True
    assert _catalog.resolve_character_video_dir("shenshen") == complete


def test_body_box_scales_hd_pack_to_logical_canvas(tmp_path):
    """720p 包的 body_box（1280×720 包像素）必须换算到逻辑 640×360（720p 几何雷）。

    旧实现按 640×360 校验包像素坐标——合法高清框全被判非法 → 回退"窗口即身体"，
    碰撞/探头几何全错。现在校验按包画布做、坐标按画布比换算。
    """
    import json as _json

    from pet import catalog as _catalog

    root = tmp_path / "hd-pack"
    root.mkdir()
    (root / "character.json").write_text(_json.dumps({
        "schema": 1, "id": "shenshen", "name": "深深", "canvas": [1280, 720],
        "fps": 24.0, "pipeline": "unblend-white-v6", "format": "lossless-frame-v1",
        "parts": ["core"],
        "clips": [{"name": "待机", "category": "idle", "dir": "anim/idle/待机",
                   "frames": 1, "key_every": 12, "part": "core",
                   "content_sha": "a" * 64, "bytes": 1}]}), encoding="utf-8")
    (root / "manifest.json").write_text(_json.dumps({
        "name": "深深", "body_box": [200, 100, 1080, 700],
        "head_box": [300, 100, 980, 420]}), encoding="utf-8")

    body = _catalog.character_body_box("shenshen", root)
    assert body == (100, 50, 540, 350), f"720p 框应按 0.5 换算: {body}"
    head = _catalog.character_head_box("shenshen", root)
    assert head == (150, 50, 490, 210), f"head_box 同样换算: {head}"
    # 超包画布的框仍判非法
    (root / "manifest.json").write_text(_json.dumps({"body_box": [0, 0, 9999, 9999]}),
                                        encoding="utf-8")
    _catalog._body_box_scaled.cache_clear()
    assert _catalog.character_body_box("shenshen", root) is None


def test_geometry_is_bound_to_pack_root_not_active_pointer(tmp_path):
    """几何绑定素材根（root_str, canvas），不绑磁盘上的 active 指针。

    契约（终审 P1 后的修正）：「使用这套」只改下次启动的指针；在播库的
    素材根不变、几何也不变。根级缓存按 (root, canvas) 隔离，两个包根
    各有各的框，不需要 set_active 清任何缓存。
    """
    import json as _json

    from pet import catalog as _catalog

    _catalog.clear_geometry_caches()
    root_a = tmp_path / "pack-a"
    root_b = tmp_path / "pack-b"
    for root, box in ((root_a, [10, 10, 100, 100]), (root_b, [20, 20, 200, 200])):
        root.mkdir()
        (root / "manifest.json").write_text(_json.dumps({"body_box": box}),
                                            encoding="utf-8")
    assert _catalog.character_body_box("shenshen", root_a) == (10, 10, 100, 100)
    assert _catalog.character_body_box("shenshen", root_b) == (20, 20, 200, 200)
    # 再读一次（走缓存）：两根仍然各自正确，互不串
    assert _catalog.character_body_box("shenshen", root_a) == (10, 10, 100, 100)
