"""Host 端口的 Qt 实现：定时器、只读配置、存储、宠物注册表、交互事件与免打扰。"""

from __future__ import annotations

import itertools
import json
import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .. import paths
from ..contracts.api import ApiMetadata, ApiRequest
from ..contracts.host import HostPorts
from ..contracts.http import HttpError, HttpRequest, HttpResponse
from ..contracts.speech import SpeechError
from ..engine.catalog import GraphType
from .speech_channel import CoreSpeechPort

logger = logging.getLogger("petcore.ui.ports")


class _SchedulerBridge(QObject):
    """定时器的 GUI 线程代理：信号排队，QTimer 只在归属线程创建/销毁。"""

    schedule = Signal(str, int, object, bool)
    stop = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._timers: dict[str, QTimer] = {}
        self.schedule.connect(self._start, Qt.ConnectionType.QueuedConnection)
        self.stop.connect(self._stop, Qt.ConnectionType.QueuedConnection)

    def _start(self, handle: str, interval_ms: int, callback, repeating: bool) -> None:
        # 定时器不挂到 bridge 名下：bridge 被 GC 时若还留着一个 deleteLater
        # 的子定时器，Qt 会留下悬空的延迟删除事件（进程崩溃）。生命周期由
        # ``_timers`` 字典持有。
        timer = QTimer()
        timer.setSingleShot(not repeating)
        timer.setInterval(max(1 if repeating else 0, int(interval_ms)))
        timer.timeout.connect(lambda: self._fire(handle, callback, repeating))
        self._timers[handle] = timer
        timer.start()

    def _fire(self, handle: str, callback, repeating: bool) -> None:
        if not repeating:
            timer = self._timers.pop(handle, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
        try:
            callback()
        except Exception:
            logger.exception("scheduled callback failed")

    def _stop(self, handle: str) -> None:
        timer = self._timers.pop(handle, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()


class QtScheduler:
    """可从任意线程调用的调度器：定时器与回调都在 GUI 线程执行。"""

    def __init__(self) -> None:
        self._counter = itertools.count(1)
        self._bridge = _SchedulerBridge()
        #: owner → 句柄：`cancel_owner()` 只撤这个 owner 的定时器。
        self._owners: dict[str, list[str]] = {}

    def _new_handle(self) -> str:
        return f"timer-{next(self._counter)}"

    def _track(self, owner: str, handle: str) -> str:
        key = str(owner or "")
        if key:
            self._owners.setdefault(key, []).append(handle)
        return handle

    def _forget(self, handle: str) -> None:
        for key, handles in list(self._owners.items()):
            if handle in handles:
                handles.remove(handle)
            if not handles:
                self._owners.pop(key, None)

    def call_later(self, delay_ms: int, callback, *, owner: str = "") -> str:
        handle = self._new_handle()
        self._track(owner, handle)
        self._bridge.schedule.emit(handle, max(0, int(delay_ms)), callback, False)
        return handle

    def call_every(self, interval_ms: int, callback, *, owner: str = "") -> str:
        handle = self._new_handle()
        self._track(owner, handle)
        self._bridge.schedule.emit(handle, max(1, int(interval_ms)), callback, True)
        return handle

    def cancel(self, handle: str) -> None:
        self._forget(handle)
        self._bridge.stop.emit(handle)

    def cancel_owner(self, owner: str) -> None:
        """撤掉这个 owner 注册过的全部定时器（卸载/重载扩展时必须调）。"""
        for handle in self._owners.pop(str(owner or ""), []):
            self._bridge.stop.emit(handle)


class QtConfigPort:
    """配置端口：读取失败或缺值一律回默认，mod 不会因配置异常中断。

    ``set`` 只给菜单命令这类显式用户动作：写全局配置并立刻落盘（与 Core 菜单
    勾选项同语义）；设置页的实时预览/取消回滚仍由对话框管理。
    """

    def __init__(self, config) -> None:
        self._config = config

    def get(self, key: str, default=None):
        try:
            value = self._config.get(key, default)
        except Exception:
            return default
        return default if value is None else value

    def set(self, key: str, value) -> None:
        target = getattr(self._config, "global_config", self._config)
        target.set(str(key), value)
        target.save()


class AudioAdapter:
    def __init__(self, window) -> None:
        self.window = window

    def play(self, path: Path | str, volume: float | None = None) -> bool:
        try:
            player = self.window.sound
            if volume is not None:
                player.set_volume(float(volume))
            return player.play(Path(path)) is not False
        except Exception:
            logger.debug("audio play failed", exc_info=True)
            return False

    def play_tracked(self, path, *, volume=1.0, on_finished, on_error):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self.stop()
        if not hasattr(self, "_player"):
            self._player = QMediaPlayer()
            self._output = QAudioOutput()
            self._player.setAudioOutput(self._output)
            self._player.mediaStatusChanged.connect(self._media_status)
            self._player.errorOccurred.connect(self._media_error)
        self._completion = (on_finished, on_error)
        self._output.setVolume(max(0.0, min(1.0, float(volume))))
        url = QUrl.fromLocalFile(str(Path(path).resolve()))
        same_source = self._player.source() == url
        if same_source and self._player.mediaStatus() != QMediaPlayer.MediaStatus.InvalidMedia:
            self._start_loaded()
        else:
            if same_source:
                self._player.setSource(QUrl())
            self._player.setSource(url)
        return True

    def _start_loaded(self):
        # Do not start FFmpeg decoding before LoadedMedia. An immediate stop
        # during asynchronous loading can otherwise block the Windows backend.
        from PySide6.QtMultimedia import QMediaPlayer

        completion = self._completion
        ready = {QMediaPlayer.MediaStatus.LoadedMedia,
                 QMediaPlayer.MediaStatus.BufferedMedia,
                 QMediaPlayer.MediaStatus.EndOfMedia}

        def play_if_ready():
            if (completion is not None and self._completion is completion
                    and self._player.mediaStatus() in ready):
                self._player.play()

        QTimer.singleShot(0, play_if_ready)

    def _media_status(self, status):
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self._completion:
                self._start_loaded()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            completion, self._completion = self._completion, None
            if completion:
                completion[0]()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._media_error()

    def _media_error(self, *_args):
        completion, self._completion = getattr(self, "_completion", None), None
        if completion:
            completion[1](SpeechError("unavailable", "audio playback failed"))

    def stop(self):
        self._completion = None
        player = getattr(self, "_player", None)
        if player is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            if player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
                player.stop()

    def close(self):
        self.stop()
        if hasattr(self, "_player"):
            self._player.deleteLater()
            self._output.deleteLater()
            del self._player, self._output

    def set_volume(self, volume: float) -> None:
        try:
            self.window.sound.set_volume(float(volume))
        except Exception:
            pass


class StorageAdapter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def data_dir(self, owner: str) -> Path:
        path = paths.feature_data_dir(self.root, owner)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def config_dir(self, owner: str) -> Path:
        return self.data_dir(owner)

    def data_root(self) -> Path:
        return self.root


class QuietMode:
    def __init__(self) -> None:
        self._until: float | None = None
        self._listeners = []

    def subscribe(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def set_quiet(self, seconds: float) -> None:
        self._until = time.monotonic() + max(0.0, float(seconds)) if seconds > 0 else None
        for callback in tuple(self._listeners):
            callback(seconds > 0)

    def quiet_active(self) -> bool:
        if self._until is None:
            return False
        if time.monotonic() >= self._until:
            self._until = None
            return False
        return True

    def quiet_until(self) -> float | None:
        return self._until

    def remaining_seconds(self) -> float:
        until = self._until
        if until is None:
            return 0.0
        remaining = until - time.monotonic()
        if remaining <= 0.0:
            self._until = None
            return 0.0
        return remaining


class PetHandleImpl:
    def __init__(self, host, window) -> None:
        self.host = host
        self.window = window
        self.slot = window.slot
        self.is_primary = window.is_primary
        self.config = window.config

    def body_rect(self) -> tuple[int, int, int, int]:
        return self.window.body_rect()

    def velocity(self) -> tuple[float, float]:
        return self.window.motion.velocity()

    def move_by(self, dx: float, dy: float) -> None:
        self.window.move(
            int(round(self.window.x() + dx)), int(round(self.window.y() + dy))
        )

    def set_velocity(self, vx: float, vy: float) -> None:
        self.window.motion.set_velocity(vx, vy)

    def show(self) -> None:
        self.window.set_visible(True)

    def hide(self) -> None:
        self.window.set_visible(False)

    def say(self, text: str, duration_ms: int = 4000, subtitle: str = "") -> None:
        subtitle = str(subtitle or "").strip()
        if subtitle:
            self.window.say(text, duration_ms, subtitle=subtitle)
        else:
            self.window.say(text, duration_ms)

    def play_animation(self, name: str) -> bool:
        return self.window.play_animation(name)

    def play_event(self, group: str, variant: str | int | None = None) -> bool:
        return self.window.play_event(group, variant)

    def animations(self) -> tuple[str, ...]:
        """当前角色的「动作」名（`GraphType.DEFAULT`，与行为链 `act()` 同池）。

        联动动作池按名字挑选，缺名时按关键词回退；没有动作的角色返回空元组。
        """
        catalog = getattr(self.window, "catalog", None)
        names_for = getattr(catalog, "names_for", None)
        if not callable(names_for):
            return ()
        try:
            return tuple(names_for(GraphType.DEFAULT))
        except Exception:
            logger.debug("animations() failed", exc_info=True)
            return ()


class PetRegistry:
    def __init__(self, host) -> None:
        self.host = host

    def pets(self) -> tuple[PetHandleImpl, ...]:
        manager = getattr(self.host, "pets", None)
        if manager is None:
            return ()
        return tuple(PetHandleImpl(self.host, window) for window in manager.each())

    def primary(self) -> PetHandleImpl | None:
        window = getattr(self.host, "window", None)
        return PetHandleImpl(self.host, window) if window is not None else None

    def active(self) -> PetHandleImpl | None:
        window = getattr(self.host, "_active_window", None) or getattr(self.host, "window", None)
        return PetHandleImpl(self.host, window) if window is not None else None


class InteractionAdapter:
    def __init__(self, host) -> None:
        self.host = host
        self._click_callbacks: dict[str, list] = {}
        self._drop_callbacks: dict[str, list] = {}
        self._timers: dict[str, list[str]] = {}
        self._scheduler = QtScheduler()
        # 每个窗口一份包装表：``id(window) -> (window, {id(callback): wrapper})``。
        # 包装器按 (窗口, 回调) 唯一，重复 attach 不会让一次点击触发多次。
        self._click_wrappers: dict[int, tuple[object, dict[int, object]]] = {}
        self._drop_wrappers: dict[int, tuple[object, dict[int, object]]] = {}

    def _windows(self):
        manager = getattr(self.host, "pets", None)
        if manager is not None:
            return list(manager.each())
        window = getattr(self.host, "window", None)
        return [window] if window is not None else []

    def _wrapper_table(self, registry, window) -> dict[int, object]:
        key = id(window)
        entry = registry.get(key)
        if entry is None or entry[0] is not window:
            entry = (window, {})
            registry[key] = entry
        return entry[1]

    def attach_window(self, window) -> None:
        """把当前所有点击/投喂回调挂到 ``window``（子宠 spawn 后必须补这一挂）。"""
        if window is None:
            return
        click_table = self._wrapper_table(self._click_wrappers, window)
        add_click = getattr(window, "add_click_listener", None)
        if callable(add_click):
            for callbacks in self._click_callbacks.values():
                for callback in callbacks:
                    marker = id(callback)
                    if marker in click_table:
                        continue
                    wrapper = lambda win, cb=callback: cb(win)
                    click_table[marker] = wrapper
                    add_click(wrapper)
        drop_table = self._wrapper_table(self._drop_wrappers, window)
        add_drop = getattr(window, "add_drop_listener", None)
        if callable(add_drop):
            for callbacks in self._drop_callbacks.values():
                for callback in callbacks:
                    marker = id(callback)
                    if marker in drop_table:
                        continue
                    wrapper = lambda items, cb=callback: cb(list(items))
                    drop_table[marker] = wrapper
                    add_drop(wrapper)

    def detach_window(self, window) -> None:
        """窗口关闭时摘掉监听，避免回调持有已销毁的窗口。"""
        if window is None:
            return
        for registry, remover in (
            (self._click_wrappers, "remove_click_listener"),
            (self._drop_wrappers, "remove_drop_listener"),
        ):
            entry = registry.pop(id(window), None)
            if entry is None or entry[0] is not window:
                continue
            remove = getattr(window, remover, None)
            if not callable(remove):
                continue
            for wrapper in entry[1].values():
                try:
                    remove(wrapper)
                except RuntimeError:
                    break

    def on_click(self, owner: str, callback):
        self._click_callbacks.setdefault(owner, []).append(callback)
        for window in self._windows():
            self.attach_window(window)
        return lambda: self._remove(
            owner, callback, self._click_callbacks, self._click_wrappers
        )

    def on_drop(self, owner: str, callback):
        self._drop_callbacks.setdefault(owner, []).append(callback)
        for window in self._windows():
            self.attach_window(window)
        return lambda: self._remove(
            owner, callback, self._drop_callbacks, self._drop_wrappers
        )

    def _remove(self, owner: str, callback, table, registry=None) -> None:
        values = table.get(owner)
        if values and callback in values:
            values.remove(callback)
        if registry is not None:
            self._detach_callback(callback, registry)

    def _detach_callback(self, callback, registry) -> None:
        marker = id(callback)
        remover = (
            "remove_click_listener"
            if registry is self._click_wrappers
            else "remove_drop_listener"
        )
        for key, (window, wrappers) in list(registry.items()):
            wrapper = wrappers.pop(marker, None)
            if wrapper is None:
                continue
            remove = getattr(window, remover, None)
            if callable(remove):
                try:
                    remove(wrapper)
                except RuntimeError:
                    pass
            if not wrappers:
                registry.pop(key, None)

    def on_tick(self, owner: str, callback, interval_ms: int = 16):
        """周期回调；返回**注销函数**（契约是 ``Callable[[], None]``）。"""
        handle = self._scheduler.call_every(
            interval_ms, lambda: callback(interval_ms / 1000.0), owner=str(owner)
        )
        self._timers.setdefault(owner, []).append(handle)

        def _dispose() -> None:
            self._scheduler.cancel(handle)
            handles = self._timers.get(owner, [])
            if handle in handles:
                handles.remove(handle)
            if not handles:
                self._timers.pop(owner, None)

        return _dispose

    def detach_all(self) -> None:
        """摘掉本适配器在所有窗口上的监听（重载扩展换 adapter 时调用）。"""
        for registry, remover in (
            (self._click_wrappers, "remove_click_listener"),
            (self._drop_wrappers, "remove_drop_listener"),
        ):
            for key, (window, wrappers) in list(registry.items()):
                remove = getattr(window, remover, None)
                if callable(remove):
                    for wrapper in list(wrappers.values()):
                        try:
                            remove(wrapper)
                        except RuntimeError:
                            pass
                registry.pop(key, None)
        self._click_callbacks.clear()
        self._drop_callbacks.clear()
        for handles in list(self._timers.values()):
            for handle in handles:
                self._scheduler.cancel(handle)
        self._timers.clear()

    def cancel_owner(self, owner: str) -> None:
        for handle in self._timers.pop(owner, []):
            self._scheduler.cancel(handle)
        for callback in self._click_callbacks.pop(owner, []):
            self._detach_callback(callback, self._click_wrappers)
        for callback in self._drop_callbacks.pop(owner, []):
            self._detach_callback(callback, self._drop_wrappers)


class CoreApiPort:
    """API 能力的 core 门面：mod 只看见元数据与一次解析结果，拿不到存储实现。"""

    OWNER = "core.api"
    WEB_SEARCH_OWNER = "core.websearch"

    def __init__(self, host, store, credentials) -> None:
        self.host = host
        self.store = store
        self.credentials = credentials

    def services(self) -> tuple[ApiMetadata, ...]:
        version = str(self.store.version())
        return tuple(service.metadata(version) for service in self.store.services())

    def metadata(self, service_id: str) -> ApiMetadata:
        service = self.store.get(service_id)
        if service is None:
            raise KeyError(f"unknown api service: {service_id}")
        return service.metadata(str(self.store.version()))

    def resolve(self, service_id: str) -> ApiRequest:
        metadata = self.metadata(service_id)
        api_key = self.credentials.get(self.OWNER, str(service_id)) or ""
        return ApiRequest(metadata, api_key)

    def subscribe(self, callback):
        return self.store.subscribe(callback)

    def open_settings(self) -> None:
        self.host.open_settings("api")

    def fetch_balance(self, service_id: str, *, force: bool = False):
        return self.host.account.fetch_balance(service_id, force=force)

    # ---------------------------------------------------------------- 联网检索密钥

    def web_search_key_status(self) -> dict[str, bool]:
        """哪些引擎已经在钥匙串里存了 Key（设置页只显示状态，不回显值）。"""
        from ..net.websearch import KEYED_ENGINES

        return {
            engine: bool(self.credentials.get(self.WEB_SEARCH_OWNER, engine))
            for engine in sorted(KEYED_ENGINES)
        }

    def set_web_search_key(self, engine: str, value: str) -> None:
        from ..net.websearch import KEYED_ENGINES

        engine = str(engine or "").strip()
        if engine not in KEYED_ENGINES:
            return
        self.credentials.set(self.WEB_SEARCH_OWNER, engine, str(value or ""))

    def credentials_available(self) -> bool:
        """钥匙串不可用时界面要说明「只在本进程内存，重启即丢」。"""
        return bool(self.credentials.available())

    def _web_search_key(self, engine: str) -> str:
        return self.credentials.get(self.WEB_SEARCH_OWNER, str(engine or "")) or ""

    def stream_chat(
        self,
        service_id: str,
        messages,
        *,
        model: str = "",
        temperature: float | None = None,
        timeout: float | None = None,
        on_delta=None,
        on_reasoning=None,
        on_status=None,
        thinking: str = "auto",
        reasoning_effort: str = "",
        web_search=None,
        cancel=None,
    ):
        """Core 唯一的对话出口：mod 只给消息与开关，HTTP/SSE 与密钥都在这里。

        ``timeout`` 留空时取该服务在 ``api.json`` 里配置的超时（1..300 已由
        ``api/spec.py`` 校验）：设置页里那一行必须真的生效，而不是永远 60 秒。
        """
        from ..api.chat import stream_chat

        api_request = self.resolve(service_id)
        if timeout is None:
            try:
                configured = float(api_request.metadata.timeout or 0)
            except (TypeError, ValueError):
                configured = 0.0
            timeout = max(5.0, min(300.0, configured)) if configured > 0 else 60.0
        search_key = ""
        if web_search is not None:
            engine = str(getattr(web_search, "engine", "") or "")
            search_key = self._web_search_key(engine)
        return stream_chat(
            api_request,
            messages,
            model=model,
            temperature=temperature,
            timeout=timeout,
            on_delta=on_delta,
            on_reasoning=on_reasoning,
            on_status=on_status,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            web_search=web_search,
            search_key=search_key,
            cancel=cancel,
        )


class CoreHttpPort:
    """通用取数端口：路由策略、失败分类、大小上限都在 core。

    mod 只描述 URL/方法/头与路由偏好；拿不到代理配置、异常链和响应体以外的
    任何东西（响应体本身可能很大，所以有 ``max_bytes`` 硬上限）。
    """

    def __init__(self, opener=None) -> None:
        self._opener = opener

    def request(self, request: HttpRequest) -> HttpResponse:
        from ..net import errors as net_errors
        from ..net.http import fetch_url

        try:
            status, headers, body = fetch_url(
                request.url,
                method=request.method,
                headers=tuple(request.headers),
                body=request.body,
                params=tuple(request.params),
                timeout=request.timeout,
                prefer_direct=request.prefer_direct,
                retry_statuses=tuple(request.retry_statuses),
                max_bytes=request.max_bytes,
                opener=self._opener,
            )
        except net_errors.NetworkError as exc:
            raise HttpError(
                str(getattr(exc, "reason", "") or "network"),
                status=int(getattr(exc, "status", 0) or 0),
                retry_after=getattr(exc, "retry_after", None),
                message=str(exc),
            ) from None
        return HttpResponse(
            status=status,
            url=str(request.url),
            headers=headers,
            body=body,
        )

    def get_json(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        headers: tuple[tuple[str, str], ...] = (),
        params: tuple[tuple[str, str], ...] = (),
        prefer_direct: bool = False,
    ):
        response = self.request(
            HttpRequest(
                url=str(url),
                timeout=float(timeout),
                headers=tuple(headers),
                params=tuple(params),
                prefer_direct=bool(prefer_direct),
            )
        )
        if response.status >= 400:
            raise HttpError("protocol", status=response.status)
        try:
            return json.loads(response.text)
        except ValueError:
            raise HttpError("protocol", status=response.status) from None


class WorkerNoticePort:
    """给 worker 的启动/崩溃/异常退出补上用户可见反馈（P3 验收项）。"""

    def __init__(self, inner, notify) -> None:
        self._inner = inner
        self._notify = notify

    def start(self, owner: str, argv, env=None, **kwargs):
        inner_error = kwargs.pop("on_error", None)
        inner_exit = kwargs.pop("on_exit", None)

        def on_error(exc):
            logger.warning("worker %s error: %s", owner, type(exc).__name__)
            self._safe_notify(f"扩展 {owner} 的后台进程出错，已停止")
            if callable(inner_error):
                inner_error(exc)

        def on_exit(code):
            if code not in (0, None):
                self._safe_notify(f"扩展 {owner} 的后台进程异常退出（{code}）")
            if callable(inner_exit):
                inner_exit(code)

        kwargs.update(on_error=on_error, on_exit=on_exit)
        return self._inner.start(owner, argv, env=env, **kwargs)

    def _safe_notify(self, message: str) -> None:
        try:
            self._notify(message)
        except Exception:
            logger.debug("worker notice failed", exc_info=True)


class QtScreenPort:
    """截屏端口：只读屏幕到内存，不落盘。"""

    def __init__(self, host) -> None:
        self.host = host

    def _window(self, pet_slot: int | None):
        window = None
        pets = getattr(self.host, "pets", None)
        if pets is not None and pet_slot is not None:
            try:
                window = pets.window_for_slot(int(pet_slot))
            except Exception:
                window = None
        return window or getattr(self.host, "window", None)

    def capture(self, *, pet_slot: int | None = None):
        from ..platform.screenshot import capture_desktop

        # Windows：虚拟桌面（所有显示器并集，物理像素）；其它平台 capture_desktop
        # 内部退回主屏。
        return capture_desktop(self._window(pet_slot))

    def capture_region(self, *, pet_slot: int | None = None, ai_available: bool = True):
        """框选 + 动作条；能力表由「已注册的动作处理者 + AI 是否可用」决定。

        本地 OCR / 翻译的具体实现都在扩展里，core 只按 ``on_action`` 的声明
        决定按钮是否可点，不认识任何引擎。
        """
        from .screen.selection import select_region

        controller = getattr(self.host, "screen_ask", None)
        supports = getattr(controller, "supports_action", None)
        has_ocr = bool(callable(supports) and supports("ocr"))
        has_translate = bool(callable(supports) and supports("translate"))
        ai = bool(ai_available)
        capabilities = {
            "ocr": has_ocr or ai,
            "translate": has_translate and ai,
            "ask": ai,
            "copy": True,
        }
        no_ai = "未配置 AI 服务（设置 → API 与账户）"
        reasons = {
            "ocr": "未安装 OCR 扩展，也没有可用的 AI 服务",
            "translate": no_ai,
            "ask": no_ai,
        }
        return select_region(
            self._window(pet_slot),
            capabilities=capabilities,
            reasons=reasons,
        )

    def foreground_app(self) -> str:
        from ..platform.foreground import foreground_app_info

        return foreground_app_info()

    def foreground_window(self):
        from ..contracts.screen import WindowInfo
        from ..platform.foreground import foreground_window_info

        info = foreground_window_info()
        if not info:
            return None
        rect = info.get("rect")
        return WindowInfo(
            hwnd=int(info.get("hwnd") or 0),
            process=str(info.get("process") or ""),
            title=str(info.get("title") or ""),
            rect=tuple(int(part) for part in rect) if rect else None,
        )

    def capture_foreground(self, *, expected_hwnd: int = 0):
        """抓前台窗口那一块画面；抓图前后各复核一次窗口，切走了就丢弃。"""
        from ..platform.foreground import foreground_window_info
        from ..platform.screenshot import ScreenshotError, capture_window

        before = foreground_window_info()
        if not before or not before.get("rect"):
            return None
        hwnd = int(before.get("hwnd") or 0)
        if expected_hwnd and hwnd != int(expected_hwnd):
            return None
        try:
            capture = capture_window(tuple(before["rect"]), hwnd=hwnd)
        except ScreenshotError:
            logger.debug("window capture failed", exc_info=True)
            return None
        after = foreground_window_info()
        if not after or int(after.get("hwnd") or 0) != hwnd:
            logger.debug("foreground window changed during capture; frame dropped")
            return None
        return capture

    def shrink_for_vision(self, png: bytes, *, max_edge: int = 1024) -> bytes:
        from ..platform.screenshot import shrink_png

        return shrink_png(png, max_edge=int(max_edge))

    def sample_foreground(self, *, expected_hwnd: int = 0):
        """只算窗口画面的指纹；确认变化后再用 ``capture_foreground`` 拿大图。"""
        from ..contracts.screen import WindowSample
        from ..platform.foreground import foreground_window_info
        from ..platform.screenshot import ScreenshotError, sample_window

        before = foreground_window_info()
        if not before or not before.get("rect"):
            return None
        hwnd = int(before.get("hwnd") or 0)
        if expected_hwnd and hwnd != int(expected_hwnd):
            return None
        try:
            fingerprint, width, height = sample_window(tuple(before["rect"]))
        except ScreenshotError:
            logger.debug("window sample failed", exc_info=True)
            return None
        after = foreground_window_info()
        if not after or int(after.get("hwnd") or 0) != hwnd:
            logger.debug("foreground window changed during sample; frame dropped")
            return None
        return WindowSample(
            hwnd=hwnd,
            dh_hash=int(fingerprint),
            width=int(width),
            height=int(height),
            process=str(before.get("process") or ""),
            title=str(before.get("title") or ""),
            rect=tuple(int(part) for part in before["rect"]),
        )


class ResourcesAdapter:
    """只读资源目录：冻结包指向 ``_MEIPASS``，源码树指向仓库根。"""

    def bundle_path(self, *parts: str) -> Path:
        if paths.is_frozen():
            base = Path(getattr(sys, "_MEIPASS", paths.install_root() / "_internal"))
        else:
            base = paths.repo_root()
        return base.joinpath(*[str(part) for part in parts])


class SystemAdapter:
    """系统信息端口：屏幕枚举、平台名与键鼠闲置时长（只读）。"""

    def screens(self) -> tuple:
        from PySide6.QtGui import QGuiApplication

        return tuple(QGuiApplication.screens())

    def platform_name(self) -> str:
        return sys.platform

    def idle_seconds(self) -> float:
        from ..platform.foreground import idle_seconds

        return float(idle_seconds())


def build_ports(host, window) -> HostPorts:
    store = getattr(host, "api_store", None)
    credentials = getattr(host, "api_credentials", None)
    config = getattr(host, "config", None)
    api = CoreApiPort(host, store, credentials) if store is not None and credentials is not None else None
    scheduler = QtScheduler()
    audio = AudioAdapter(window)
    from .reminders import ReminderDelivery
    delivery = ReminderDelivery(getattr(host, "quiet", None), scheduler)
    host.reminders = delivery
    speech_service = getattr(host, "speech", None)
    return HostPorts(
        presentation=host.presentation_port(),
        reminders=delivery,
        scheduler=scheduler,
        audio=audio,
        storage=StorageAdapter(host.root),
        resources=ResourcesAdapter(),
        config=QtConfigPort(config) if config is not None else None,
        pets=PetRegistry(host),
        interaction=InteractionAdapter(host),
        quiet=getattr(host, "quiet", None),
        credentials=credentials,
        api=api,
        worker=(
            WorkerNoticePort(
                host.worker_host,
                lambda message: host._notify(None, message),
            )
            if getattr(host, "worker_host", None) is not None
            else None
        ),
        chat=getattr(host, "chat", None),
        quick_chat=getattr(host, "quick_chat", None),
        screen=QtScreenPort(host),
        screen_ask=getattr(host, "screen_ask", None),
        system=SystemAdapter(),
        http=CoreHttpPort(),
        speech=(
            CoreSpeechPort(speech_service, audio, scheduler=scheduler, quiet=getattr(host, "quiet", None))
            if speech_service is not None
            else None
        ),
    )
