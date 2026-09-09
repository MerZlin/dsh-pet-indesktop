"""Alert queue / bubble-suppression / self-talk helpers (host-based).

PetWindow retains thin compatibility methods passing itself as host, so
existing callers, signal connections and test patches (including unbound
``PetWindow.show_alert(pet, ...)`` calls) keep working unchanged.
"""

from __future__ import annotations

import logging
import random
from collections import deque

from PySide6.QtCore import QTimer

from . import catalog

def alert_survives_suppression(alert_type: str, *, sticky: bool, buttons, priority: int) -> bool:
    """Settings only suppress ordinary presentation; stateful events survive."""
    kind = str(alert_type or "").strip().lower()
    if kind in {
        "approval", "question", "interaction", "approval/resolved", "question/resolved",
        "interaction/resolved", "control", "control-result", "bridge/control-result",
        "watchdog/control-result", "lifecycle", "turn/end", "task_complete",
        "execution/failed", "agent/request-error", "session/end", "balance",
    }:
        return True
    return bool(sticky or buttons) and int(priority) <= 1


def set_bubble_suppressed(host, suppressed: bool) -> None:
    """设置窗口打开期间暂停气泡显示；True 时立即隐藏当前气泡。"""
    host._bubble_suppressed = bool(suppressed)
    if host._bubble_suppressed:
        bubble = getattr(host, "_speech_bubble", None)
        if bubble is not None:
            bubble.hide()
    else:
        current = getattr(host, "_alert_current", None)
        if current is not None and alert_survives_suppression(
                current.get("alertType", ""), sticky=bool(current.get("sticky")),
                buttons=current.get("buttons"), priority=int(current.get("priority", 3))):
            if current.get("sticky"):
                bubble = getattr(host, "_speech_bubble", None)
                if bubble is not None:
                    bubble.show_text(
                        current["text"], host.visible_content_rect(), 0,
                        pet_scale=host.scale, subtitle=current.get("subtitle", ""),
                        sticky=True, buttons=current.get("buttons"),
                    )
        elif current is None:
            pump = getattr(host, "_pump_alerts", None)
            if callable(pump):
                pump()
        else:
            # 抑制期间已被隐藏的普通限时提醒（非 sticky 且不存活）：
            # 结束它并推进队列，否则后续提醒会被永久吞掉。
            host._sticky_bubble_active = False
            host._sticky_text = ""
            host._sticky_subtitle = ""
            host._sticky_buttons = None
            host._alert_current = None
            pump = getattr(host, "_pump_alerts", None)
            if callable(pump):
                pump()


def show_alert(host, text: str, *, subtitle: str = "", duration_ms: int = 0,
               buttons: list[tuple[str, object]] | None = None,
               sticky: bool = True, alert_id: str = "", priority: int = 3,
               alert_type: str = "watchdog", metadata: dict | None = None) -> None:
    """提醒消息队列：需要用户注意的提醒统一入队，一次只展示一个。"""
    if not host.isVisible():
        return
    if host._bubble_suppressed and not alert_survives_suppression(
            alert_type, sticky=sticky, buttons=buttons, priority=priority):
        return
    item = {
        "id": alert_id or "",
        "text": str(text),
        "subtitle": str(subtitle or ""),
        "buttons": list(buttons) if buttons else None,
        "duration_ms": int(duration_ms),
        "sticky": bool(sticky),
        "priority": int(priority),
        "alertType": str(alert_type or "watchdog"),
        "preemptedAlertId": "",
    }
    if isinstance(metadata, dict):
        for key in ("sessionId", "riskScore", "riskReasons", "targetCount", "targets"):
            if key in metadata:
                item[key] = metadata[key]
    logging.getLogger("dsh-pet-standalone").info(
        "alert enqueue alertType=%s priority=%s alertId=%s preemptedAlertId=%s",
        item["alertType"], item["priority"], item["id"], item["preemptedAlertId"],
    )
    if alert_id:
        # 同 id 正在展示：就地替换（升级文案/按钮），不排队
        cur = host._alert_current
        if cur is not None and cur.get("id") == alert_id:
            host._alert_current = item
            host._sticky_bubble_active = bool(sticky)
            host._sticky_text = item["text"]
            host._sticky_subtitle = item["subtitle"]
            host._sticky_buttons = item["buttons"]
            if item["sticky"]:
                host._speech_bubble.show_text(
                    item["text"], host.visible_content_rect(), 0,
                    pet_scale=host.scale, subtitle=item["subtitle"],
                    sticky=True, buttons=item["buttons"],
                )
            return
        # 同 id 在排队：移除旧条目，由新条目接管
        host._alert_queue = deque(
            q for q in host._alert_queue if q.get("id") != alert_id
        )
    current = host._alert_current
    if current is not None and item["priority"] < int(current.get("priority", 3)):
        # High-priority interactions preempt low-priority watchdog/status
        # alerts. Sticky watchdog interactions remain recoverable.
        old = current
        host._alert_current = None
        host._sticky_bubble_active = False
        host._speech_bubble.dismiss()
        item["preemptedAlertId"] = old.get("id", "")
        logging.getLogger("dsh-pet-standalone").info(
            "alert preempted alertType=%s priority=%s alertId=%s by=%s",
            old.get("alertType", ""), old.get("priority", 3), old.get("id", ""), item.get("id", ""),
        )
        if old.get("sticky") and int(old.get("priority", 3)) >= 2:
            host._alert_queue.appendleft(old)
    host._alert_queue.append(item)
    host._alert_queue = deque(sorted(
        host._alert_queue, key=lambda queued: int(queued.get("priority", 3))
    ))
    host._pump_alerts()


def resolve_alert(host, alert_id: str) -> None:
    """按 alert_id 关闭某条提醒：若正在展示则收起并弹下一条，若在队列中则移除。

    供 AgentLinkManager 在审批/问题 resolved 时精确定位，避免多 agent 并发
    审批时 hide_bubble 误关他人的提醒。"""
    if not alert_id:
        return
    cur = host._alert_current
    if cur is not None and cur.get("id") == alert_id:
        # 当前展示的就是这条：收起并推进队列
        host._sticky_bubble_active = False
        host._sticky_text = ""
        host._sticky_subtitle = ""
        host._sticky_buttons = None
        host._alert_current = None
        host._speech_bubble.dismiss()
        host._pump_alerts()
        return
    # 在队列中：移除该条（不打断当前展示）
    host._alert_queue = deque(
        item for item in host._alert_queue if item.get("id") != alert_id
    )


def pump_alerts(host) -> None:
    """若当前无提醒展示且队列非空，弹出队首展示。"""
    if host._alert_current is not None:
        return
    if not host._alert_queue:
        return
    if not host.isVisible() or host._bubble_suppressed:
        return
    item = host._alert_queue.popleft()
    host._alert_current = item
    if item.get("sticky"):
        host._sticky_bubble_active = True
        host._sticky_text = item["text"]
        host._sticky_subtitle = item.get("subtitle", "")
        host._sticky_buttons = item.get("buttons")
        host._speech_bubble.show_text(
            item["text"], host.visible_content_rect(), 0,
            pet_scale=host.scale, subtitle=item.get("subtitle", ""),
            sticky=True, buttons=item.get("buttons"),
        )
    else:
        duration_ms = item.get("duration_ms") or 6000
        host._speech_bubble.show_text(
            item["text"], host.visible_content_rect(), duration_ms,
            pet_scale=host.scale, subtitle=item.get("subtitle", ""),
        )


def clear_alerts(host) -> None:
    """清空提醒消息队列并关闭当前提醒（DSH 离线/重启时全部失效）。"""
    host._alert_queue.clear()
    host._alert_current = None
    host._sticky_bubble_active = False
    host._sticky_text = ""
    host._sticky_subtitle = ""
    host._sticky_buttons = None
    host._speech_bubble.dismiss()


def hide_bubble(host) -> None:
    """主动关闭当前气泡并解除 sticky（审批结束 / DSH 离线时调用）。

    若关闭的是队列中的提醒，则自动弹出下一条。"""
    host._sticky_bubble_active = False
    host._sticky_text = ""
    host._sticky_subtitle = ""
    host._sticky_buttons = None
    host._alert_current = None
    host._speech_bubble.dismiss()
    host._pump_alerts()


def on_speech_bubble_hidden(host) -> None:
    """气泡被隐藏（临时气泡超时 / dismiss / 窗口隐藏）后的恢复/推进逻辑。

    sticky 恢复防抖：同一 sticky 内容在 300ms 内不重复 show_text，
    避免「审批被盖→恢复→再盖→再恢复」的卡顿循环。"""
    from .window import time as _window_time  # 兼容 seam：与 HEAD 同读 pet.window.time
    if not host.isVisible() or host._bubble_suppressed:
        return
    cur = host._alert_current
    if cur is not None:
        if cur.get("sticky"):
            # 防抖：同一 sticky 内容在 300ms 内不重复恢复
            now = _window_time.monotonic()
            if abs(now - getattr(host, "_last_sticky_restore", 0.0)) < 0.3:
                return
            host._last_sticky_restore = now
            host._speech_bubble.show_text(
                cur["text"], host.visible_content_rect(), 0,
                pet_scale=host.scale, subtitle=cur.get("subtitle", ""),
                sticky=True, buttons=cur.get("buttons"),
            )
        else:
            # 限时提醒（硬失败/卡住）：展示结束，弹出下一条
            host._alert_current = None
            host._pump_alerts()
        return
    # 旧路径兼容：sticky 审批气泡（不经队列）被盖掉后恢复
    if host._sticky_bubble_active and host._sticky_text:
        host._speech_bubble.show_text(
            host._sticky_text, host.visible_content_rect(), 0,
            pet_scale=host.scale, subtitle=host._sticky_subtitle, sticky=True,
            buttons=host._sticky_buttons,
        )


def read_self_talk_texts(value) -> list[str]:
    from .window import DEFAULT_SELF_TALK_TEXTS
    if not isinstance(value, list):
        return list(DEFAULT_SELF_TALK_TEXTS)
    texts = []
    for item in value:
        text = str(item).strip()[:120]
        if text and text not in texts:
            texts.append(text)
    return texts or list(DEFAULT_SELF_TALK_TEXTS)


def expression_style_text(host, text: str) -> str:
    from .window import DEFAULT_SELF_TALK_TEXTS
    """Apply the shared expression style only to built-in host-talk text."""
    if text not in DEFAULT_SELF_TALK_TEXTS:
        return text
    mode = str(host.cfg.get("dialogue_mode", "legacy") or "legacy")
    from .persona_phrases import PhrasePicker
    picker = getattr(host, "_expression_picker", None)
    if picker is None:
        picker = host._expression_picker = PhrasePicker()
    if mode == "custom":
        return picker.custom(host.cfg.get("dialogue_phrases", {}), "thinking", text)
    return picker.get(mode, "thinking", text)


def schedule_self_talk(host, *, after_display: bool = False) -> None:
    host._self_talk_timer.stop()
    if not host._self_talk_enabled or not (
        host._self_talk_texts or host._self_talk_images
    ):
        return
    delay = random.uniform(host._self_talk_min_interval, host._self_talk_max_interval)
    if after_display:
        delay += host._self_talk_duration_seconds
    host._self_talk_timer.start(max(1000, int(round(delay * 1000))))


def show_self_talk_text(host, text: str) -> bool:
    from .window import _set_speech_bubble_interactive
    if getattr(host, "_bubble_suppressed", False):
        return False
    duration_ms = int(round(host._self_talk_duration_seconds * 1000))
    anchor = host.visible_content_rect()
    _set_speech_bubble_interactive(host)
    host._speech_bubble.show_text(
        text, anchor, duration_ms, pet_scale=host.scale
    )
    return True


def show_random_self_talk(host) -> bool:
    from .window import _set_speech_bubble_interactive
    if getattr(host, "_bubble_suppressed", False):
        return False

    # 审批等一直挂着的气泡优先，自言自语不覆盖
    if getattr(host, "_sticky_bubble_active", False) or getattr(host, "_alert_current", None) is not None:
        return False

    # 惰性剔除运行期间被删除的图片
    live_images = [
        path for path in host._self_talk_images
        if path.is_file()
    ]
    if len(live_images) != len(host._self_talk_images):
        host._self_talk_images = live_images

    choices = [
                  ("text", text)
                  for text in host._self_talk_texts
              ] + [
                  ("image", path)
                  for path in host._self_talk_images
              ]

    if not choices:
        return False

    kind, value = random.choice(choices)
    duration_ms = int(
        round(host._self_talk_duration_seconds * 1000)
    )
    anchor = host.visible_content_rect()

    _set_speech_bubble_interactive(host)

    if kind == "image":
        return host._speech_bubble.show_image(
            value,
            anchor,
            duration_ms,
            pet_scale=host.scale,
            image_scale=host._self_talk_image_scale,
        )

    return host._show_self_talk_text(value)


def show_click_self_talk(host, click_name: str) -> bool:
    """优先播放当前点击动画绑定的台词；未绑定则回退全局随机自言自语。"""
    character_id = str(host.cfg.get('character', catalog.DEFAULT_CHARACTER))
    texts = host.cfg.click_talk_texts_for(character_id, click_name)
    if texts:
        return host._show_self_talk_text(random.choice(texts))
    return host._show_random_self_talk()


def on_self_talk_timeout(host) -> None:
    from .window import time as _window_time  # 兼容 seam：与 HEAD 同读 pet.window.time
    if _window_time.monotonic() < host._bubble_busy_until:
        # 重要气泡占用中：本次自言自语跳过，重新排队下一次
        host._schedule_self_talk()
        return
    displayed = False
    if host._self_talk_enabled and host.isVisible():
        displayed = host._show_random_self_talk()
    host._schedule_self_talk(after_display=displayed)


def start_music_sing_polling(host) -> None:
    """启动音乐检测并尽量立即检查一次，避免等一个轮询周期才唱歌。"""
    if not host._music_sing_enabled:
        return
    host._music_sing_timer.start()
    if host.isVisible():
        QTimer.singleShot(0, host, host._check_music_sing)


def check_music_sing(host) -> None:
    """检测后台音乐并自动播放唱歌动画（可配置开关）。

    音乐播放期间唱歌动画会持续循环；音乐停止或开关关闭后恢复普通动画链。
    不打断正在播放的一次性动作/点击/拖拽。
    """
    from .window import SING_ANIM
    if not host.isVisible():
        return
    if not host._music_sing_enabled:
        host._music_sing_active = False
        return
    from . import music_detect
    playing = music_detect.is_music_playing()
    if host._music_sing_active:
        if not playing:
            host._music_sing_active = False
        return
    if host._dragging or host._is_one_shot_playing():
        return
    if playing:
        host._music_sing_active = True
        host._switch(SING_ANIM)
