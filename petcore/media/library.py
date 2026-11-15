from __future__ import annotations

import logging
from pathlib import Path

from ..contracts.content import AnimationSpec
from .clip import PetClip, create_clip, create_frame_clip, create_fallback_clip
from . import backends as _backends  # noqa: F401  (registers built-in backends)

logger = logging.getLogger("petcore.media.library")


class ClipLibrary:
    #: 缓存上界：每个 clip 都带播放器/预取 worker 与至多 1–2 帧内存；没有上界时
    #: 90+ 段动画会被整场留着（旧版线程/句柄上涨的同构风险面）。
    MAX_CLIPS = 16
    #: 精灵表单张解码图常驻内存；16 段 LRU 只按个数限流会吃掉几百 MB。
    MAX_SHEET_BYTES = 96 * 1024 * 1024

    def __init__(self, frameseq_mode: str = "auto"):
        self.frameseq_mode = frameseq_mode if frameseq_mode in {"auto", "off", "only"} else "auto"
        self._clips: dict[str, PetClip] = {}
        self._bytes: dict[str, int] = {}
        #: LRU：最近使用在末尾；淘汰从头部取。
        self._order: list[str] = []

    def clip_for(self, spec: AnimationSpec) -> PetClip | None:
        cached = self._clips.get(spec.name)
        if cached is not None:
            self._touch(spec.name)
            return cached
        clip = self._create(spec)
        if clip is None:
            return None
        self._clips[spec.name] = clip
        self._bytes[spec.name] = int(getattr(clip, "estimated_bytes", 0) or 0)
        self._order.append(spec.name)
        self._trim()
        return clip

    def _touch(self, name: str) -> None:
        if name in self._order:
            self._order.remove(name)
        self._order.append(name)

    def _trim(self) -> None:
        while len(self._order) > self.MAX_CLIPS or (
            len(self._order) > 1 and sum(self._bytes.values()) > self.MAX_SHEET_BYTES
        ):
            victim = self._order.pop(0)
            clip = self._clips.pop(victim, None)
            self._bytes.pop(victim, None)
            if clip is not None:
                try:
                    clip.close()
                except Exception:  # noqa: BLE001 - 淘汰失败不许影响播放
                    logger.debug("clip eviction failed: %s", victim, exc_info=True)

    def _create(self, spec: AnimationSpec) -> PetClip | None:
        if spec.frameseq_dir is not None and self.frameseq_mode != "off":
            clip = create_frame_clip(Path(spec.frameseq_dir), spec.name)
            if clip is not None:
                return clip
            if self.frameseq_mode == "only":
                return None
        if self.frameseq_mode == "only":
            return None
        if spec.sheet is not None:
            from .sheet_clip import create_sheet_clip

            clip = create_sheet_clip(spec.sheet, spec.name)
            if getattr(clip, "error", ""):
                logger.warning("sprite sheet decode failed: %s", clip.error)
                clip.deleteLater()
                return None
            return PetClip(clip, spec.name)
        if spec.path is not None:
            return create_clip(Path(spec.path), spec.name)
        return None

    def fallback(self, name: str = "fallback") -> PetClip:
        clip = self._clips.get(name)
        if clip is None:
            clip = create_fallback_clip(name)
            self._clips[name] = clip
        return clip

    def warm_first_frame(self, spec: AnimationSpec) -> None:
        clip = self.clip_for(spec)
        if clip is not None:
            clip.warm_first_frame()

    def stop_all(self) -> None:
        for clip in self._clips.values():
            clip.stop()

    def close_all(self) -> None:
        for clip in self._clips.values():
            clip.close()
        self._clips.clear()
        self._bytes.clear()
