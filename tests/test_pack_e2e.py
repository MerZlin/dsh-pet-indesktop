# -*- coding: utf-8 -*-
"""角色包端到端：本机 HTTP 下载 → 校验 → 安装 → 库加载并识别可播放。

这条用例覆盖用户问的那个问题——**没有远端服务器也能端到端验证**：用本机
HTTP 服务器提供真实 zip，走完整的 download → install → MovieLibrary 链路。
"""
from __future__ import annotations

import hashlib
import http.server
import json
import threading
import zipfile
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from pet import character_pack as cp
from pet import frameseq_provision
from pet import pack_download as pd
from pet.frameseq_clip import FrameSeqClip
from pet.library import MovieLibrary

FRAMES = 4


def _make_clip(base: Path, name: str, category: str = "idle") -> dict:
    """造一个**真实**帧序列目录（Qt 写的真 webp），返回索引条目。"""
    d = base / "anim" / category / name
    d.mkdir(parents=True)
    for i in range(FRAMES):
        img = QImage(32, 24, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        img.setPixel(0, 0, (0xFF << 24) | ((i * 40) << 8) | 0x33)
        assert img.save(str(d / f"f_{i + 1:04d}.webp"), "webp", 100)
    (d / "meta.json").write_text(json.dumps({
        "fps": 24.0, "frames": FRAMES,
        "encoder": frameseq_provision.ENCODER_DESC,
    }, ensure_ascii=False), encoding="utf-8")
    return {
        "name": name, "category": category, "dir": f"anim/{category}/{name}",
        "frames": FRAMES, "key_every": 12, "part": "core",
        "content_sha": pd.frame_digest(d),
        "bytes": sum(p.stat().st_size for p in d.glob("f_*.webp")),
    }


def _build_zip(tmp_path: Path, clips: list[dict]) -> Path:
    zpath = tmp_path / "shenshen-2.0.0-base-32x24.core.zip"
    doc = {
        "schema": cp.SCHEMA, "id": "shenshen", "name": "深深",
        # 画布必须声明**真实帧尺寸**（安装校验会解码比对——拿 32×24 冒充
        # 640×360 是终审点名不许再犯的口径）
        "canvas": [32, 24], "fps": 24.0,
        "pipeline": frameseq_provision.UNBLEND_MARK, "format": "lossless-frame-v1",
        "parts": ["core"], "clips": clips,
    }
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("part.json", json.dumps(doc, ensure_ascii=False, indent=1))
        zf.writestr("manifest.json", json.dumps({"body_box": [1, 2, 3, 4]}, ensure_ascii=False))
        for clip in clips:
            src = tmp_path / clip["dir"]
            for p in sorted(src.iterdir()):
                zf.write(p, f"{clip['dir']}/{p.name}")
    return zpath


class _Srv(http.server.BaseHTTPRequestHandler):
    payload = b""

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        data = type(self).payload
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture()
def served(tmp_path):
    # core 闭包五类各一段（激活闸门要求必需动作闭包，少一类都不可激活）
    clips = [_make_clip(tmp_path, "待机"), _make_clip(tmp_path, "点击", "click"),
             _make_clip(tmp_path, "走路", "move"), _make_clip(tmp_path, "转身", "turn"),
             _make_clip(tmp_path, "拖拽", "drag")]
    zpath = _build_zip(tmp_path, clips)
    _Srv.payload = zpath.read_bytes()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Srv)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield zpath, f"http://127.0.0.1:{srv.server_address[1]}/p.zip"
    finally:
        srv.shutdown()
        srv.server_close()


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


def test_end_to_end_download_install_and_library_loads(served, tmp_path):
    zpath, url = served
    data = zpath.read_bytes()
    part = pd.Part(name="core.zip", urls=(url,), bytes=len(data),
                   sha256=hashlib.sha256(data).hexdigest())

    dl_dir = tmp_path / "dl"
    archive = pd.download_part(part, dl_dir)
    assert archive.read_bytes() == data

    char_dir = tmp_path / "characters" / "shenshen"
    info = pd.install_part(archive, char_dir)
    assert info["clips"] == 5

    # 库必须能把无源包认成「可播放」（B1 的端到端版）
    lib = MovieLibrary(character_id="shenshen", asset_dir=char_dir / "videos",
                       prewarm_enabled=False)
    try:
        assert set(lib.names()) == {"待机", "点击", "走路", "转身", "拖拽"}
        clip = lib.movie("待机")
        assert isinstance(clip, FrameSeqClip)
        assert clip.frameCount() == FRAMES
    finally:
        _close_all(lib)


def test_installed_pack_passes_admission_gate(served, tmp_path):
    """装好的包要真的过采纳闸门（帧数/档位/连续帧/内容摘要全对）。"""
    zpath, _url = served
    char_dir = tmp_path / "characters" / "shenshen"
    pd.install_part(zpath, char_dir)
    idx = cp.load_index(char_dir)
    assert idx is not None and idx.id == "shenshen"
    entry = idx.by_name()["待机"]
    clip_dir = char_dir / entry.dir
    names = sorted(p.name for p in clip_dir.glob("f_*.webp"))
    ok, why = cp.admissible(entry, frameseq_provision.read_meta(clip_dir), names,
                            encoder=frameseq_provision.ENCODER_DESC,
                            fps=idx.fps, canvas=idx.canvas)
    assert ok, why


def test_installed_pack_is_discovered_by_product_chain(served, tmp_path, monkeypatch):
    """装到**产品发现链认的目录**，`MovieLibrary(character_id=...)` 就能用上它。

    终审点名的断链：UI 装到 `<root>/<id>/<版本>/`，而 `resolve_character_video_dir`
    只找 `<id>/videos` —— UI 显示"已安装"但桌宠不会用。这条用例**不显式注入
    asset_dir**，走真实解析链。
    """
    from pet import catalog

    zpath, _url = served
    root = tmp_path / "characters"
    monkeypatch.setattr(catalog, "external_character_dirs", lambda: [root])

    char_dir = root / "shenshen" / "2.0.0-base-32x24"
    pd.install_part(zpath, char_dir)

    # 1) 装完**不自动激活**（新语义）：没有活动指针时解析链不认任何包
    assert catalog.resolve_character_video_dir("shenshen") != char_dir,         "装了什么不等于用哪个：没有 active.json 时不许自动激活"

    # 2) 显式"使用这套"之后才生效
    from pet import character_pack as cp
    assert cp.set_active(char_dir) is True
    assert catalog.resolve_character_video_dir("shenshen") == char_dir

    # 3) 不传 asset_dir 的库要能起来并用上包里的动画
    lib = MovieLibrary(character_id="shenshen", prewarm_enabled=False)
    try:
        assert lib._asset_dir == char_dir
        assert set(lib.names()) == {"待机", "点击", "走路", "转身", "拖拽"}
        assert isinstance(lib.movie("待机"), FrameSeqClip)
    finally:
        _close_all(lib)
