# -*- coding: utf-8 -*-
"""点击自言自语朗读（self_talk_speak_enabled）的聚焦回归。

产品契约：
- 点击触发的自言自语在**文字真实显示**之后，把同一句交给音频通道
  （窗口的 ``on_self_talk_speak``，由 app 接线到 ``AppShell.speak_self_talk``）；
- 图片气泡没有可读文本 → 不出声；
- 朗读开关关闭 → 只出气泡；
- 宿主未接线（旧宿主 / 测试替身）或通道抛异常 → 静默降级，绝不影响点击本身。
"""

from __future__ import annotations

from pet import window_alerts


class _Cfg(dict):
    def click_talk_texts_for(self, character_id, click_name):
        return list(self.get("click_texts") or [])


class _Host:
    """最小宿主替身：只实现 show_click_self_talk 依赖的那几个接口。"""

    def __init__(self, *, texts=(), speak_enabled=True, on_speaker=None, fallback_text=None):
        self.cfg = _Cfg(
            {
                "character": "shenshen",
                "self_talk_speak_enabled": speak_enabled,
                "click_texts": list(texts),
                "fallback_text": fallback_text,
            }
        )
        self.shown = []
        self.spoken = []
        self._last_self_talk_text = None
        if on_speaker is not None:
            self.on_self_talk_speak = on_speaker

    # PetWindow 的薄委托：本测试只关心"显示了什么、朗读了什么"
    def _show_self_talk_text(self, text):
        self.shown.append(text)
        return True

    def _show_random_self_talk(self):
        """模拟回退路径：文本落地时记 _last_self_talk_text，图片落地时记 None。"""
        text = self.cfg.get("fallback_text")
        self._last_self_talk_text = text
        if text:
            self.shown.append(text)
        return True


def _record(host):
    return lambda text: host.spoken.append(text)


def test_click_talk_text_is_spoken_verbatim():
    host = _Host(texts=["今天也要好好吃饭。"])
    host.on_self_talk_speak = _record(host)

    assert window_alerts.show_click_self_talk(host, "click-1") is True

    assert host.shown == ["今天也要好好吃饭。"]
    assert host.spoken == host.shown  # 听到的 == 看到的


def test_random_text_fallback_is_spoken():
    host = _Host(fallback_text="再陪你一会儿。")
    host.on_self_talk_speak = _record(host)

    assert window_alerts.show_click_self_talk(host, "click-1") is True
    assert host.spoken == ["再陪你一会儿。"]


def test_image_bubble_stays_silent():
    host = _Host(fallback_text=None)  # 随机到图片：只弹图，没有可读文本
    host.on_self_talk_speak = _record(host)

    assert window_alerts.show_click_self_talk(host, "click-1") is True
    assert host.spoken == []


def test_speech_toggle_off_keeps_bubble_only():
    host = _Host(texts=["今天也要好好吃饭。"], speak_enabled=False)
    host.on_self_talk_speak = _record(host)

    assert window_alerts.show_click_self_talk(host, "click-1") is True
    assert host.shown == ["今天也要好好吃饭。"]
    assert host.spoken == []


def test_missing_channel_never_breaks_the_click():
    host = _Host(texts=["今天也要好好吃饭。"])  # 未接线：on_self_talk_speak 不存在

    assert window_alerts.show_click_self_talk(host, "click-1") is True
    assert host.shown == ["今天也要好好吃饭。"]


def test_channel_exception_is_swallowed():
    def boom(text):
        raise RuntimeError("audio device gone")

    host = _Host(texts=["今天也要好好吃饭。"], on_speaker=boom)

    assert window_alerts.show_click_self_talk(host, "click-1") is True
    assert host.shown == ["今天也要好好吃饭。"]


def test_speak_setting_defaults_on_and_persists(tmp_path):
    """新键默认开启（与 config.py 默认值一致），且能落盘回读。"""
    from pet.config import Config

    config = Config(base=tmp_path)
    assert config.get("self_talk_speak_enabled") is True

    config.set("self_talk_speak_enabled", False)
    config.save()

    assert Config(base=tmp_path).get("self_talk_speak_enabled") is False


# --------------------------------------------------------------- 点击侧与周期气泡解耦


class _ClickCfg(_Cfg):
    dir = None


def _click_once(*, click_self_talk: bool, periodic: bool, speak_enabled: bool = True):
    """走真实点击入口：``PetWindow._on_click`` + 最小宿主替身。

    只提供该路径真正用到的属性/方法；``_show_click_self_talk`` 委托到真实 seam
    （``window_alerts.show_click_self_talk``），所以"显示什么、朗读什么"由产品代码决定。
    """
    from pet.window import PetWindow

    class _Pet:
        _just_dragged = False
        clicks = ["click-1"]
        click_show_balance = False
        on_show_balance = None
        on_restore_fun_windows = None
        _effects_consume_click = None
        _effects_route_click_golden_spin = None

        def __init__(self):
            self.cfg = _ClickCfg(
                {
                    "click_sound_pack": {"kind": "custom"},
                    "character": "shenshen",
                    "self_talk_speak_enabled": speak_enabled,
                }
            )
            self.click_show_self_talk = click_self_talk
            self._self_talk_enabled = periodic
            self.shown: list[str] = []
            self.spoken: list[str] = []
            self.scheduled: list[bool] = []
            self.on_self_talk_speak = self.spoken.append

        def _pick(self, sequence):
            return sequence[0]

        def _cancel_move(self) -> None:
            pass

        def _start_squash(self) -> None:
            pass

        def _switch(self, name) -> None:
            pass

        def _schedule_click_sound(self) -> None:
            pass

        def _show_click_self_talk(self, click_name):
            return window_alerts.show_click_self_talk(self, click_name)

        def _show_self_talk_text(self, text):
            self.shown.append(text)
            self._last_self_talk_text = text
            return True

        def _show_random_self_talk(self):
            self._last_self_talk_text = "再陪你一会儿。"
            self.shown.append(self._last_self_talk_text)
            return True

        def _schedule_self_talk(self, *, after_display=False):
            self.scheduled.append(bool(after_display))

    pet = _Pet()
    PetWindow._on_click(pet)
    return pet


def test_click_self_talk_speaks_without_periodic_bubbles():
    """只开「点击触发自言自语」、关掉「气泡自言自语」也要出气泡并朗读。

    本轮解耦的契约：点击自言自语是**独立开关**，不再依附周期气泡总开关
    （原先 ``_on_click`` 要求两者同时开启，导致只想点击听声的用户无从开启）。
    """
    pet = _click_once(click_self_talk=True, periodic=False)

    assert pet.shown == ["再陪你一会儿。"]
    assert pet.spoken == ["再陪你一会儿。"]
    assert pet.scheduled == [True]


def test_click_self_talk_off_stays_silent_even_with_periodic_bubbles():
    """反向：只开周期气泡、关掉「点击触发自言自语」时，点击不出气泡也不朗读。"""
    pet = _click_once(click_self_talk=False, periodic=True)

    assert pet.shown == []
    assert pet.spoken == []
    assert pet.scheduled == []


def test_click_self_talk_speech_toggle_off_keeps_bubble_only_at_click():
    """点击侧朗读开关关闭：仍然出气泡，只是不出声。"""
    pet = _click_once(click_self_talk=True, periodic=False, speak_enabled=False)

    assert pet.shown == ["再陪你一会儿。"]
    assert pet.spoken == []


def test_click_speech_alone_asks_for_the_audio_channel():
    """音频通道存在性判定：只想要点击出声的用户也要拿到通道。"""
    from pet.app import AppShell

    class _Shell:
        def __init__(self, **values):
            self.config = _Cfg(values)

    def wanted(**values) -> bool:
        return AppShell._self_talk_speak_wanted(_Shell(**values))

    assert wanted(self_talk_speak_enabled=True, click_show_self_talk=True, self_talk_enabled=False) is True
    assert wanted(self_talk_speak_enabled=True, click_show_self_talk=False, self_talk_enabled=True) is False
    assert wanted(self_talk_speak_enabled=False, click_show_self_talk=True, self_talk_enabled=True) is False
