"""语音文件缓存：``<data_root>/cache/tts/`` 下的扁平文件。

缓存键是 ``sha1(provider + flavor + text)``——换后端、换音色、换语速都换键，
不会把上一版的声音当命中。**命中要求文件非空**：合成中途失败/被杀留下的 0 字节
文件如果被当成缓存，用户听到的是永久静音，所以空文件当场删掉并重新合成。

失败产物由调用方（``SpeechService``）在 ``finally`` 里删；本模块只做纯粹的
文件操作，不认识 provider。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ..contracts.speech import SpeechError
from .. import paths


class SpeechCache:
    def __init__(self, root: Path, *, limit: int = 200) -> None:
        self.root = Path(root)
        self.limit = max(1, int(limit))

    # ------------------------------------------------------------ 路径
    def directory(self) -> Path:
        path = paths.cache_dir(self.root, "cache", "tts")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def key(self, provider: str, flavor: str, text: str) -> str:
        raw = f"{provider}\x00{flavor}\x00{text}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def path(
        self, provider: str, flavor: str, text: str, *, suffix: str = "mp3"
    ) -> Path:
        extension = str(suffix or "mp3").lstrip(".").lower()
        name = f"{self.key(provider, flavor, text)}.{extension}"
        return paths.cache_dir(self.root, "cache", "tts") / name

    def temp_path(self, destination: Path) -> Path:
        """同一目录下的临时名：``os.replace`` 才是原子的（跨盘 rename 不是）。"""
        destination = Path(destination)
        return destination.with_name(
            f".{destination.stem}.{os.getpid()}-{os.urandom(4).hex()}.part"
        )

    # ------------------------------------------------------------ 读写
    def hit(
        self, provider: str, flavor: str, text: str, *, suffix: str = "mp3"
    ) -> Path | None:
        path = self.path(provider, flavor, text, suffix=suffix)
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size > 0:
            return path
        self.discard(path)
        return None

    def replace(self, source: Path, destination: Path) -> Path:
        """把合成产物原子地放进缓存位置；空产物视为失败并删掉。"""
        source = Path(source)
        destination = Path(destination)
        try:
            size = source.stat().st_size
        except OSError:
            raise SpeechError("cache", "合成没有产出文件") from None
        if size <= 0:
            self.discard(source)
            raise SpeechError("cache", "合成产物为空")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        return destination

    def discard(self, path: Path | str | None) -> None:
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------ 容量
    def entries(self) -> list[Path]:
        try:
            return [path for path in self.directory().iterdir() if path.is_file()]
        except OSError:
            return []

    def count(self) -> int:
        return len(self.entries())

    def trim(self, limit: int | None = None) -> int:
        """按 mtime 保留最新 ``limit`` 个文件，返回删除数量。"""
        keep = max(1, int(self.limit if limit is None else limit))
        files: list[tuple[float, Path]] = []
        for path in self.entries():
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                continue
        if len(files) <= keep:
            return 0
        files.sort(key=lambda item: item[0])
        removed = 0
        for _mtime, path in files[: len(files) - keep]:
            self.discard(path)
            removed += 1
        return removed
