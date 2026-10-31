"""音频输出路由：进程级设备选择，空配置跟随系统默认。

所有桌宠声音（点击音效、Agent 提示音、自言自语/报时等 TTS）都必须经过这里，
避免「手动选了耳机，耳机拔掉后从扬声器外放」。规则：

- ``audio_output_device`` 为空：跟随系统默认设备，设备变化由
  ``QMediaDevices.audioOutputsChanged`` 触发重解析；
- 手动设备存在：所有播放器都用它；
- 手动设备不存在：``audible=False``，静音且**不回落**到默认设备；
- QtMultimedia 不可用 / 没有 QApplication：``mode="unavailable"``，保持旧行为
  （不设置设备，由 Qt 自己决定）。

本模块只在 GUI 线程使用 Qt；不要在 ``petcore --check``（没有 QApplication）里
调用 ``list_outputs()/route()``。``AudioRoute`` 是纯数据，测试可替换整个路由。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("petcore.audio_output")

MODE_FOLLOW = "follow"
MODE_MANUAL = "manual"
MODE_MISSING = "missing"
MODE_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AudioRoute:
    """一次解析结果；``device`` 是 ``QAudioDevice``（或测试替身）。"""

    device: object | None
    audible: bool
    mode: str
    device_id: str = ""
    device_name: str = ""

    @property
    def key(self) -> tuple[str, str, bool]:
        return (self.mode, self.device_id, self.audible)


def _media_devices_class():
    try:
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtMultimedia import QMediaDevices
    except Exception:
        return None
    if QCoreApplication.instance() is None:
        return None
    return QMediaDevices


def _device_id(device) -> str:
    try:
        return bytes(device.id()).decode("utf-8", "replace")
    except Exception:
        return ""


def _device_name(device) -> str:
    try:
        return str(device.description() or "")
    except Exception:
        return ""


def _is_null(device) -> bool:
    try:
        return bool(device.isNull())
    except Exception:
        return device is None


def list_outputs() -> list[tuple[str, str, bool]]:
    """返回 ``[(device_id, description, is_default)]``；不可用时返回空列表。"""
    devices_class = _media_devices_class()
    if devices_class is None:
        return []
    try:
        devices = list(devices_class.audioOutputs())
        default = devices_class.defaultAudioOutput()
        default_id = "" if _is_null(default) else _device_id(default)
    except Exception:
        logger.debug("audio output enumeration failed", exc_info=True)
        return []
    result: list[tuple[str, str, bool]] = []
    for device in devices:
        device_id = _device_id(device)
        if not device_id:
            continue
        result.append((device_id, _device_name(device), device_id == default_id))
    return result


def default_output_id() -> str:
    devices_class = _media_devices_class()
    if devices_class is None:
        return ""
    try:
        default = devices_class.defaultAudioOutput()
    except Exception:
        return ""
    return "" if _is_null(default) else _device_id(default)


class AudioOutputRouter:
    """读配置 → 解析设备；设备热插拔/默认设备变化时通知订阅者。"""

    def __init__(self, config) -> None:
        self._config = config
        self._route: AudioRoute | None = None
        self._route_key: tuple[str, str, bool] | None = None
        self._configured_key: tuple[str, str] = ("", "")
        self._listeners: list[Callable[[AudioRoute], None]] = []
        self._media_devices = None
        self._closed = False
        self._connect_devices()

    def _connect_devices(self) -> None:
        devices_class = _media_devices_class()
        if devices_class is None:
            return
        try:
            media = devices_class()
        except Exception:
            logger.debug("QMediaDevices init failed", exc_info=True)
            return
        signal = getattr(media, "audioOutputsChanged", None)
        if signal is not None:
            try:
                signal.connect(self._on_devices_changed)
            except Exception:
                logger.debug("audioOutputsChanged connect failed", exc_info=True)
        self._media_devices = media

    def _on_devices_changed(self, *_args) -> None:
        if not self._closed:
            self.refresh()

    def _configured(self) -> tuple[str, str]:
        getter = getattr(self._config, "get", None)
        if not callable(getter):
            return "", ""
        try:
            device_id = str(getter("audio_output_device", "") or "").strip()
            name = str(getter("audio_output_device_name", "") or "").strip()
        except Exception:
            return "", ""
        return device_id, name

    def _resolve(self) -> AudioRoute:
        devices_class = _media_devices_class()
        if devices_class is None:
            return AudioRoute(None, True, MODE_UNAVAILABLE)
        configured, saved_name = self._configured()
        try:
            devices = list(devices_class.audioOutputs())
        except Exception:
            logger.debug("audio output enumeration failed", exc_info=True)
            return AudioRoute(None, True, MODE_UNAVAILABLE)
        if not configured:
            try:
                default = devices_class.defaultAudioOutput()
            except Exception:
                default = None
            if default is None or _is_null(default):
                return AudioRoute(None, True, MODE_FOLLOW)
            return AudioRoute(
                default,
                True,
                MODE_FOLLOW,
                _device_id(default),
                _device_name(default),
            )
        for device in devices:
            if _device_id(device) == configured:
                return AudioRoute(
                    device,
                    True,
                    MODE_MANUAL,
                    configured,
                    _device_name(device) or saved_name,
                )
        return AudioRoute(None, False, MODE_MISSING, configured, saved_name or configured)

    def route(self) -> AudioRoute:
        configured = self._configured()
        if self._route is not None and configured == self._configured_key:
            return self._route
        current = self._resolve()
        self._configured_key = configured
        if self._route_key != current.key:
            self._route_key = current.key
            self._route = current
        return self._route or current

    def refresh(self) -> AudioRoute:
        configured = self._configured()
        current = self._resolve()
        self._configured_key = configured
        if current.key == self._route_key:
            self._route = current
            return current
        self._route_key = current.key
        self._route = current
        for callback in list(self._listeners):
            try:
                callback(current)
            except Exception:
                logger.debug("audio route listener failed", exc_info=True)
        return current

    def subscribe(self, callback: Callable[[AudioRoute], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def close(self) -> None:
        self._closed = True
        media = self._media_devices
        self._media_devices = None
        if media is not None:
            signal = getattr(media, "audioOutputsChanged", None)
            if signal is not None:
                try:
                    signal.disconnect(self._on_devices_changed)
                except Exception:
                    pass
        self._listeners.clear()
