"""「常规 → 素材包」的应用内下载行。

一次点击走三步：读索引（6 小时磁盘缓存，失败回退旧缓存）→ 分段下载（服务端不支持
Range 就回退单流）→ 校验。下载完成只显示「导入这个素材包」，不自动安装：
两个素材包行指向同一个包时，自动导入会撞上「不覆盖已有角色」而看起来像失败。

线程边界与 ``roots_job`` 一致：守护线程干活、信号回 GUI，控件只在 GUI 线程碰。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ... import paths
from ...net import packs
from ..download_progress import DownloadPanel
from .base import make_button

logger = logging.getLogger("petcore.ui.settings.pack_download")

#: 索引磁盘缓存的保鲜期：索引很小，6 小时足够，又能保证换包当天可见。
INDEX_TTL_S = 6 * 60 * 60

#: 单次下载的并发连接数（与市场页同口径）。
DOWNLOAD_CONNECTIONS = 4

#: 素材包体积上限：超过 4 GiB 直接拒绝，避免把用户磁盘塞满。
MAX_PACK_BYTES = 4 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PackTarget:
    pack_id: str
    index_url: str
    links: Mapping[str, str]
    #: 主索引源失败时按顺序尝试的兜底地址。
    index_fallbacks: tuple[str, ...] = ()

    def link(self, key: str) -> str:
        return str(self.links.get(key) or "")

    def index_urls(self) -> tuple[str, ...]:
        return tuple(url for url in (self.index_url, *self.index_fallbacks) if url)


# --------------------------------------------------------------------- 缓存
def cache_path(root: Path) -> Path:
    return paths.cache_dir(root, "packs") / "index.json"


def load_cached_index(
    path: Path, *, max_age: float = INDEX_TTL_S, allow_stale: bool = False
) -> packs.PackIndex | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not allow_stale and time.time() - stat.st_mtime > max_age:
        return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    try:
        return packs.parse_index(payload)
    except Exception:  # noqa: BLE001 - 坏缓存不能让下载入口崩
        # 自愈：坏缓存必须删掉，否则用户会被一个解析不了的文件卡到 TTL 结束。
        logger.warning("素材包索引缓存不可用，已删除：%s", path.name)
        discard_cached_index(path)
        return None


def discard_cached_index(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("删除素材包索引缓存失败")


def save_cached_index(path: Path, payload: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp.write_bytes(payload)
        os.replace(temp, path)
    except OSError:
        logger.warning("写入素材包索引缓存失败")


def fetch_index_payload(url: str, *, timeout: float = 15.0) -> bytes:
    """取索引原文（落缓存用），解析交给 :func:`petcore.net.packs.parse_index`。"""
    from ...net import http

    status, _headers, body = http.fetch_url(
        str(url),
        headers=(("Accept", "application/json"), ("User-Agent", "dsh-pet-core-packs")),
        timeout=timeout,
        max_bytes=4 * 1024 * 1024,
    )
    if status < 200 or status >= 300:
        raise ValueError(f"索引返回 {status}")
    if getattr(body, "truncated", False):
        raise ValueError("索引超过 4 MiB")
    return bytes(body)


def fetch_first_index(
    urls: Sequence[str],
    cache_file: Path,
    *,
    attempts: int = 2,
    use_cache: bool = True,
) -> packs.PackIndex:
    """按顺序试每个索引源（每个最多试 ``attempts`` 次）；成功就落缓存。

    链路会抖：同一个地址 2 分钟前 2.4 s 成功、下一分钟就超时，所以每个源都要重试一次
    再换下一个。全失败时抛出**每个**主机的原因，别让用户只看到「索引读取失败」。

    **先解析、后落缓存**：jsDelivr 对分支文件有 CDN 缓存，实测会把上一版索引（分片 urls
    为空）原样返回；以前先写缓存再解析，结果坏内容既污染了缓存、又直接终止整条链路
    （用户看到「分片 1 urls 为空」）。现在坏内容只跳过一个源，绝不留痕。
    """
    if use_cache:
        cached = load_cached_index(cache_file)
        if cached is not None:
            return cached
    problems: list[str] = []
    for url in urls:
        host = packs.host_of(url) or "-"
        for _attempt in range(max(1, int(attempts))):
            try:
                payload = fetch_index_payload(str(url), timeout=20.0)
                index = packs.parse_index(payload)
            except Exception as exc:  # noqa: BLE001 - 换下一个索引源
                logger.info(
                    "素材包索引不可用：host=%s type=%s", host, type(exc).__name__
                )
                problems.append(f"{host}: {type(exc).__name__}")
                continue
            save_cached_index(cache_file, payload)
            return index
    discard_cached_index(cache_file)
    raise ValueError("索引读取失败：" + "；".join(dict.fromkeys(problems)))


# --------------------------------------------------------------------- 任务
class PackJob(QObject):
    progress = Signal(int, int)
    status = Signal(str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        target: PackTarget,
        dest_dir: Path,
        cache_file: Path,
        connections: int = DOWNLOAD_CONNECTIONS,
        policy: str = "auto",
    ) -> None:
        super().__init__()
        self._target = target
        self._dest_dir = Path(dest_dir)
        self._cache_file = Path(cache_file)
        self._connections = int(connections)
        self._policy = str(policy or "auto")
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        #: 索引里的那条记录（`done` 之后 UI 需要它的 `character` 才能装成对的角色 id）。
        self.entry: packs.PackEntry | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="petcore-pack", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for name in ("progress", "status", "done", "failed"):
                try:
                    getattr(self, name).disconnect()
                except (RuntimeError, TypeError):
                    pass

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _say(self, message: str) -> None:
        if self._cancel.is_set():
            return
        self.status.emit(str(message))

    def _run(self) -> None:
        try:
            self._say("正在读取素材包索引…")
            index = fetch_first_index(self._target.index_urls(), self._cache_file)
            entry = index.by_id(self._target.pack_id)
            if entry is None:
                raise ValueError(f"索引里没有素材包 {self._target.pack_id}")
            self.entry = entry
            self._say(
                f"开始下载 {entry.name}（{entry.size / (1024 * 1024):.1f} MiB → {self._dest_dir}）"
            )
            result = packs.download_pack(
                entry,
                self._dest_dir,
                connections=self._connections,
                policy=self._policy,
                progress=self.progress.emit,
                cancel=self._cancel.is_set,
                max_bytes=MAX_PACK_BYTES,
            )
        except Exception as exc:  # noqa: BLE001 - 线程异常必须变成信号
            if self._cancel.is_set():
                return
            logger.warning("素材包下载失败：%s", type(exc).__name__)
            detail = str(exc) or type(exc).__name__
            self.failed.emit(detail)
        else:
            if self._cancel.is_set():
                return
            self.done.emit(result)
        finally:
            self._thread = None


class _ImportSignals(QObject):
    done = Signal(object)


class _ImportJob(QRunnable):
    def __init__(
        self,
        source: Path,
        root: Path,
        signals: _ImportSignals,
        *,
        character_id: str = "",
    ) -> None:
        super().__init__()
        self._source = Path(source)
        self._root = Path(root)
        self._signals = signals
        self._character_id = str(character_id or "")

    def run(self) -> None:
        try:
            from ...content.importer import install_character_source

            result = install_character_source(
                self._source, self._root, character_id=self._character_id or None
            )
        except Exception as exc:  # noqa: BLE001 - 异常不许穿出 QRunnable
            result = (False, f"导入失败：{type(exc).__name__}", None)
        try:
            self._signals.done.emit(result)
        except RuntimeError:
            pass


# --------------------------------------------------------------------- 行
class PackRow(QWidget):
    """素材包下载行：应用内下载 + 原来的两个跳转按钮。"""

    def __init__(
        self,
        page,
        *,
        target: PackTarget,
        dest_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._page = page
        self._target = target
        self._dest_dir = Path(dest_dir) if dest_dir is not None else None
        self._job: PackJob | None = None
        self._import_signals: _ImportSignals | None = None
        self._downloaded: Path | None = None
        self._character = ""
        self._disposed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        self.download_button = make_button("GitHub 下载", buttons)
        self.download_button.setToolTip("在设置页内下载，显示进度与实时网速；服务端支持分段时多连接加速")
        self.download_button.clicked.connect(self.start_download)
        self.github_button = make_button("打开 GitHub", buttons)
        self.github_button.clicked.connect(
            lambda: self._open(self._target.link("github"))
        )
        self.quark_button = make_button("夸克网盘", buttons)
        self.quark_button.clicked.connect(lambda: self._open(self._target.link("quark")))
        self.import_button = make_button("导入这个素材包", buttons)
        self.import_button.clicked.connect(self.import_downloaded)
        self.import_button.hide()
        for button in (
            self.download_button,
            self.github_button,
            self.quark_button,
            self.import_button,
        ):
            button_layout.addWidget(button)
        button_layout.addStretch(1)
        layout.addWidget(buttons)

        self.panel = DownloadPanel(self, with_cancel=True)
        self.panel.cancel_requested.connect(self.cancel_download)
        layout.addWidget(self.panel)

    # ------------------------------------------------------------ 状态
    def set_dest_dir(self, dest_dir: Path) -> None:
        self._dest_dir = Path(dest_dir)

    def dest_dir(self) -> Path:
        if self._dest_dir is not None:
            return self._dest_dir
        resolver = getattr(self._page, "pack_download_dir", None)
        if callable(resolver):
            self._dest_dir = Path(resolver())
        else:
            self._dest_dir = Path(self._page.host.root) / "tmp"
        return self._dest_dir

    def _set_status(self, text: str) -> None:
        self.panel.set_status(text)

    def _open(self, url: str) -> None:
        if not url:
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))

    def busy(self) -> bool:
        return self._job is not None and self._job.is_running()

    # ------------------------------------------------------------ 下载
    def start_download(self) -> None:
        if self._disposed or self.busy():
            return
        self._downloaded = None
        self.import_button.hide()
        self.download_button.setEnabled(False)
        self.panel.begin(0, connections=DOWNLOAD_CONNECTIONS)
        self._set_status("正在准备…")
        policy = "auto"
        config = getattr(self._page, "config", None)
        if callable(config):
            try:
                policy = str(config().get("pack_mirror_policy") or "auto")
            except Exception:  # noqa: BLE001 - 配置读不出来就用默认
                policy = "auto"
        job = PackJob(
            target=self._target,
            dest_dir=self.dest_dir(),
            cache_file=cache_path(self._page.host.root),
            policy=policy,
        )
        job.progress.connect(self.panel.update_progress)
        job.status.connect(self._set_status)
        job.done.connect(self._on_done)
        job.failed.connect(self._on_failed)
        self._job = job
        job.start()

    def cancel_download(self) -> None:
        if self._job is None:
            return
        self._job.cancel()
        self._job = None
        self.download_button.setEnabled(True)
        self.panel.fail("已取消；已下载的字节保留，下次续传。")
        self.panel.cancel_button.setEnabled(False)

    def _finish(self) -> None:
        self.download_button.setEnabled(True)
        if self.panel.cancel_button is not None:
            self.panel.cancel_button.setEnabled(True)

    def _on_done(self, result) -> None:
        if self._disposed:
            return
        self._finish()
        self._downloaded = Path(result.path)
        entry = getattr(self._job, "entry", None) if self._job is not None else None
        self._character = str(getattr(entry, "character", "") or self._target.pack_id)
        origin = "已有缓存" if getattr(result, "from_cache", False) else "下载完成"
        host = str(getattr(result, "url_host", "") or "")
        self.panel.finish(
            f"{origin}：{self._downloaded.name} · "
            f"{self._downloaded.stat().st_size / (1024 * 1024):.1f} MiB · "
            f"{max(1, int(getattr(result, 'connections', 1)))} 连接"
            + (f" · 来自 {host}" if host else "")
        )
        self.import_button.show()

    def character_id(self) -> str:
        """导入时用的角色 id：索引里的 `character` 优先，缺省退回包 id。

        不能用 ZIP 文件名推断——`shenshen-1.zip` 会装成 `character.shenshen-1`。
        """
        return self._character or self._target.pack_id

    def _on_failed(self, message: str) -> None:
        if self._disposed:
            return
        self._finish()
        hint = ""
        if "github.com" in message or "Timeout" in message or "timeout" in message:
            hint = "；GitHub 直连不可达时可先开代理再试，或点「打开 GitHub」手动下载"
        self.panel.fail(f"下载失败：{message}{hint}")

    # ------------------------------------------------------------ 导入
    def import_downloaded(self) -> None:
        if self._downloaded is None or not self._downloaded.is_file():
            self._set_status("还没有下载好的素材包")
            return
        self.import_button.setEnabled(False)
        self._set_status("正在导入角色…（大包需要几十秒）")
        signals = _ImportSignals(self)
        signals.done.connect(self._on_imported)
        self._import_signals = signals
        QThreadPool.globalInstance().start(
            _ImportJob(
                self._downloaded,
                Path(self._page.host.root),
                signals,
                character_id=self.character_id(),
            )
        )

    def _on_imported(self, result) -> None:
        if self._disposed:
            return
        self.import_button.setEnabled(True)
        ok, message, character_id = result
        if ok:
            self._set_status(f"已导入 {character_id}；到「常规 → 角色」切换即可使用。")
        else:
            self._set_status(f"导入失败：{message}")

    # ------------------------------------------------------------ 释放
    def dispose(self) -> None:
        self._disposed = True
        if self._job is not None:
            self._job.cancel()
            self._job = None
        self._import_signals = None
