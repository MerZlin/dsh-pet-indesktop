# -*- coding: utf-8 -*-
"""tools/convert_frameseq.py 端到端单测（offscreen 可跑）。

链路：Qt 生成 3 帧 → ffmpeg 现场压一个 3 帧 webm → convert_clip 转成
WebP 帧序列 → 断言帧数/fps/meta/幂等跳过。真实 ffmpeg（本机 winget 安装），
验证的正是 bgra 直通那组防 chroma 下采样的参数。

改两段式（W1/W2）后这段跑的是**完整管线**：阶段一真 ffmpeg 抓无损帧 → 阶段二
Pillow 逐帧白底反解 + Q70 重编码。所以这里同时盯两件事：真实素材上 alpha 位流
照旧逐像素存活（libwebp 的 alpha 通道走无损位流），以及产物**逐帧都是 Q70 有损**
（RIFF 容器里全是 ``VP8L`` = 无损）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from tools.convert_frameseq import clip_out_dir, convert_clip
from pet.frameseq_provision import ENCODER_DESC, UNBLEND_MARK, source_sha256

app = QApplication.instance() or QApplication([])

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                reason="本机无 ffmpeg")

# 仓库内真实 yuva420p 素材：stream copy 裁 6 帧做夹具——ffmpeg 现场从
# PNG 压 VP9 会丢 alpha（8.1 实测），必须用真素材。用 idle 而非 random：
# random/工作状态-垂头叹气冒汗.webm 实测整帧不透明，验不了 alpha
REAL_WEBM = (Path(__file__).resolve().parent.parent
             / "assets" / "characters" / "shenshen" / "videos"
             / "idle" / "待机呼吸休闲.webm")


def _make_tiny_webm(tmp_path: Path, frames: int = 6) -> Path:
    """真素材 stream copy 裁帧 → 带 alpha 的小 webm（秒级）。"""
    webm = tmp_path / "tiny.webm"
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-i", str(REAL_WEBM), "-frames:v", str(frames),
         "-c:v", "copy", str(webm)],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return webm


def test_convert_clip_produces_stamped_q70_frames(tmp_path):
    """真素材 6 帧端到端：帧数/fps/身份戳照旧，产物是 Q70（不是旧的无损档）。"""
    webm = _make_tiny_webm(tmp_path)
    out_dir = clip_out_dir(webm, tmp_path, tmp_path / "frameseq")
    converted, err = convert_clip(webm, out_dir)
    assert err == ""
    assert converted is True

    frames = sorted(out_dir.glob("f_*.webp"))
    assert len(frames) == 6
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["fps"] == 24.0
    assert meta["frames"] == 6
    assert meta["source"] == "tiny.webm"
    assert meta["encoder"] == ENCODER_DESC
    assert meta["unblend"] == UNBLEND_MARK
    # 源身份戳：目录名 = sha256 前 12 位，meta 带全量 sha256
    assert meta["source_sha256"] == source_sha256(webm)
    assert out_dir.name.endswith("." + "g" + meta["source_sha256"][:12])
    # 每一帧都是无损容器（v6 起阶段二输出无损；VP8L = 无损的标志）
    assert all(frame.read_bytes()[12:16] == b"VP8L" for frame in frames)

    # alpha 存活：真素材有透明区（bgra 直通，不 chroma 下采样）
    img = QImage(str(frames[0]))
    assert not img.isNull()
    alphas = {(img.pixel(x, y) >> 24) & 0xFF
              for x in range(0, img.width(), 17) for y in range(0, img.height(), 17)}
    assert 0 in alphas and 0xFF in alphas


def test_convert_clip_idempotent_skip(tmp_path):
    webm = _make_tiny_webm(tmp_path)
    out_dir = clip_out_dir(webm, tmp_path, tmp_path / "frameseq")
    convert_clip(webm, out_dir)
    converted, err = convert_clip(webm, out_dir)
    assert err == ""
    assert converted is False               # 当前源已有可用世代 → 跳过
    converted, err = convert_clip(webm, out_dir, force=True)
    assert converted is True                # force 重转（构建期/维修）


def test_convert_clip_lossy_tier_real_ffmpeg(tmp_path):
    """统一 Q70 档 + 白底反解端到端（真 ffmpeg + 真素材 6 帧）。

    两段式的口径：meta 的 encoder 串必须是当前档串（老串在采纳闸门上不可用）、
    带反解标记、产物**逐帧都不是无损容器**（阶段二真的重编过）、alpha 位流照旧
    逐像素存活；``lossy`` 兼容参数不再改变任何产物（工具侧调用点仍在传它）。
    """
    webm = _make_tiny_webm(tmp_path)
    out_dir = clip_out_dir(webm, tmp_path, tmp_path / "frameseq")

    converted, err = convert_clip(webm, out_dir, lossy=True)

    assert err == ""
    assert converted is True
    frames = sorted(out_dir.glob("f_*.webp"))
    assert len(frames) == 6
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["encoder"] == ENCODER_DESC
    assert meta["unblend"] == UNBLEND_MARK
    assert meta["frames"] == len(frames)

    # 阶段二真的把每一帧重编成**无损**（RIFF chunk：VP8L = 无损）
    assert all(frame.read_bytes()[12:16] == b"VP8L" for frame in frames)

    img = QImage(str(frames[0]))
    assert not img.isNull()
    alphas = {(img.pixel(x, y) >> 24) & 0xFF
              for x in range(0, img.width(), 17) for y in range(0, img.height(), 17)}
    assert 0 in alphas and 0xFF in alphas      # alpha 位流没被有损编码吃掉

    # 幂等：同一份源不重转；``lossy`` 参数不再是档位开关（改档后一档到底）
    assert convert_clip(webm, out_dir, lossy=True) == (False, "")
    assert convert_clip(webm, out_dir) == (False, "")
