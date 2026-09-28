# -*- coding: utf-8 -*-
"""天气服务壳测试：缓存 / 去重 / 队列消费 / 城市列表读写 / 气泡出口。

线程在测试里被换成"同步执行"的替身（``InlineThread``），因此断言是确定性的，
不依赖真网络、也不依赖 sleep 抢时序。
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from pet import weather_service as svc


class FakeConfig:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.saved = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.saved += 1


class FakeHost(QWidget):
    def __init__(self, config):
        super().__init__()
        self.cfg = config


class InlineThread:
    """把 threading.Thread 换成同线程立即执行，测试里就没有竞态。"""

    def __init__(self, target=None, daemon=None, name=None):
        self._target = target

    def start(self):
        if self._target is not None:
            self._target()


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def _make_host(values=None):
    return FakeHost(FakeConfig(values or {"weather_enabled": True, "weather_city": "汕头"}))


def test_request_without_city_does_nothing(_app):
    host = _make_host({"weather_enabled": True, "weather_city": ""})
    assert svc.request(host) is False


def test_request_disabled_does_nothing(_app):
    host = _make_host({"weather_enabled": False, "weather_city": "汕头"})
    assert svc.request(host) is False


def test_request_fetch_then_tick_presents_bubble(_app, monkeypatch):
    host = _make_host()
    presented: list[str] = []
    monkeypatch.setattr(svc, "_present", lambda h, text, **kw: presented.append(text))
    monkeypatch.setattr(svc.threading, "Thread", InlineThread)
    monkeypatch.setattr(svc, "_fetch", lambda city: ("26", "阴"))

    assert svc.request(host) is True
    assert svc.state(host)["queue"]          # 结果已落队列，等主线程消费
    svc.tick(host)
    assert presented == ["汕头今天 26°，天气阴"]
    assert svc.state(host)["queue"] == []
    assert svc.state(host)["busy"] is False


def test_request_uses_cache_within_ttl(_app, monkeypatch):
    host = _make_host()
    presented: list[str] = []
    calls: list[str] = []
    monkeypatch.setattr(svc, "_present", lambda h, text, **kw: presented.append(text))
    monkeypatch.setattr(svc.threading, "Thread", InlineThread)
    monkeypatch.setattr(svc, "_fetch", lambda city: calls.append(city) or ("26", "阴"))

    svc.request(host)
    svc.tick(host)
    svc.request(host)                        # 第二次命中缓存，不再联网
    assert calls == ["汕头"]
    assert presented == ["汕头今天 26°，天气阴"] * 2


def test_request_reports_failure_without_blaming_user(_app, monkeypatch):
    host = _make_host()
    presented: list[str] = []
    monkeypatch.setattr(svc, "_present", lambda h, text, **kw: presented.append(text))
    monkeypatch.setattr(svc.threading, "Thread", InlineThread)
    monkeypatch.setattr(svc, "_fetch", lambda city: None)

    svc.request(host)
    svc.tick(host)
    assert presented and presented[0].startswith("呜～")


def test_search_and_add_picks_single_candidate(_app, monkeypatch):
    host = _make_host({"weather_enabled": True, "weather_city": ""})
    presented: list[str] = []
    monkeypatch.setattr(svc, "_present", lambda h, text, **kw: presented.append(text))
    monkeypatch.setattr(svc.threading, "Thread", InlineThread)
    monkeypatch.setattr(svc, "_lookup", lambda query: [("汕头（广东）", "汕头")])
    monkeypatch.setattr(svc, "_fetch", lambda city: ("26", "阴"))

    assert svc.search_and_add(host, "汕头") is True
    svc.tick(host)
    assert svc.current_city(host) == "汕头"
    assert presented == ["汕头今天 26°，天气阴"]


def test_search_and_add_reports_empty_candidates(_app, monkeypatch):
    host = _make_host({"weather_enabled": True, "weather_city": ""})
    presented: list[str] = []
    monkeypatch.setattr(svc, "_present", lambda h, text, **kw: presented.append(text))
    monkeypatch.setattr(svc.threading, "Thread", InlineThread)
    monkeypatch.setattr(svc, "_lookup", lambda query: [])

    svc.search_and_add(host, "某某地")
    svc.tick(host)
    assert presented and "没搜到" in presented[0]


def test_set_city_keeps_current_first_and_saves(_app):
    host = _make_host({"weather_enabled": True, "weather_city": "汕头",
                       "weather_city_list": ["汕头", "上海"]})
    assert svc.set_city(host, "上海") == "上海"
    assert svc.cities(host) == ["上海", "汕头"]
    assert host.cfg.saved == 1                 # 菜单点一下就该落盘


def test_remove_city_promotes_next_and_keeps_one(_app):
    host = _make_host({"weather_enabled": True, "weather_city": "汕头",
                       "weather_city_list": ["汕头", "上海"]})
    svc.remove_city(host, "汕头")
    assert svc.current_city(host) == "上海"
    assert svc.cities(host) == ["上海"]


def test_present_goes_through_alert_queue(_app, monkeypatch):
    from pet import window_alerts

    host = _make_host()
    seen: dict = {}
    monkeypatch.setattr(
        window_alerts, "show_alert",
        lambda h, text, **kw: seen.update({"text": text, **kw}),
    )
    svc._present(host, "汕头今天 26°，天气阴")
    assert seen["text"] == "汕头今天 26°，天气阴"
    assert seen["alert_type"] == "weather"     # 免打扰期间会被压住并攒着
    assert seen["sticky"] is False


def test_weather_cities_submenu_lists_cities_then_actions(_app):
    """菜单结构契约：先列城市（当前城市打勾），再是搜索/手填/删除三条。"""
    from PySide6.QtWidgets import QMenu

    from pet.context_menus import registry as registry_mod

    host = _make_host({"weather_enabled": True, "weather_city": "上海",
                       "weather_city_list": ["汕头", "上海"]})
    menu = QMenu()
    registry_mod.MenuActionRegistry()._specs["weather_cities"].build(menu, host)

    submenu = menu.actions()[0].menu()
    assert [action.text() for action in submenu.actions() if not action.isSeparator()] == [
        "上海", "汕头", "添加城市（联网搜索）…", "手动输入城市…", "删掉一个城市…",
    ]
    checked = [action.text() for action in submenu.actions() if action.isChecked()]
    assert checked == ["上海"]
