# -*- coding: utf-8 -*-
"""多桌宠围圈/面对面聚集互动控制器（GUI 线程）。

仅在本窗口 ``group_gathering.enabled`` 且碰撞会话已 attach 时懒创建；
关闭时不创建对象、不连接信号、不起任何 timer。多进程/单进程多开都经
碰撞 QLocal 会话中继 begin/ready/action/done/cancel，协调者保证单会话权威。
"""
from __future__ import annotations

import math
import random
import secrets
import time
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication

from . import collision
from . import group_geometry
from . import group_presets

WALK_TIMER_INTERVAL_MS = 30
AUTO_PROXIMITY_PX = 520.0
AUTO_STABLE_SECONDS = 1.0
AUTO_COOLDOWN_SECONDS = 180.0
AUTO_CHANCE_PER_SNAPSHOT = 0.03
FALLBACK_WALK_SECONDS = 1.2


def _cfg_enabled(win: Any) -> bool:
    value = getattr(win, "cfg", None)
    if value is None or not callable(getattr(value, "get", None)):
        return False
    group = value.get("group_gathering") or {}
    return isinstance(group, dict) and bool(group.get("enabled", False))


def _cfg_auto_enabled(win: Any) -> bool:
    value = getattr(win, "cfg", None)
    if value is None or not callable(getattr(value, "get", None)):
        return False
    group = value.get("group_gathering") or {}
    return isinstance(group, dict) and bool(group.get("auto_enabled", False))


def _screen_dict(win: Any) -> dict[str, float] | None:
    screen_available = getattr(win, "screen_available", None)
    try:
        qscreen = screen_available() if callable(screen_available) else win.screen()
    except Exception:
        qscreen = None
    if qscreen is None:
        qscreen = QApplication.primaryScreen()
    if qscreen is None:
        return None
    geo = qscreen.availableGeometry()
    return {
        "left": float(geo.left()),
        "top": float(geo.top()),
        "right": float(geo.right()),
        "bottom": float(geo.bottom()),
    }


def _window_center(win: Any) -> tuple[float, float]:
    content_rect = getattr(win, "collision_content_rect", None)
    try:
        if callable(content_rect):
            rect = content_rect()
            return float(rect.center().x()), float(rect.center().y())
    except Exception:
        pass
    try:
        frame = win.frameGeometry()
        return float(frame.center().x()), float(frame.center().y())
    except Exception:
        pass
    return float(getattr(win, "x", lambda: 0.0)()), float(getattr(win, "y", lambda: 0.0)())


def _window_width(win: Any) -> float:
    width_method = getattr(win, "width", None)
    try:
        if callable(width_method):
            return float(width_method())
    except Exception:
        pass
    frame_geometry = getattr(win, "frameGeometry", None)
    try:
        if callable(frame_geometry):
            return float(frame_geometry().width())
    except Exception:
        pass
    return 320.0


def _window_height(win: Any) -> float:
    height_method = getattr(win, "height", None)
    try:
        if callable(height_method):
            return float(height_method())
    except Exception:
        pass
    frame_geometry = getattr(win, "frameGeometry", None)
    try:
        if callable(frame_geometry):
            return float(frame_geometry().height())
    except Exception:
        pass
    return 180.0


def _peer_snapshots(win: Any) -> dict[str, Any]:
    method = getattr(win, "group_peer_snapshots", None)
    if callable(method):
        try:
            return dict(method() or {})
        except Exception:
            pass
    return {}


class GroupGatheringController(QObject):
    """每窗一个；封装本地参与围圈聚集的会话状态、走位与动作播放。"""

    def __init__(self, win: Any, *, session=None, parent=None, clock=None) -> None:
        super().__init__(parent)
        self.win = win
        self._clock = clock if callable(clock) else time.monotonic
        self._session = session
        self._auto_enabled = bool(_cfg_auto_enabled(win))
        self._active = False
        self._phase = ""
        self._token = ""
        self._clip = ""
        self._target_center: tuple[float, float] | None = None
        self._walk_start_left = 0.0
        self._walk_start_top = 0.0
        self._walk_started_at = 0.0
        self._walk_duration_seconds = 0.0
        self._walk_done = False
        self._last_auto_at = -AUTO_COOLDOWN_SECONDS
        self._auto_stable_since: float | None = None
        self._auto_stable_signature = ""

        self._walk_timer = QTimer(self)
        self._walk_timer.setInterval(WALK_TIMER_INTERVAL_MS)
        self._walk_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._walk_timer.timeout.connect(self._on_walk_tick)

        self._action_timer = QTimer(self)
        self._action_timer.setSingleShot(True)
        self._action_timer.timeout.connect(self._play_action)

        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(self._on_action_finished)

        self._connect_session()

    # ------------------------------------------------------------ 连接
    def _connect_session(self) -> None:
        session = self._session
        if session is None:
            return
        try:
            session.group_message_ready.connect(
                self._on_group_message, Qt.ConnectionType.QueuedConnection
            )
        except (RuntimeError, TypeError):
            pass
        if self._auto_enabled:
            try:
                session.snapshot_ready.connect(
                    self._on_snapshot, Qt.ConnectionType.QueuedConnection
                )
            except (RuntimeError, TypeError):
                pass

    def _disconnect_session(self) -> None:
        session = self._session
        if session is None:
            return
        try:
            session.group_message_ready.disconnect(self._on_group_message)
        except (RuntimeError, TypeError):
            pass
        if self._auto_enabled:
            try:
                session.snapshot_ready.disconnect(self._on_snapshot)
            except (RuntimeError, TypeError):
                pass
        self._session = None

    def sync_config(self) -> None:
        """设置刷新：自动偶遇开关变化时挂/摘 snapshot 监听。"""
        auto_enabled = bool(_cfg_auto_enabled(self.win))
        if auto_enabled == self._auto_enabled:
            return
        self._auto_enabled = auto_enabled
        if self._session is None:
            return
        if auto_enabled:
            try:
                self._session.snapshot_ready.connect(
                    self._on_snapshot, Qt.ConnectionType.QueuedConnection
                )
            except (RuntimeError, TypeError):
                pass
        else:
            try:
                self._session.snapshot_ready.disconnect(self._on_snapshot)
            except (RuntimeError, TypeError):
                pass

    def shutdown(self) -> None:
        """窗级关闭/功能关闭：取消进行中会话并断开信号、停表。"""
        if self._active:
            self.abort("shutdown")
        self._walk_timer.stop()
        self._action_timer.stop()
        self._done_timer.stop()
        self._disconnect_session()

    # ------------------------------------------------------------ 参与者
    def _own_member(self) -> dict[str, Any]:
        cx, cy = _window_center(self.win)
        return {
            "runtime_id": str(getattr(self._session, "runtime_id", "local")),
            "x": cx,
            "y": cy,
            "w": _window_width(self.win),
            "h": _window_height(self.win),
            "character": str(getattr(self.win.cfg, "get", lambda *_: "")("character", "")),
        }

    def _participant_members(self) -> list[dict[str, Any]]:
        """同屏、可见、开启聚集互动的成员（含本窗）。"""
        own = self._own_member()
        screen = _screen_dict(self.win)
        peers = _peer_snapshots(self.win)
        members = [own]
        if screen is not None:
            for runtime_id, raw in peers.items():
                flags = int(raw.get("flags", 0) or 0)
                if not (flags & collision.FLAG_GROUP_ENABLED):
                    continue
                if not (flags & collision.FLAG_VISIBLE) or (flags & collision.FLAG_PAUSED):
                    continue
                try:
                    x = float(raw.get("x", 0.0))
                    y = float(raw.get("y", 0.0))
                except (TypeError, ValueError):
                    continue
                if not (screen["left"] <= x <= screen["right"]
                        and screen["top"] <= y <= screen["bottom"]):
                    continue
                members.append({
                    "runtime_id": str(runtime_id),
                    "x": x,
                    "y": y,
                    "w": float(raw.get("w", 320) or 320),
                    "h": float(raw.get("h", 180) or 180),
                    "character": str(raw.get("character") or ""),
                })
        return group_geometry.same_screen_members(members, screen) if screen else members

    def _common_presets(self) -> list[dict[str, str]]:
        members = self._participant_members()
        if len(members) < 2:
            return []
        pets = []
        for member in members:
            character = str(member.get("character") or "")
            if str(getattr(self.win, "cfg", {}).get("character", "")) == character:
                names = set()
                lib = getattr(self.win, "lib", None)
                if lib is not None and callable(getattr(lib, "names", None)):
                    names = set(lib.names())
                pets.append({"character": character, "names": names})
            else:
                # 其它进程角色素材在本机同一 assets/数据目录下，只读扫名。
                pets.append({"character": character})
        return group_presets.common_presets(pets)

    # ------------------------------------------------------------ 手动入口
    def request_group(self, preset_id: str) -> bool:
        """发起一次聚集（手动点播）。返回是否成功提交 begin。"""
        if self._active or self._session is None:
            return False
        preset = group_presets.resolve_group_preset(preset_id)
        if preset is None:
            return False
        members = self._participant_members()
        if len(members) < 2:
            return False
        if all(p["id"] != preset_id for p in self._common_presets()):
            return False
        screen = _screen_dict(self.win)
        if screen is None:
            return False
        arrangement = group_geometry.arrange_group(members, screen)
        if len(arrangement) < 2:
            return False
        targets = [
            {
                "runtime_id": str(member["runtime_id"]),
                "x": float(arrangement[str(member["runtime_id"])]["x"]),
                "y": float(arrangement[str(member["runtime_id"])]["y"]),
                "facing": str(arrangement[str(member["runtime_id"])]["facing"]),
            }
            for member in members
            if str(member["runtime_id"]) in arrangement
        ]
        token = f"{getattr(self._session, 'runtime_id', 'local')}-{secrets.token_hex(6)}"
        message = {
            "type": "group", "kind": "begin", "token": token,
            "leader": str(getattr(self._session, "runtime_id", "")),
            "preset_id": str(preset["id"]), "clip": str(preset["clip"]),
            "targets": targets, "screen": screen,
        }
        self._session.submit_group_message(message)
        return True

    # ------------------------------------------------------------ 会话处理
    def _on_group_message(self, message: dict[str, Any]) -> None:
        if not isinstance(message, dict):
            return
        kind = str(message.get("kind") or "")
        token = str(message.get("token") or "")
        if kind == "begin":
            self._begin_session(token, message)
        elif kind == "cancel":
            if self._active and (not token or token == self._token):
                self._reset_after_cancel(reason=str(message.get("reason") or "cancelled"))
        elif kind == "action":
            if self._active and token == self._token:
                self._schedule_action(message)

    def _begin_session(self, token: str, message: dict[str, Any]) -> None:
        if self._active:
            return
        targets = message.get("targets")
        if not isinstance(targets, list):
            return
        own_runtime_id = str(getattr(self._session, "runtime_id", ""))
        target = next(
            (item for item in targets
             if isinstance(item, dict) and str(item.get("runtime_id") or "") == own_runtime_id),
            None,
        )
        if target is None:
            return
        clip = str(message.get("clip") or "")
        names = set()
        lib = getattr(self.win, "lib", None)
        if lib is not None and callable(getattr(lib, "names", None)):
            names = set(lib.names())
        if clip and names and clip not in names:
            self._send_cancel(token, "missing_clip")
            return
        self._active = True
        self._token = token
        self._clip = clip
        self._phase = "walk"
        self._walk_done = False
        target_x = float(target.get("x", 0.0))
        target_y = float(target.get("y", 0.0))
        facing = str(target.get("facing") or "right")
        self._begin_walk(target_x, target_y, facing)

    def _send_cancel(self, token: str, reason: str) -> None:
        if self._session is None:
            return
        self._session.submit_group_message({
            "type": "group", "kind": "cancel", "token": token, "reason": reason,
        })

    # ------------------------------------------------------------ 走位
    def _begin_walk(self, target_x: float, target_y: float, facing: str) -> None:
        win = self.win
        self._target_center = (target_x, target_y)
        try:
            start_left = float(win.x())
            start_top = float(win.y())
        except Exception:
            start_left, start_top = 0.0, 0.0
        self._walk_start_left = start_left
        self._walk_start_top = start_top
        self._walk_started_at = self._clock()
        self._walk_done = False

        move_name = None
        moves = getattr(win, "moves", None) or []
        if moves:
            move_name = moves[0]
        if move_name:
            switch = getattr(win, "switch_clip", None)
            if callable(switch):
                try:
                    switch(move_name)
                except Exception:
                    move_name = None
        duration = FALLBACK_WALK_SECONDS
        lib = getattr(win, "lib", None)
        if move_name and lib is not None and callable(getattr(lib, "duration", None)):
            try:
                raw_duration = float(lib.duration(move_name) or 0.0)
                if raw_duration > 0:
                    duration = raw_duration
            except (TypeError, ValueError):
                pass
        self._walk_duration_seconds = max(0.1, duration)
        if facing and hasattr(win, "facing"):
            win.facing = facing
        self._walk_timer.start()

    def _on_walk_tick(self) -> None:
        if not self._active or self._phase != "walk":
            self._walk_timer.stop()
            return
        win = self.win
        target_x, target_y = self._target_center or (0.0, 0.0)
        window_w = _window_width(win)
        window_h = _window_height(win)
        target_left = target_x - window_w / 2.0
        target_top = target_y - window_h / 2.0
        progress = (self._clock() - self._walk_started_at) / self._walk_duration_seconds
        if progress >= 1.0:
            self._walk_timer.stop()
            self._walk_done = True
            try:
                win.move(int(round(target_left)), int(round(target_top)))
            except Exception:
                pass
            self._phase = "ready"
            self._send_ready()
            return
        left = self._walk_start_left + (target_left - self._walk_start_left) * progress
        top = self._walk_start_top + (target_top - self._walk_start_top) * progress
        try:
            win.move(int(round(left)), int(round(top)))
        except Exception:
            pass

    def _send_ready(self) -> None:
        if self._session is None:
            return
        self._session.submit_group_message({
            "type": "group", "kind": "ready", "token": self._token,
        })

    # ------------------------------------------------------------ 动作
    def _schedule_action(self, message: dict[str, Any]) -> None:
        delay_ms = max(0, int(message.get("start_delta_ms") or 0))
        self._action_timer.stop()
        self._action_timer.start(delay_ms)

    def _play_action(self) -> None:
        if not self._active or not self._clip:
            return
        self._phase = "acting"
        switch = getattr(self.win, "switch_clip", None)
        if callable(switch):
            try:
                switch(self._clip, link_request=True)
            except Exception:
                pass
        duration_seconds = FALLBACK_WALK_SECONDS
        lib = getattr(self.win, "lib", None)
        if lib is not None and callable(getattr(lib, "duration", None)):
            try:
                raw = float(lib.duration(self._clip) or 0.0)
                if raw > 0:
                    duration_seconds = raw
            except (TypeError, ValueError):
                pass
        self._done_timer.stop()
        self._done_timer.start(max(100, int(duration_seconds * 1000.0)))

    def _on_action_finished(self) -> None:
        if not self._active:
            return
        token = self._token
        self._active = False
        self._phase = ""
        if self._session is not None:
            self._session.submit_group_message({
                "type": "group", "kind": "done", "token": token,
            })

    # ------------------------------------------------------------ 取消
    def abort(self, reason: str = "interrupted") -> None:
        """本窗交互/隐藏/退出：若在会话中则向协调者发 cancel。"""
        if not self._active or self._session is None:
            return
        self._send_cancel(self._token, reason)
        self._reset_after_cancel(reason)

    def _reset_after_cancel(self, reason: str) -> None:
        self._walk_timer.stop()
        self._action_timer.stop()
        self._done_timer.stop()
        was_active = self._active
        self._active = False
        self._phase = ""
        self._token = ""
        self._clip = ""
        self._target_center = None
        if was_active:
            request_idle = getattr(self.win, "request_link_idle", None)
            if callable(request_idle):
                try:
                    request_idle()
                except Exception:
                    pass

    # ------------------------------------------------------------ 自动偶遇
    def _on_snapshot(self, message: dict[str, Any]) -> None:
        del message  # 成员表经 peer_snapshots 已同步，这里只需要触发判定
        if not self._auto_enabled or self._active or self._session is None:
            return
        members = self._participant_members()
        if len(members) < 2:
            self._auto_stable_signature = ""
            self._auto_stable_since = None
            return
        own_id = str(getattr(self._session, "runtime_id", ""))
        leader = min(members, key=lambda m: str(m.get("runtime_id", "")))
        if str(leader.get("runtime_id", "")) != own_id:
            self._auto_stable_signature = ""
            self._auto_stable_since = None
            return
        close = [leader]
        for member in members:
            if member is leader:
                continue
            distance = math.hypot(
                float(member.get("x", 0.0)) - float(leader.get("x", 0.0)),
                float(member.get("y", 0.0)) - float(leader.get("y", 0.0)),
            )
            if distance <= AUTO_PROXIMITY_PX:
                close.append(member)
        if len(close) < 2:
            self._auto_stable_signature = ""
            self._auto_stable_since = None
            return
        now = self._clock()
        signature = "|".join(sorted(str(m.get("runtime_id", "")) for m in close))
        if signature != self._auto_stable_signature:
            self._auto_stable_signature = signature
            self._auto_stable_since = now
            return
        if now - self._last_auto_at < AUTO_COOLDOWN_SECONDS:
            return
        if self._auto_stable_since is None or now - self._auto_stable_since < AUTO_STABLE_SECONDS:
            return
        if random.random() > AUTO_CHANCE_PER_SNAPSHOT:
            return
        # 只从近簇内可公共播放的预设里选
        pets = [
            {"character": str(m.get("character") or ""), "names": None}
            for m in close
        ]
        # 本窗角色优先用 lib.names() 提供内存名，避免不必要扫盘
        for pet, member in zip(pets, close):
            own_character = str(getattr(self.win.cfg, "get", lambda *_: "")("character", ""))
            if str(member.get("character") or "") == own_character:
                lib = getattr(self.win, "lib", None)
                if lib is not None and callable(getattr(lib, "names", None)):
                    pet["names"] = set(lib.names())
        common = group_presets.common_presets(pets)
        if not common:
            return
        preset = random.choice(common)
        self._last_auto_at = now
        self.request_group(preset["id"])
