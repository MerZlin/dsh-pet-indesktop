"""Alert queue / bubble-suppression / self-talk helpers (host-based).

PetWindow retains thin compatibility methods passing itself as host, so
existing callers, signal connections and test patches (including unbound
``PetWindow.show_alert(pet, ...)`` calls) keep working unchanged.

气泡门禁只走 :func:`bubble_blocked`：安装位（设置页抑制位 ``_bubble_suppressed``）
与飞行位（壳按 sprite 判定的飞行集合）由 host 自己给，本模块不再各处现读
``_bubble_suppressed``——两套守卫并存就是「飞行期漏气泡」的成因。
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque

from PySide6.QtCore import QTimer

from . import catalog
from . import feature_activation
from . import quiet_mode
from . import window_placement

# 确认"音乐真的停了"所需的持续静音时长（秒）。歌曲的前奏/间奏/轻声段会让
# 音频峰值瞬时跌到阈值下，太小会导致唱歌状态反复退出。
MUSIC_SING_GRACE_SECONDS = 6.0

_log = logging.getLogger("dsh-pet-standalone")

#: 待处理提醒条数上限（另有 1 条 current 展示位）。**有界是硬要求**：审批/提问这类
#: 关键项同样在此限内——本地提示队列不是远程许可，丢弃只影响本地呈现（见
#: ``docs/DSH-REQUEST-EVENT-CATALOG.md``「本地提示有界」一节）。
ALERT_QUEUE_LIMIT = 64

#: 非 sticky 提醒未给 ``duration_ms`` 时的展示时长（pump 与迁移重挂同一口径）。
DEFAULT_ALERT_DURATION_MS = 6000


def alert_bubble(host):
    """当前提醒**实际承载**的那只气泡（壳上会随承载宠迁移）。

    ``OverlayShell._alert_bubble`` = 真正在展示 current 的气泡，它可能不是"当前
    动态目标"（提醒挂在子宠上、主位已回来时不抢回，见 ``overlay_shell.
    _sync_alert_copy``）。legacy ``PetWindow`` 没有这一面，退回 ``_speech_bubble``
    ——单窗下两者本就是同一只。
    """
    bubble = getattr(host, "_alert_bubble", None)
    if bubble is None:
        bubble = getattr(host, "_speech_bubble", None)
    return bubble


def _anchor_for(host, bubble):
    """气泡的锚点矩形：按**该气泡所属的那只宠**算。

    壳提供 ``bubble_anchor_rect_for``（提醒可迁移到子宠，锚点按当前目标算会让
    子宠物气泡跑到主位头顶）；legacy 宿主恒为 ``bubble_anchor_rect(host)``。
    """
    provider = getattr(host, "bubble_anchor_rect_for", None)
    if callable(provider):
        try:
            rect = provider(bubble)
        except Exception:
            rect = None
        if rect is not None:
            return rect
    return window_placement.bubble_anchor_rect(host)


def _dismiss_bubble(bubble) -> None:
    """收起一只气泡；已随它那只宠销毁（C++ 侧没了）时静默跳过。"""
    if bubble is None:
        return
    try:
        bubble.dismiss()
    except RuntimeError:
        return


def _show_item_on(host, bubble, item) -> bool:
    """在某只气泡上按 item 的语义呈现（sticky → 不限时 + 按钮；限时 → 原时长）。"""
    if bubble is None:
        return False
    anchor = _anchor_for(host, bubble)
    try:
        if item.get("sticky"):
            _remember_sticky(host, item)
            bubble.show_text(
                item["text"], anchor, 0,
                pet_scale=host.scale, subtitle=item.get("subtitle", ""),
                sticky=True, buttons=item.get("buttons"),
            )
        else:
            bubble.show_text(
                item["text"], anchor,
                item.get("duration_ms") or DEFAULT_ALERT_DURATION_MS,
                pet_scale=host.scale, subtitle=item.get("subtitle", ""),
            )
    except RuntimeError:
        return False
    return True


def _show_on_target(host, item) -> bool:
    """在**当前目标**气泡上展示一条提醒（新条目/推进下一条）。"""
    return _show_item_on(host, getattr(host, "_speech_bubble", None), item)


def _remember_sticky(host, item) -> None:
    """记下粘滞提醒的正文/副标题/按钮（legacy 恢复链与 hidden 恢复读这三个字段）。"""
    host._sticky_bubble_active = True
    host._sticky_text = item["text"]
    host._sticky_subtitle = item.get("subtitle", "")
    host._sticky_buttons = item.get("buttons")


def _reshow_on_carrier(host, item) -> bool:
    """在**承载气泡**上按原语义重挂 current（粘滞恢复 / 抑制解除 / 同 ID 升级）。

    与 :func:`_show_on_target` 的差别只在落点：这条提醒已经在某只宠上呈现过，
    重挂必须回到那一只（主位回归不抢回）。
    """
    return _show_item_on(host, alert_bubble(host), item)


def _worst_priority(queue) -> int:
    return max(int(queued.get("priority", 3)) for queued in queue)


def _evict_worst_oldest(host, item) -> bool:
    """淘汰最低优先级里最旧的一条腾位（并留 warning）。"""
    queue = host._alert_queue
    worst = _worst_priority(queue)
    for index, queued in enumerate(queue):
        if int(queued.get("priority", 3)) == worst:
            size = len(queue)
            del queue[index]
            _note_overflow(host, "evict", item, size, dropped=queued)
            return True
    return False


def _admit_to_queue(host, item) -> bool:
    """有界入队裁决：队列满时保高优先级，并留下可诊断的 warning。

    满队列的两种处置（本函数是唯一实现，调用方不得各写一份）：
    - 新条目比队列里**最低**优先级还低 → **拒收新条**（不挤掉别人）；
    - 否则 → **淘汰最低优先级里最旧的一条**腾位（同优先级时保新不保旧）。

    ``host._alert_overflow_count`` 是可诊断计数（宿主拿不到就只记日志）；
    绝不因为"队列满"去弹汇总气泡或新增设置项。
    """
    queue = host._alert_queue
    if len(queue) < ALERT_QUEUE_LIMIT:
        return True
    if int(item["priority"]) > _worst_priority(queue):
        _note_overflow(host, "reject", item, len(queue))
        return False
    return _evict_worst_oldest(host, item)


def _enforce_queue_bound(host) -> None:
    """把待处理队列压回上限（抢占把旧 current 塞回队首时可能短暂超限）。"""
    while len(host._alert_queue) > ALERT_QUEUE_LIMIT:
        if not _evict_worst_oldest(host, host._alert_queue[0]):
            return


def _note_overflow(host, action: str, item, size: int, dropped=None) -> None:
    """队列满的处置留痕：计数 + warning（含来源类型/优先级/ID，便于事后定位）。"""
    try:
        host._alert_overflow_count = int(getattr(host, "_alert_overflow_count", 0)) + 1
        count = host._alert_overflow_count
    except Exception:
        count = -1
    _log.warning(
        "提醒队列已满(%s)：%s priority=%s alertType=%s alertId=%s droppedId=%s overflowCount=%s",
        ALERT_QUEUE_LIMIT, action, item.get("priority"), item.get("alertType"),
        item.get("id"), (dropped or {}).get("id", ""), count,
    )

def alert_survives_suppression(alert_type: str, *, sticky: bool, buttons, priority: int) -> bool:
    """Settings only suppress ordinary presentation; stateful events survive."""
    kind = str(alert_type or "").strip().lower()
    if kind in {
        "approval", "question", "interaction", "approval/resolved", "question/resolved",
        "interaction/resolved", "lifecycle", "turn/end", "task_complete",
        "execution/failed", "agent/request-error", "session/end", "balance",
    }:
        return True
    return bool(sticky or buttons) and int(priority) <= 1


def bubble_blocked(host, sprite=None) -> bool:
    """统一气泡门禁：设置页抑制期 **或** 该 sprite 正在飞行。

    host 自带 ``_bubble_blocked``（overlay 壳按 sprite 判定，见
    ``overlay_shell.OverlayShell._bubble_blocked``）时以它为准；PetWindow 没有
    sprite 级飞行态，退回既有的 ``_bubble_suppressed`` 契约（行为逐字不变）。

    ``sprite=None`` = host 的默认宠（壳 = 主 sprite）。调用方一律走这个函数，
    绝不各写一份 ``getattr(host, "_bubble_suppressed")``——那样飞行期就漏出
    气泡来（B1 的缺口正是这六处各写各的守卫）。

    **免打扰不在这里**：自言自语（含点击回应台词）也走本门禁，而它属于交互
    应答，免打扰期间必须照常冒（issue #208 规格）。普通提醒/气泡请改读
    :func:`quiet_blocked`。
    """
    checker = getattr(host, "_bubble_blocked", None)
    if callable(checker):
        return bool(checker(sprite))
    return bool(getattr(host, "_bubble_suppressed", False))


def quiet_blocked(host) -> bool:
    """免打扰（手动 **或** 全屏等效）是否拦下**普通**提醒/气泡。

    全应用唯一判据是 ``quiet_mode.QuietState.suppression_active()``；本函数只
    负责取 host 的配置（``quiet_also_on_fullscreen``，默认开）并转发。

    **穿透类不读它**（issue #208 规格）：审批/提问/错误等状态类事件走
    ``alert_survives_suppression``；自言自语/点击台词走 ``bubble_blocked``；
    联动状态气泡走 ``show_bubble`` 直发——三类都不经这道门，压了会出事故。
    """
    state = quiet_mode.shared_quiet_state()
    getter = getattr(getattr(host, "cfg", None), "get", None)
    also_on_fullscreen = quiet_mode.DEFAULT_QUIET_ALSO_ON_FULLSCREEN
    if callable(getter):
        try:
            also_on_fullscreen = bool(
                getter("quiet_also_on_fullscreen", also_on_fullscreen)
            )
        except Exception:
            pass
    return state.suppression_active(also_on_fullscreen=also_on_fullscreen)


def defer_quiet(text: str) -> None:
    """把被免打扰拦下的普通提醒记入暂存队列（上限 20，溢出丢最旧）。"""
    quiet_mode.shared_quiet_state().remember(text)


def report_quiet_summary(host, items) -> bool:
    """免打扰结束后用一条气泡汇报「免打扰期间有 N 条提醒」。

    走 ``show_alert``（``alert_type="lifecycle"`` 属穿透类，不会被本模块的任何
    门禁拦下）而不是 ``show_bubble``：结束那一刻最常见的情形是"正挂着一条审批
    气泡"，直发气泡会被让路丢弃，走提醒队列才保证送达。宿主没有提醒队列面
    （overlay 的 SpriteMenuFacade）时回退直发气泡——**这条回退也必须自证上屏**：
    overlay 的气泡面在不可见/飞行期/正挂提醒时是当场丢弃的，无条件报成功会让
    调用方把暂存排空而用户什么都没看到（本批自己立的交付确认制）。

    返回是否确实交付出去了（空暂存 = 无需打扰用户，返回 False）。
    """
    texts = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not texts:
        return False
    latest = texts[-1]
    if len(latest) > quiet_mode.QUIET_TEXT_LIMIT:
        latest = latest[: quiet_mode.QUIET_TEXT_LIMIT] + "…"
    if len(texts) == 1:
        text = f"免打扰期间有 1 条提醒：{latest}"
    else:
        text = f"免打扰期间有 {len(texts)} 条提醒，最近一条：{latest}"
    alert = getattr(host, "show_alert", None)
    if callable(alert):
        alert(
            text,
            duration_ms=quiet_mode.QUIET_SUMMARY_MS,
            sticky=False,
            alert_type="lifecycle",
            priority=3,
        )
        return True
    bubble = getattr(host, "show_bubble", None)
    if callable(bubble):
        # 取真实投递结果：False = 没上屏，调用方不得 ack（轮询表会重试）。
        # 有 show_alert 的宿主（PetWindow / MultiWindowProxy）不走这一支。
        return bool(bubble(text, duration_ms=quiet_mode.QUIET_SUMMARY_MS))
    return False


def sprite_in_flight(host, sprite=None) -> bool:
    """该 sprite 是否正在飞行（气泡门禁里的**飞行分量**，不含设置页抑制）。

    只有需要区分两种抑制的调用方才用它：飞行期是瞬时的（鱼飞几秒），上屏
    推迟到落地即可；设置页抑制期是长时的用户意图，节日提醒/语音报时在这期间
    仍按既有语义退到气泡位直写展示（见 ``festival_service._bubble``）。
    无该接口的 host（PetWindow / 测试替身）恒 False。
    """
    checker = getattr(host, "_sprite_in_flight", None)
    if callable(checker):
        return bool(checker(sprite))
    return False


def set_bubble_suppressed(host, suppressed: bool) -> None:
    """设置窗口打开期间暂停气泡显示；True 时立即隐藏当前气泡。"""
    host._bubble_suppressed = bool(suppressed)
    if host._bubble_suppressed:
        # 收的是**提醒实际承载**的那只气泡（提醒可能挂在子宠上；legacy 单窗下
        # 与 `_speech_bubble` 同一只）。
        bubble = alert_bubble(host)
        if bubble is not None:
            try:
                bubble.hide()
            except RuntimeError:  # 气泡已随它那只宠销毁
                pass
    else:
        restore_after_suppression(host)


def restore_after_suppression(host) -> None:
    """抑制结束后的恢复：粘滞提醒重挂、限时提醒续时、无 current 则推进队列。

    两份调用方共用同一口径（``set_bubble_suppressed(False)`` 与「飞行落地」）：
    共同语义都是「刚才不让冒的泡，现在可以冒了」，差别只在抑制位本身（设置页
    开关 vs. 壳的飞行集合）——绝不把两者合并成一次 ``set_bubble_suppressed``
    调用，那会顺手把设置页抑制位也清掉（proactive/联动节流读它）。

    入口再判一次门禁：抑制刚解除但另一路仍然挡着（典型 = 鱼还在飞：设置页在
    飞行中途开关）时不破门，留给那一路自己的结束后恢复。

    壳（提醒承载宠可迁移、限时提醒按剩余 TTL）把「重挂 current」整段交给自己的
    单一漏斗 ``_sync_alert_copy``：这里按当前目标重挂会把已经迁移到子宠的提醒
    拉回主位（"主位恢复不抢回"被破），也会把限时提醒的 TTL 重置成整段。恢复路径
    要的是"副本还在但不可见 → 补挂回来"，故走 ``_resume_alert_copy``（force）。
    """
    if bubble_blocked(host):
        return
    current = getattr(host, "_alert_current", None)
    resume = getattr(host, "_resume_alert_copy", None)
    if current is not None and callable(resume):
        resume()
        return
    if current is not None and alert_survives_suppression(
            current.get("alertType", ""), sticky=bool(current.get("sticky")),
            buttons=current.get("buttons"), priority=int(current.get("priority", 3))):
        # 存活（含非 sticky 的 task_complete/turn/balance 等）→ 按原语义重挂到它
        # 自己的承载气泡上：抑制期被隐藏且 hidden 链路被抑制守卫截断——不重挂则
        # ``_alert_current`` 永不清除，``pump_alerts`` 永久 early-return，后续
        # 提醒全部被吞（队列死锁）。超时后走正常 hidden → 清除 → 推进闭环。
        _reshow_on_carrier(host, current)
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


def redirect_hidden_bubble(host, text: str, *, subtitle: str = "",
                           duration_ms: int = 3200) -> bool:
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


def show_alert(host, text: str, *, subtitle: str = "", duration_ms: int = 0,
               buttons: list[tuple[str, object]] | None = None,
               sticky: bool = True, alert_id: str = "", priority: int = 3,
               alert_type: str = "watchdog", metadata: dict | None = None) -> None:
    """提醒消息队列：需要用户注意的提醒统一入队，一次只展示一个。"""
    if quiet_blocked(host) and not alert_survives_suppression(
            alert_type, sticky=sticky, buttons=buttons, priority=priority):
        # 免打扰（含全屏等效）期间的**普通**提醒不冒：进暂存队列，免打扰结束
        # （到时/提前/退出全屏）后统一汇报。
        #
        # 这一段刻意放在 isVisible 判定**之前**：全屏避让会把桌宠藏起来，若先走
        # 「不可见」分支，普通提醒会被改道/丢弃，暂存队列就永远收不到东西——
        # 而"全屏时攒着、退出后一次性告知"正是 quiet_also_on_fullscreen 的价值。
        defer_quiet(text)
        return
    if not host.isVisible():
        # 桌宠隐藏：非交互提醒（无按钮）改道灵动岛反馈面；交互气泡与
        # 设置页抑制期维持原丢弃行为。
        if not buttons and not bubble_blocked(host) and redirect_hidden_bubble(
                host, text, subtitle=subtitle, duration_ms=duration_ms or 3200):
            return
        return
    if bubble_blocked(host) and not alert_survives_suppression(
            alert_type, sticky=sticky, buttons=buttons, priority=priority):
        # 设置页抑制（长时用户意图）照旧丢弃；**飞行期不丢**——飞行只有几秒，
        # 瞬时抑制的语义是"先不上屏"：入队后由 pump_alerts 的守卫挡住，落地
        # 走 restore_after_suppression 正常弹出（B1：飞行结束后提醒队列恢复）。
        if getattr(host, "_bubble_suppressed", False):
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
                # 就地升级当前那份副本：落点 = **承载气泡**（提醒可能挂在子宠上，
                # 按当前目标升级会在主位那边多出一份、子宠那份变孤儿）。
                _reshow_on_carrier(host, item)
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
        _dismiss_bubble(alert_bubble(host))
        item["preemptedAlertId"] = old.get("id", "")
        logging.getLogger("dsh-pet-standalone").info(
            "alert preempted alertType=%s priority=%s alertId=%s by=%s",
            old.get("alertType", ""), old.get("priority", 3), old.get("id", ""), item.get("id", ""),
        )
        if old.get("sticky") and int(old.get("priority", 3)) >= 2:
            host._alert_queue.appendleft(old)
            _enforce_queue_bound(host)
    if not _admit_to_queue(host, item):
        if host._alert_current is None:
            # 抢占刚把旧 current 拿下来，这条又被拒收：屏幕不能空着——立刻推进
            # 队首（被抢占的那条按原语义回到屏上），否则 current 为 None 而队列
            # 有货，后面所有提醒都要等下一次 show_alert 才有机会上屏。
            host._pump_alerts()
        return
    host._alert_queue.append(item)
    host._alert_queue = deque(sorted(
        host._alert_queue, key=lambda queued: int(queued.get("priority", 3))
    ))
    host._pump_alerts()


def resolve_alert(host, alert_id: str) -> None:
    """按 alert_id 关闭某条提醒：若正在展示则收起并弹下一条，若在队列中则移除。

    供 AgentLinkManager 在审批/问题 resolved 时精确定位，避免多 agent 并发
    审批时 hide_bubble 误关他人的提醒。收起的是**实际承载**那条提醒的气泡
    （提醒可能已随承载宠迁移到别的宠上），不是"当前目标"的空气泡。"""
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
        _dismiss_bubble(alert_bubble(host))
        host._pump_alerts()
        return
    # 在队列中：移除该条（不打断当前展示）
    host._alert_queue = deque(
        item for item in host._alert_queue if item.get("id") != alert_id
    )


def pump_alerts(host) -> None:
    """若当前无提醒展示且队列非空，弹出队首展示（落点 = 当前目标）。"""
    if host._alert_current is not None:
        return
    if not host._alert_queue:
        return
    if quiet_blocked(host):
        # 免打扰开始**之前**已入队的普通提醒：一并转存暂存队列，否则它们会在
        # 免打扰期间照旧冒出来（而且恰好是"刚开免打扰就冒一条"的观感）。
        # 穿透类（审批/提问/生命周期）留在队列里照常推进。
        while host._alert_queue and not alert_survives_suppression(
            host._alert_queue[0].get("alertType", ""),
            sticky=bool(host._alert_queue[0].get("sticky")),
            buttons=host._alert_queue[0].get("buttons"),
            priority=int(host._alert_queue[0].get("priority", 3)),
        ):
            defer_quiet(host._alert_queue.popleft().get("text", ""))
        if not host._alert_queue:
            return
    if not host.isVisible() or bubble_blocked(host):
        return
    item = host._alert_queue.popleft()
    if not _show_on_target(host, item):
        # 目标气泡不可用（跟随器建失败/已随宠销毁）：条目留着，不静默吞掉。
        host._alert_queue.appendleft(item)
        return
    host._alert_current = item


def clear_alerts(host) -> None:
    """清空提醒消息队列并关闭当前提醒（DSH 离线/重启时全部失效）。"""
    host._alert_queue.clear()
    host._alert_current = None
    host._sticky_bubble_active = False
    host._sticky_text = ""
    host._sticky_subtitle = ""
    host._sticky_buttons = None
    _dismiss_bubble(alert_bubble(host))


def hide_bubble(host) -> None:
    """主动关闭当前气泡并解除 sticky（审批结束 / DSH 离线时调用）。

    若关闭的是队列中的提醒，则自动弹出下一条。"""
    host._sticky_bubble_active = False
    host._sticky_text = ""
    host._sticky_subtitle = ""
    host._sticky_buttons = None
    host._alert_current = None
    _dismiss_bubble(alert_bubble(host))
    host._pump_alerts()


def on_speech_bubble_hidden(host) -> None:
    """气泡被隐藏（临时气泡超时 / dismiss / 窗口隐藏）后的恢复/推进逻辑。

    sticky 恢复防抖：同一 sticky 内容在 300ms 内不重复 show_text，
    避免「审批被盖→恢复→再盖→再恢复」的卡顿循环。"""
    from .window import time as _window_time  # 兼容 seam：与 HEAD 同读 pet.window.time
    if not host.isVisible() or bubble_blocked(host):
        # 飞行期同样在这里截断：气泡是壳在起飞边沿主动收的，不能让它顺着
        # hidden 链路推进队列（否则飞行中会接着弹下一条）。_alert_current 原样
        # 留着，落地走 restore_after_suppression 重挂/续时。
        return
    cur = host._alert_current
    if cur is not None:
        if cur.get("sticky"):
            # 防抖：同一 sticky 内容在 300ms 内不重复恢复
            now = _window_time.monotonic()
            if abs(now - getattr(host, "_last_sticky_restore", 0.0)) < 0.3:
                return
            host._last_sticky_restore = now
            _reshow_on_carrier(host, cur)
        else:
            # 限时提醒（硬失败/卡住）：展示结束，弹出下一条
            host._alert_current = None
            host._pump_alerts()
        return
    # 旧路径兼容：sticky 审批气泡（不经队列）被盖掉后恢复
    if host._sticky_bubble_active and host._sticky_text:
        bubble = alert_bubble(host)
        if bubble is None:
            return
        try:
            bubble.show_text(
                host._sticky_text, _anchor_for(host, bubble), 0,
                pet_scale=host.scale, subtitle=host._sticky_subtitle, sticky=True,
                buttons=host._sticky_buttons,
            )
        except RuntimeError:
            return


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
    if bubble_blocked(host):
        return False
    duration_ms = int(round(host._self_talk_duration_seconds * 1000))
    anchor = window_placement.bubble_anchor_rect(host)
    _set_speech_bubble_interactive(host)
    host._speech_bubble.show_text(
        text, anchor, duration_ms, pet_scale=host.scale
    )
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
    if bubble_blocked(host):
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

    picked = pick_self_talk_choice(
        host._self_talk_texts, live_images, self_talk_image_chance(host)
    )
    if picked is None:
        return False

    kind, value = picked
    duration_ms = int(
        round(host._self_talk_duration_seconds * 1000)
    )
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
    """点击自言自语是否朗读（缺配置或配置损坏时按默认关闭，与 config.py 一致）。"""
    cfg = getattr(host, "cfg", None)
    if cfg is None:
        return False
    try:
        return bool(cfg.get("self_talk_speak_enabled", False))
    except Exception:
        return False


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
    character_id = str(host.cfg.get('character', catalog.DEFAULT_CHARACTER))
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
    if not feature_activation.is_active("music"):
        # 音乐模块未激活：唱歌链整条不建（表不起、COM 不调）。行为开关的值
        # 原样留在配置里，重新激活后按它照旧工作。
        return
    if not host._music_sing_enabled:
        return
    host._music_sing_timer.start()
    if host.isVisible():
        QTimer.singleShot(0, host, host._check_music_sing)


def _push_browser_media_setting(host, music_detect) -> None:
    """把「浏览器媒体会话参与歌词」开关推给 ``music_detect``。

    宿主（PetWindow / OverlayShell）是唯一持有 cfg 的一层，music_detect 不引
    配置模块；每拍读一次而不是初始化一次，避免多一份会过期的副本。没有 cfg
    的最小宿主保持 music_detect 当前值（默认关）。
    """
    getter = getattr(getattr(host, "cfg", None), "get", None)
    if not callable(getter):
        return
    try:
        music_detect.set_browser_media_enabled(
            bool(getter("music_browser_media_enabled", False))
        )
    except Exception:
        # 坏配置（自定义 cfg 替身）不能把唱歌检测整个打死。
        pass


def check_music_sing(host) -> None:
    """检测后台音乐并自动播放唱歌动画（可配置开关）。

    音乐播放期间唱歌动画会持续循环；音乐停止或开关关闭后恢复普通动画链。
    不打断正在播放的一次性动作/点击/拖拽。

    退出唱歌要经过一段宽限期，而不是一检测到静音就退：``is_music_playing``
    看的是音频峰值，歌曲的前奏/间奏/轻声段会让峰值瞬时跌到阈值以下，立刻退出
    会表现为"唱着唱着主动退出、然后静默不唱"。

    当前会话被否决（明确的视频、或关掉"浏览器媒体会话参与歌词"后的浏览器会话）
    时 ``is_music_playing`` 直接为 False，于是走上面这段宽限期退出——**刻意不
    在这里另加一条立即退出的分支**：那会让"只桩 is_music_playing 的既有测试"
    绕过桩直接读到真实 SMTC（本机开着浏览器视频时用例就红），也把否决规则拆成
    两处。规则只留在 ``music_detect`` 一处。
    """
    if not feature_activation.is_active("music"):
        # 音乐模块未激活：本拍直接收口——不调 music_detect（每秒一次 pycaw COM）、
        # 不拉常驻 SMTC 探针线程，也不付 pet.window 的导入。行为开关的值原样留着。
        host._music_sing_active = False
        host._music_sing_silent_since = None
        return
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
    from .music import detect as music_detect
    _push_browser_media_setting(host, music_detect)
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
        elif (
            time.monotonic() - silent_since >= music_sing_grace_seconds(host)
        ):
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
