# -*- coding: utf-8 -*-
"""G2 提醒生命周期收口：承载宠迁移 / 剩余 TTL / 有界队列 / 迁移与重启纪律。

裁决（本轮规范，逐条对应下面的用例）：

1. **承载宠可迁移**：一条有效提醒正在某只宠的气泡上呈现时，那只宠退出 / 逐只隐藏 /
   屏迁移（跟随器重建）→ 只要还有别的可见宠，整条提醒**交给新的可见目标**：
   同 event ID、同剩余 TTL、**不重新入队**、不出声、**恰好一份可见副本**。
2. **主位恢复不抢回**：slot0 回来（生成或显示）只影响**此后新产生**的事件；
   当前提醒继续留在仍然有效的承载宠上，不重挂、不重放。
3. **收口针对实际承载气泡**：``resolve_alert`` / ``hide_bubble`` / ``clear_alerts``
   收掉的是**真正在展示那条提醒**的气泡，而不是"当前动态目标"的空气泡。
4. **无宠 / 全隐藏**：不触碰已销毁的 QObject；current 可以留着待恢复，但
   ``resolved`` 必须**立即清掉且不复活**。
5. **限时提醒不永生**：非 sticky 提醒跨迁移按**剩余** TTL 重挂，绝不每次都重置整段时长。
6. **队列有界**：待处理条目上限 64（另加 1 条 current 展示位）；同 ID 更新不计增量；
   满时优先保高优先级（淘汰最低优先级里最旧的一条，或拒收更低优先级的新条），
   且必须可诊断（warning + 计数），不弹无限汇总、不新增设置项。
7. **迁移 / 重启纪律**：屏迁移后按 ID 既能收 current 也能收排队条目；
   同目录的新壳不会恢复旧壳的内存队列（队列不落盘）。

纪律：offscreen；真壳 + 真 PetSprite + 真 PetSpeechBubble；时间用注入时钟
（``shell._alert_clock``），事件用真事件循环轮询（不 sleep 赌时序）。
"""
from __future__ import annotations

import logging
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import pet.window as window_mod  # noqa: E402
from pet import click_sound  # noqa: E402
from pet.speech_bubble import PetSpeechBubble  # noqa: E402
from pet.sprite_bubble import sprite_anchor_rect_global  # noqa: E402
from tests.test_overlay_collision_toggle import _make_slot_shell  # noqa: E402

app = QApplication.instance() or QApplication([])

ALERT_TEXT = "Agent 需要你批准一条命令"
QUEUED_TEXT = "排队中的提醒"

#: 待处理队列基准（另有 1 条 current 展示位）：规范锁死的常数，实现必须一致。
QUEUE_LIMIT_BASELINE = 64


# ---------------------------------------------------------------- 脚手架
@pytest.fixture
def probes(monkeypatch):
    """记录全部 ``show_text``（含时长）与全部音效播放。

    音效统计**只算本用例自己提交的**：``SpriteSoundPlayer`` 把播放推迟到下一轮
    事件循环（``QTimer.singleShot(0)``，见 ``_submit``），而音效池是进程级的，
    上一条用例撞出来的那一声会落在本用例的事件循环里。先冲一次事件循环再清表，
    "迁移不得出声"这条断言测的才是本用例的动作。
    """
    shown: list[dict] = []
    sounds: list[object] = []
    original = PetSpeechBubble.show_text

    def recording_show_text(self, text, anchor, duration_ms=3200, **kwargs):
        shown.append({"bubble": self, "text": str(text), "duration_ms": int(duration_ms),
                      "sticky": bool(kwargs.get("sticky", False))})
        return original(self, text, anchor, duration_ms, **kwargs)

    monkeypatch.setattr(PetSpeechBubble, "show_text", recording_show_text)
    monkeypatch.setattr(click_sound.ClickSoundPool, "play_sound",
                        lambda self, path, volume=1.0: sounds.append(path) or True)
    app.processEvents()          # 冲掉上一条用例延后提交的播放
    sounds.clear()
    return shown, sounds


def _child(shell):
    children = [s for s in shell._all_sprites() if s is not shell.sprite]
    assert len(children) == 1
    return children[0]


def _is_visible(bubble) -> bool:
    try:
        return bool(bubble is not None and bubble.isVisible())
    except RuntimeError:  # C++ 侧已销毁：不可见（且绝不因此炸掉用例）
        return False


def _all_bubbles(shell):
    """(owner_sprite, bubble)；主 sprite 记 ``shell.sprite``。"""
    pairs = []
    follower = getattr(shell, "_bubble_follower", None)
    if follower is not None and follower.bubble is not None:
        pairs.append((shell.sprite, follower.bubble))
    for sprite, follower in shell._bubble_followers.items():
        if follower is not None and follower.bubble is not None:
            pairs.append((sprite, follower.bubble))
    return pairs


def _copies(shell, text):
    """当前**可见**且正文等于 text 的气泡对象。"""
    return [bubble for _owner, bubble in _all_bubbles(shell)
            if _is_visible(bubble) and str(getattr(bubble, "_raw_text", "")) == text]


def _bubble_of(shell, sprite):
    follower = shell._bubble_for(sprite)
    return None if follower is None else follower.bubble


def _shown_count(shown, text) -> int:
    return sum(1 for item in shown if item["text"] == text)


def _drain(predicate=None, budget=2.0) -> bool:
    """跑真事件循环直到条件成立（宽预算；不用固定 sleep 猜时序）。"""
    deadline = time.monotonic() + budget
    while True:
        app.processEvents()
        if predicate is None or predicate():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)


def _cleanup(shell):
    try:
        shell.stop()
    finally:
        shell._delete_runtime_marker()


def _show_alert_on(shell, sprite, text, **kwargs):
    """让一条提醒落到**指定**的宠上（其余可见宠先藏起来，落定后再恢复）。"""
    others = [s for s in shell._all_sprites() if s is not sprite]
    for other in others:
        shell.set_sprite_visible(other, False)
    shell.show_alert(text, **kwargs)
    app.processEvents()
    for other in others:
        shell.set_sprite_visible(other, True)
    app.processEvents()


# ---------------------------------------------------------------- 1. 承载宠迁移
def test_carrier_exit_moves_alert_to_child_same_id_no_replay(tmp_path, probes):
    """slot0（承载宠）退出：提醒整体交接到可见子宠，恰好一份、同 ID、不出声、不重排队。"""
    shown, sounds = probes
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        shell.show_alert(ALERT_TEXT, alert_id="ap-1")
        app.processEvents()
        item = shell._alert_current
        assert item is not None and item["id"] == "ap-1"
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, main)], \
            "前提：提醒呈现主位气泡上"
        shows_before = _shown_count(shown, ALERT_TEXT)

        assert shell.exit_pet(main) is True
        app.processEvents()

        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)], \
            "承载宠退出后，提醒必须整体搬到仍可见的子宠上（恰好一份副本）"
        assert shell._alert_current is item, "迁移不得重建 current 条目"
        assert not shell._alert_queue, "迁移不得重新摄入/重建队列条目"
        assert _shown_count(shown, ALERT_TEXT) <= shows_before + 1, "迁移最多重挂一次"
        assert sounds == [], "迁移不得出声"

        shell.resolve_alert("ap-1")
        app.processEvents()
        assert shell._alert_current is None
        assert _copies(shell, ALERT_TEXT) == [], "收起必须收掉真正在展示的那一份"
    finally:
        _cleanup(shell)


def test_carrier_hide_moves_copy_and_slot0_return_does_not_steal(tmp_path, probes):
    """逐只隐藏承载宠 → 迁移到子宠；主位恢复只影响后续事件，不抢回当前提醒。"""
    shown, _sounds = probes
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        shell.show_alert(ALERT_TEXT, alert_id="ap-1")
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, main)]

        shell.set_sprite_visible(main, False)
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)], \
            "承载宠被隐藏（仍在场）时提醒同样交接"

        shows_before = _shown_count(shown, ALERT_TEXT)
        shell.set_sprite_visible(main, True)
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)], \
            "主位恢复不得抢回仍在有效承载宠上的提醒"
        assert _shown_count(shown, ALERT_TEXT) == shows_before, "不得重挂/重放"

        shell.resolve_alert("ap-1")
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == []

        # 新事件才优先主位：此刻主宠可见，新提醒落在主位气泡上。
        shell.show_alert(QUEUED_TEXT, alert_id="ap-2")
        app.processEvents()
        assert _copies(shell, QUEUED_TEXT) == [_bubble_of(shell, main)]
    finally:
        _cleanup(shell)


def test_hide_bubble_and_clear_target_the_real_carrier(tmp_path, probes):
    """hide_bubble / clear_alerts 收的是实际承载气泡，不是动态目标的空气泡。"""
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        _show_alert_on(shell, main, ALERT_TEXT, alert_id="ap-1")
        shell.set_sprite_visible(main, False)
        app.processEvents()
        carrier = _bubble_of(shell, child)
        assert _copies(shell, ALERT_TEXT) == [carrier]
        shell.set_sprite_visible(main, True)
        app.processEvents()

        shell.hide_bubble()
        app.processEvents()
        assert _is_visible(carrier) is False, "hide_bubble 必须收掉真正在展示提醒的气泡"
        assert _copies(shell, ALERT_TEXT) == []
        assert shell._alert_current is None

        _show_alert_on(shell, child, ALERT_TEXT, alert_id="ap-3")
        shell.set_sprite_visible(child, False)
        app.processEvents()
        shell.set_sprite_visible(child, True)
        app.processEvents()
        carrier = shell._alert_bubble
        assert _is_visible(carrier) is True
        shell.clear_alerts()
        app.processEvents()
        assert _is_visible(carrier) is False, "clear_alerts 必须收掉实际承载气泡"
        assert shell._alert_current is None and not shell._alert_queue
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 4. 无宠 / 全隐藏
def test_all_pets_gone_keeps_alert_pending_then_restores_on_spawn(tmp_path, probes):
    """无可见宠：current 留着待恢复，不碰已销毁对象；再生成时恢复到新宠上。"""
    shown, _sounds = probes
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        shell.show_alert(ALERT_TEXT, alert_id="ap-1")
        app.processEvents()
        item = shell._alert_current
        assert len(_copies(shell, ALERT_TEXT)) == 1

        assert shell.exit_pet(main) is True
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)]

        assert shell.exit_pet(child) is True
        app.processEvents()
        assert shell.alert_target_sprite() is None
        assert shell._alert_current is item, "无宠期 current 留着待恢复（绝不空转丢失）"
        assert _copies(shell, ALERT_TEXT) == []
        assert not shell._alert_queue

        shows_before = _shown_count(shown, ALERT_TEXT)
        shell.spawn_pet()
        app.processEvents()
        assert shell.sprite is not None
        assert len(_copies(shell, ALERT_TEXT)) == 1, "恢复可见宠后提醒回到屏上"
        assert shell._alert_current is item
        assert _shown_count(shown, ALERT_TEXT) == shows_before + 1, "恢复只补一份"
    finally:
        _cleanup(shell)


def test_resolve_without_carrier_clears_immediately_and_never_resurrects(tmp_path, probes):
    """无可见宠时 resolved：立即清掉（不碰死对象），此后生成宠物也不复活。"""
    shown, _sounds = probes
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        shell.show_alert(ALERT_TEXT, alert_id="ap-1")
        app.processEvents()
        assert shell.exit_pet(main) is True
        app.processEvents()
        assert shell.exit_pet(child) is True
        app.processEvents()
        assert shell.alert_target_sprite() is None
        assert shell._alert_current is not None

        shell.resolve_alert("ap-1")
        app.processEvents()
        assert shell._alert_current is None, "resolved 必须立即清掉 current"
        assert shell._alert_bubble is None

        shows_before = _shown_count(shown, ALERT_TEXT)
        shell.spawn_pet()
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [], "已 resolved 的提醒绝不复活"
        assert _shown_count(shown, ALERT_TEXT) == shows_before
        assert not shell._alert_queue
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 5. 剩余 TTL
def test_timed_alert_rehangs_with_remaining_ttl_and_expires(tmp_path, probes, monkeypatch):
    """非 sticky 限时提醒：迁移按**剩余** TTL 重挂，跨迁移不永生、超时即收口。"""
    shown, _sounds = probes
    clock = [1000.0]
    shell, _cfg = _make_slot_shell(tmp_path)
    monkeypatch.setattr(shell, "_alert_clock", lambda: clock[0], raising=False)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        # 让这条限时提醒先落在子宠上（主宠先藏 → 目标 = 子宠）
        shell.set_sprite_visible(main, False)
        shell.show_alert(ALERT_TEXT, alert_id="t1", sticky=False,
                         duration_ms=6000, alert_type="watchdog")
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)]
        assert shown[-1]["duration_ms"] == 6000

        shell.set_sprite_visible(main, True)          # 主位回来：不抢回
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)]

        clock[0] += 4.0                               # 已经过去 4 秒
        shell.set_sprite_visible(child, False)        # 承载宠藏了 → 交接到主位
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, main)], "必须交接"
        remaining = shown[-1]["duration_ms"]
        assert 1200 <= remaining <= 2100, \
            f"迁移必须按剩余 TTL 重挂（6000 - 4000），实得 {remaining}"

        clock[0] += 3.0                               # 剩余时间也走完了
        shell.set_sprite_visible(child, True)
        app.processEvents()
        shell.set_sprite_visible(main, False)         # 再交接一次 → 到期收口
        app.processEvents()
        assert shell._alert_current is None, "限时提醒不得靠迁移永生在屏"
        assert _copies(shell, ALERT_TEXT) == []
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 6. 有界队列
def test_pending_queue_is_bounded_prefers_high_priority_and_is_diagnosable(tmp_path, probes, caplog):
    """待处理队列上限 64（另加 current）：同 ID 更新不增量，满时保高优先级，可诊断。"""
    from pet import window_alerts

    assert window_alerts.ALERT_QUEUE_LIMIT == QUEUE_LIMIT_BASELINE, "队列基准不得漂移"
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        # current = 优先级 0（审批级）；排队条目优先级 1 → 便于验证"满时保高优先级"
        shell.show_alert("当前提醒", alert_id="cur", sticky=True,
                         alert_type="approval", priority=0)
        for index in range(QUEUE_LIMIT_BASELINE):
            shell.show_alert(f"排队{index}", alert_id=f"q{index}", sticky=True,
                             alert_type="approval", priority=1)
        ids = [item["id"] for item in shell._alert_queue]
        assert ids == [f"q{i}" for i in range(QUEUE_LIMIT_BASELINE)]
        assert shell._alert_current["id"] == "cur"

        text_on_screen = shell._speech_bubble._raw_text
        overflow_before = getattr(shell, "_alert_overflow_count", 0)
        with caplog.at_level(logging.WARNING):
            shell.show_alert("更低优先级的新条", alert_id="low", sticky=True,
                             alert_type="watchdog", priority=2)
        assert [item["id"] for item in shell._alert_queue] == ids, \
            "队列已满时低优先级新条必须被拒收，不得挤掉别人"
        assert len(shell._alert_queue) == QUEUE_LIMIT_BASELINE
        assert shell._speech_bubble._raw_text == text_on_screen, "不得弹汇总/换掉当前气泡"
        assert "队列已满" in caplog.text, "拒收必须留下可诊断的 warning"
        assert getattr(shell, "_alert_overflow_count", 0) == overflow_before + 1

        # 同 ID 更新不增量（就地替换排队条目）
        shell.show_alert("排队5更新", alert_id="q5", sticky=True,
                         alert_type="approval", priority=1)
        assert len(shell._alert_queue) == QUEUE_LIMIT_BASELINE, "同 ID 更新不得增长队列"
        assert [item["text"] for item in shell._alert_queue if item["id"] == "q5"] == ["排队5更新"]

        # 更高优先级的新条进得来：淘汰最低优先级里最旧的一条
        shell.show_alert("更高的优先级", alert_id="crit", sticky=True,
                         alert_type="approval", priority=0)
        ids = [item["id"] for item in shell._alert_queue]
        assert len(ids) == QUEUE_LIMIT_BASELINE
        assert "crit" in ids
        assert "q0" not in ids, "满队列必须淘汰最低优先级里最旧的一条"
        assert "q1" in ids
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 7. 迁移 / 重启
def test_full_queue_preemption_stays_bounded_and_keeps_screen_busy(tmp_path, probes, caplog):
    """满队列上高优先级抢占：队列不得超上限，抢占后屏幕不得空着（current 不留空）。"""
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.show_alert("被抢占的低优先级", alert_id="cur", sticky=True,
                         alert_type="watchdog", priority=3)
        for index in range(QUEUE_LIMIT_BASELINE):
            shell.show_alert(f"排队{index}", alert_id=f"q{index}", sticky=True,
                             alert_type="watchdog", priority=3)
        assert len(shell._alert_queue) == QUEUE_LIMIT_BASELINE

        with caplog.at_level(logging.WARNING):
            shell.show_alert("更急的审批", alert_id="crit", sticky=True,
                             alert_type="approval", priority=0)
        app.processEvents()

        assert len(shell._alert_queue) <= QUEUE_LIMIT_BASELINE, "抢占不得让队列超上限"
        assert shell._alert_current is not None, "抢占后屏幕不得空着（否则队列静默卡死）"
        assert shell._alert_current["id"] == "crit"
        assert shell._speech_bubble._raw_text == "更急的审批"
        assert "队列已满" in caplog.text, "越界淘汰必须留可诊断 warning"
    finally:
        _cleanup(shell)


def test_screen_migration_keeps_alert_and_resolve_by_id_works(tmp_path, probes):
    """换家后提醒仍挂在承载宠那道气泡上，且按 ID 能分别收 current 与排队条目。"""
    shown, _sounds = probes
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.start()
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        assert shell.exit_pet(shell.sprite) is True
        app.processEvents()
        follower_before = shell._bubble_followers.get(child)

        shell.show_alert(ALERT_TEXT, alert_id="c1")
        shell.show_alert(QUEUED_TEXT, alert_id="q1")
        app.processEvents()
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)]
        assert [item["id"] for item in shell._alert_queue] == ["q1"]

        shell._migrate_to_screen(QGuiApplication.primaryScreen())
        app.processEvents()

        assert shell._bubble_followers.get(child) is follower_before, \
            "单窗：换家不重建跟随器（它记的窗口对象还是同一个）"
        assert _copies(shell, ALERT_TEXT) == [_bubble_of(shell, child)], \
            "屏迁移后提醒副本必须重挂到承载宠的新气泡上"
        assert shell._alert_current["id"] == "c1"
        assert [item["id"] for item in shell._alert_queue] == ["q1"]

        shell.resolve_alert("c1")
        _drain(lambda: (shell._alert_current or {}).get("id") == "q1")
        assert shell._alert_current is not None and shell._alert_current["id"] == "q1", \
            "收掉 current 后必须推进排队条目"
        assert not shell._alert_queue
        assert len(_copies(shell, QUEUED_TEXT)) == 1

        assert _shown_count(shown, ALERT_TEXT) == 1, (
            "单窗换家不重挂提醒：同一条只显示一次（不会出现第二份副本）")
        shell.resolve_alert("q1")
        app.processEvents()
        assert shell._alert_current is None
        assert _copies(shell, QUEUED_TEXT) == []
    finally:
        _cleanup(shell)


def test_new_shell_in_same_config_dir_does_not_restore_old_queue(tmp_path, probes):
    """同目录新壳：只按各源当前有效状态重建，不恢复旧壳的内存提醒队列。"""
    shell, cfg = _make_slot_shell(tmp_path)
    second = None
    try:
        shell.overlay.show()
        shell.show_alert(ALERT_TEXT, alert_id="old-cur")
        shell.show_alert(QUEUED_TEXT, alert_id="old-q")
        app.processEvents()
        assert shell._alert_current["id"] == "old-cur"
        assert [item["id"] for item in shell._alert_queue] == ["old-q"]
        _cleanup(shell)

        second, _cfg2 = _make_slot_shell(tmp_path)
        second.overlay.show()
        app.processEvents()
        assert second._alert_current is None, "重启后不得恢复旧队列的 current"
        assert list(second._alert_queue) == [], "重启后不得恢复旧队列的排队条目"
        assert not [p.name for p in cfg.dir.iterdir() if "alert" in p.name.lower()], \
            "提醒队列是内存态，不得落盘成第二真值"
    finally:
        if second is not None:
            _cleanup(second)


# ---------------------------------------------------------------- 3. hidden 链路守卫
def test_carrier_bubble_hidden_wired_once_across_rebinds(tmp_path, probes):
    """主位缺席 + 跟随器重建（屏迁移）后同一只承载气泡只接一次 hidden。

    接两次的代价很具体：一次隐藏触发两次推进——第一条被收掉后第二次会顺手清掉
    刚弹上来的下一条（静默跳过提醒），而屏幕上看不出任何异常。
    """
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.start()
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        assert shell.exit_pet(shell.sprite) is True
        app.processEvents()

        shell.show_alert(QUEUED_TEXT, alert_id="n1", sticky=False,
                         duration_ms=6000, alert_type="watchdog")
        shell.show_alert("下一条", alert_id="n2", sticky=False,
                         duration_ms=6000, alert_type="watchdog")
        app.processEvents()
        assert shell._alert_current["id"] == "n1"

        shell._migrate_to_screen(QGuiApplication.primaryScreen())
        app.processEvents()
        assert shell._alert_current["id"] == "n1"
        assert _copies(shell, QUEUED_TEXT) == [_bubble_of(shell, child)]

        carrier = shell._alert_bubble
        carrier.hide()
        assert _drain(lambda: (shell._alert_current or {}).get("id") == "n2")
        assert shell._alert_current is not None and shell._alert_current["id"] == "n2", \
            "一次隐藏只能推进一条（重复接线会把刚弹上来的下一条一起清掉）"
        assert len(_copies(shell, "下一条")) == 1
    finally:
        _cleanup(shell)


def test_queued_alert_resumes_when_window_shows_again(tmp_path, probes):
    """整窗隐藏期排队等着的提醒：显示恢复时必须继续推进（不靠下一次新提醒捞它）。"""
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.show_alert(ALERT_TEXT, alert_id="q1")
        shell.show_alert(QUEUED_TEXT, alert_id="q2")
        app.processEvents()
        assert shell._alert_current["id"] == "q1"
        assert [item["id"] for item in shell._alert_queue] == ["q2"]

        shell.set_pet_visible(False)          # 整窗隐藏
        shell.resolve_alert("q1")             # 隐藏期把 current 收掉（DSH 侧解决了）
        app.processEvents()
        assert shell._alert_current is None
        assert [item["id"] for item in shell._alert_queue] == ["q2"], "隐藏期不得吞队列"

        shell.set_pet_visible(True)           # 显示恢复 → 队首必须接上
        _drain(lambda: (shell._alert_current or {}).get("id") == "q2")
        assert shell._alert_current is not None and shell._alert_current["id"] == "q2"
        assert not shell._alert_queue
        assert len(_copies(shell, QUEUED_TEXT)) == 1
    finally:
        _cleanup(shell)


def test_hidden_signal_chain_drives_restore_and_queue_advance(tmp_path, probes, monkeypatch):
    """hidden 链路必须真的驱动重挂/推进（真事件循环），且防抖把重挂次数封住。"""
    shown, _sounds = probes
    clock = [500.0]
    monkeypatch.setattr(window_mod, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    shell, _cfg = _make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = _child(shell)
        main = shell.sprite

        _show_alert_on(shell, child, ALERT_TEXT, alert_id="h1")
        shell.set_sprite_visible(main, True)          # 主位回来：不抢回
        app.processEvents()
        child_bubble = _bubble_of(shell, child)
        assert _copies(shell, ALERT_TEXT) == [child_bubble]
        assert shell._alert_bubble is child_bubble, "前提：承载气泡 = 子宠气泡"

        # 真隐藏（不是吞掉信号）：hidden 链路必须把粘滞副本重挂
        shows_before = _shown_count(shown, ALERT_TEXT)
        child_bubble.hide()
        assert _drain(lambda: _is_visible(child_bubble)), "hidden 链路必须驱动重挂"
        assert _shown_count(shown, ALERT_TEXT) == shows_before + 1, \
            "hidden 链路被吞掉就会假绿：重挂必须真的走 show_text"
        assert shell._alert_current["id"] == "h1"
        assert child_bubble._anchor_rect == sprite_anchor_rect_global(
            child, shell.overlay.geometry().topLeft()), \
            "子宠气泡锚点必须按它自己那只算（迁移后不能跑到主位头顶）"

        # 同一防抖窗内反复隐藏：重挂次数被封住（不无限递归），状态仍然完整
        for _ in range(6):
            child_bubble.hide()
            _drain()
        assert _shown_count(shown, ALERT_TEXT) <= shows_before + 2, \
            "300ms 防抖必须把重挂次数封住（不无限递归）"
        assert shell._alert_current["id"] == "h1"

        # 非粘滞：同一条 hidden 链路推进队列（真事件循环里等状态，不猜时序）
        clock[0] += 1.0
        shell.resolve_alert("h1")
        app.processEvents()
        shell.show_alert(QUEUED_TEXT, alert_id="h2", sticky=False,
                         duration_ms=6000, alert_type="watchdog")
        shell.show_alert("下一条", alert_id="h3", sticky=False,
                         duration_ms=6000, alert_type="watchdog")
        app.processEvents()
        assert shell._alert_current["id"] == "h2"
        carrier = shell._alert_bubble
        carrier.hide()
        assert _drain(lambda: (shell._alert_current or {}).get("id") == "h3"), \
            "限时提醒隐藏后必须清除 current 并推进下一条"
        assert len(_copies(shell, "下一条")) == 1
    finally:
        _cleanup(shell)


# ---------------------------------------------------------------- 有界重试（4 次上限）
def test_bounded_command_retry_exhausts_by_event_sync_not_wall_clock(tmp_path, monkeypatch, caplog):
    """既有"最多重试到第 4 次"机制用事件同步驱动验证——绝不用 12 秒墙钟等待。

    ``MAX_COMMAND_ATTEMPTS = 3`` + 首次领取 = 总共 4 次尝试机会；同步直调消费入口
    （不跑 3 秒轮询定时器）就能把这条链条走完，耗时必须是毫秒级。
    """
    from pet import overlay_settings_command as cmd

    shell, cfg = _make_slot_shell(tmp_path)
    try:
        monkeypatch.setattr(shell, "_apply_child_profile", lambda slot: False)
        assert cmd.write_command(cfg.dir, cmd.CMD_PROFILE_RESET, target=1)
        with caplog.at_level(logging.WARNING):
            started = time.monotonic()
            for _ in range(4):
                shell._consume_settings_command()
            elapsed = time.monotonic() - started

        assert cmd.read_command(cfg.dir) is None, "4 次尝试后必须失败结束（不再占队）"
        assert "重试耗尽" in caplog.text
        assert elapsed < 2.0, f"不得靠墙钟等待（4 × 3s 轮询），实测 {elapsed:.3f}s"

        # 通道仍然可用：失败结束不阻塞后续指令
        assert cmd.write_command(cfg.dir, cmd.CMD_EXIT_SPAWNED_PETS, target=None)
        shell._consume_settings_command()
        assert cmd.read_command(cfg.dir) is None
    finally:
        _cleanup(shell)
