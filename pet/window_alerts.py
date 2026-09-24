"""Alert queue / bubble-suppression / self-talk helpers (host-based).

PetWindow retains thin compatibility methods passing itself as host, so
existing callers, signal connections and test patches (including unbound
``PetWindow.show_alert(pet, ...)`` calls) keep working unchanged.
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque

from PySide6.QtCore import QTimer

from . import catalog
from . import window_placement

# 确认"音乐真的停了"所需的持续静音时长（秒）。歌曲的前奏/间奏/轻声段会让
# 音频峰值瞬时跌到阈值下，太小会导致唱歌状态反复退出。
MUSIC_SING_GRACE_SECONDS = 6.0


def alert_survives_suppression(alert_type: str, *, sticky: bool, buttons, priority: int) -> bool:
    """Settings only suppress ordinary presentation; stateful events survive."""
    kind = str(alert_type or "").strip().lower()
    if kind in {
        "approval",
        "question",
        "interaction",
        "approval/resolved",
        "question/resolved",
        "interaction/resolved",
        "control",
        "control-result",
        "bridge/control-result",
        "watchdog/control-result",
        "lifecycle",
        "turn/end",
        "task_complete",
        "execution/failed",
        "agent/request-error",
        "session/end",
        "balance",
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
            current.get("alertType", ""), sticky=bool(current.get("sticky")), buttons=current.get("buttons"), priority=int(current.get("priority", 3))
        ):
            bubble = getattr(host, "_speech_bubble", None)
            if current.get("sticky"):
                if bubble is not None:
                    bubble.show_text(
                        current["text"],
                        window_placement.bubble_anchor_rect(host),
                        0,
                        pet_scale=host.scale,
                        subtitle=current.get("subtitle", ""),
                        sticky=True,
                        buttons=current.get("buttons"),
                    )
            elif bubble is not None:
                # 存活但非 sticky 的 current（task_complete/turn/balance 等）：
                # 抑制期被隐藏且 hidden 链路被抑制守卫截断——不重挂则
                # _alert_current 永不清除，pump_alerts 永久 early-return，
                # 后续提醒全部被吞（队列死锁）。按原时长重挂，超时后走正常
                # hidden → 清除 → 推进闭环。
                bubble.show_text(
                    current["text"],
                    window_placement.bubble_anchor_rect(host),
                    current.get("duration_ms") or 6000,
                    pet_scale=host.scale,
                    subtitle=current.get("subtitle", ""),
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


def redirect_hidden_bubble(host, text: str, *, subtitle: str = "", duration_ms: int = 3200) -> bool:
    """桌宠隐藏时的气泡改道：交给注入的灵动岛反馈面（AppShell 经
    ``hidden_bubble_redirect`` 注入；island_chat 让岛在桌宠隐藏时代理交互面）。

    返回是否已改道。无注入 / 岛不可用 / 注入方拒绝时返回 False，调用方
    维持原丢弃行为。仅覆盖非交互反馈气泡；审批/问题等带按钮的交互气泡
    需要桌宠可见（岛气泡暂不支持按钮），不走本改道。"""
    redirect = getattr(host, "hidden_bubble_redirect", None)
    if not callable(redirect):
        return False
    try:
        return bool(redirect(str(text), subtitle=str(subtitle or ""), duration_ms=int(duration_ms)))
    except Exception:
        logging.getLogger("dsh-pet-standalone").exception("隐藏期气泡改道到灵动岛失败")
        return False


def show_alert(
    host,
    text: str,
    *,
    subtitle: str = "",
    duration_ms: int = 0,
    buttons: list[tuple[str, object]] | None = None,
    sticky: bool = True,
    alert_id: str = "",
    priority: int = 3,
    alert_type: str = "watchdog",
    metadata: dict | None = None,
) -> None:
    """提醒消息队列：需要用户注意的提醒统一入队，一次只展示一个。"""
    if not host.isVisible():
        # 桌宠隐藏：非交互提醒（无按钮）改道灵动岛反馈面；交互气泡与
        # 设置页抑制期维持原丢弃行为。
        if (
            not buttons
            and not getattr(host, "_bubble_suppressed", False)
            and redirect_hidden_bubble(host, text, subtitle=subtitle, duration_ms=duration_ms or 3200)
        ):
            return
        return
    if host._bubble_suppressed and not alert_survives_suppression(alert_type, sticky=sticky, buttons=buttons, priority=priority):
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
        item["alertType"],
        item["priority"],
        item["id"],
        item["preemptedAlertId"],
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
                    item["text"],
                    window_placement.bubble_anchor_rect(host),
                    0,
                    pet_scale=host.scale,
                    subtitle=item["subtitle"],
                    sticky=True,
                    buttons=item["buttons"],
                )
            return
        # 同 id 在排队：移除旧条目，由新条目接管
        host._alert_queue = deque(q for q in host._alert_queue if q.get("id") != alert_id)
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
            old.get("alertType", ""),
            old.get("priority", 3),
            old.get("id", ""),
            item.get("id", ""),
        )
        if old.get("sticky") and int(old.get("priority", 3)) >= 2:
            host._alert_queue.appendleft(old)
    host._alert_queue.append(item)
    host._alert_queue = deque(sorted(host._alert_queue, key=lambda queued: int(queued.get("priority", 3))))
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
    host._alert_queue = deque(item for item in host._alert_queue if item.get("id") != alert_id)


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
            item["text"],
            window_placement.bubble_anchor_rect(host),
            0,
            pet_scale=host.scale,
            subtitle=item.get("subtitle", ""),
            sticky=True,
            buttons=item.get("buttons"),
        )
    else:
        duration_ms = item.get("duration_ms") or 6000
        host._speech_bubble.show_text(
            item["text"],
            window_placement.bubble_anchor_rect(host),
            duration_ms,
            pet_scale=host.scale,
            subtitle=item.get("subtitle", ""),
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
                cur["text"],
                window_placement.bubble_anchor_rect(host),
                0,
                pet_scale=host.scale,
                subtitle=cur.get("subtitle", ""),
                sticky=True,
                buttons=cur.get("buttons"),
            )
        else:
            # 限时提醒（硬失败/卡住）：展示结束，弹出下一条
            host._alert_current = None
            host._pump_alerts()
        return
    # 旧路径兼容：sticky 审批气泡（不经队列）被盖掉后恢复
    if host._sticky_bubble_active and host._sticky_text:
        host._speech_bubble.show_text(
            host._sticky_text,
            window_placement.bubble_anchor_rect(host),
            0,
            pet_scale=host.scale,
            subtitle=host._sticky_subtitle,
            sticky=True,
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
    if not host._self_talk_enabled or not (host._self_talk_texts or host._self_talk_images):
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
    anchor = window_placement.bubble_anchor_rect(host)
    _set_speech_bubble_interactive(host)
    host._speech_bubble.show_text(text, anchor, duration_ms, pet_scale=host.scale)
    return True


DEFAULT_IMAGE_CHANCE = 30  # 与 config.py 的 DEFAULT_SELF_TALK_IMAGE_CHANCE 保持一致


def self_talk_image_chance(host) -> int:
    """自言自语出图概率（百分比 0~100）。

    与 ``self_talk_speak_enabled`` 同样在运行时读配置、不缓存到窗口字段：概率随时
    会改，而且读不到配置时要按默认值走——不让一次读取失败变成"点了不出图"。
    """
    cfg = getattr(host, "cfg", None)
    if cfg is None:
        return DEFAULT_IMAGE_CHANCE
    try:
        value = float(cfg.get("self_talk_image_chance", DEFAULT_IMAGE_CHANCE))
    except Exception:
        return DEFAULT_IMAGE_CHANCE
    return int(max(0.0, min(100.0, value)))


def pick_self_talk_choice(texts, images, image_chance):
    """按出图概率抽一条自言自语，返回 ``(kind, value)``；两边都空返回 None。

    为什么要先掷骰子：图片目录常有几十张图，和文本混在一起**等权随机**会让图片
    彻底压过文本——实测 24 张图 + 5 句文本 → 出图概率 82.8%，点击桌宠几乎总是
    弹图。现在先决定"这次出图还是出文本"，再在对应池里等权选一条，用户设的百分比
    就是真实概率（0 = 只出文本，100 = 只出图）。

    只有一边有内容时不掷骰子：必须显示那一边，否则这一次点击会什么都不出现。
    """
    clean_texts = [text for text in (texts or []) if str(text).strip()]
    image_list = list(images or [])
    if not clean_texts and not image_list:
        return None
    if not image_list:
        return ("text", random.choice(clean_texts))
    if not clean_texts:
        return ("image", random.choice(image_list))
    chance = max(0.0, min(100.0, float(image_chance)))
    if random.random() * 100.0 < chance:
        return ("image", random.choice(image_list))
    return ("text", random.choice(clean_texts))


def show_random_self_talk(host) -> bool:
    from .window import _set_speech_bubble_interactive

    if getattr(host, "_bubble_suppressed", False):
        return False

    # 审批等一直挂着的气泡优先，自言自语不覆盖
    if getattr(host, "_sticky_bubble_active", False) or getattr(host, "_alert_current", None) is not None:
        return False

    # 惰性剔除运行期间被删除的图片
    live_images = [path for path in host._self_talk_images if path.is_file()]
    if len(live_images) != len(host._self_talk_images):
        host._self_talk_images = live_images

    picked = pick_self_talk_choice(host._self_talk_texts, live_images, self_talk_image_chance(host))
    if picked is None:
        return False

    kind, value = picked
    duration_ms = int(round(host._self_talk_duration_seconds * 1000))
    anchor = window_placement.bubble_anchor_rect(host)

    _set_speech_bubble_interactive(host)

    if kind == "image":
        # 图片气泡没有可朗读的文本：显式记 None，点击路径据此保持安静。
        host._last_self_talk_text = None
        return host._speech_bubble.show_image(
            value,
            anchor,
            duration_ms,
            pet_scale=host.scale,
            image_scale=host._self_talk_image_scale,
        )

    host._last_self_talk_text = value
    return host._show_self_talk_text(value)


def self_talk_speak_enabled(host) -> bool:
    """点击自言自语是否朗读（缺配置或配置损坏时按默认开启，与 config.py 一致）。"""
    cfg = getattr(host, "cfg", None)
    if cfg is None:
        return False
    try:
        return bool(cfg.get("self_talk_speak_enabled", True))
    except Exception:
        return True


def speak_click_self_talk(host, text: str) -> None:
    """把点击自言自语**实际显示的那句话**交给语音通道读出来。

    刻意复用语音报时服务的音频通道（窗口的 ``on_self_talk_speak`` 由 app 接线到
    ``AppShell.speak_self_talk``）：报时 / 节日提醒 / 点击自言自语共用一条音频
    通道，才能像节日提醒那样在结构上保证不叠音。

    通道缺失、开关关闭或播报失败都只降级为「有气泡没声音」，绝不影响点击本身。
    """
    stripped = str(text or "").strip()
    if not stripped or not self_talk_speak_enabled(host):
        return
    speaker = getattr(host, "on_self_talk_speak", None)
    if not callable(speaker):
        return
    try:
        speaker(stripped)
    except Exception:
        logging.getLogger("dsh-pet-standalone").exception("点击自言自语语音播报失败")


def show_click_self_talk(host, click_name: str) -> bool:
    """优先播放当前点击动画绑定的台词；未绑定则回退全局随机自言自语。

    显示成功后把实际显示的文本一并朗读（图片气泡没有文本，自然不出声），
    使"听到的"与"看到的"永远是同一句。
    """
    character_id = str(host.cfg.get("character", catalog.DEFAULT_CHARACTER))
    texts = host.cfg.click_talk_texts_for(character_id, click_name)
    if texts:
        value = random.choice(texts)
        shown = host._show_self_talk_text(value)
    else:
        host._last_self_talk_text = None
        shown = host._show_random_self_talk()
        value = getattr(host, "_last_self_talk_text", None)
    if shown and value:
        speak_click_self_talk(host, value)
    return shown


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

    退出唱歌要经过一段宽限期，而不是一检测到静音就退：``is_music_playing``
    看的是音频峰值，歌曲的前奏/间奏/轻声段会让峰值瞬时跌到阈值以下，立刻退出
    会表现为"唱着唱着主动退出、然后静默不唱"。
    """
    from .window import SING_ANIM

    if not host.isVisible():
        return
    if not host._music_sing_enabled:
        host._music_sing_active = False
        host._music_sing_silent_since = None
        return
    # 当前是纯音乐（配乐/OST/演奏曲）：没有可唱的句子，不唱歌。
    # 标志由歌词控制器维护；没启用歌词功能时该标志恒为 False，不受影响。
    if getattr(host, "_instrumental_playing", False):
        host._music_sing_active = False
        host._music_sing_silent_since = None
        return
    from . import music_detect

    playing = music_detect.is_music_playing()
    if host._music_sing_active:
        # 静音起点用 getattr 兜底读取：window.py 的行数预算已满，不新增字段。
        silent_since = getattr(host, "_music_sing_silent_since", None)
        if playing:
            # 还有声音：刷新静音计时，保持唱歌。
            host._music_sing_silent_since = None
        elif silent_since is None:
            # 刚转为静音：起算宽限期，先不退出。
            host._music_sing_silent_since = time.monotonic()
        elif time.monotonic() - silent_since >= music_sing_grace_seconds(host):
            host._music_sing_active = False
            host._music_sing_silent_since = None
        # 宽限期内：保持 _music_sing_active，动画继续循环。
        return
    if host._dragging or host._is_one_shot_playing():
        return
    if playing:
        host._music_sing_active = True
        host._music_sing_silent_since = None
        host._switch(SING_ANIM)


def music_sing_grace_seconds(host) -> float:
    """确认"音乐真的停了"所需的持续静音时长（秒）。"""
    raw = getattr(getattr(host, "cfg", None), "get", None)
    value = None
    if callable(raw):
        value = raw("music_sing_grace_seconds", None)
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = MUSIC_SING_GRACE_SECONDS
    # 下限 1 秒：低于这个值就退化回"瞬时静音即退出"的老问题。
    return max(1.0, seconds)
