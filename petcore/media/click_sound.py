from __future__ import annotations

import random
from collections.abc import Sequence
from pathlib import Path

SUPPORTED_SUFFIXES = frozenset({".wav", ".mp3", ".ogg", ".flac", ".m4a"})

#: 自定义文件夹一次最多取多少个候选（防止用户误选巨大目录）。
MAX_FOLDER_CANDIDATES = 128

#: 进程内 folder 包候选缓存条目上限（点击路径每兜底重解析一次都付目录扫描）。
_FOLDER_CACHE_LIMIT = 8

#: 自带音效包 id 白名单：目录名即包名，新增素材 = 放目录 + 在 ``petcore/config.py``
#: 的 ``_CLICK_SOUND_BUILTINS`` 登记（``default`` 是单文件 ``click.wav``，不在此表）。
BUILTIN_PACK_DIRS = frozenset({"duck", "Deep!Seek!", "to鲸wu"})

_DEFAULT_PACK = {"kind": "builtin", "id": "default", "path": ""}

_folder_cache: dict[tuple[str, int], tuple[Path, ...]] = {}
_builtin_cache: dict[str, tuple[Path, ...]] = {}


def _pack_fields(pack) -> tuple[str, str, str]:
    if not isinstance(pack, dict):
        pack = _DEFAULT_PACK
    kind = str(pack.get("kind") or "builtin").strip().lower()
    pack_id = str(pack.get("id") or "default").strip()
    path = str(pack.get("path") or "").strip()
    return kind, pack_id, path


def _duck_candidates(duck_dir: Path) -> list[Path]:
    """内置音效包候选：wav 优先，同名 mp3 只在缺 wav 时补位（转码后不会重复播两遍）。

    包内素材是打包资源，运行期不会变；点击与碰撞每次命中都会走到这里，
    不缓存就是 GUI 线程上每次一次目录扫描。返回副本，调用方的改动不污染缓存。
    """
    key = str(duck_dir)
    cached = _builtin_cache.get(key)
    if cached is not None:
        return list(cached)
    candidates: list[Path] = []
    if duck_dir.is_dir():
        try:
            entries = sorted(duck_dir.iterdir())
        except OSError:
            entries = []
        wavs = [path for path in entries if path.is_file() and path.suffix.lower() == ".wav"]
        covered = {path.stem.lower() for path in wavs}
        mp3s = [
            path
            for path in entries
            if path.is_file()
            and path.suffix.lower() == ".mp3"
            and path.stem.lower() not in covered
        ]
        candidates = wavs + mp3s
    _builtin_cache[key] = tuple(candidates)
    return candidates


def _folder_candidates(folder: Path) -> list[Path]:
    """用户自定义文件夹候选（带进程内缓存 + 目录不可读兜底）。

    点击路径每次松手都会解析候选；网络盘 / OneDrive 占位 / 休眠 U 盘上的目录
    ``iterdir`` 会阻塞 GUI 线程。缓存键取目录 mtime：增删文件后自动失效，
    读不到目录只返回空表，绝不让异常打断点击反馈。
    """
    try:
        stamp = folder.stat().st_mtime_ns
    except OSError:
        return []
    key = (str(folder), stamp)
    cached = _folder_cache.get(key)
    if cached is not None:
        return list(cached)
    try:
        files = sorted(
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
    except OSError:
        return []
    files = files[:MAX_FOLDER_CANDIDATES]
    if len(_folder_cache) >= _FOLDER_CACHE_LIMIT:
        _folder_cache.pop(next(iter(_folder_cache)))
    _folder_cache[key] = tuple(files)
    return files



class ClickSoundPair:
    """按下/释放音效的 token 配对器。

    目前内置音效包只有 release 音；因此 ``begin`` 在没有按下候选时只
    登记 token，释放时仍播放原有 click 音。自定义包可传入 press 候选，
    失焦、拖拽和关闭则通过 ``cancel`` 丢弃 token，重复释放不会重播。
    """

    def __init__(self, play, *, rng: random.Random | None = None):
        self._play = play
        self._rng = rng
        self._next = 0
        self._active: set[int] = set()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def begin(self, press_candidates: Sequence[Path] = ()) -> int:
        self._next += 1
        token = self._next
        self._active.add(token)
        chosen = choose_click_sound(press_candidates, rng=self._rng)
        if chosen is not None:
            self._play(chosen)
        return token

    def release(self, token: int | None, release_candidates: Sequence[Path] = ()) -> bool:
        if token is None or token not in self._active:
            return False
        self._active.remove(token)
        chosen = choose_click_sound(release_candidates, rng=self._rng)
        if chosen is None:
            return False
        return bool(self._play(chosen))

    def cancel(self, token: int | None) -> bool:
        if token is None or token not in self._active:
            return False
        self._active.remove(token)
        return True

    def cancel_all(self) -> None:
        self._active.clear()

def resolve_click_sound_candidates(pack, asset_root: Path) -> list[Path]:
    """按音效包配置解析候选音频文件（不存在/不支持一律返回空表）。"""
    kind, pack_id, path_text = _pack_fields(pack)
    sounds = Path(asset_root) / "sounds"
    if kind == "builtin":
        if pack_id == "default":
            click = sounds / "click.wav"
            return [click] if click.is_file() else []
        if pack_id not in BUILTIN_PACK_DIRS:
            return []
        return _duck_candidates(sounds / pack_id)
    if kind == "file":
        if not path_text:
            return []
        candidate = Path(path_text).expanduser()
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SUFFIXES:
            return [candidate]
        return []
    if kind == "folder":
        if not path_text:
            return []
        folder = Path(path_text).expanduser()
        if not folder.is_dir():
            return []
        return _folder_candidates(folder)
    return []


def choose_click_sound(candidates: Sequence[Path], rng: random.Random | None = None) -> Path | None:
    """从候选列表里挑一个（传入 rng 可复现）；空表返回 None。"""
    items = list(candidates or ())
    if not items:
        return None
    if len(items) == 1:
        return items[0]
    picker = rng if rng is not None else random
    return picker.choice(items)
