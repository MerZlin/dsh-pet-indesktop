# -*- coding: utf-8 -*-
"""角色包索引 ``character.json`` 的解析与校验（无源视频资源契约）。

**存在理由**：新的分发形态里角色目录**可能完全没有 ``*.webm``**——动画直接以
完好帧序列的形式随包下发。而 ``pet/library.py::_load_all`` 现在扫不到 webm/gif
就抛 ``FileNotFoundError``，``rescan_frameseq`` 又用「源 webm 的 sha256」当采纳
身份，于是无源素材根本建不了库。

本模块只做**纯数据**的事：读索引、校验、给出 clip 清单。它不碰 Qt、不碰文件内容、
不解码任何图——接线在 ``library.py``。契约细则见
``.scratch/asset-arch/RESOURCE-CONTRACT.md``。
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

INDEX_NAME = "character.json"
SCHEMA = 1
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_PARTS = ("core", "extra")
#: 当前产品能播的存储格式与处理链。新格式/新处理链必须在这里登记，
#: 否则解析阶段就拒收——不许"看不懂也照播"。
SUPPORTED_FORMATS = frozenset({"lossless-frame-v1"})
SUPPORTED_PIPELINES = frozenset({"white-v6", "unblend-white-v6"})


class IndexError_(ValueError):
    """索引非法（字段缺失/类型错/越界/路径穿越）。"""


@dataclass(frozen=True)
class ClipEntry:
    """一个 clip 的索引条目。``dir`` 是相对角色目录的 POSIX 路径。"""

    name: str
    category: str
    dir: str
    frames: int
    key_every: int
    part: str
    content_sha: str
    bytes: int = 0


@dataclass(frozen=True)
class CharacterIndex:
    schema: int
    id: str
    name: str
    canvas: tuple[int, int]
    fps: float
    pipeline: str
    format: str
    parts: tuple[str, ...]
    clips: tuple[ClipEntry, ...]

    def by_name(self) -> dict[str, ClipEntry]:
        return {c.name: c for c in self.clips}

    def categories(self) -> dict[str, list[str]]:
        """category → [clip 名]，供 ``library.folder_files`` 同构使用。"""
        out: dict[str, list[str]] = {}
        for c in self.clips:
            out.setdefault(c.category, []).append(c.name)
        return out


def _as_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IndexError_(f"{field} 必须是整数，收到 {value!r}")
    return value


def _as_str(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndexError_(f"{field} 必须是非空字符串，收到 {value!r}")
    return value.strip()


def safe_rel_dir(value, field: str) -> str:
    """相对路径校验：不允许绝对路径、盘符、``..`` 上跳（下载包必须防穿越）。"""
    raw = _as_str(value, field).replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise IndexError_(f"{field} 不能是绝对路径: {raw}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise IndexError_(f"{field} 不能包含 '..': {raw}")
    if not parts:
        raise IndexError_(f"{field} 为空: {raw}")
    return "/".join(parts)


def parse_index(data) -> CharacterIndex:
    """把 ``character.json`` 的 dict 解析成 :class:`CharacterIndex`（非法即抛）。"""
    if not isinstance(data, dict):
        raise IndexError_("character.json 顶层必须是对象")
    schema = _as_int(data.get("schema"), "schema")
    if schema != SCHEMA:
        raise IndexError_(f"不支持的索引 schema: {schema}（本版支持 {SCHEMA}）")
    canvas = data.get("canvas")
    if (not isinstance(canvas, (list, tuple)) or len(canvas) != 2
            or any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in canvas)):
        raise IndexError_(f"canvas 必须是两个正整数: {canvas!r}")
    fps = data.get("fps")
    if (not isinstance(fps, (int, float)) or isinstance(fps, bool)
            or not math.isfinite(float(fps)) or fps <= 0):
        # NaN 必须在这里挡住：`abs(NaN - x) > 0.01` 恒为 False，会一路穿透到采纳闸门。
        raise IndexError_(f"fps 必须是有限正数: {fps!r}")
    # 格式白名单：不认识的存储格式一律拒绝（终审 B2："错误格式必拒绝"要有实据）
    fmt = _as_str(data.get("format"), "format")
    if fmt not in SUPPORTED_FORMATS:
        raise IndexError_(f"不支持的存储格式: {fmt!r}（支持 {sorted(SUPPORTED_FORMATS)}）")
    pipe = _as_str(data.get("pipeline"), "pipeline")
    if pipe not in SUPPORTED_PIPELINES:
        raise IndexError_(f"不支持的处理链: {pipe!r}（支持 {sorted(SUPPORTED_PIPELINES)}）")
    parts_raw = data.get("parts") or list(_PARTS)
    if not isinstance(parts_raw, (list, tuple)) or not parts_raw:
        raise IndexError_("parts 必须是非空数组")
    parts = tuple(_as_str(p, "parts[]") for p in parts_raw)

    clips_raw = data.get("clips")
    if not isinstance(clips_raw, (list, tuple)) or not clips_raw:
        raise IndexError_("clips 必须是非空数组")
    clips: list[ClipEntry] = []
    seen: set[str] = set()
    for i, raw in enumerate(clips_raw):
        if not isinstance(raw, dict):
            raise IndexError_(f"clips[{i}] 必须是对象")
        name = _as_str(raw.get("name"), f"clips[{i}].name")
        if name in seen:
            raise IndexError_(f"clips 名字重复: {name}")
        seen.add(name)
        category = _as_str(raw.get("category"), f"clips[{i}].category").lower()
        clip_dir = safe_rel_dir(raw.get("dir"), f"clips[{i}].dir")
        frames = _as_int(raw.get("frames"), f"clips[{i}].frames")
        if frames <= 0:
            raise IndexError_(f"clips[{i}].frames 必须 > 0: {frames}")
        key_every = _as_int(raw.get("key_every", 12), f"clips[{i}].key_every")
        if key_every < 1:
            raise IndexError_(f"clips[{i}].key_every 必须 >= 1: {key_every}")
        part = _as_str(raw.get("part", "core"), f"clips[{i}].part")
        if part not in parts:
            raise IndexError_(f"clips[{i}].part 不在 parts 里: {part}")
        sha = _as_str(raw.get("content_sha"), f"clips[{i}].content_sha").lower()
        if not _SHA_RE.match(sha):
            raise IndexError_(f"clips[{i}].content_sha 必须是 64 位十六进制: {sha!r}")
        size = raw.get("bytes", 0)
        size = 0 if size is None else _as_int(size, f"clips[{i}].bytes")
        clips.append(ClipEntry(name=name, category=category, dir=clip_dir, frames=frames,
                               key_every=key_every, part=part, content_sha=sha, bytes=size))

    return CharacterIndex(
        schema=schema,
        id=_as_str(data.get("id"), "id"),
        name=_as_str(data.get("name", data.get("id")), "name"),
        canvas=(int(canvas[0]), int(canvas[1])),
        fps=float(fps),
        pipeline=_as_str(data.get("pipeline"), "pipeline"),
        format=_as_str(data.get("format"), "format"),
        parts=parts,
        clips=tuple(clips),
    )


def load_index(asset_dir: Path | str) -> CharacterIndex | None:
    """读 ``<角色目录>/character.json``；文件不存在返回 ``None``（老包/开发树）。

    解析失败**抛** :class:`IndexError_`——调用方决定是回退还是报错；静默忽略会
    让「索引坏了」看起来像「没有索引」。
    """
    path = Path(asset_dir) / INDEX_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IndexError_(f"character.json 读不开: {exc}") from exc
    except ValueError as exc:
        raise IndexError_(f"character.json 不是合法 JSON: {exc}") from exc
    return parse_index(data)


#: core 分件必须覆盖的交互闭包（缺任何一类，桌宠的日常交互就缺一角——
#: 「只有一个 idle、没有点击拖拽走路」的包不许通过准入，终审点名）。
CORE_REQUIRED_CATEGORIES: tuple[str, ...] = ("idle", "move", "turn", "click", "drag")


def core_gaps(root, index: CharacterIndex) -> list[str]:
    """``core`` 分件的**可玩性缺口**（返回缺口描述列表，空 = core 闭环可激活）。

    为什么要有这道闸（终审第四轮 B11）：页面允许单独装 extra，装完会生成本地
    索引；发现链只认"最高版本目录有索引"，于是"只装了 extra"的版本会在重启后
    **顶掉**原本完整的包 —— 用户丢掉 core 的交互闭包（点击/拖拽/走路），而且看不出
    原因。

    准入判据（三道，全过才算闭环）：
    1. **必需动作闭包**：``CORE_REQUIRED_CATEGORIES`` 每一类都至少有一段 core clip；
    2. core 声明的每一段都在盘上，且 meta 摘要与索引一致（不重算帧摘要，
       启动期不该变成全库读放大）；
    3. 磁盘帧数 == meta 帧数（一次 scandir 计数）——「全删帧、留下 meta」
       的包在这一条上现形（终审：meta 对不代表能播）。
    """
    gaps: list[str] = []
    core_clips = [c for c in index.clips if c.part == 'core']
    if not core_clips:
        # 索引里一段 core 都没有（只装过 extra）→ 它不是"可激活的完整包"。
        # 空集合不能当"闭包成立"，否则 extra-only 版本会顶掉完整包（B11）。
        return ['（无 core 段）']
    present_categories = {c.category for c in core_clips}
    for category in CORE_REQUIRED_CATEGORIES:
        if category not in present_categories:
            gaps.append(f'（缺 {category} 类动作）')
    for clip in core_clips:
        clip_dir = Path(root) / clip.dir
        if not clip_dir.is_dir():
            gaps.append(clip.name)
            continue
        try:
            meta = json.loads((clip_dir / 'meta.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            gaps.append(clip.name)
            continue
        if not isinstance(meta, dict):
            gaps.append(clip.name)
            continue
        if clip.content_sha and meta.get('content_sha') != clip.content_sha:
            gaps.append(clip.name)
            continue
        # 与运行时采纳闸门同方向的四条（终审第六轮：缺 encoder 的包能装能激活、
        # 运行时却把全部 clip 拒掉——准入不许比采纳松）
        from .frameseq_provision import ENCODER_DESC
        if str(meta.get('encoder') or '') != ENCODER_DESC:
            gaps.append(clip.name)
            continue
        meta_fps = meta.get('fps')
        if (not isinstance(meta_fps, (int, float)) or isinstance(meta_fps, bool)
                or not math.isfinite(float(meta_fps))
                or abs(float(meta_fps) - float(index.fps)) > 0.01):
            gaps.append(clip.name)
            continue
        declared = meta.get('frames')
        if (isinstance(declared, bool) or not isinstance(declared, int)
                or declared <= 0 or declared != clip.frames):
            gaps.append(clip.name)
            continue
        try:
            names = sorted(p.name for p in clip_dir.iterdir()
                           if p.name.startswith('f_') and p.suffix == '.webp')
        except OSError:
            gaps.append(clip.name)
            continue
        if names != [f"f_{i:04d}.webp" for i in range(1, declared + 1)]:
            gaps.append(clip.name)
    return gaps


ACTIVE_NAME = "active.json"


def set_active(version_dir) -> bool:
    """把某个版本目录设为该角色的**活动包**（原子写 ``active.json``）。

    装完不自动切：安装只是把字节放上盘，用哪套由用户显式选择（见
    docs/asset-arch 的市场设计）。写指针前**必须**再过一次 core 闭包，否则
    一个只装了 extra 的版本能靠写指针把自己变成活动包。
    """
    version_dir = Path(version_dir)
    try:
        index = load_index(version_dir)
    except IndexError_:
        index = None
    if index is None:
        return False
    if core_gaps(version_dir, index):
        logging.getLogger(__name__).warning(
            '拒绝激活 core 不闭环的版本: %s', version_dir)
        return False
    pointer = version_dir.parent / ACTIVE_NAME
    tmp = pointer.with_name(pointer.name + '.tmp')
    tmp.write_text(json.dumps({'use': version_dir.name}, ensure_ascii=False),
                   encoding='utf-8')
    os.replace(tmp, pointer)
    return True


def missing_parts(index: CharacterIndex, present: set[str]) -> set[str]:
    """索引里声明、但当前**未安装**的分件（用于动作池可用性判定）。"""
    return {c.part for c in index.clips} - set(present)


def clips_in_parts(index: CharacterIndex, present: set[str]) -> tuple[ClipEntry, ...]:
    """已安装分件里的 clip（未安装分件不得进入候选池）。"""
    return tuple(c for c in index.clips if c.part in present)


# ---------------------------------------------------------------------- 采纳闸门
def admissible(entry: ClipEntry, meta, frame_names, *,
               encoder: str, fps: float, canvas: tuple[int, int]) -> tuple[bool, str]:
    """无源包的**采纳闸门**：这个目录能不能当 ``entry`` 播放。

    终审 B2 指出的缺陷：原实现只比 "meta 帧数" 与 "文件数量"，于是旧 Q70 产物、
    错误处理链、甚至声明差分格式的目录，只要数量对就能交给独立帧播放器。
    这里把契约逐条落成**失败关闭**的检查，任何一条不符都不采纳（返回原因）。

    ``frame_names`` 是目录里实际存在的 ``f_*.webp`` 文件名列表（未排序也可）。
    """
    if not isinstance(meta, dict):
        return False, "meta 不是对象"
    raw_frames = meta.get("frames")
    if isinstance(raw_frames, bool) or not isinstance(raw_frames, int):
        return False, f"meta.frames 不是整数: {raw_frames!r}"
    if raw_frames != entry.frames:
        return False, f"帧数不符: meta={raw_frames} 索引={entry.frames}"
    enc = str(meta.get("encoder") or "")
    if enc != encoder:
        return False, f"编码档不符: {enc!r} != {encoder!r}"
    raw_fps = meta.get("fps")
    if not isinstance(raw_fps, (int, float)) or isinstance(raw_fps, bool):
        return False, f"meta.fps 非法: {raw_fps!r}"
    if not math.isfinite(float(raw_fps)) or float(raw_fps) <= 0:
        return False, f"meta.fps 必须是有限正数: {raw_fps!r}"
    if abs(float(raw_fps) - float(fps)) > 0.01:
        return False, f"帧率不符: {raw_fps} != {fps}"
    wh = meta.get("canvas") or meta.get("size")
    if wh is not None:
        try:
            if (int(wh[0]), int(wh[1])) != (int(canvas[0]), int(canvas[1])):
                return False, f"画布不符: {wh} != {list(canvas)}"
        except (TypeError, ValueError, IndexError):
            return False, f"meta 画布字段非法: {wh!r}"
    declared = meta.get("content_sha")
    # 内容身份**失败关闭**（终审 B2）：没有摘要就不可采纳——缺摘要无从
    # 判断磁盘帧是否就是索引声明的那批，宁可拒收也不能当成一致。
    if not declared:
        return False, "meta 缺内容摘要（无法确认磁盘帧与索引一致）"
    if str(declared).lower() != entry.content_sha:
        return False, "内容摘要与索引不符"
    expected = [f"f_{i:04d}.webp" for i in range(1, entry.frames + 1)]
    if sorted(frame_names) != expected:
        missing = len(expected) - len(frame_names)
        return False, f"帧文件不连续或不完整（期望 {entry.frames} 个，实有 {len(frame_names)}，差 {missing}）"
    return True, ""
