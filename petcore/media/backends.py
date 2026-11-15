from __future__ import annotations

from pathlib import Path

from .clip import create_clip, register_backend


def _webm(path: Path):
    from pet.webm_clip import WebMClip

    return WebMClip(path)


def _gif(path: Path):
    from pet.library import GifClip

    return GifClip(path)


def _webp(path: Path):
    from .webp_clip import WebPClip

    return WebPClip(path)


register_backend("webm", _webm)
register_backend("gif", _gif)
register_backend("webp", _webp)


def create(path: Path, name: str):
    return create_clip(Path(path), name)
