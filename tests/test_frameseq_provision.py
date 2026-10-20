# -*- coding: utf-8 -*-
"""首跑帧序列自动供给（帧序列化 B 档）offscreen 单测。

覆盖：
- 源身份（sha256 → 世代目录名）+ 采纳谓词：无戳旧版产物、半成品 tmp、帧数与
  磁盘不符一律不采纳；采纳路径不读帧内容（155MB 帧集永不哈希/解码）；
- 源身份只认内容、不认 stat：同尺寸 + mtime 复原的源替换（原地改写与原子替换
  两种形态）必须当场失效——按 stat 记忆会静默沿用旧世代的戳继续播上一版帧；
  发布前再验一次身份，转换期间源被换掉 ⇒ 产物丢弃（绝不发布戳与帧不符的世代）；
- 原子发布（``<世代名>.tmp/`` → rename）+ 固化 ffmpeg 参数（阶段一**永远无损**）+
  幂等跳过 + 拒绝向「非源身份命名的目录」发布（不许再造无戳缓存）；
- **两段式管线（W1/W2）**：阶段一（无损抓帧）→ 阶段二（逐帧白底反解 + Q70 重编码，
  原地覆盖）→ 发布。调用序列、阶段二的输入就是阶段一的像素、产物逐帧都是**有损**
  容器（RIFF chunk 不是 ``VP8L``）、alpha 逐位不变；阶段二整段失败 ⇒ 丢弃半成品
  （绝不发布「meta 写着 Q70+反解、帧却是无损」的假凭证）；单帧坏掉 ⇒ 整段作废、
  帧数契约不变、整段照发；取消/会话结束落在阶段二 ⇒ 逐帧停手 + 丢弃半成品；
- 发布收口：同路径旧目标先挪 ``<世代名>.replacing/`` 再发布，rename 抛错则
  回滚（旧目标一个字节不丢）+ 半成品路径被普通文件占住时清掉照转——发布/回滚/
  半成品清理/exe 起不来/meta 写不进一律按 ``(False, 错误文本)`` 返回，不许掀掉
  整轮；
- 世代生命周期：发布新世代只给旧世代打退役标记；清扫只删「已标记退役 +
  满宽限期 + 不在用」的世代目录（活 clip 可能正在读 → 永不删）；回朝（源退回
  某一版）的世代在供给轮里清零退役时钟——旧龄不跨回朝，闸门把「当前世代带着
  退役标记」当待办（否则它再次离场那一轮就被旧龄删掉）；
- QLockFile 多实例互斥、无 ffmpeg exe 静默 no-op、session_ending 闸门
  （issue #111）、PET_FRAMESEQ=0 逃生门、库收尾 cancel + 有界等待；
- 供给预算口径：迁移配额是**进程内共享**的（一个启动周期三宠三库合计最多迁 1 个
  legacy clip，不是每库一份）、供给序 idle → move → 其余（确定性）、
  ``ROUND_BUDGET_S`` 是**起手上限**而非墙钟硬上限（已起手的 clip 跑完，不硬杀
  ffmpeg）、退避表写失败只 warning 声明"未落盘"（不谎称已退避）；
- 锁被占时库侧有界重试：锁失败不永久放弃别的角色，也不无限轮询；
- 库接线：无戳旧版产物不采纳（回退 WebM，不冻结）、源换版后旧世代立即失效、
  后台供给转出新世代后 rescan 采纳、有待清扫世代或回朝旧账时也拉 worker；
- 编码档位与供给范围：**全部 clip 统一一档**（Q70 + 白底反解，encoder 串比对进
  采纳闸门）；改档前的「热集无损 / 冷集 Q80」世代一律不采纳并重新进待转清单
  （迁移路径：回退 WebM 播放 + 后台按新档重新供给）；供给范围 = 热集 + random +
  events，供给序热集 → events → random；冷集不再换档；
- 递归扫描（R2）：``events/balance/<stem>.g<戳>/`` 这类三层世代入表并被 rescan 采纳，
  世代目录内部不再下钻；
- 磁盘准入（R2）：剩余空间 < max(1GiB, 源大小 × 20) 的 clip 不起手（不吃迁移配额、
  不删已转产物）；
- GUI 线程减负（R2）：「有没有活干」的三项判定只在 worker 线程跑（GUI 线程不做
  plan_clips），无事可做的收尾不调 rescan；
- tools/convert_frameseq.py 薄壳：复用核心、幂等、退出码口径不变。

纪律（AGENTS.md）：全部 offscreen，不依赖真实 ffmpeg 与真实 153MB 素材——
桩 ``_popen``（落**真** RGBA 无损 webp 帧，阶段二才会真解真编）+ 桩 fps 探测 +
tmp_path 小夹具；线程时序用事件泵 + 宽预算，不用固定 sleep 赌时序。真实 ffmpeg +
真实素材的 6 帧端到端在 ``test_convert_frameseq.py``（本机有 ffmpeg 时自动跑）。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sys
import threading
import time
from collections import namedtuple
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication

from pet import frameseq_provision as fp
from pet import library as library_mod
from pet.library import MovieLibrary
from pet.webm_clip import set_session_ending

app = QApplication.instance() or QApplication([])

FRAME_COUNT = 3
DAY_S = 24 * 3600.0


# ---------------------------------------------------------------- 测试夹具
class _FakeClip:
    """极简假 clip（WebM/帧序列两用）：不碰 ffmpeg/Qt（拷自 test_library_priority_warm 口径）。"""

    def __init__(self, path, parent=None):
        self.path = Path(path)

    def warm_meta(self):
        return

    def warm_first_frame(self):
        return

    def stop(self):
        return

    def cleanup(self):
        return


def _make_pack(tmp_path: Path, folders: dict[str, list[str]]) -> tuple[Path, Path]:
    """临时角色包：videos/<folder>/<name>.webm（占位字节；name 可带子目录）。"""
    videos = tmp_path / "videos"
    for folder, names in folders.items():
        directory = videos / folder
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fake-webm")
    return videos, tmp_path / "frameseq"


def _meta(webm: Path, frames: int, *, stamp: str | None = None,
          encoder: str | None = None) -> dict:
    """带源身份戳的 meta（缺 stamp 参数则现算当前源的 sha256）。

    默认写**当前档**的 encoder 串与反解标记（Q70 + 白底反解）——档位是采纳条件的
    一部分，写错档的夹具等于造一份不可采纳的产物。
    """
    return {
        "fps": 24.0,
        "source": Path(webm).name,
        "frames": int(frames),
        "encoder": fp.ENCODER_DESC if encoder is None else encoder,
        "unblend": fp.UNBLEND_MARK,
        "source_sha256": stamp if stamp is not None else fp.source_sha256(webm),
    }


def _frame_blend(rgb: tuple[int, int, int], alpha: int) -> tuple[int, int, int]:
    """素材的真实形态：在白底上抠图，「角色色 × a + 255 × (1-a)」的存值。"""
    return tuple(round(c * alpha / 255 + 255 * (1 - alpha / 255)) for c in rgb)


def _frame_rectified(stored: tuple[int, int, int], alpha: int) -> tuple[int, int, int]:
    """阶段二该把**存值**（``_frame_blend`` 的输出）反解成什么。

    口径与 ``frameseq_provision.unblend_rgba`` 同一公式；两端不反解的 alpha 原样
    返回（输入就直接是角色色了）。"""
    if not (fp.UNBLEND_MIN_ALPHA <= alpha < fp.UNBLEND_MAX_ALPHA):
        return stored
    return tuple(max(0, min(255, (v - 255 + alpha) * 255 // alpha)) for v in stored)


# 桩产物的三档像素：透明 / 半透明（白底污染最明显）/ 不透明
STUB_PIXELS = ((255, 255, 255), (200, 100, 40), (30, 180, 90))
STUB_ALPHAS = (0, 128, 255)
STUB_SIZE = (8, 8)


_STUB_FRAME_BYTES: bytes | None = None


def _stub_frame_bytes() -> bytes:
    """真 RGBA 无损 webp 字节：阶段二（Pillow）要真解真编，占位字节不行。

    内容 = 三段平带（每带宽 ``STUB_SIZE[0]/3``），每段一个 alpha 档，颜色按白底
    混合存值给——阶段二跑完就能断言"反解回了角色色、alpha 一位没动"。
    """
    global _STUB_FRAME_BYTES
    if _STUB_FRAME_BYTES is None:
        width, height = STUB_SIZE
        payload = bytearray()
        for _y in range(height):
            for x in range(width):
                band = min(len(STUB_PIXELS) - 1, x * len(STUB_PIXELS) // width)
                payload += bytes((*_frame_blend(STUB_PIXELS[band], STUB_ALPHAS[band]),
                                  STUB_ALPHAS[band]))
        buffer = io.BytesIO()
        Image.frombytes("RGBA", STUB_SIZE, bytes(payload)).save(
            buffer, format="WEBP", lossless=True, quality=100)
        _STUB_FRAME_BYTES = buffer.getvalue()
    return _STUB_FRAME_BYTES


def _write_frames(out_dir: Path, frames: int = FRAME_COUNT, *, blob: bytes = b"webp") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, frames + 1):
        (out_dir / f"f_{i:04d}.webp").write_bytes(blob)


def _stamped(webm: Path, videos: Path, frameseq: Path, *, frames: int = FRAME_COUNT,
             blob: bytes = b"webp", encoder: str | None = None) -> Path:
    """按产品命名契约落一个「当前源」的可用世代目录（f_*.webp + 带戳 meta）。

    encoder 默认写**当前档**串（Q70 + 白底反解）——档位是采纳条件的一部分，写错档
    的夹具等于造一份不可采纳的产物；``encoder`` 可显式传旧档串去验迁移路径。
    """
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _write_frames(out_dir, frames, blob=blob)
    (out_dir / "meta.json").write_text(
        json.dumps(_meta(webm, frames, encoder=encoder)), encoding="utf-8")
    return out_dir


def _legacy_dir(webm: Path, videos: Path, frameseq: Path, *,
                frames: int = FRAME_COUNT, meta: bool = True) -> Path:
    """旧版产物（真实部署包形态）：``frameseq/<folder>/<stem>/``，meta 无源身份戳。

    顺带写一份旧档 encoder 串（无损）：真实部署包里的 153MB 帧集正是那个年代的，
    它无论如何都不可采纳——无戳本身就是第一道否决（``is_complete``）。
    """
    out_dir = frameseq / Path(webm).relative_to(videos).with_suffix("")
    _write_frames(out_dir, frames)
    if meta:
        (out_dir / "meta.json").write_text(
            json.dumps({"fps": 24.0, "source": Path(webm).name, "frames": frames,
                        "encoder": fp.LEGACY_ENCODER_DESCS[0]}), encoding="utf-8")
    return out_dir


def _past_generation(webm: Path, videos: Path, frameseq: Path, *,
                     stamp: str = "0" * 64, frames: int = FRAME_COUNT) -> Path:
    """造一个「上一版源」的世代目录（戳来自另一份内容，模拟源换版前的产物）。"""
    base = fp.clip_base_dir(webm, videos, frameseq)
    out_dir = base.with_name(f"{base.name}{fp.GEN_INFIX}{fp.stamp_of(stamp)}")
    _write_frames(out_dir, frames)
    (out_dir / "meta.json").write_text(
        json.dumps(_meta(webm, frames, stamp=stamp)), encoding="utf-8")
    return out_dir


def _age(path: Path, *, days: float) -> None:
    """把文件/目录 mtime 拨回 days 天前（退役宽限用真实时钟，不 sleep）。"""
    when = time.time() - days * DAY_S
    os.utime(path, (when, when))


def _install_fake_ffmpeg(monkeypatch, *, frames: int = FRAME_COUNT,
                         fail: bool = False, on_convert=None,
                         fail_on=None) -> list:
    """桩 subprocess：communicate() 时把 f_%04d.webp 落进参数里的输出目录。

    ``fail`` 全部失败；``fail_on(argv)`` 按参数选择性失败（退避用例要"只挂一个
    clip"）。返回每次转换的桩进程（argv/returncode），供参数与调用次数断言。
    """
    calls: list = []

    class _Proc:
        def __init__(self, argv):
            self.argv = list(argv)
            self.returncode = 1 if (fail or (fail_on is not None
                                             and fail_on(self.argv))) else 0
            self._done = False

        def poll(self):
            return self.returncode if self._done else None

        def terminate(self):
            self.returncode = -1
            self._done = True

        def communicate(self):
            if not fail:
                out_dir = Path(self.argv[-1]).parent
                blob = _stub_frame_bytes()      # 真 RGBA 无损 webp：阶段二要真解真编
                for i in range(1, frames + 1):
                    (out_dir / f"f_{i:04d}.webp").write_bytes(blob)
            if on_convert is not None:
                on_convert(self.argv)
            self._done = True
            return (b"", b"" if self.returncode == 0 else b"stub failure")

    def _fake_popen(argv, **_kwargs):
        proc = _Proc(argv)
        calls.append(proc)
        return proc

    monkeypatch.setattr(fp, "_popen", _fake_popen)
    monkeypatch.setattr(fp, "probe_fps", lambda _webm: 24.0)
    return calls


def _fail_publish_rename(monkeypatch, target: Path) -> list:
    """桩 ``os.rename``：只让「往 ``target`` 发布世代目录」的那次抛错。

    挪走旧目标（````<世代名>.replacing````）与发布失败后的回滚照常执行 —— 正是
    Windows 上目标被活 reader 占住时的失败形态。返回全部 rename 尝试 (src, dst)。
    """
    real_rename = os.rename
    attempts: list = []

    def _rename(src, dst):
        attempts.append((Path(src), Path(dst)))
        if Path(dst) == Path(target) and str(src).endswith(fp.TMP_SUFFIX):
            raise OSError("目标目录被占用")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", _rename)
    return attempts


def _light_clips(monkeypatch) -> None:
    """库测试用假 clip：不构造真 FrameSeqClip（共享预取线程留给 test_frameseq_clip）。"""
    monkeypatch.setattr(library_mod, "WebMClip", _FakeClip)
    monkeypatch.setattr("pet.frameseq_clip.FrameSeqClip", _FakeClip)


def _pump_until(cond, timeout_s: float = 10.0) -> None:
    """事件泵：等到条件为真（跨线程 queued 信号需要事件循环）。"""
    deadline = time.monotonic() + timeout_s
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("事件泵超时")
        time.sleep(0.005)


def _pump_events(seconds: float = 0.2) -> None:
    """有界泵事件（负断言用：给 0ms singleShot 一个触发机会）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """模块级进程状态逐用例清干净，互不串味（源身份无进程记忆）。

    含「每启动一个启动周期的进程内迁移配额台账」：它是**进程生命周期**状态，
    用例之间必须归零，否则前一个用例迁过的那个 clip 会吃掉后一个用例的配额。
    """
    fp.clear_live_generations()
    fp.reset_migration_slots()
    yield
    set_session_ending(False)
    fp.clear_live_generations()
    fp.reset_migration_slots()


# ---------------------------------------------------------------- 源身份
def _swap_source_keeping_size_and_mtime(webm: Path, payload: bytes, *,
                                        atomic: bool) -> None:
    """同长度换内容后把 mtime 拨回复原（打包器/解压器保留时间戳的形态）。

    两种替换形态都覆盖：``atomic=False`` 原地改写（inode 不变，只有内容变），
    ``atomic=True`` 新文件顶掉旧文件（inode 变）——按 stat 记忆的缓存两种都会漏。
    """
    info = webm.stat()
    assert len(payload) == info.st_size, "换内容必须保持 size（本场景的前提）"
    if atomic:
        incoming = webm.with_name(webm.name + ".incoming")
        incoming.write_bytes(payload)
        os.replace(incoming, webm)
    else:
        webm.write_bytes(payload)
    os.utime(webm, ns=(info.st_atime_ns, info.st_mtime_ns))       # mtime 复原
    after = webm.stat()
    assert (after.st_size, after.st_mtime_ns) == (info.st_size, info.st_mtime_ns)


@pytest.mark.parametrize("atomic", [False, True], ids=["in_place", "atomic"])
def test_source_sha256_is_content_authoritative(tmp_path, atomic):
    """源身份只认内容：同尺寸 + mtime 复原的替换绝不许沿用旧摘要。

    ``(mtime_ns, size)`` 记忆撑不住这个形态——解压/复制保留时间戳落地的新素材常见
    「同长度 + mtime 被复原」：stat 记忆把它当「没变」，旧世代的戳继续被认成当前源
    身份 → 静默播上一版帧。同一路径连续两次调用也必须重读内容（无进程级记忆）。
    """
    videos, _ = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    original = webm.read_bytes()
    digest = fp.source_sha256(webm)
    assert len(digest) == 64 and digest == digest.lower()
    assert digest == hashlib.sha256(original).hexdigest()
    assert fp.stamp_of(digest) == digest[:fp.GEN_STAMP_HEX]

    for payload in (b"fake-webX", b"fake-webY"):
        _swap_source_keeping_size_and_mtime(webm, payload, atomic=atomic)
        assert fp.source_sha256(webm) == hashlib.sha256(payload).hexdigest()
        assert fp.source_sha256(webm) != digest       # 第二次调用同样重读内容
    assert webm.stat().st_size == len(original)       # 全程同尺寸（前提成立）


def test_corrupt_meta_is_not_complete(tmp_path):
    """meta 损坏/不是对象（空文件、坏 JSON、数组）→ 无身份凭证 → 不采纳。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)
    for payload in ("", "{not json", "[]", "null"):
        (gen / "meta.json").write_text(payload, encoding="utf-8")
        assert fp.is_complete(gen) is False
        assert fp.current_generation(webm, videos, frameseq) is None


def test_source_sha256_missing_source_is_none(tmp_path):
    """源不存在/不可读：返回 None（调用方据此判定「无法确定身份」）。"""
    assert fp.source_sha256(tmp_path / "nope.webm") is None
    assert fp.current_generation(
        tmp_path / "nope.webm", tmp_path, tmp_path / "frameseq") is None


def test_stamp_from_name_rejects_non_generation_names(tmp_path):
    """目录名解析：只有 ``<stem>.g<12位hex>`` 算世代目录；tmp/旧名/坏戳都不是。"""
    assert fp.stamp_from_name("a.g0123456789ab") == "0123456789ab"
    assert fp.stamp_from_name("a.g0123456789AB") == "0123456789ab"
    assert fp.stamp_from_name("a") is None
    assert fp.stamp_from_name("a.tmp") is None
    assert fp.stamp_from_name("a.g0123456789ab.tmp") is None
    assert fp.stamp_from_name("a.g0123456789") is None       # 长度不足
    assert fp.stamp_from_name("a.g0123456789zz") is None     # 非 hex
    assert fp.stamp_from_name(".g0123456789ab") is None      # 空 stem


# ---------------------------------------------------------------- 采纳谓词
def test_unstamped_meta_is_never_complete(tmp_path):
    """真实部署包形态：有帧、meta 也在，但没有源身份戳 → 永不采纳。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    legacy = _legacy_dir(webm, videos, frameseq)
    assert fp.is_complete(legacy) is False
    assert fp.current_generation(webm, videos, frameseq) is None
    assert [w.name for w, _ in fp.plan_clips(videos, frameseq)] == ["a.webm"]


def test_stale_generation_is_not_current(tmp_path):
    """戳只对产出它的那一版源成立：源换版 → 旧世代立即判定过期（不许「有帧就用」）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    old_dir = _stamped(webm, videos, frameseq)
    assert fp.current_generation(webm, videos, frameseq) == old_dir

    webm.write_bytes(b"fake-webm-v2")        # 源换版（同尺寸无关：size 已变）
    new_sha = fp.source_sha256(webm)
    assert fp.current_generation(webm, videos, frameseq) is None
    assert fp.is_complete(old_dir, sha=new_sha) is False
    assert fp.is_complete(old_dir) is True   # 本地自洽仍成立，只是不再对应当前源
    assert [w.name for w, _ in fp.plan_clips(videos, frameseq)] == ["a.webm"]


@pytest.mark.parametrize("atomic", [False, True], ids=["in_place", "atomic"])
def test_same_size_restored_mtime_swap_invalidates_generation(tmp_path, atomic):
    """同尺寸 + mtime 复原的源替换：旧世代立刻不采纳、clip 重新进待供给清单。

    这是「静默命中旧帧身份」的端到端形态：按 stat 记忆时 ``current_generation``
    继续返回旧世代目录，库照旧播上一版帧，供给轮也判定「范围内已完整」不再重转。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    old_gen = _stamped(webm, videos, frameseq)
    assert fp.current_generation(webm, videos, frameseq) == old_gen

    _swap_source_keeping_size_and_mtime(webm, b"fake-webX", atomic=atomic)

    assert fp.current_generation(webm, videos, frameseq) is None
    assert [w.name for w, _ in fp.plan_clips(videos, frameseq)] == ["a.webm"]
    assert old_gen.is_dir()                  # 旧世代只失效、不删（宽限期兜底）
    assert fp.frames_on_disk(old_gen) == FRAME_COUNT


def test_frame_count_mismatch_is_not_complete(tmp_path):
    """帧数与磁盘不符（半拷贝/丢帧）→ 不采纳：不能拿残帧当整段播放。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)
    assert fp.is_complete(gen) is True

    (gen / "f_0003.webp").unlink()
    assert fp.frames_on_disk(gen) == 2
    assert fp.is_complete(gen) is False
    assert fp.current_generation(webm, videos, frameseq) is None
    assert [w.name for w, _ in fp.plan_clips(videos, frameseq)] == ["a.webm"]

    (gen / "f_0003.webp").write_bytes(b"webp")
    (gen / "meta.json").write_text(
        json.dumps({**_meta(webm, FRAME_COUNT), "frames": 0}), encoding="utf-8")
    assert fp.is_complete(gen) is False      # 非正数帧数同样不算完成


def test_name_stamp_and_meta_stamp_must_agree(tmp_path):
    """目录名与 meta 戳必须一致（改名/串目录的产物不当当前源用）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)
    other = gen.with_name("a.g" + "0" * fp.GEN_STAMP_HEX)
    gen.rename(other)
    assert fp.is_complete(other) is False
    assert fp.current_generation(webm, videos, frameseq) is None


def test_tmp_workbench_dir_is_never_complete(tmp_path):
    """半成品 ``<世代名>.tmp/``（帧与 meta 都齐）永不被采纳。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)
    tmp = gen.with_name(gen.name + fp.TMP_SUFFIX)
    tmp.mkdir(parents=True)
    for item in gen.iterdir():
        (tmp / item.name).write_bytes(item.read_bytes())
    assert fp.is_complete(tmp) is False
    assert fp.current_generation(webm, videos, frameseq) == gen


def test_adoption_reads_no_frame_bytes(tmp_path, monkeypatch):
    """采纳只看「带戳 meta + 帧数」：帧文件一个字节都不读（155MB 帧集永不哈希）。

    帧写成空文件也能采纳 —— 这正是不扫帧内容的机器化证据。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq, blob=b"")
    assert all(f.stat().st_size == 0 for f in gen.glob("f_*.webp"))

    real_open = open

    def _no_frame_reads(file, *args, **kwargs):
        if str(file).endswith(".webp"):
            raise AssertionError("采纳路径不得读取帧内容")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _no_frame_reads)
    assert fp.current_generation(webm, videos, frameseq) == gen


def test_scan_generations_maps_base_and_ignores_legacy(tmp_path):
    """一次两级遍历拿到 {基础目录: [世代目录]}；旧版 ``<stem>/`` 与 tmp 都不算世代。"""
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm"], "move": ["m.webm"]})
    a_gen = _stamped(videos / "idle" / "a.webm", videos, frameseq)
    _legacy_dir(videos / "move" / "m.webm", videos, frameseq)
    (frameseq / "idle" / "junk").mkdir()                      # 非世代名目录
    (frameseq / "idle" / "a.tmp").mkdir()                     # 半成品名（非世代）
    (frameseq / "idle" / "not-a-dir.g0123456789ab").write_bytes(b"file")

    found = fp.scan_generations(frameseq)
    assert found == {frameseq / "idle" / "a": [a_gen]}


# ---------------------------------------------------------------- 转换核心
def test_convert_clip_atomic_publish_and_stamp(tmp_path, monkeypatch):
    """转换中只有 <世代名>.tmp/，成功后才 rename 成世代目录并写带戳 meta。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    seen = {}

    def _during(_argv):
        seen["target_exists"] = out_dir.exists()
        seen["tmp_frames"] = len(list(tmp_dir.glob("f_*.webp")))

    calls = _install_fake_ffmpeg(monkeypatch, on_convert=_during)
    converted, err = fp.convert_clip(webm, out_dir)

    assert (converted, err) == (True, "")
    assert len(calls) == 1
    # 转换进行中：目标目录不存在（library 永远捡不到半成品），帧落在 tmp
    assert seen == {"target_exists": False, "tmp_frames": FRAME_COUNT}
    assert not tmp_dir.exists()
    assert len(list(out_dir.glob("f_*.webp"))) == FRAME_COUNT
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta == _meta(webm, FRAME_COUNT)
    assert out_dir.name == f"a{fp.GEN_INFIX}{fp.stamp_of(meta['source_sha256'])}"
    assert fp.is_complete(out_dir, sha=fp.source_sha256(webm)) is True


def test_convert_clip_ffmpeg_argv_is_frozen(tmp_path, monkeypatch):
    """ffmpeg 参数固化：bgra 直通 + libvpx-vp9 解码 + -nostdin，一项都不许少。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    calls = _install_fake_ffmpeg(monkeypatch)

    converted, err = fp.convert_clip(webm, out_dir, exe="fake-ffmpeg")
    assert (converted, err) == (True, "")
    assert calls[0].argv == [
        "fake-ffmpeg",
        "-nostdin", "-v", "error", "-y",
        "-c:v", "libvpx-vp9",
        "-i", str(webm),
        "-pix_fmt", "bgra", "-c:v", "libwebp", "-lossless", "1",
        str(out_dir.with_name(out_dir.name + fp.TMP_SUFFIX) / "f_%04d.webp"),
    ]


def test_convert_clip_idempotent_skip(tmp_path, monkeypatch):
    """已完成（帧 + 带戳 meta 且对应本源）→ 跳过，且不再拉 ffmpeg。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _install_fake_ffmpeg(monkeypatch)
    assert fp.convert_clip(webm, out_dir) == (True, "")

    calls = _install_fake_ffmpeg(monkeypatch)
    assert fp.convert_clip(webm, out_dir) == (False, "")
    assert calls == []
    assert fp.is_complete(out_dir, sha=fp.source_sha256(webm)) is True


def test_convert_clip_discards_output_when_source_identity_drifts(tmp_path, monkeypatch):
    """转换期间源被同尺寸 + mtime 复原地换掉：产物丢弃，绝不发布戳与帧不符的世代。

    meta 里的戳是身份凭证：帧来自 v2 而戳写 v1 就是一份假凭证——源将来回到 v1 时
    它会被采纳，播的却是 v2 的帧（比"没有缓存"更糟）。宁可这一轮不产出，下一轮按
    新身份重转。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    stale_sha = fp.source_sha256(webm)

    def _swap_during_convert(_argv):
        _swap_source_keeping_size_and_mtime(webm, b"fake-webX", atomic=False)

    calls = _install_fake_ffmpeg(monkeypatch, on_convert=_swap_during_convert)

    converted, err = fp.convert_clip(webm, out_dir)

    assert len(calls) == 1                   # ffmpeg 跑完了，坏在发布闸门
    assert converted is False and "身份" in err
    assert not out_dir.exists()              # 半成品绝不发布
    assert not tmp_dir.exists()
    assert fp.source_sha256(webm) != stale_sha


def test_convert_clip_refuses_non_generation_target(tmp_path, monkeypatch):
    """输出目录名不是源身份命名 → 拒绝发布（不许再造无戳缓存、不拉 ffmpeg）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    legacy_target = frameseq / "idle" / "a"
    calls = _install_fake_ffmpeg(monkeypatch)

    converted, err = fp.convert_clip(webm, legacy_target)
    assert converted is False
    assert "世代" in err
    assert calls == []
    assert not legacy_target.exists()


def test_convert_clip_refuses_wrong_stamp_target(tmp_path, monkeypatch):
    """目录名里的戳与当前源不符（陈旧/伪造）→ 拒绝写进去。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    bogus = frameseq / "idle" / ("a" + fp.GEN_INFIX + "0" * fp.GEN_STAMP_HEX)
    calls = _install_fake_ffmpeg(monkeypatch)

    converted, err = fp.convert_clip(webm, bogus)
    assert converted is False and calls == []
    assert not bogus.exists()


def test_convert_clip_replaces_invalid_leftover_at_same_path(tmp_path, monkeypatch):
    """同路径存在「同名但不可采纳」的残留（半拷贝/损坏）→ 重转覆盖它。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _write_frames(out_dir, 1)                # 只有 1 帧、无 meta：不可采纳
    assert fp.is_complete(out_dir) is False

    calls = _install_fake_ffmpeg(monkeypatch)
    assert fp.convert_clip(webm, out_dir) == (True, "")
    assert len(calls) == 1
    assert fp.frames_on_disk(out_dir) == FRAME_COUNT
    assert fp.is_complete(out_dir) is True


def test_convert_clip_failure_cleans_tmp(tmp_path, monkeypatch):
    """ffmpeg 失败：清掉 tmp，世代目录不出现（半成品绝不留存）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _install_fake_ffmpeg(monkeypatch, fail=True)

    converted, err = fp.convert_clip(webm, out_dir)
    assert converted is False
    assert "stub failure" in err
    assert not out_dir.exists()
    assert not out_dir.with_name(out_dir.name + fp.TMP_SUFFIX).exists()


def test_convert_clip_publish_rename_failure_keeps_old_target(tmp_path, monkeypatch):
    """发布 rename 抛错：旧目标一个字节不丢、半成品清掉、按 (False, 文本) 返回。

    替换同路径旧内容（残留 / ``force`` 维修）时旧目标先挪到 ``.replacing``：发布
    失败必须回滚旧目标才算收口——「先删旧目标再 rename」的写法一旦 rename 抛出
    （Windows 上目标被活 reader 占住就是这个形态）就是净丢一份在用素材。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    aside = out_dir.with_name(out_dir.name + fp.REPLACE_SUFFIX)
    _write_frames(out_dir, 1, blob=b"old")       # 同路径残留：不可采纳，但内容不能丢
    calls = _install_fake_ffmpeg(monkeypatch)
    attempts = _fail_publish_rename(monkeypatch, out_dir)

    converted, err = fp.convert_clip(webm, out_dir)

    assert converted is False and "目标目录被占用" in err
    assert len(calls) == 1                       # 失败只在发布这一步，ffmpeg 已跑完
    assert [dst for _src, dst in attempts] == [aside, out_dir, out_dir]
    assert (out_dir / "f_0001.webp").read_bytes() == b"old"   # 旧目标复位、内容原样
    assert fp.frames_on_disk(out_dir) == 1
    assert fp.is_complete(out_dir) is False      # 复位的是旧内容，不是半成品
    assert not aside.exists()                    # 不留 .replacing 残壳
    assert not out_dir.with_name(out_dir.name + fp.TMP_SUFFIX).exists()


def test_convert_clip_recovers_from_stray_workbench_file(tmp_path, monkeypatch):
    """半成品路径被同名普通文件占住（手工残留）：清掉它继续转，不掀整轮。

    ``rmtree`` 对普通文件是静默失败（``ignore_errors=True``）+ ``mkdir`` 抛
    ``FileExistsError`` = 整轮被一个残留文件带走。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    stray = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"junk")
    calls = _install_fake_ffmpeg(monkeypatch)

    assert fp.convert_clip(webm, out_dir) == (True, "")
    assert len(calls) == 1
    assert out_dir.is_dir() and stray.is_file() is False
    assert fp.is_complete(out_dir, sha=fp.source_sha256(webm)) is True


@pytest.mark.parametrize("seam, expected", [
    ("_popen", "ffmpeg 无法启动"),
    ("_write_meta", "meta 写入失败"),
])
def test_convert_clip_os_failure_returns_error_not_raises(
        tmp_path, monkeypatch, seam, expected):
    """OS 层 I/O 失败（exe 起不来 / meta 写不进）同样按 (False, 文本) 收口。

    这类失败以前会直接抛出，把一个 clip 的坏运气变成整轮供给的终结。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _install_fake_ffmpeg(monkeypatch)

    def _boom(*_args, **_kwargs):
        raise OSError("io 坏了")

    monkeypatch.setattr(fp, seam, _boom)

    converted, err = fp.convert_clip(webm, out_dir)

    assert converted is False and expected in err and "io 坏了" in err
    assert not out_dir.exists()
    assert not out_dir.with_name(out_dir.name + fp.TMP_SUFFIX).exists()


def test_convert_clip_refuses_to_replace_live_generation(tmp_path, monkeypatch):
    """在用世代目录绝不替换：本进程已采纳的目录（活 clip 可能正读）即使已不可
    采纳也不动它；``force`` 是显式维修口，允许重建。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)
    (gen / "meta.json").unlink()                 # 采纳后被外部破坏
    fp.protect_generations([gen])

    calls = _install_fake_ffmpeg(monkeypatch)
    converted, err = fp.convert_clip(webm, gen)
    assert converted is False and "在用" in err
    assert calls == []                           # 连 ffmpeg 都不拉
    assert fp.frames_on_disk(gen) == FRAME_COUNT  # 帧一个不少

    assert fp.convert_clip(webm, gen, force=True) == (True, "")
    assert len(calls) == 1
    assert fp.is_complete(gen, sha=fp.source_sha256(webm)) is True


def test_convert_clip_cancelled_before_spawn(tmp_path, monkeypatch):
    """取消谓词已置位：不拉 ffmpeg、不留目录。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    calls = _install_fake_ffmpeg(monkeypatch)
    converted, err = fp.convert_clip(
        webm, fp.clip_out_dir(webm, videos, frameseq), cancelled=lambda: True)
    assert (converted, err) == (False, "")
    assert calls == []


def test_plan_clips_excludes_complete(tmp_path):
    """待供给清单只含未完成的 clip；范围 = 热集 + random + events（冷集也参与）。"""
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm", "b.webm"], "random": ["r.webm"]})
    _stamped(videos / "idle" / "a.webm", videos, frameseq)
    plan = fp.plan_clips(videos, frameseq)
    assert [w.relative_to(videos).as_posix() for w, _ in plan] == [
        "idle/b.webm", "random/r.webm"]
    assert plan[0] == (videos / "idle" / "b.webm",
                       fp.clip_out_dir(videos / "idle" / "b.webm", videos, frameseq))


# ---------------------------------------------------------------- 世代生命周期
def test_provision_marks_old_generation_and_keeps_it(tmp_path, monkeypatch):
    """源换版后发布新世代：旧世代只打退役标记、目录与帧一个都不删
    （它可能正被已创建的活 clip 读取）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    _install_fake_ffmpeg(monkeypatch)
    assert fp.provision_once(videos, frameseq).converted == 1
    old_gen = fp.clip_out_dir(webm, videos, frameseq)
    assert old_gen.is_dir()

    webm.write_bytes(b"fake-webm-v2")        # 源换版
    _install_fake_ffmpeg(monkeypatch)
    report = fp.provision_once(videos, frameseq)

    new_gen = fp.clip_out_dir(webm, videos, frameseq)
    assert report.converted == 1 and report.retired == 0
    assert new_gen.is_dir() and new_gen != old_gen
    assert old_gen.is_dir()                              # 未满宽限：不删
    assert fp.frames_on_disk(old_gen) == FRAME_COUNT     # 帧一个不少
    assert fp.retired_marker(old_gen).is_file()          # 已标记退役时刻
    assert fp.retired_age_s(old_gen) < 60.0
    assert fp.pending_retirements(videos, frameseq) == []   # 宽限内：无待清扫


def test_reverted_generation_grace_restarts_when_superseded_again(tmp_path, monkeypatch):
    """回朝再退役：世代中途回朝成当前世代后又被取代 → 退役期从这次离场重算。

    标记记的是"距上一次离场的时长"，sidecar 不会因源内容回退而消失。不作废它，
    回朝前那段旧龄会让它在再次离场的同一轮就被清掉——跨进程唯一缓冲（宽限窗口）
    形同虚设，另一个实例里还在读它的 clip 直接踩空。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    v1 = webm.read_bytes()
    _install_fake_ffmpeg(monkeypatch)
    fp.provision_once(videos, frameseq)
    gen1 = fp.clip_out_dir(webm, videos, frameseq)

    webm.write_bytes(b"fake-webm-v2")               # 换版：gen1 被取代并打标记
    _install_fake_ffmpeg(monkeypatch)
    fp.provision_once(videos, frameseq)
    gen2 = fp.clip_out_dir(webm, videos, frameseq)
    assert gen2 != gen1 and fp.retired_marker(gen1).is_file()

    webm.write_bytes(v1)                            # 回朝：源退回 gen1 那一版
    assert fp.current_generation(webm, videos, frameseq) == gen1
    _age(fp.retired_marker(gen1), days=2.0)         # 回朝前的旧龄：已超宽限
    assert fp.pending_retirements(videos, frameseq) == []   # 当前世代：旧标记不生效
    assert fp.stale_current_markers(videos, frameseq) == [gen1]   # 闸门据此起一轮
    fp.provision_once(videos, frameseq)             # 回朝窗口里的一轮：作废旧标记
    assert not fp.retired_marker(gen1).exists()
    assert fp.stale_current_markers(videos, frameseq) == []

    webm.write_bytes(b"fake-webm-v3")               # 再次离场：被新世代取代
    _install_fake_ffmpeg(monkeypatch)
    report = fp.provision_once(videos, frameseq)
    gen3 = fp.clip_out_dir(webm, videos, frameseq)

    assert report.converted == 1 and report.retired == 0
    assert gen3 != gen2 and gen3.is_dir()
    assert gen1.is_dir() and gen2.is_dir()          # 两个历史世代都还在宽限内
    assert fp.retired_age_s(gen1) < 60.0            # 新标记：从这次离场起算
    assert fp.pending_retirements(videos, frameseq) == []


def test_sweep_deletes_only_aged_retired_generation(tmp_path, monkeypatch):
    """清扫：已标记 + 满宽限 + 不在用 → 删除；当前世代与在用世代永不删。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    _install_fake_ffmpeg(monkeypatch)
    fp.provision_once(videos, frameseq)
    old_gen = fp.clip_out_dir(webm, videos, frameseq)
    webm.write_bytes(b"fake-webm-v2")
    _install_fake_ffmpeg(monkeypatch)
    fp.provision_once(videos, frameseq)
    new_gen = fp.clip_out_dir(webm, videos, frameseq)

    _age(fp.retired_marker(old_gen), days=2.0)
    assert fp.pending_retirements(videos, frameseq) == [old_gen]
    assert fp.sweep_retired(videos, frameseq) == [old_gen]
    assert not old_gen.exists()
    assert not fp.retired_marker(old_gen).exists()
    assert new_gen.is_dir() and fp.is_complete(new_gen) is True


def test_sweep_never_deletes_live_generation(tmp_path, monkeypatch):
    """在本进程被采纳过的世代目录即使已标记 + 远超宽限也不删（活 clip 在读）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    retired_gen = _stamped(webm, videos, frameseq)
    fp.protect_generations([retired_gen])
    assert fp.mark_retired(retired_gen) is True
    _age(fp.retired_marker(retired_gen), days=30.0)

    assert fp.pending_retirements(videos, frameseq) == []
    assert fp.sweep_retired(videos, frameseq) == []
    assert retired_gen.is_dir()


def test_sweep_never_deletes_current_generation(tmp_path, monkeypatch):
    """当前世代的目录永不删：哪怕被误标记 + 超宽限（源回退到旧内容时会重新变当前）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)
    assert fp.mark_retired(gen) is True
    _age(fp.retired_marker(gen), days=30.0)

    assert fp.pending_retirements(videos, frameseq) == []
    assert fp.sweep_retired(videos, frameseq) == []
    assert gen.is_dir() and fp.is_complete(gen, sha=fp.source_sha256(webm)) is True


def test_mark_retired_keeps_first_timestamp(tmp_path):
    """退役标记幂等：重复标记不刷新时刻（否则永远不会到宽限期）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    gen = _stamped(videos / "idle" / "a.webm", videos, frameseq)
    assert fp.mark_retired(gen) is True
    _age(fp.retired_marker(gen), days=3.0)
    assert fp.mark_retired(gen) is False
    assert fp.retired_age_s(gen) > 2 * DAY_S


def test_sweep_does_not_scan_frame_hashes(tmp_path, monkeypatch):
    """清扫只按目录名 + 标记 mtime 判定：不读 meta、不读帧内容。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    _stamped(webm, videos, frameseq)                     # 当前世代（不参与清扫）
    gen = _past_generation(webm, videos, frameseq)       # 上一版源的世代
    fp.mark_retired(gen)
    _age(fp.retired_marker(gen), days=2.0)
    for frame in gen.glob("f_*.webp"):
        frame.write_bytes(b"")

    real_open = open

    def _no_reads(file, *args, **kwargs):
        if str(file).startswith(str(gen)):
            raise AssertionError("清扫不得读取世代目录内容")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _no_reads)
    assert fp.sweep_retired(videos, frameseq) == [gen]


# ------------------------------------------------- 清扫可中断（关机窗口的有界收口）
def _retired_past_generations(webm: Path, videos: Path, frameseq: Path, count: int, *,
                              frames: int = FRAME_COUNT) -> list[Path]:
    """造 count 个「已标记退役 + 已满宽限」的上一版世代目录（待清扫候选）。

    戳必须**前 12 位**就不同（目录名只取前 12 位），否则多个"不同"戳会落到同一个
    目录名上，候选数悄悄退化成 1。
    """
    gens: list[Path] = []
    for i in range(count):
        stamp = f"{i + 1:012x}" + "0" * 52
        gen = _past_generation(webm, videos, frameseq, stamp=stamp, frames=frames)
        fp.mark_retired(gen)
        _age(fp.retired_marker(gen), days=2.0)
        gens.append(gen)
    assert len(set(gens)) == count
    return gens


def test_sweep_retired_stops_before_next_dir_when_cancelled(tmp_path):
    """取消谓词置位：清扫在**下一个目录开始前**停手，未开工的目录一个字节不动。

    取消判据取自文件系统（第一个世代删净即置位），不赌目录枚举顺序、不赌
    调用次数。库 shutdown 的 2s 有界等待要成立，清扫必须能在这里停。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gens = _retired_past_generations(webm, videos, frameseq, 3)

    removed = fp.sweep_retired(
        videos, frameseq, cancelled=lambda: any(not g.exists() for g in gens))

    assert len(removed) == 1                      # 只删了先手那一个
    left = [g for g in gens if g not in removed]
    assert len(left) == 2
    for gen in left:
        assert gen.is_dir()
        assert fp.frames_on_disk(gen) == FRAME_COUNT      # 未开工：帧一个不少
        assert fp.retired_marker(gen).is_file()           # 仍是候选：下一轮继续
    assert len(fp.pending_retirements(videos, frameseq)) == 2


def test_sweep_retired_aborts_mid_dir_with_bounded_granularity(tmp_path):
    """单个目录的删除必须**有界可停**：逐条目删、条目之间复查取消。

    ``shutil.rmtree`` 进目录后不可中断：一个上千帧的世代目录足以让关机窗口的
    2s 有界等待失效。取消后目录只被删掉一部分（绝非一口气删完），退役标记保留
    ——仍是待清扫候选，下一轮从断点续删。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    frames = 40
    gen = _retired_past_generations(webm, videos, frameseq, 1, frames=frames)[0]
    assert fp.frames_on_disk(gen) == frames

    def cancelled() -> bool:
        return frames - fp.frames_on_disk(gen) >= 2

    assert fp.sweep_retired(videos, frameseq, cancelled=cancelled) == []
    left = fp.frames_on_disk(gen)
    assert 0 < left < frames                       # 部分删除 = 条目粒度可中断
    assert gen.is_dir() and fp.retired_marker(gen).is_file()
    assert fp.pending_retirements(videos, frameseq) == [gen]

    assert fp.sweep_retired(videos, frameseq) == [gen]    # 无取消：续删收敛
    assert not gen.exists() and not fp.retired_marker(gen).exists()


def test_sweep_keeps_marker_when_one_entry_cannot_be_removed(tmp_path, monkeypatch):
    """单条目删不掉（Windows 上活 reader 占着句柄就是 ``PermissionError``）：
    目录不算已清扫、退役标记保留、下一轮续删——绝不把没删掉的目录记成清扫完成。

    "不删在读目录"在 OS 层就是这个形态：句柄一放下一轮就删干净。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _retired_past_generations(webm, videos, frameseq, 1, frames=5)[0]
    blocked = gen / "f_0003.webp"
    real_unlink = os.unlink
    state = {"deny": True}

    def _deny(path, *args, **kwargs):
        if state["deny"] and str(path) == str(blocked):
            raise PermissionError(13, "占位：读在用的帧文件占着句柄")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", _deny)
    assert fp.sweep_retired(videos, frameseq) == []
    assert blocked.exists() and gen.is_dir()
    assert fp.retired_marker(gen).is_file()               # 标记保留：仍是候选
    assert fp.pending_retirements(videos, frameseq) == [gen]

    state["deny"] = False                                 # 句柄释放
    assert fp.sweep_retired(videos, frameseq) == [gen]
    assert not gen.exists() and not fp.retired_marker(gen).exists()


def test_sweep_retired_stops_when_session_ending(tmp_path):
    """issue #111：会话结束闸门置位 → 一个世代目录都不动（关机窗口不做批量删除）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gens = _retired_past_generations(webm, videos, frameseq, 2)

    set_session_ending(True)
    try:
        assert fp.sweep_retired(videos, frameseq) == []
        for gen in gens:
            assert gen.is_dir() and fp.frames_on_disk(gen) == FRAME_COUNT
            assert fp.retired_marker(gen).is_file()
    finally:
        set_session_ending(False)


def test_provision_once_forwards_cancel_predicate_to_sweep(tmp_path, monkeypatch):
    """取消谓词必须一路走到清扫（否则 shutdown 的有界等待对清扫形同虚设）。

    黑盒口径：两个满宽限退役世代 + 「第一个删净即置位」的谓词 → 只删一个，
    另一个原封不动；取消后本轮不再进入转换（另一个待转 clip 不拉 ffmpeg）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"], "move": ["b.webm"]})
    webm = videos / "idle" / "a.webm"
    gens = _retired_past_generations(webm, videos, frameseq, 2)
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(
        videos, frameseq, exe="fake-ffmpeg",
        cancelled=lambda: any(not g.exists() for g in gens))

    assert report.retired == 1
    assert [g.is_dir() for g in gens].count(True) == 1
    assert report.converted == 0 and calls == []


# ---------------------------------------------------------------- 互斥 / 闸门 / 逃生门
def test_provision_once_lock_mutex(tmp_path, monkeypatch):
    """另一实例持有 QLockFile：直接放弃本轮（locked=True），不等待、不转换。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    frameseq.mkdir(parents=True)
    held = QLockFile(str(frameseq / fp.LOCK_NAME))
    assert held.tryLock(0) is True
    try:
        calls = _install_fake_ffmpeg(monkeypatch)
        report = fp.provision_once(videos, frameseq)
        assert report.locked is True
        assert (report.converted, report.skipped, report.failed, report.retired) == (0, 0, 0, 0)
        assert calls == []
        assert list(fp.scan_generations(frameseq)) == []
    finally:
        held.unlock()

    # 锁释放后可正常转（证明上一步唯一差异就是锁）
    calls = _install_fake_ffmpeg(monkeypatch)
    report = fp.provision_once(videos, frameseq)
    assert report.locked is False
    assert report.converted == 1
    assert len(calls) == 1


def test_lock_retry_delay_grows_and_caps():
    """锁重试的退让阶梯：逐次翻倍、封顶，纯数据（重试本身在库侧排期）。"""
    waits = [fp.lock_retry_delay_ms(attempt) for attempt in range(1, 7)]

    assert waits[0] == fp.PROVISION_LOCK_RETRY_DELAY_MS
    assert waits == sorted(waits)
    assert waits[-1] <= fp.PROVISION_LOCK_RETRY_MAX_DELAY_MS
    assert fp.lock_retry_delay_ms(64) == fp.PROVISION_LOCK_RETRY_MAX_DELAY_MS


def test_provision_once_stops_after_session_ending(tmp_path, monkeypatch):
    """issue #111 闸门：第一个 clip 转换期间会话结束 → 不再起后续 clip，且正在跑的
    那一段**在阶段二立刻停手**（半成品丢弃，不在关机窗口里再烧一遍反解+编码）。

    改两段式之前，这一轮会把阶段一已跑完的那段发布出去（闸门管的是"不再起新的活"）；
    现在阶段一之后还有一段秒级的 Python 工作（逐帧反解 + Q70 重编码），关机窗口里
    必须连它也停住——所以这一段被丢弃（``skipped``），下次启动按需重转。「不再起新活」
    的部分照旧：第二个 clip 连 ffmpeg 都不派生。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm", "b.webm"]})
    calls = _install_fake_ffmpeg(
        monkeypatch, on_convert=lambda _argv: set_session_ending(True))

    report = fp.provision_once(videos, frameseq)
    assert (report.converted, report.skipped) == (0, 1)
    assert len(calls) == 1                       # 只派生了第一个 clip
    assert fp.current_generation(
        videos / "idle" / "a.webm", videos, frameseq) is None    # 半成品已丢弃
    assert fp.current_generation(
        videos / "idle" / "b.webm", videos, frameseq) is None
    assert fp.plan_clips(videos, frameseq)       # 两个 clip 都还在待转清单里


def test_provision_once_disabled_by_env(tmp_path, monkeypatch):
    """PET_FRAMESEQ=0：整体禁用（dev 逃生门），零转换零进程。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    monkeypatch.setenv(fp.ENV_DISABLE, "0")
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)
    assert (report.converted, report.skipped, report.failed) == (0, 0, 0)
    assert calls == []
    assert list(fp.scan_generations(frameseq)) == []


def test_provision_once_no_ffmpeg_is_silent(tmp_path, monkeypatch):
    """无 ffmpeg exe：编码静默 no-op（不计失败、不拉进程），但世代清扫照做。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    monkeypatch.setattr(fp, "ffmpeg_exe", lambda: None)
    _stamped(webm, videos, frameseq)                     # 当前世代（保留）
    gen = _past_generation(webm, videos, frameseq)       # 上一版源的世代
    fp.mark_retired(gen)
    _age(fp.retired_marker(gen), days=2.0)

    report = fp.provision_once(videos, frameseq)
    assert (report.converted, report.skipped, report.failed) == (0, 0, 0)
    assert report.retired == 1
    assert not gen.exists()                  # 清扫不依赖 ffmpeg（纯本地 I/O）


def test_publish_failure_does_not_lose_whole_round(tmp_path, monkeypatch):
    """一个 clip 发布失败不许掀掉整轮：后续 clip 照转、失败计入报告、旧目标不丢。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm", "b.webm"]})
    first = videos / "idle" / "a.webm"
    first_out = fp.clip_out_dir(first, videos, frameseq)
    _write_frames(first_out, 1, blob=b"old")     # 首个 clip 同路径残留（不可采纳）
    calls = _install_fake_ffmpeg(monkeypatch)
    _fail_publish_rename(monkeypatch, first_out)

    report = fp.provision_once(videos, frameseq)

    assert (report.locked, report.converted, report.failed) == (False, 1, 1)
    assert (first_out / "f_0001.webp").read_bytes() == b"old"
    second = videos / "idle" / "b.webm"
    assert fp.current_generation(second, videos, frameseq) is not None
    assert len(calls) == 2


# ------------------------------------------- 首跑预算 / 失败退避（升级迁移闸门）
def _webm_of(legacy: Path) -> str:
    """旧产物目录 ``<stem>/`` → clip 文件名（``a`` → ``a.webm``）。"""
    return legacy.name + ".webm"


def _legacy_hot_set(videos: Path, frameseq: Path, folder: str,
                    names: tuple[str, ...]) -> list[Path]:
    """真实升级形态：一个热集目录下每段 clip 都带一份旧版无戳 ``<stem>/`` 产物。"""
    return [_legacy_dir(videos / folder / f"{n}.webm", videos, frameseq)
            for n in names]


def test_migration_round_converts_single_clip_per_start(tmp_path, monkeypatch):
    """升级首跑（旧无戳热集在场）：本轮只迁 1 个 clip，余下留到下次启动。

    旧产物是"等价缓存"而不是身份凭证——它既不被采纳也不被改写，所以升级不属于
    "没得用"的场景：没必要一次烧掉 11 个 clip 的 ffmpeg（约 3 分钟后台重编码）。
    """
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm", "b.webm", "c.webm"]})
    legacy = _legacy_hot_set(videos, frameseq, "idle", ("a", "b", "c"))
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, report.failed, report.deferred_budget) == (1, 0, 2)
    assert len(calls) == 1
    migrated = [p for p in legacy if fp.current_generation(
        videos / "idle" / _webm_of(p), videos, frameseq) is not None]
    assert len(migrated) == 1
    for path in legacy:            # 旧产物：不删、不改写、不补戳、不打退役标记
        assert path.is_dir()
        assert "source_sha256" not in json.loads(
            (path / "meta.json").read_text(encoding="utf-8"))
        assert fp.is_complete(path) is False
        assert not fp.retired_marker(path).exists()


def test_migration_quota_is_one_per_launch_across_libraries(tmp_path, monkeypatch):
    """迁移配额按**启动周期**结算，不是每库一份：三宠三库共用一个素材根。

    审计结论：``MIGRATION_CLIPS_PER_ROUND = 1`` 实际按 MovieLibrary 轮记账，三宠三库
    会依次各迁一个（一个启动周期共 3 个）。配额是**进程内共享**的台账（进程 = 一次
    启动），这里用两个角色目录各跑一轮（= 两个库各排期一次）证明它确实共享：第二个
    库一个都不迁，余下留到下次启动。
    """
    videos_a, frameseq = _make_pack(tmp_path / "char_a", {"idle": ["a.webm"]})
    videos_b, _ = _make_pack(tmp_path / "char_b", {"idle": ["b.webm"]})
    _legacy_hot_set(videos_a, frameseq, "idle", ("a",))
    _legacy_hot_set(videos_b, frameseq, "idle", ("b",))
    calls = _install_fake_ffmpeg(monkeypatch)

    first = fp.provision_once(videos_a, frameseq)     # 先拿到锁的库（角色 A）
    second = fp.provision_once(videos_b, frameseq)    # 同一启动周期里的角色 B

    assert (first.converted, first.deferred_budget) == (1, 0)
    assert (second.converted, second.deferred_budget) == (0, 1)
    assert len(calls) == 1                            # 一次启动只派生一个 ffmpeg
    assert fp.current_generation(
        videos_a / "idle" / "a.webm", videos_a, frameseq) is not None
    assert fp.current_generation(
        videos_b / "idle" / "b.webm", videos_b, frameseq) is None


def test_next_launch_migrates_one_more_clip(tmp_path, monkeypatch):
    """预算按「每次启动」结算：下一次启动（新进程 → 台账归零）再迁 1 个。

    台账是进程内状态（进程 = 一次启动），所以"下一次启动"在这里用
    ``reset_migration_slots()`` 模拟——不能像审计前那样拿"同进程再跑一轮"当
    下一次启动：那正是每库一份配额的错误口径。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm", "b.webm"]})
    _legacy_hot_set(videos, frameseq, "idle", ("a", "b"))
    _install_fake_ffmpeg(monkeypatch)

    first = fp.provision_once(videos, frameseq)
    fp.reset_migration_slots()               # 新进程 = 新启动周期
    second = fp.provision_once(videos, frameseq)

    assert (first.converted, first.deferred_budget) == (1, 1)
    assert (second.converted, second.deferred_budget) == (1, 0)
    assert all(fp.current_generation(videos / "idle" / f"{n}.webm", videos,
                                     frameseq) is not None for n in ("a", "b"))


def test_fresh_install_round_is_not_quota_limited(tmp_path, monkeypatch):
    """无旧产物（全新安装）：**不受迁移配额约束**——帧序列是那里唯一来源。

    但"不受配额约束"不等于"必须一轮转完"：起手上限到点照样收手，余下留到下次启动
    （见 ``test_round_time_cap_defers_rest_to_next_start``，它用的正是全新安装形态）。
    """
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm", "b.webm"], "move": ["m.webm"]})
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, report.deferred_budget) == (3, 0)
    assert report.budget_stopped is False
    assert len(calls) == 3


def test_plan_supply_order_is_idle_then_move_then_rest(tmp_path):
    """供给序 = idle → move → 其余（档内按相对路径）：确定性，不赌目录枚举顺序。

    ``hot_webms`` 仍按相对路径排序（构建期工具与库扫描共用）；供给序落在
    ``plan_clips`` 上：迁移配额一个启动周期只有 1 个，字典序会让 click 长期霸占
    这个名额（审计结论），而 idle/move 是播放时长占比最高的两类。
    """
    videos, frameseq = _make_pack(tmp_path, {
        "idle": ["b.webm", "a.webm"], "move": ["m.webm"], "click": ["c.webm"],
        "turn": ["t.webm"], "drag": ["d.webm"]})

    plan = fp.plan_clips(videos, frameseq)

    assert [webm.relative_to(videos).as_posix() for webm, _ in plan] == [
        "idle/a.webm", "idle/b.webm", "move/m.webm",
        "click/c.webm", "drag/d.webm", "turn/t.webm"]


def test_migration_quota_is_spent_on_idle_before_click(tmp_path, monkeypatch):
    """迁移配额先给 idle：三档都带旧产物、只迁 1 个时，迁的必须是 idle 那个。"""
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["i.webm"], "move": ["m.webm"], "click": ["c.webm"]})
    for folder, name in (("idle", "i"), ("move", "m"), ("click", "c")):
        _legacy_hot_set(videos, frameseq, folder, (name,))
    _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, report.deferred_budget) == (1, 2)
    assert fp.current_generation(
        videos / "idle" / "i.webm", videos, frameseq) is not None
    assert fp.current_generation(
        videos / "click" / "c.webm", videos, frameseq) is None


def test_migration_quota_does_not_starve_clips_without_legacy(tmp_path, monkeypatch):
    """配额只管「有等价旧产物」的 clip：无旧产物的 clip 本轮照转，不被配额饿死。"""
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm", "b.webm"], "move": ["m.webm"]})
    _legacy_hot_set(videos, frameseq, "idle", ("a", "b"))
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, report.deferred_budget) == (2, 1)
    assert len(calls) == 2
    assert fp.current_generation(
        videos / "move" / "m.webm", videos, frameseq) is not None


def test_round_time_cap_defers_rest_to_next_start(tmp_path, monkeypatch):
    """起手上限：到点不再起下一个 clip，余下留到下次启动（本轮不再派生 ffmpeg）。

    注意"上限"是**准入线**：最后一个已起手的 clip 会跑完（不硬杀 ffmpeg），所以
    实际墙钟必然可能超出——见 ``test_round_budget_is_start_cap_not_wallclock``。
    """
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm", "b.webm", "c.webm"]})
    now = [0.0]
    calls = _install_fake_ffmpeg(
        monkeypatch, on_convert=lambda _argv: now.__setitem__(0, now[0] + 100.0))

    report = fp.provision_once(videos, frameseq, round_seconds=120.0,
                               clock=lambda: now[0])

    assert (report.converted, report.deferred_budget,
            report.budget_stopped) == (2, 1, True)
    assert len(calls) == 2
    assert fp.current_generation(
        videos / "idle" / "c.webm", videos, frameseq) is None


def test_round_budget_is_start_cap_not_wallclock(tmp_path, monkeypatch, caplog):
    """上限是起手线而不是墙钟硬上限，且日志必须把"可能超出一个 clip"说清楚。

    审计：预算只在起 clip 前检查，最后一个 clip 照跑到底（不硬杀 ffmpeg），所以
    "本轮墙钟 ≤ 上限"是假的；日志若只说"按预算收手"，读日志的人会以为本轮没有溢出。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm", "b.webm"]})
    now = [0.0]
    calls = _install_fake_ffmpeg(
        monkeypatch, on_convert=lambda _argv: now.__setitem__(0, now[0] + 400.0))

    with caplog.at_level(logging.INFO, logger=fp.logger.name):
        report = fp.provision_once(videos, frameseq, round_seconds=120.0,
                                   clock=lambda: now[0])

    assert (report.converted, report.deferred_budget,
            report.budget_stopped) == (1, 1, True)
    assert [proc.returncode for proc in calls] == [0]   # 在飞 clip 跑完，没被 terminate
    assert "起手上限" in caplog.text
    assert "超出" in caplog.text and "一个 clip" in caplog.text


def test_failed_clip_is_backed_off_and_skipped_without_spawn(tmp_path, monkeypatch):
    """失败退避：失败的 clip 落盘记一笔，退避窗口内连 ffmpeg 都不派生。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm", "b.webm"]})
    webm_a = videos / "idle" / "a.webm"
    key = fp.backoff_key(webm_a, videos, frameseq)
    _install_fake_ffmpeg(monkeypatch, fail_on=lambda argv: any(
        str(arg).endswith("a.webm") for arg in argv))

    first = fp.provision_once(videos, frameseq)

    assert (first.converted, first.failed) == (1, 1)
    table = fp.read_backoff(frameseq)
    assert table[key]["failures"] == 1
    assert fp.backoff_remaining(table, key, now=time.time()) > 0

    calls = _install_fake_ffmpeg(monkeypatch)
    second = fp.provision_once(videos, frameseq)

    assert (second.converted, second.failed, second.deferred_backoff) == (0, 0, 1)
    assert calls == []                      # 退避窗口内：进程都不派生
    assert fp.current_generation(webm_a, videos, frameseq) is None


def test_migration_budget_counts_attempts_not_successes(tmp_path, monkeypatch):
    """配额按**尝试**记账：迁移 clip 挂了也吃掉本轮配额，一轮里不连着拉多个 ffmpeg。

    下一轮那个 clip 进了退避窗口 → 跳过（不占配额）→ 从下一个 clip 继续迁；所以
    一份坏素材既不掀掉整轮，也不让一次启动反复烧预算。
    """
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["a.webm", "b.webm", "c.webm"]})
    _legacy_hot_set(videos, frameseq, "idle", ("a", "b", "c"))
    calls = _install_fake_ffmpeg(monkeypatch, fail_on=lambda argv: any(
        str(arg).endswith("a.webm") for arg in argv))

    first = fp.provision_once(videos, frameseq)

    assert (first.converted, first.failed, first.deferred_budget) == (0, 1, 2)
    assert len(calls) == 1                       # 只尝试了 a，没去试 b/c
    calls = _install_fake_ffmpeg(monkeypatch)
    fp.reset_migration_slots()                   # 下一次启动（新进程）

    second = fp.provision_once(videos, frameseq)

    assert (second.converted, second.failed) == (1, 0)
    assert len(calls) == 1
    assert any(fp.current_generation(videos / "idle" / f"{n}.webm", videos,
                                     frameseq) is not None for n in ("b", "c"))
    assert fp.current_generation(
        videos / "idle" / "a.webm", videos, frameseq) is None


def test_backoff_write_failure_warns_instead_of_claiming_backoff(
        tmp_path, monkeypatch, caplog):
    """退避表写失败：warning 必须说**没落盘**，不许谎称"Ns 内不再重试"。

    审计：``write_backoff`` 的返回值此前被丢掉，日志照说"300s 内不再重试"，而实际
    下一次启动会立刻重试这个 clip——日志与行为相反，读日志的人会以为退避生效了。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    _install_fake_ffmpeg(monkeypatch, fail=True)
    monkeypatch.setattr(fp, "write_backoff", lambda _root, _table: False)

    with caplog.at_level(logging.WARNING, logger=fp.logger.name):
        report = fp.provision_once(videos, frameseq)

    assert report.failed == 1
    assert "退避表写入失败" in caplog.text
    assert "下次启动" in caplog.text
    assert "不再重试" not in caplog.text          # 不许声称已退避
    assert fp.read_backoff(frameseq) == {}


def test_backoff_window_expiry_retries_and_success_clears_entry(tmp_path, monkeypatch):
    """退避窗口到期即重试；一旦成功立刻销账（退避不许变成永久拉黑）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    key = fp.backoff_key(webm, videos, frameseq)
    frameseq.mkdir(parents=True, exist_ok=True)   # 供给轮自己建根目录（这里手工预置）
    table = fp.read_backoff(frameseq)
    fp.note_failure(table, key, "boom", now=time.time() - fp.BACKOFF_BASE_S - 1.0)
    assert fp.write_backoff(frameseq, table) is True
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, report.deferred_backoff) == (1, 0)
    assert len(calls) == 1
    assert fp.read_backoff(frameseq) == {}
    assert not (frameseq / fp.BACKOFF_NAME).exists()   # 空表 = 不留常驻 sidecar


def test_backoff_delay_grows_exponentially_and_caps():
    """退避阶梯：BASE × 2^(n-1)，到顶封顶；销账只删条目（纯数据，不碰盘）。"""
    table: dict = {}
    key = "idle/a"
    now = 1000.0

    waits = [fp.note_failure(table, key, now=now) for _ in range(3)]
    assert waits == [fp.BACKOFF_BASE_S, fp.BACKOFF_BASE_S * 2,
                     fp.BACKOFF_BASE_S * 4]
    assert fp.note_failure(table, key, now=now) == fp.BACKOFF_BASE_S * 8
    for _ in range(64):                       # 指数不许溢出，也不许无限拉长
        last = fp.note_failure(table, key, now=now)
    assert last == fp.BACKOFF_MAX_S
    assert fp.backoff_remaining(table, key, now=now) == fp.BACKOFF_MAX_S
    assert fp.backoff_remaining(table, key, now=now + fp.BACKOFF_MAX_S + 1.0) == 0.0
    assert fp.note_success(table, key) is True
    assert table == {}
    assert fp.note_success(table, key) is False          # 幂等
    assert fp.backoff_remaining(table, key, now=now) == 0.0


def test_corrupt_backoff_sidecar_is_ignored(tmp_path, monkeypatch):
    """退避 sidecar 损坏：当空表处理——供给绝不能被一个坏文件卡死。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    frameseq.mkdir(parents=True, exist_ok=True)
    (frameseq / fp.BACKOFF_NAME).write_text("{ 不是 json", encoding="utf-8")
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, report.deferred_backoff) == (1, 0)
    assert len(calls) == 1
    assert fp.read_backoff(frameseq) == {}


def test_round_logs_budget_deferral(tmp_path, monkeypatch, caplog):
    """日志：预算搁置必须留痕（读日志能看懂 ffmpeg 为什么只转了 1 个就停）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm", "b.webm"]})
    _legacy_hot_set(videos, frameseq, "idle", ("a", "b"))
    _install_fake_ffmpeg(monkeypatch)

    with caplog.at_level(logging.INFO, logger=fp.logger.name):
        report = fp.provision_once(videos, frameseq)

    assert report.deferred_budget == 1
    assert "预算搁置 1 个" in caplog.text


# ---------------------------------------------------------------- 库接线
def test_maybe_provision_disabled_no_worker(tmp_path, monkeypatch):
    """PET_FRAMESEQ=0 时 maybe_provision_frameseq 连排期都不做。"""
    videos, _ = _make_pack(tmp_path, {"idle": ["x.webm"]})
    monkeypatch.setenv(fp.ENV_DISABLE, "0")
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    _light_clips(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_events(0.2)
        assert lib._frameseq_provision_requested is False
        assert lib._frameseq_worker is None
    finally:
        lib.shutdown()


def test_library_ignores_unstamped_legacy_and_keeps_webm(tmp_path, monkeypatch):
    """真实部署形态（无戳 153MB 帧集在场）：库不采纳、movie() 走 WebM（不冻结）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    legacy = _legacy_dir(videos / "idle" / "x.webm", videos, frameseq)
    _light_clips(monkeypatch)

    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}
        assert isinstance(lib.movie("x"), _FakeClip)     # WebM 路径逐行不变
        assert legacy.is_dir()                            # 旧目录不被删也不被补戳
        assert "source_sha256" not in json.loads(
            (legacy / "meta.json").read_text(encoding="utf-8"))
    finally:
        lib.shutdown()


def test_library_falls_back_when_source_changed(tmp_path, monkeypatch):
    """源换版：旧世代（带戳但对应旧源）立即不采纳 → 回退 WebM。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    old_gen = _stamped(webm, videos, frameseq)
    webm.write_bytes(b"fake-webm-v2")
    _light_clips(monkeypatch)

    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}
        assert isinstance(lib.movie("x"), _FakeClip)
        assert old_gen.is_dir()
    finally:
        lib.shutdown()


def test_library_rejects_v3_generation_and_adopts_v5(tmp_path, monkeypatch):
    """改档回归：磁盘上的 **v3** 世代不再被采纳，按当前档重出的同一路径才被采纳。

    ``white-v5`` 相对 v3 只多一步 alpha 底板清理（把 ``a == 1`` 的整块背景清成 0），
    对库来说这就是一次改档：世代目录名随**源身份**（不变），档位在 meta 里 —— 所以
    "同一路径上换成新档产物"正是升级时的真实形态（供给按当前档重转同一目录）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    v3_encoder = "libwebp lossy q70 (bgra straight, unblend-white-v3)"
    assert v3_encoder in fp.LEGACY_ENCODER_DESCS
    gen = fp.clip_out_dir(webm, videos, frameseq)
    _write_frames(gen, FRAME_COUNT)
    (gen / "meta.json").write_text(json.dumps({
        "fps": 24.0, "source": webm.name, "frames": FRAME_COUNT,
        "encoder": v3_encoder, "unblend": "white-v3",
        "source_sha256": fp.source_sha256(webm)}), encoding="utf-8")
    _light_clips(monkeypatch)

    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}                   # v3 世代不采纳
        assert isinstance(lib.movie("x"), _FakeClip)      # 回退 WebM（不冻结、不播旧档）
        assert fp.current_generation(webm, videos, frameseq) is None
        assert [pair[0] for pair in fp.plan_clips(videos, frameseq)] == [webm]

        (gen / "meta.json").write_text(               # 同一路径按当前档重出
            json.dumps(_meta(webm, FRAME_COUNT)), encoding="utf-8")
        lib.rescan_frameseq()

        assert lib._frameseq_dirs == {"x": gen}           # 新档世代被采纳
        assert fp.plan_clips(videos, frameseq) == []
    finally:
        lib.shutdown()


def test_library_protects_adopted_generation(tmp_path, monkeypatch):
    """库采纳的世代目录立刻登记为「在用」——清扫据此跳过（活 clip 在读）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    gen = _stamped(webm, videos, frameseq)
    _light_clips(monkeypatch)

    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {"x": gen}
        assert gen in fp.live_generations()
        fp.mark_retired(gen)
        _age(fp.retired_marker(gen), days=30.0)
        assert fp.sweep_retired(videos, frameseq) == []
        assert gen.is_dir()
    finally:
        lib.shutdown()


def test_source_swap_keeps_playing_generation_alive(tmp_path, monkeypatch):
    """源被同尺寸 + mtime 复原地换掉：库不再采纳旧世代，但**在播的旧世代不删**。

    已创建的 FrameSeqClip 不换实现（``movie()`` 有缓存，它继续读旧世代目录），所以
    旧世代必须留在「在用」集合里：源换版只让它出「当前世代」这道闸，删除闸门
    （已标记 + 满宽限 + 非当前 + 非在用）依然放行不了它。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    gen = _stamped(webm, videos, frameseq)
    _light_clips(monkeypatch)

    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {"x": gen}       # 换源前：采纳
        assert isinstance(lib.movie("x"), _FakeClip)  # 活 clip 正在读这个世代目录

        _swap_source_keeping_size_and_mtime(webm, b"fake-webX", atomic=False)
        lib.rescan_frameseq()

        assert lib._frameseq_dirs == {}               # 不再拿旧世代的帧当当前源
        assert isinstance(lib.movie("x"), _FakeClip)  # 回退 WebM（不冻结、不播错素材）
        assert gen in fp.live_generations()           # 在播世代仍在用集合里

        fp.mark_retired(gen)                          # 供给轮给被取代的世代打标记
        _age(fp.retired_marker(gen), days=30.0)       # 远超宽限
        assert fp.sweep_retired(videos, frameseq) == []
        assert gen.is_dir()
        assert fp.frames_on_disk(gen) == FRAME_COUNT  # 帧一个不少
    finally:
        lib.shutdown()


def test_maybe_provision_then_rescan_switches_new_clip(tmp_path, monkeypatch):
    """端到端：延迟触发 → 后台转换 → 完成回调 rescan → 新 clip 走帧序列。

    这里源已换版（旧世代过期），因此转换同时证明「过期世代不会被拿来蒙混」；
    范围内的冷集（random）这一轮一起转（有损档）——不再是"冷集仍走 webm"。
    """
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["x.webm"], "random": ["r.webm"]})
    webm = videos / "idle" / "x.webm"
    old_gen = _stamped(webm, videos, frameseq)
    webm.write_bytes(b"fake-webm-v2")
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    calls = _install_fake_ffmpeg(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}                  # 过期世代不采纳
        lib.maybe_provision_frameseq()
        lib.maybe_provision_frameseq()  # 幂等：只排一次
        _pump_until(lambda: lib._frameseq_worker is None
                    and set(lib._frameseq_dirs) == {"x", "r"})
        new_gen = fp.clip_out_dir(webm, videos, frameseq)
        assert len(calls) == 2                           # 热集 + random 各一段
        assert lib._frameseq_dirs["x"] == new_gen
        assert new_gen != old_gen and old_gen.is_dir()   # 旧世代保留（宽限期）
        r_gen = lib._frameseq_dirs["r"]                  # 冷集这一轮也转成帧序列
        assert json.loads((r_gen / "meta.json").read_text(
            encoding="utf-8"))["encoder"] == fp.ENCODER_DESC
    finally:
        lib.shutdown()


def test_upgrade_round_provisions_one_clip_and_rescans(tmp_path, monkeypatch):
    """库接线（升级形态）：旧无戳热集在场时一次启动只迁 1 段，其余仍走 webm。

    「旧缓存不存在仍自动供给」由 ``test_maybe_provision_then_rescan_switches_new_clip``
    守着；本条守另一半：有等价旧产物时供给照排期，但一次启动只花一个 clip 的代价。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm", "y.webm"]})
    legacy = _legacy_hot_set(videos, frameseq, "idle", ("x", "y"))
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    calls = _install_fake_ffmpeg(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}                  # 旧无戳产物不采纳
        assert isinstance(lib.movie("x"), _FakeClip)     # 迁移完成前：webm 照播
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None and lib._frameseq_dirs)
        assert len(calls) == 1                           # 一次启动只迁一段
        assert set(lib._frameseq_dirs) == {"x"}           # 清单序：x 先迁
        assert all(path.is_dir() for path in legacy)      # 旧目录一个不删
    finally:
        lib.shutdown()


def test_lock_contention_does_not_permanently_abandon_other_library(tmp_path, monkeypatch):
    """锁被占（另一实例 / 同进程先拿到的库）：有界重试——锁一松开立刻补上。

    三宠三库在同一个素材根上各自排期，QLockFile 只有先到的能跑；而库的首跑入口
    ``maybe_provision_frameseq`` 每库一个进程只排一次。没有重试，另外那些角色在
    这次启动里就再也不会供给（审计口径："锁失败永久放弃别的角色"）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    frameseq.mkdir(parents=True, exist_ok=True)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "PROVISION_LOCK_RETRY_DELAY_MS", 50)
    monkeypatch.setattr(fp, "PROVISION_LOCK_RETRY_LIMIT", 50)   # 别在断言期间耗尽
    calls = _install_fake_ffmpeg(monkeypatch)
    held = QLockFile(str(frameseq / fp.LOCK_NAME))
    assert held.tryLock(0) is True
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None
                    and lib._frameseq_lock_retries >= 1)
        assert lib._frameseq_dirs == {}       # 拿不到锁这一轮：一个 clip 都没转
        assert calls == []

        held.unlock()
        _pump_until(lambda: bool(lib._frameseq_dirs))
        assert len(calls) == 1                # 锁松开后补上，不是永久放弃
    finally:
        held.unlock()
        lib.shutdown()


def test_lock_retry_is_bounded(tmp_path, monkeypatch):
    """锁一直被占：重试有界（不无限轮询）——余下留到下次启动，也不是永久放弃。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    frameseq.mkdir(parents=True, exist_ok=True)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "PROVISION_LOCK_RETRY_DELAY_MS", 1)
    monkeypatch.setattr(fp, "PROVISION_LOCK_RETRY_LIMIT", 2)
    calls = _install_fake_ffmpeg(monkeypatch)
    held = QLockFile(str(frameseq / fp.LOCK_NAME))
    assert held.tryLock(0) is True
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None
                    and lib._frameseq_lock_retries >= 2)
        _pump_events(0.2)
        assert lib._frameseq_lock_retries == 2      # 不再排第三次
        assert lib._frameseq_worker is None
        assert calls == []
    finally:
        held.unlock()
        lib.shutdown()


def test_complete_hot_set_still_provisions_cold_set(tmp_path, monkeypatch):
    """热集已完整不等于"无事可做"：random/events 也在供给范围内，照样转。

    旧行为只转热集、冷集保留 webm（"热集已完整 → 静默 no-op"）。现在供给范围是
    热集 + random + events（冷集走有损 Q80），所以完整热集下这一轮仍要起 worker 转
    random 那个 clip；已完整的 idle 不重复编码，worker 收尾 rescan 后新 clip 采纳。
    """
    videos, frameseq = _make_pack(
        tmp_path, {"idle": ["x.webm"], "random": ["r.webm"]})
    hot_gen = _stamped(videos / "idle" / "x.webm", videos, frameseq)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    calls = _install_fake_ffmpeg(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None
                    and "r" in lib._frameseq_dirs)

        assert [Path(proc.argv[proc.argv.index("-i") + 1]).name
                for proc in calls] == ["r.webm"]      # 热集不重复转
        assert lib._frameseq_dirs["x"] == hot_gen
        meta = json.loads((lib._frameseq_dirs["r"] / "meta.json").read_text(
            encoding="utf-8"))
        assert meta["encoder"] == fp.ENCODER_DESC
    finally:
        lib.shutdown()


def test_reverted_generation_spawns_worker_to_reset_marker(tmp_path, monkeypatch):
    """回朝旧账也走供给闸门：当前世代还带着退役标记 → 拉 worker 把标记作废。

    没这一路，源回退期间的供给轮不会被排期，旧龄只能拖到它再次离场的那一轮
    （那一轮的清扫先跑，宽限期已经作废）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    gen = _stamped(webm, videos, frameseq)
    fp.mark_retired(gen)
    _age(fp.retired_marker(gen), days=2.0)          # 回朝前的旧龄：已超宽限
    assert fp.stale_current_markers(videos, frameseq) == [gen]
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "ffmpeg_exe", lambda: None)   # 只清账，不编码
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()

        def _marker_cleared() -> bool:
            return (lib._frameseq_worker is None
                    and not fp.retired_marker(gen).exists())

        _pump_until(_marker_cleared)
        assert gen.is_dir()                          # 当前世代：一个字节不动
        assert fp.frames_on_disk(gen) == FRAME_COUNT
        assert lib._frameseq_dirs["x"] == gen
        assert fp.stale_current_markers(videos, frameseq) == []
    finally:
        lib.shutdown()


def test_pending_retirement_spawns_worker_and_sweeps(tmp_path, monkeypatch):
    """热集完整但有待清扫世代 → 仍拉 worker，清扫只发生在后台线程。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    current = _stamped(webm, videos, frameseq)
    stale = _past_generation(webm, videos, frameseq)     # 上一版源的世代目录
    fp.mark_retired(stale)
    _age(fp.retired_marker(stale), days=2.0)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None and not stale.exists())
        assert current.is_dir()                      # 当前世代不受牵连
        assert lib._frameseq_dirs["x"] == current
    finally:
        lib.shutdown()


def test_shutdown_cancels_provision_worker_bounded(tmp_path, monkeypatch):
    """库收尾：置取消谓词 + 有界等待（≤2s），不留不受控重编码进程。"""
    videos, _ = _make_pack(tmp_path, {"idle": ["x.webm"]})
    _light_clips(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    events = {}

    class _FakeWorker:
        def cancel(self):
            events["cancelled"] = True

        def wait(self, ms):
            events["wait_ms"] = ms
            return True

    lib._frameseq_worker = _FakeWorker()
    lib.shutdown()
    assert events == {"cancelled": True, "wait_ms": 2000}
    assert lib._frameseq_worker is None


def test_frameseq_finish_slot_after_shutdown_does_not_rescan(tmp_path, monkeypatch):
    """库已收尾后投递的收尾槽：不 rescan（不重算源哈希、不再登记在用世代）。

    库都没了还往进程级「在用世代」集合里登记 → 这些目录永远清扫不掉（磁盘残留
    与"活 clip 在读"再也无法区分）；关机窗口里这些 I/O 也全是白做的（issue #111）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    gen = _stamped(webm, videos, frameseq)
    _light_clips(monkeypatch)
    scans: list = []
    real_scan = fp.scan_generations

    def _counting_scan(root):
        scans.append(Path(root))
        return real_scan(root)

    monkeypatch.setattr(fp, "scan_generations", _counting_scan)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {"x": gen}
        lib.shutdown()
        fp.clear_live_generations()          # 库已收尾：进程内登记随之作废
        scans.clear()
        lib._on_frameseq_provision_finished()     # 迟到的收尾信号（queued 投递）
        assert scans == []
        assert fp.live_generations() == frozenset()
    finally:
        lib.shutdown()


def test_provision_worker_finishing_after_shutdown_does_not_rescan(tmp_path, monkeypatch):
    """真实时序：worker 在飞时库收尾，迟到的 ``finished_work`` 才被投递。

    与"直接调槽"不是一回事：关机/切角色窗口里 worker 收到 cancel → terminate 在飞
    ffmpeg → 线程退出，而 queued 收尾信号必须等 GUI 事件循环，必然落在
    ``shutdown()`` 之后。此刻 rescan 会重算源哈希、重建映射、往在用集合里登记。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    out_dir = fp.clip_out_dir(videos / "idle" / "x.webm", videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "ffmpeg_exe", lambda: "fake-ffmpeg")
    started = threading.Event()
    release = threading.Event()

    class _GatedProc:
        """桩 ffmpeg：``communicate()`` 阻塞到 ``terminate()``（与真 Popen 同形）。"""

        def __init__(self, argv):
            self.argv = list(argv)
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -1
            release.set()

        def communicate(self):
            assert release.wait(20.0)         # 事件同步 + 宽预算（不赌 sleep 时序）
            return b"", b""

    def _fake_popen(argv, **_kwargs):
        proc = _GatedProc(argv)
        started.set()
        return proc

    monkeypatch.setattr(fp, "_popen", _fake_popen)
    monkeypatch.setattr(fp, "probe_fps", lambda _webm: 24.0)
    scans: list = []
    real_scan = fp.scan_generations

    def _counting_scan(root):
        scans.append(Path(root))
        return real_scan(root)

    monkeypatch.setattr(fp, "scan_generations", _counting_scan)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(started.is_set)
        worker = lib._frameseq_worker
        assert worker is not None and worker.isRunning() is True
        scans.clear()                        # 此刻 worker 正阻塞在 ffmpeg 上

        lib.shutdown()                       # 在飞收尾：cancel + 有界等待
        assert worker.isRunning() is False   # 线程真的在 2s 内结束（不留孤儿线程）
        _pump_events(0.2)                    # 投递迟到的 finished_work
        assert scans == []                   # 收尾槽不再 rescan
        assert not out_dir.exists()          # 取消：半成品不发布
        assert not tmp_dir.exists()          # tmp 残渣也清掉
    finally:
        release.set()
        _pump_events(0.1)
        lib.shutdown()


# ---------------------------------------------- 会话结束/退出收口的精确交错（B1/B2/B3）
def test_convert_clip_rechecks_gate_after_probe_before_spawn(tmp_path, monkeypatch):
    """B2：闸门在 fps 探测期间置位（第一道检查之后、``_popen`` 之前）→ 不派生。

    两道闸门之间还有半成品清理、mkdir，探测自己又派生一次 ffmpeg：只在进函数时查
    一次，会话结束落在这段窗口里照样会 CreateProcess 转换进程（issue #111 现象）。
    桩探测精确制造"探测期间会话结束"这个交错，不用 sleep 猜时序。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    spawned: list = []
    monkeypatch.setattr(fp, "_popen", lambda argv, **kw: spawned.append(list(argv)))

    def _probe(_webm):
        set_session_ending(True)          # 探测期间会话结束（关机/注销窗口）
        return 24.0

    converted, err = fp.convert_clip(webm, out_dir, exe="fake-ffmpeg", fps_probe=_probe)

    assert spawned == [], "探针之后必须复查闸门：会话结束不得再派生转换进程"
    assert (converted, err) == (False, "")
    assert not tmp_dir.exists(), "拒绝派生即清掉半成品"
    assert not out_dir.exists()


def test_convert_clip_rechecks_caller_cancel_after_probe_before_spawn(tmp_path, monkeypatch):
    """同上，机制换成调用方取消谓词（线程 Event）——worker 的 cancel 走这条。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    spawned: list = []
    monkeypatch.setattr(fp, "_popen", lambda argv, **kw: spawned.append(list(argv)))
    cancel = threading.Event()

    def _probe(_webm):
        cancel.set()                      # 探测期间收到取消（线程 Event，非时钟猜测）
        return 24.0

    converted, err = fp.convert_clip(webm, out_dir, exe="fake-ffmpeg", fps_probe=_probe,
                                     cancelled=cancel.is_set)

    assert spawned == [], "探针之后必须复查取消位：取消后不得再派生转换进程"
    assert (converted, err) == (False, "")
    assert not tmp_dir.exists()


class _GatedConvertProc:
    """桩转换进程：``communicate()`` 只有被 terminate 才返回（与真 Popen 同形）。"""

    def __init__(self, forced: threading.Event, budget_s: float = 10.0) -> None:
        self.returncode = None
        self._forced = forced
        self._budget_s = budget_s

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -1
        self._forced.set()

    def communicate(self):
        forced = self._forced.wait(self._budget_s)   # 宽预算：不许赌 sleep 时序
        self.returncode = -1 if forced else 0
        return b"", b""


def test_worker_cancel_landing_between_popen_and_register_still_terminates(
        tmp_path, monkeypatch):
    """B3：``cancel()`` 落在 ``_popen`` 返回到 ``on_proc`` 注册之间。

    那段窗口里 ``_proc`` 还是 ``None``，cancel 看不见刚派生出来的进程。桩 ``_popen``
    在**返回之前**调 ``worker.cancel()``，就是这段窗口本身（派生已发生、注册还没
    发生）。注册时不复查取消位就等于那个 ffmpeg 没人 terminate：``communicate()``
    陪它跑到自然结束，库侧的有界等待随之失效——关机窗口里留着一个正在跑的转换进程。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    out_dir = fp.clip_out_dir(videos / "idle" / "x.webm", videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "ffmpeg_exe", lambda: "fake-ffmpeg")
    monkeypatch.setattr(fp, "probe_fps", lambda _webm: 24.0)
    created: list = []
    real_worker = fp.FrameseqProvisionWorker

    class _RecordingWorker(real_worker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(fp, "FrameseqProvisionWorker", _RecordingWorker)
    spawned = threading.Event()
    terminated = threading.Event()

    def _fake_popen(argv, **_kwargs):
        spawned.set()
        created[0].cancel()      # 精确交错：派生已发生、注册尚未发生
        return _GatedConvertProc(terminated)

    monkeypatch.setattr(fp, "_popen", _fake_popen)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(spawned.is_set)
        worker = created[0]

        _pump_until(worker.isFinished)       # 有界：取消必须当场生效

        assert terminated.is_set(), "注册时必须复查取消位并 terminate 在飞进程"
        assert not tmp_dir.exists(), "取消：半成品不发布"
        assert not out_dir.exists()
    finally:
        terminated.set()                    # 兜底：红灯时别把桩进程留在 10s 等待里
        _pump_events(0.1)
        lib.shutdown()


def test_stop_all_clips_cancels_inflight_provision(tmp_path, monkeypatch):
    """B1：会话结束入口 ``stop_all_clips`` 必须取消在飞的供给线程。

    本方法是两条拓扑唯一的会话结束入口（legacy ``AppShell._on_session_end`` 逐窗、
    overlay ``OverlayShell._on_session_end`` 逐库）。只停 clip 不取消供给线程，关机
    窗口里那个线程照样能派生转换 ffmpeg。桩进程只有被 terminate 才让 ``communicate``
    返回，所以"线程在有界等待内退出"等价于"在飞 ffmpeg 被 terminate 了"。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    out_dir = fp.clip_out_dir(videos / "idle" / "x.webm", videos, frameseq)
    tmp_dir = out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "ffmpeg_exe", lambda: "fake-ffmpeg")
    monkeypatch.setattr(fp, "probe_fps", lambda _webm: 24.0)
    started = threading.Event()
    terminated = threading.Event()

    def _fake_popen(argv, **_kwargs):
        started.set()
        return _GatedConvertProc(terminated)

    monkeypatch.setattr(fp, "_popen", _fake_popen)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        lib.maybe_provision_frameseq()
        _pump_until(started.is_set)
        worker = lib._frameseq_worker
        assert worker is not None and worker.isRunning() is True
        _pump_until(lambda: worker._proc is not None)   # 等注册完成（本用例只测"看得见"这条）

        lib.stop_all_clips()                 # 会话结束入口（GUI 线程直调）

        assert terminated.is_set(), "会话结束必须 terminate 在飞转换进程"
        assert worker.isRunning() is False, "有界等待内退出：不留活线程"
        assert lib._frameseq_worker is None
        assert not tmp_dir.exists()          # 取消：半成品不发布
        assert not out_dir.exists()
    finally:
        terminated.set()
        _pump_events(0.1)
        lib.shutdown()


class _StuckProvisionWorker(fp.FrameseqProvisionWorker):
    """忽略取消的供给线程：模拟卡在不可中断 I/O 上（取消位已置却不响应）。"""

    def __init__(self, videos, root, release: threading.Event, parent=None) -> None:
        super().__init__(videos, root, parent=parent)
        self._release = release
        self.entered = threading.Event()

    def run(self):  # noqa: D401 - QThread 入口
        self.entered.set()
        self._release.wait(20.0)
        self.finished_work.emit()


def test_cancel_provision_timeout_hands_worker_to_orphan_registry(tmp_path):
    """审查项：``wait(2000)`` 超时只警告、不保护活线程的缺陷。

    超时时线程还在跑，而它是库的 QThread 子对象——库被销毁（窗口关闭/进程退出）时
    Qt 会把活线程一起析构，那是 abort 而不是异常。所以超时路径必须把线程摘出库 +
    交给模块级登记处强引用持有：本用例断言"摘出库 / 仍然活着 / 跑完后摘除"这条链。
    这里**不真的去销毁库**——修复前那一步是 abort，写进用例只会把红灯变成整进程
    崩溃，断言反而看不见。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    release = threading.Event()
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    worker = _StuckProvisionWorker(videos, frameseq, release, parent=lib)
    worker.finished.connect(worker.deleteLater)      # 与产品接线同形
    lib._frameseq_worker = worker
    try:
        worker.start()
        assert worker.entered.wait(10.0)             # 事件同步：确认线程真的在跑

        exited = lib.cancel_frameseq_provision(timeout_ms=100)

        assert exited is False, "取消未被响应：有界等待必须如实报超时（不许假装成功）"
        assert worker.isRunning() is True            # 线程还活着 —— 所以必须保护它
        assert lib._frameseq_worker is None, "库不再持有它（下面凭登记处持有）"
        assert worker.parent() is None, "摘出库：库被销毁不会连带析构活线程"
        assert worker in library_mod.orphan_provision_workers()
    finally:
        release.set()
        assert worker.wait(20000) is True            # 让卡住的线程自己退出
        lib.shutdown()
    _pump_events(0.2)                                # 投递 deleteLater（若已排队）
    assert library_mod.reap_orphan_provision_workers() == 1
    assert library_mod.orphan_provision_workers() == ()


class _RaisingWaitWorker(fp.FrameseqProvisionWorker):
    """``wait()`` 必抛的供给线程：模拟 QThread 半销毁 / 收尾异常时的等待失败。"""

    def run(self):  # noqa: D401 - QThread 入口
        return

    def wait(self, *args, **kwargs):
        raise RuntimeError("QThread 半销毁：等待失败")


def test_cancel_provision_wait_failure_orphans_worker(tmp_path):
    """缺陷 22：``worker.wait()`` 抛异常的收尾分支同样必须孤儿化线程。

    该分支此前直接 ``return False``：线程仍 ``setParent(self)`` 挂在库上，库被
    销毁（窗口关闭/进程退出）时 Qt 会把活线程一起析构 = abort（不是异常），
    而这一分支根本没读过"超时/异常都不是线程已安全收口"的前提。

    这里不真的销毁库——修复前那一步是 abort，写进用例只会把红灯变成整进程
    崩溃，断言反而看不见；断言落在"摘出库 + 进孤儿登记处"这条链上。
    """
    videos, _frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    worker = _RaisingWaitWorker(videos, tmp_path / "frameseq", parent=lib)
    lib._frameseq_worker = worker
    try:
        assert lib.cancel_frameseq_provision(timeout_ms=100) is False, \
            "等待异常必须如实返回失败"
        assert lib._frameseq_worker is None, "库不再持有它"
        assert worker.parent() is None, "摘出库：库被销毁不会连带析构该线程"
        assert worker in library_mod.orphan_provision_workers(), \
            "等待失败的线程同样必须交孤儿登记处持有"
    finally:
        # 该线程从未 start（isFinished 恒假），reap 摘不掉：显式摘除，
        # 不把残留丢给后续用例（同文件另一条用例断言登记处为空）。
        with library_mod._ORPHAN_PROVISION_LOCK:
            library_mod._ORPHAN_PROVISION_WORKERS.discard(worker)
        lib.shutdown()


# ---------------------------------------------------------------- tools 薄壳
def test_tools_shell_delegates_to_core():
    """薄壳只做命令行：转换核心与参数表都直接复用 pet.frameseq_provision。"""
    import tools.convert_frameseq as shell

    assert shell.convert_clip is fp.convert_clip
    assert shell.HOT_FOLDERS == fp.HOT_FOLDERS


def test_tools_shell_idempotent_and_exit_codes(tmp_path, monkeypatch, capsys):
    """CLI 幂等与退出码不变：完成热集 → 新转 0 / 退出 0；角色缺失 → 退出 1。"""
    import tools.convert_frameseq as shell

    monkeypatch.setattr(shell, "REPO_ROOT", tmp_path)
    videos = tmp_path / "assets" / "characters" / "x" / "videos"
    (videos / "idle").mkdir(parents=True)
    (videos / "idle" / "a.webm").write_bytes(b"webm" * 10)
    _stamped(videos / "idle" / "a.webm", videos, videos.parent / "frameseq")

    monkeypatch.setattr(sys, "argv", ["convert_frameseq.py", "--character", "x"])
    assert shell.main() == 0
    out = capsys.readouterr().out
    assert "clips: 新转 0 / 跳过 1 / 失败 0" in out
    assert "磁盘账" in out

    monkeypatch.setattr(sys, "argv", ["convert_frameseq.py", "--character", "nope"])
    assert shell.main() == 1
    assert "角色素材目录不存在" in capsys.readouterr().err

    # 无匹配 webm（角色目录存在但热集为空）→ 同样退出 1
    (tmp_path / "assets" / "characters" / "empty" / "videos").mkdir(parents=True)
    monkeypatch.setattr(sys, "argv",
                        ["convert_frameseq.py", "--character", "empty"])
    assert shell.main() == 1
    assert "没有匹配目录的 webm" in capsys.readouterr().err


# ------------------- R2：有损档（random/events）+ 递归扫描 + 磁盘准入 + GUI 线程减负
def _argv_for(calls: list, webm: Path) -> list[str]:
    """桩进程里属于这个源 webm 的那次转换 argv。"""
    for proc in calls:
        if str(webm) in proc.argv:
            return proc.argv
    raise AssertionError(f"{webm} 没有被转换：{[p.argv for p in calls]}")


def _expected_tail(webm: Path, videos: Path, frameseq: Path) -> list[str]:
    """阶段一 argv 的固定尾段：**永远无损**（有损在阶段二），一项都不许动。"""
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    return ["-nostdin", "-v", "error", "-y",
            "-c:v", "libvpx-vp9", "-i", str(webm),
            "-pix_fmt", "bgra", "-c:v", "libwebp", "-lossless", "1",
            str(out_dir.with_name(out_dir.name + fp.TMP_SUFFIX) / "f_%04d.webp")]


def test_encoder_strings_are_frozen():
    """档位常量冻结：新串一档到底，旧档串只作历史识别（绝不写进 meta）。

    ``white → white-v3 → white-v5`` 都是**像素级**的改档（v2 的产物在深色角色边缘
    留着白边；v3 的产物铺着一整块 ``a == 1`` 的底板），所以 v2/v3 的串与更早两档
    并列进 ``LEGACY_ENCODER_DESCS``：它们的产物按既有迁移机制自动不采纳、后台按
    v5 重供，绝不与 v5 混用。**v4 不是档**：镂空还原（``HOLE_RESTORE_ENABLED``）
    从未启用，档号直接落到 v5。
    """
    assert fp.LOSSY_FOLDERS == ("random", "events")
    assert fp.ENCODER_DESC == "libwebp lossless (bgra straight, unblend-white-v6)"
    assert fp.UNBLEND_MARK == "white-v6"
    # v6 起运行编码层必须位精确：无损 + exact（exact 是"透明区 RGB 不被改写"的前提）
    assert fp.WEBP_LOSSLESS is True
    assert fp.WEBP_EXACT is True
    assert not hasattr(fp, "WEBP_QUALITY"), "有损档位常量应已移除，避免有人再挂回去"
    assert fp.LEGACY_ENCODER_DESCS == (
        "libwebp lossless (bgra straight)",
        "libwebp lossy q80 (bgra straight)",
        "libwebp lossy q70 (bgra straight, unblend-white)",
        "libwebp lossy q70 (bgra straight, unblend-white-v3)",
        "libwebp lossy q70 (bgra straight, unblend-white-v5)",
    )
    assert fp.ENCODER_DESC not in fp.LEGACY_ENCODER_DESCS
    # 反解 v3 的三个可调常量（依据见模块注释）+ v5 清掉的 alpha 值
    assert (fp.UNBLEND_EXCESS_LUMA, fp.UNBLEND_PULL_MAX_ALPHA,
            fp.UNBLEND_ERODE_PX) == (30, 128, 1)
    assert fp.FAINT_ALPHA == 1
    # 镂空还原仍挂起（v5 里没有它；开启要另行换档）
    assert fp.HOLE_RESTORE_ENABLED is False


def test_scope_follows_first_level_folder(tmp_path):
    """供给范围由 webm 相对 videos_dir 的第一级目录决定；冷集不再换档。"""
    videos, _ = _make_pack(tmp_path, {"idle": ["a.webm"], "random": ["r.webm"],
                                      "events": ["balance/e.webm"]})
    assert fp.is_lossy_clip(videos / "idle" / "a.webm", videos) is False
    assert fp.is_lossy_clip(videos / "random" / "r.webm", videos) is True
    assert fp.is_lossy_clip(videos / "events" / "balance" / "e.webm", videos) is True
    assert set(fp.SUPPLY_FOLDERS) == set(fp.HOT_FOLDERS) | set(fp.LOSSY_FOLDERS)


def test_all_folders_share_one_lossless_stage_one_argv(tmp_path, monkeypatch):
    """热集与冷集的**阶段一** argv 完全一致（都是一趟无损抓帧）。

    改档（W1/W2）后档位不再由目录决定：三条 clip 的 argv 必须逐项相同，冷集参数
    只留给阶段二（Q70 + 反解）。
    """
    videos, frameseq = _make_pack(tmp_path, {
        "idle": ["a.webm"], "random": ["r.webm"], "events": ["balance/e.webm"]})
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert report.converted == 3
    for webm in (videos / "idle" / "a.webm", videos / "random" / "r.webm",
                 videos / "events" / "balance" / "e.webm"):
        tail = _argv_for(calls, webm)[1:]
        assert tail == _expected_tail(webm, videos, frameseq)
        assert tail[tail.index("-lossless") + 1] == "1"
        assert "-quality" not in tail


def test_convert_clip_lossy_flag_is_inert_and_meta_is_single_tier(
        tmp_path, monkeypatch):
    """``convert_clip(lossy=True)`` 只作兼容参数：argv/meta 与不带它时一模一样。"""
    videos, frameseq = _make_pack(tmp_path, {"random": ["r.webm"]})
    webm = videos / "random" / "r.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    calls = _install_fake_ffmpeg(monkeypatch)

    assert fp.convert_clip(webm, out_dir, exe="fake-ffmpeg", lossy=True) == (True, "")
    argv = calls[0].argv
    assert argv[argv.index("-lossless") + 1] == "1"
    assert "-quality" not in argv
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["encoder"] == fp.ENCODER_DESC
    assert meta["unblend"] == fp.UNBLEND_MARK
    assert fp.current_generation(webm, videos, frameseq) == out_dir


def test_legacy_encoder_generations_are_not_adopted_and_are_replanned(tmp_path):
    """改档迁移路径：旧的「热集无损 / 冷集 Q80」世代一律不采纳，并重新进待转清单。

    这是**设计好的**迁移：不采纳 ⇒ 回退 WebM 播放 + 后台按新档重新供给。两个目录
    方向都要成立（热集的无损串与冷集的 Q80 串都失效），且 ``is_complete`` 语义不变
    （身份 + 帧数自洽仍然为真，它只是不管档位）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"], "random": ["r.webm"]})
    hot = videos / "idle" / "a.webm"
    lossy = videos / "random" / "r.webm"
    hot_gen = _stamped(hot, videos, frameseq,
                       encoder=fp.LEGACY_ENCODER_DESCS[0])       # 旧热集：无损串
    cold_gen = _stamped(lossy, videos, frameseq,
                        encoder=fp.LEGACY_ENCODER_DESCS[1])      # 旧冷集：Q80 串

    assert fp.is_complete(hot_gen) is True    # 身份 + 帧数自洽（档位不在此判）
    assert fp.is_complete(cold_gen) is True
    assert fp.current_generation(hot, videos, frameseq) is None
    assert fp.current_generation(lossy, videos, frameseq) is None
    assert sorted(w.name for w, _ in fp.plan_clips(videos, frameseq)) == [
        "a.webm", "r.webm"]

    # 换成当前档串（Q70 + 反解）即刻可采纳
    for webm, gen in ((hot, hot_gen), (lossy, cold_gen)):
        (gen / "meta.json").write_text(
            json.dumps(_meta(webm, FRAME_COUNT)), encoding="utf-8")
    assert fp.current_generation(hot, videos, frameseq) == hot_gen
    assert fp.current_generation(lossy, videos, frameseq) == cold_gen
    assert fp.plan_clips(videos, frameseq) == []


def test_missing_or_broken_meta_is_never_adopted(tmp_path):
    """既有契约不破：meta 缺戳 / 帧数不符 / 缺 encoder 一律不采纳（回退 WebM）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq)

    def _rewrite(payload):
        (gen / "meta.json").write_text(json.dumps(payload), encoding="utf-8")

    base = _meta(webm, FRAME_COUNT)
    _rewrite({k: v for k, v in base.items() if k != "source_sha256"})   # 缺身份戳
    assert fp.current_generation(webm, videos, frameseq) is None
    _rewrite({**base, "frames": FRAME_COUNT + 1})                      # 帧数不符
    assert fp.current_generation(webm, videos, frameseq) is None
    _rewrite({k: v for k, v in base.items() if k != "encoder"})        # 缺档位串
    assert fp.current_generation(webm, videos, frameseq) is None
    assert fp.is_complete(gen, sha=fp.source_sha256(webm)) is True     # 身份仍自洽
    _rewrite(base)
    assert fp.current_generation(webm, videos, frameseq) == gen


def test_v2_unblend_generation_is_not_adopted_and_replans(tmp_path):
    """反解 v2（``unblend-white``）的世代不采纳、重新进待转清单 —— 迁移路径。

    v2 → v3 是**像素级**改档（收色 + alpha 微缩），v2 的产物在深色角色边缘留着白边，
    拿它顶替等于用户实机目验的那个缺陷继续存在。判据仍是 encoder 串：v2 串不等于
    ``ENCODER_DESC`` ⇒ 回退 WebM 播放 + 后台按 v3 重供；v3 串的世代照常采纳。
    """
    v2_desc = "libwebp lossy q70 (bgra straight, unblend-white)"
    assert v2_desc in fp.LEGACY_ENCODER_DESCS and v2_desc != fp.ENCODER_DESC
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    gen = _stamped(webm, videos, frameseq, encoder=v2_desc)
    # 反解标记也换成 v2 的（v2 产物就该长这样：串 + 标记成套）
    (gen / "meta.json").write_text(
        json.dumps({**_meta(webm, FRAME_COUNT, encoder=v2_desc),
                    "unblend": "white"}), encoding="utf-8")

    assert fp.is_complete(gen) is True                     # 身份 + 帧数自洽
    assert fp.current_generation(webm, videos, frameseq) is None
    assert [w.name for w, _ in fp.plan_clips(videos, frameseq)] == ["a.webm"]

    (gen / "meta.json").write_text(
        json.dumps(_meta(webm, FRAME_COUNT)), encoding="utf-8")   # 换成 v3 串
    assert fp.current_generation(webm, videos, frameseq) == gen
    assert fp.plan_clips(videos, frameseq) == []


def test_mismatched_encoder_generation_is_reencoded(tmp_path, monkeypatch):
    """档位不符（旧档）的产物按当前档重转（否则 plan 会永远列它、供给永不自洽）。"""
    videos, frameseq = _make_pack(tmp_path, {"random": ["r.webm"]})
    webm = videos / "random" / "r.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _write_frames(out_dir)
    (out_dir / "meta.json").write_text(json.dumps(
        _meta(webm, FRAME_COUNT, encoder=fp.LEGACY_ENCODER_DESCS[1])),
        encoding="utf-8")
    calls = _install_fake_ffmpeg(monkeypatch)

    report = fp.provision_once(videos, frameseq)

    assert (report.converted, len(calls)) == (1, 1)
    assert json.loads((out_dir / "meta.json").read_text(
        encoding="utf-8"))["encoder"] == fp.ENCODER_DESC
    assert fp.current_generation(webm, videos, frameseq) == out_dir


def test_scan_generations_finds_deep_generations_and_rescan_adopts(tmp_path, monkeypatch):
    """递归扫描：三层世代 ``events/balance/<stem>.g<戳>/`` 入表并被 rescan 采纳。

    旧实现只扫两层，``events/balance/*.webm`` 的世代目录永远扫不到 —— 既不会被
    采纳（帧序列白转），也永远不清扫（退役账还不上）。世代目录内部不再下钻。
    """
    videos, frameseq = _make_pack(tmp_path, {"events": ["balance/e.webm"]})
    webm = videos / "events" / "balance" / "e.webm"
    gen = _stamped(webm, videos, frameseq)
    assert gen.parent == frameseq / "events" / "balance"
    nested = gen / f"inner{fp.GEN_INFIX}{'0' * fp.GEN_STAMP_HEX}"
    nested.mkdir()
    (nested / "f_0001.webp").write_bytes(b"webp")

    found = fp.scan_generations(frameseq)

    assert found == {frameseq / "events" / "balance" / "e": [gen]}
    assert fp.current_generation(webm, videos, frameseq) == gen
    _light_clips(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {"e": gen}
    finally:
        lib.shutdown()


def test_plan_supply_order_hot_then_events_then_random(tmp_path):
    """供给序：热集（idle → move → 其余）在前，events 其次，random 最后。"""
    videos, frameseq = _make_pack(tmp_path, {
        "random": ["r1.webm", "r2.webm"], "events": ["balance/e.webm"],
        "idle": ["b.webm", "a.webm"], "move": ["m.webm"], "turn": ["t.webm"]})

    plan = fp.plan_clips(videos, frameseq)

    assert [w.relative_to(videos).as_posix() for w, _ in plan] == [
        "idle/a.webm", "idle/b.webm", "move/m.webm", "turn/t.webm",
        "events/balance/e.webm", "random/r1.webm", "random/r2.webm"]


def test_disk_headroom_required_scales_with_source_size(tmp_path):
    """准入线 = max(1GiB, 源 webm 大小 × 20)：小素材吃 1GiB 地板，大素材按倍数。"""
    videos, _ = _make_pack(tmp_path, {"random": ["r.webm"]})
    webm = videos / "random" / "r.webm"
    assert fp.disk_headroom_bytes(webm, floor=0, factor=20) == \
        20 * webm.stat().st_size
    assert fp.disk_headroom_bytes(webm) == fp.DISK_FREE_FLOOR_BYTES


def test_provision_skips_clip_when_disk_headroom_is_short(tmp_path, monkeypatch):
    """磁盘不足：该 clip 不起手（连 ffmpeg 都不派生），已转产物一个字节不删。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"], "random": ["r.webm"]})
    done = _stamped(videos / "idle" / "a.webm", videos, frameseq)
    calls = _install_fake_ffmpeg(monkeypatch)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda _path: usage(10 ** 12, 10 ** 12 - 4096, 4096))

    report = fp.provision_once(videos, frameseq)

    assert calls == []                        # 一个 ffmpeg 都没派生
    assert (report.converted, report.deferred_disk) == (0, 1)
    assert (report.failed, report.deferred_backoff) == (0, 0)
    assert fp.frames_on_disk(done) == FRAME_COUNT    # 已转产物不删

    monkeypatch.setattr(shutil, "disk_usage",
                        lambda _path: usage(10 ** 12, 0, 10 ** 12))
    calls = _install_fake_ffmpeg(monkeypatch)

    second = fp.provision_once(videos, frameseq)

    assert (second.converted, second.deferred_disk) == (1, 0)
    assert len(calls) == 1


def test_disk_blocked_clip_does_not_spend_migration_quota(tmp_path, monkeypatch):
    """磁盘不够时连迁移配额都不扣：配额是"起手"的名额，不是"探测过"的名额。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    _legacy_hot_set(videos, frameseq, "idle", ("a",))
    calls = _install_fake_ffmpeg(monkeypatch)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage",
                        lambda _path: usage(10 ** 12, 10 ** 12 - 4096, 4096))

    blocked = fp.provision_once(videos, frameseq)

    assert (blocked.converted, blocked.migrations, blocked.deferred_disk) == (0, 0, 1)
    assert calls == []

    monkeypatch.setattr(shutil, "disk_usage",
                        lambda _path: usage(10 ** 12, 0, 10 ** 12))
    calls = _install_fake_ffmpeg(monkeypatch)

    second = fp.provision_once(videos, frameseq)

    assert (second.converted, second.migrations) == (1, 1)   # 配额还在：这轮照迁


def test_start_frameseq_provision_does_not_plan_on_gui_thread(tmp_path, monkeypatch):
    """GUI 侧不再做「有没有活干」的三项判定：plan_clips 只在 worker 线程跑。

    plan_clips 要给供给范围内的每个 clip 算一次源哈希（现在含 random 45MB/events
    3MB），放在 GUI 线程就是一次卡顿；判定挪进 worker，无事可做时线程立即结束。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    _install_fake_ffmpeg(monkeypatch)
    threads: list[int] = []
    real_plan = fp.plan_clips

    def _recording_plan(*args, **kwargs):
        threads.append(threading.get_ident())
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(fp, "plan_clips", _recording_plan)
    gui_thread = threading.get_ident()
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        threads.clear()
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None and bool(lib._frameseq_dirs))

        assert threads and gui_thread not in threads     # 判定全在 worker 线程
    finally:
        lib.shutdown()


def test_idle_round_marks_report_and_does_not_rescan(tmp_path, monkeypatch):
    """无事可做（热集 + 冷集全完整、无退役/回朝旧账）：report 标明未做事且不 rescan。

    rescan 要在 GUI 线程重算源哈希（现在含 random/events），无事时的重哈希纯属白做。
    """
    videos, frameseq = _make_pack(tmp_path, {
        "idle": ["x.webm"], "random": ["r.webm"], "events": ["balance/e.webm"]})
    for rel in ("idle/x.webm", "random/r.webm", "events/balance/e.webm"):
        _stamped(videos / rel, videos, frameseq)
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    monkeypatch.setattr(fp, "ffmpeg_exe", lambda: "fake-ffmpeg")
    calls = _install_fake_ffmpeg(monkeypatch)
    gui_thread = threading.get_ident()
    scans: list = []
    real_scan = fp.scan_generations

    def _counting_scan(root):
        scans.append((Path(root), threading.get_ident()))
        return real_scan(root)

    monkeypatch.setattr(fp, "scan_generations", _counting_scan)
    created: list = []
    real_worker = fp.FrameseqProvisionWorker

    class _RecordingWorker(real_worker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(fp, "FrameseqProvisionWorker", _RecordingWorker)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        scans.clear()
        lib.maybe_provision_frameseq()
        _pump_until(lambda: bool(created) and lib._frameseq_worker is None
                    and created[0].isFinished())
        _pump_events(0.2)

        assert created[0].report.idle is True            # 未做事留痕
        assert created[0].report.converted == 0
        assert calls == []                                # 一个 ffmpeg 都没派生
        # worker 线程内的"有没有活干"判定扫描（列目录名）是允许的；GUI 线程一次都不扫
        # ——rescan 会在这里按当前源重算哈希，无事可做的收尾里它是白做的。
        assert scans, "worker 线程必须真的做过判定"
        assert all(ident != gui_thread for _root, ident in scans)
    finally:
        lib.shutdown()


# ================= W1/W2：两段式管线（无损抓帧 → 逐帧白底反解 + Q70）=================
def _tmp_dir_of(out_dir: Path) -> Path:
    return out_dir.with_name(out_dir.name + fp.TMP_SUFFIX)


def _read_frames(out_dir: Path) -> list[tuple[int, int, int, int]]:
    """把世代目录里的帧逐像素读出来（唯一一处真解码：管线测试要验的是**产物**）。"""
    frames = []
    for path in sorted(out_dir.glob("f_*.webp")):
        with Image.open(path) as opened:
            frames.append(list(opened.convert("RGBA").get_flattened_data()))
    return frames


def _webp_is_lossless(path: Path) -> bool:
    """RIFF 容器里的编码 chunk 是不是无损（``VP8L`` = 无损，``VP8X``/``VP8 `` = 有损）。

    这是"阶段二真的把每一帧重编成了 Q70"的**机器可查**证据：只看文件头四个字节，
    不依赖体积或 PSNR 这类会被内容影响的旁证。
    """
    return path.read_bytes()[12:16] == b"VP8L"


def test_two_stage_call_order_and_lossless_stage_one(tmp_path, monkeypatch):
    """两段式调用序列：先一趟**无损** ffmpeg，再对 tmp 里的帧跑阶段二，最后才发布。

    阶段二必须拿到"已经落盘的那批帧"（不是另起一趟编码），发布必须发生在阶段二
    **之后**——顺序错了就是「先发布的半成品被阶段二改写」或「有损帧进了第二轮反解」。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = _tmp_dir_of(out_dir)
    calls = _install_fake_ffmpeg(monkeypatch)
    seen = {}
    real_unblend = fp.unblend_frames_in_place

    def _spy(frames_dir, **kwargs):
        seen["dir"] = Path(frames_dir)
        seen["frames_present"] = len(list(Path(frames_dir).glob("f_*.webp")))
        seen["stage1_done"] = len(calls)
        seen["published"] = out_dir.exists()
        return real_unblend(frames_dir, **kwargs)

    monkeypatch.setattr(fp, "unblend_frames_in_place", _spy)

    assert fp.convert_clip(webm, out_dir, exe="fake-ffmpeg") == (True, "")

    assert seen["dir"] == tmp_dir                    # 阶段二跑在 tmp 里，不是目标目录
    assert seen["frames_present"] == FRAME_COUNT     # 拿到的是阶段一落好的那批帧
    assert seen["stage1_done"] == 1                  # 阶段一已经跑完
    assert seen["published"] is False                 # 发布在阶段二之后
    assert out_dir.is_dir() and _tmp_dir_of(out_dir).exists() is False
    # 阶段一 = 无损（有损只在阶段二）
    argv = calls[0].argv
    assert argv[argv.index("-lossless") + 1] == "1"
    assert "-quality" not in argv
    # 阶段二真的重编了每一帧：产物必须**全是** VP8L（无损档，v6 起运行层位精确）
    published = sorted(out_dir.glob("f_*.webp"))
    assert len(published) == FRAME_COUNT
    assert all(_webp_is_lossless(path) for path in published)


def test_stage_two_receives_stage_one_pixels_and_rectifies_them(tmp_path, monkeypatch):
    """阶段二的**输入就是阶段一的输出**，且反解口径与 ``unblend_rgba`` 逐像素一致。

    在管线里拦一次 ``unblend_rgba``：入参必须是桩帧那批白底混合存值、出参必须是
    反解后的角色色——这条把"阶段一的像素真的流进了阶段二"钉死（只看最终产物会被
    有损编码的量化噪声糊住）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _install_fake_ffmpeg(monkeypatch)
    pixels = []
    real_unblend = fp.unblend_rgba

    def _spy(im):
        pixels.append((im.copy(), real_unblend(im)))
        return pixels[-1][1]

    monkeypatch.setattr(fp, "unblend_rgba", _spy)

    assert fp.convert_clip(webm, out_dir, exe="fake-ffmpeg") == (True, "")

    assert len(pixels) == FRAME_COUNT
    width = STUB_SIZE[0]
    for source, rectified in pixels:
        for x in range(width):
            band = min(len(STUB_PIXELS) - 1, x * len(STUB_PIXELS) // width)
            alpha = STUB_ALPHAS[band]
            stored = _frame_blend(STUB_PIXELS[band], alpha)
            before = source.getpixel((x, 0))
            after = rectified.getpixel((x, 0))
            assert before[3] == alpha and after[3] == alpha
            if alpha < fp.UNBLEND_MIN_ALPHA:
                # 透明区的 RGB 无意义：libwebp 的**无损**编解码本身就不保它
                # （桩帧落盘再读回来已经不是夹具写进去的那三个字节），只能比"没被动过"
                assert after == before
                continue
            assert before[:3] == stored                       # 阶段一的像素原样进来了
            want = _frame_rectified(stored, alpha)
            for value, expected in zip(after[:3], want):
                assert abs(value - expected) <= 1, (alpha, after, want)


def test_published_frames_keep_alpha_and_move_towards_character_color(
        tmp_path, monkeypatch):
    """产物侧：帧数是契约、alpha 只降不升（v3 微缩）、半透明像素被反解**拉向角色色**。

    v3 起 alpha 不再"逐位不变"：``erode_alpha`` 把轮廓外圈收 1px（用户实机目验的
    软白边靠它压掉）。契约收窄成两条可判定的性质：**alpha 只降不升**，且**邻域全是
    不透明像素的像素保持 255**（微缩只啃边界，不伤内部）。

    RGB 不做逐位断言：阶段二是 Q70 **有损**编码，小夹具上的量化噪声本来就到几十
    个色阶（8×8 实测最坏 ≈45）——精确口径留在纯函数单测里。这里断言的是有损编码
    改不了的两件事：alpha 的方向性、白底污染的方向被纠正（反解值比白底混合存值更
    靠近角色色，两者相差 ≈90 色阶，噪声吃不掉这个差）。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    _install_fake_ffmpeg(monkeypatch)

    assert fp.convert_clip(webm, out_dir, exe="fake-ffmpeg") == (True, "")
    frames = _read_frames(out_dir)
    assert len(frames) == FRAME_COUNT
    # v6：产物必须是无损容器（阶段二不再有损重编）
    assert all(_webp_is_lossless(p) for p in sorted(out_dir.glob("f_*.webp")))
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["frames"] == len(list(out_dir.glob("f_*.webp"))) == FRAME_COUNT
    assert meta["unblend"] == fp.UNBLEND_MARK
    assert meta["encoder"] == fp.ENCODER_DESC
    assert fp.current_generation(webm, videos, frameseq) == out_dir

    width = STUB_SIZE[0]
    for frame in frames:
        for x in range(width):
            band = min(len(STUB_PIXELS) - 1, x * len(STUB_PIXELS) // width)
            character = STUB_PIXELS[band]
            alpha = STUB_ALPHAS[band]
            pixel = frame[x]
            assert pixel[3] <= alpha                    # 微缩：alpha 只降不升
            # 内部（x=7 的 3x3 邻域里没有更低的 alpha）保持 255
            if alpha >= fp.UNBLEND_MAX_ALPHA and x == width - 1:
                assert pixel[3] == 255
            if alpha < fp.UNBLEND_MIN_ALPHA or pixel[3] == 0:
                continue                   # 透明区 RGB 无意义（libwebp 不保）
            blended = _frame_blend(character, alpha)
            if alpha >= fp.UNBLEND_MAX_ALPHA:
                # 不透明档：白底项为 0，存值本来就是角色色 —— 没有可除的白（公式恒等）
                assert blended == character
                continue
            rectified = _frame_rectified(blended, alpha)
            near_character = max(abs(p - c) for p, c in zip(pixel[:3], character))
            near_blended = max(abs(p - c) for p, c in zip(pixel[:3], blended))
            # 夹具自证：这一档的存值反解回来就是角色色（half-level 取整余量）
            assert all(abs(r - c) <= 2 for r, c in zip(rectified, character))
            assert near_character < near_blended, (pixel, character, blended)


def test_stage_two_single_bad_frame_discards_whole_clip(tmp_path, monkeypatch, caplog):
    """单帧坏掉 ⇒ **整段作废**，绝不发布「一部分重编、一部分仍是无损帧」的混合产物。

    这是像素真值契约的一部分：整个 clip 必须同属一条处理链/一个档位。一帧处理
    失败就整段丢弃，由既有退避机制重试——不许拿「帧数对」冒充「档位对」。旧口径
    （跳过该帧、整段照常发布）会让播放器同时读到两种像素来源，已废弃。
    这里用的是**真的坏帧**（0 字节），不 mock 解码失败。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = _tmp_dir_of(out_dir)

    def _corrupt_one(argv):
        (Path(argv[-1]).parent / "f_0002.webp").write_bytes(b"")   # 0 字节 = 解不开

    _install_fake_ffmpeg(monkeypatch, on_convert=_corrupt_one)

    with caplog.at_level(logging.WARNING):
        ok, problem = fp.convert_clip(webm, out_dir, exe="fake-ffmpeg")

    assert ok is False, "有帧处理失败时必须判失败"
    assert problem, "必须给出可诊断的失败原因"
    assert any("整段作废" in record.getMessage() for record in caplog.records),         "必须留下整段作废的日志留痕"
    assert not out_dir.exists(), "半成品绝不能发布成世代目录"
    assert not tmp_dir.exists(), "临时目录必须清干净"
    assert fp.current_generation(webm, videos, frameseq) is None,         "作废的 clip 不得被采纳"


def test_stage_two_structural_failure_discards_scratch_and_reports(
        tmp_path, monkeypatch):
    """整段进不了阶段二 ⇒ 半成品清干净、目标不落地、按失败返回（假 meta 绝不发布）。

    结构失败（Pillow/ImageMath 不可用）与"单帧坏掉"是两回事：前者意味着这一轮
    根本没产出**当前档**的帧，发布一份写着 Q70 + 反解的 meta 就是假凭证。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = _tmp_dir_of(out_dir)
    calls = _install_fake_ffmpeg(monkeypatch)
    monkeypatch.setattr(fp, "unblend_frames_in_place",
                        lambda *_a, **_k: (0, 0, False, "阶段二不可用（桩）"))

    converted, err = fp.convert_clip(webm, out_dir, exe="fake-ffmpeg")

    assert (converted, bool(err)) == (False, True)
    assert "阶段二" in err
    assert not out_dir.exists()                  # 目标一个字节都不落
    assert not tmp_dir.exists()                  # 半成品清干净（含已抓好的无损帧）
    assert calls and calls[0].argv[calls[0].argv.index("-lossless") + 1] == "1"
    assert fp.current_generation(webm, videos, frameseq) is None
    assert [w.name for w, _ in fp.plan_clips(videos, frameseq)] == ["a.webm"]


def test_stage_two_cancel_discards_scratch_and_stops_per_frame(tmp_path, monkeypatch):
    """取消落在阶段二：逐帧停手、半成品丢弃、返回 ``(False, "")``（关机窗口口径）。"""
    videos, frameseq = _make_pack(tmp_path, {"idle": ["a.webm"]})
    webm = videos / "idle" / "a.webm"
    out_dir = fp.clip_out_dir(webm, videos, frameseq)
    tmp_dir = _tmp_dir_of(out_dir)
    _install_fake_ffmpeg(monkeypatch, frames=6)
    cancelled = {"flag": False}
    real_open = Image.open
    seen = {"frames": 0}

    def _open(source, *args, **kwargs):
        opened = real_open(source, *args, **kwargs)
        if not isinstance(source, Path):
            seen["frames"] += 1
            if seen["frames"] >= 2:
                cancelled["flag"] = True          # 第 2 帧起置位（模拟关机落在阶段二）
        return opened

    monkeypatch.setattr(Image, "open", _open)

    converted, err = fp.convert_clip(
        webm, out_dir, exe="fake-ffmpeg", cancelled=lambda: cancelled["flag"])

    assert (converted, err) == (False, "")
    assert seen["frames"] < 6                     # 没跑完全部帧就停手
    assert not tmp_dir.exists() and not out_dir.exists()


def test_upgrade_round_reprovisions_when_only_legacy_tier_generation_exists(
        tmp_path, monkeypatch):
    """升级形态：磁盘上只有旧档世代 ⇒ 供给轮真的重转一份当前档产物并采纳。

    这是迁移路径的端到端形态（计划 → 重转 → rescan 采纳），旧世代一份不删。
    """
    videos, frameseq = _make_pack(tmp_path, {"idle": ["x.webm"]})
    webm = videos / "idle" / "x.webm"
    old_gen = _stamped(webm, videos, frameseq,
                       encoder=fp.LEGACY_ENCODER_DESCS[0])
    _light_clips(monkeypatch)
    monkeypatch.setattr(fp, "PROVISION_DELAY_MS", 0)
    calls = _install_fake_ffmpeg(monkeypatch)
    lib = MovieLibrary(asset_dir=videos, prewarm_enabled=False)
    try:
        assert lib._frameseq_dirs == {}               # 旧档世代不采纳（回退 WebM）
        lib.maybe_provision_frameseq()
        _pump_until(lambda: lib._frameseq_worker is None and lib._frameseq_dirs)

        assert len(calls) == 1                        # 重转一份当前档产物
        assert lib._frameseq_dirs["x"] == fp.clip_out_dir(webm, videos, frameseq)
        assert old_gen.is_dir()                       # 旧世代保留（宽限期，不净删）
        assert fp.current_generation(webm, videos, frameseq) == \
            fp.clip_out_dir(webm, videos, frameseq)
    finally:
        lib.shutdown()


def test_creation_flags_hide_ffmpeg_console_on_windows(monkeypatch):
    """ffmpeg 转换子进程必须隐藏控制台窗口（CREATE_NO_WINDOW）：缺了它每次
    转换都闪出一个终端窗口（默认终端承载子进程控制台），开机自启的集中供给
    时段最刺眼。POSIX 无控制台概念，返回 0。"""
    CREATE_NO_WINDOW = 0x08000000
    BELOW_NORMAL = 0x00004000

    # 只换 fp 命名空间里的 os/subprocess：改全局 os.name 会让 pathlib 在
    # POSIX 上实例化 WindowsPath（失败报告生成即 INTERNALERROR、monkeypatch
    # 来不及还原）；POSIX 的 subprocess 也没有这两个 Windows 常量。
    from types import SimpleNamespace

    monkeypatch.setattr(fp, "subprocess", SimpleNamespace(
        CREATE_NO_WINDOW=CREATE_NO_WINDOW,
        BELOW_NORMAL_PRIORITY_CLASS=BELOW_NORMAL))
    monkeypatch.setattr(fp, "os", SimpleNamespace(name="nt"))
    flags = fp._creation_flags()
    assert flags & CREATE_NO_WINDOW, "转换子进程不能闪终端窗口"
    assert flags & BELOW_NORMAL, "低优先级档位不能丢"

    monkeypatch.setattr(fp, "os", SimpleNamespace(name="posix"))
    assert fp._creation_flags() == 0
