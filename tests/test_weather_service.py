# -*- coding: utf-8 -*-
"""天气服务：后台取数 + 冒泡落地（失败口径、忙门、线程边界）。

分层与用例口径：
- 取数在**后台线程**（联网是慢操作，绝不在 GUI 线程），结果经 queued 信号回
  GUI 线程冒泡——用例用 ``run_async`` 注入同步执行器把这条链路变成确定性的，
  另外**单独一条**用真实线程验证「默认执行器不在 GUI 线程上跑取数」；
- 气泡走宿主既有的 ``show_bubble``（与余额/节日同一通道，天然落在提醒队列与
  免打扰门控之内），所以断言点就是"宿主收到了什么文案"；
- 不真发外网请求：``client`` 全程注入假客户端。
"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pet import weather as W  # noqa: E402
from pet.weather.service import WeatherService  # noqa: E402

app = QApplication.instance() or QApplication([])


class _Host:
    """气泡宿主替身：只记 ``show_bubble`` 调用（真实宿主是 PetWindow / OverlayShell）。"""

    def __init__(self, visible=True):
        self.bubbles: list[tuple[str, int]] = []
        self._visible = visible

    def isVisible(self) -> bool:  # noqa: N802 - Qt 形接口
        return self._visible

    def show_bubble(self, text, duration_ms=3200, **_kwargs):
        self.bubbles.append((str(text), int(duration_ms)))

    def texts(self) -> list[str]:
        return [text for text, _ in self.bubbles]


class _App:
    """AppShell 替身：只需 ``config`` 与 ``win``。"""

    def __init__(self, values, win=None):
        self.config = dict(values)
        self.win = win


class _Reading:
    """可注入取数器：记录调用线程/城市/地区码，按需返回或抛错。"""

    def __init__(self, reading=None, error=None):
        self.reading = reading
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.codes: list[str] = []

    def fetch(self, city, *, code="", deadline=None):
        self.calls.append((city, threading.current_thread().name))
        self.codes.append(code)
        if self.error is not None:
            raise self.error
        return self.reading


def _reading(**overrides):
    data = dict(city="鹿城", condition="晴", temperature="16.6℃", wind="北风 1级",
                humidity="67%", source=W.SOURCE_CN)
    data.update(overrides)
    return W.WeatherReading(**data)


def _pump_until(predicate, timeout=5.0):
    """宽预算轮询：让 queued 信号在 GUI 线程落地（不赌固定 sleep）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _service(values, *, client=None, run_async=None):
    host = _Host()
    shell = _App(values, win=host)
    kwargs = {"client": client}
    if run_async is not None:
        kwargs["run_async"] = run_async
    return WeatherService(shell, **kwargs), host


def _sync(work):
    work()
    return None


# ---------------------------------------------------------------- 无城市 / 已关闭
def test_no_city_bubbles_guidance_without_any_request():
    client = _Reading(_reading())
    service, host = _service({"weather_city": "", "weather_city_list": []},
                             client=client, run_async=_sync)
    service.show_weather(host)
    assert host.texts() == [W.NO_CITY_TEXT]
    assert client.calls == []


def test_disabled_switch_bubbles_and_never_fetches():
    client = _Reading(_reading())
    service, host = _service({"weather_enabled": False, "weather_city": "鹿城"},
                             client=client, run_async=_sync)
    service.show_weather(host)
    assert len(host.texts()) == 1 and "关闭" in host.texts()[0]
    assert client.calls == []


# ---------------------------------------------------------------- 成功 / 失败
def test_success_bubbles_the_reading_text():
    client = _Reading(_reading())
    service, host = _service({"weather_city": "鹿城"}, client=client, run_async=_sync)
    service.show_weather(host)
    assert client.calls[0][0] == "鹿城"
    assert host.texts() == ["鹿城 晴 16.6℃，北风 1级，湿度 67%（中国天气网）"]
    assert host.bubbles[0][1] == WeatherService.BUBBLE_DURATION_MS


def test_city_falls_back_to_the_first_listed_city():
    client = _Reading(_reading(city="北京", temperature="9.7℃"))
    service, host = _service({"weather_city": "", "weather_city_list": ["北京", "上海"]},
                             client=client, run_async=_sync)
    service.show_weather(host)
    assert client.calls[0][0] == "北京"
    assert host.texts()[0].startswith("北京 晴 9.7℃")


def test_both_sources_failing_bubbles_an_honest_failure_and_is_not_silent():
    error = W.WeatherError(
        "天气获取失败（鹿城）：中国天气网：请求超时；open-meteo：没有找到这个城市",
        ((W.SOURCE_CN, "请求超时"), (W.SOURCE_OPEN_METEO, "没有找到这个城市")),
    )
    client = _Reading(error=error)
    service, host = _service({"weather_city": "鹿城"}, client=client, run_async=_sync)
    service.show_weather(host)
    assert len(host.texts()) == 1
    assert "天气获取失败" in host.texts()[0]
    assert W.SOURCE_CN in host.texts()[0] and W.SOURCE_OPEN_METEO in host.texts()[0]
    assert host.bubbles[0][1] == WeatherService.FAILURE_DURATION_MS


def test_unexpected_exception_still_bubbles_a_failure_line():
    client = _Reading(error=RuntimeError("boom"))
    service, host = _service({"weather_city": "鹿城"}, client=client, run_async=_sync)
    service.show_weather(host)
    assert "天气获取失败" in host.texts()[0]


# ---------------------------------------------------------------- 线程与忙门
def test_rapid_clicks_do_not_start_a_second_fetch():
    pending: list = []
    client = _Reading(_reading())
    service, host = _service({"weather_city": "鹿城"}, client=client,
                             run_async=lambda work: pending.append(work))
    service.show_weather(host)
    service.show_weather(host)
    assert len(pending) == 1, "连点两次只允许一次取数"
    pending[0]()
    # 上一次收尾后可以再次取数
    service.show_weather(host)
    assert len(pending) == 2


def test_guidance_bubble_does_not_release_an_inflight_fetch():
    """清空城市时给引导气泡，但不能把正在飞的那次取数的忙门一起放掉。"""
    pending: list = []
    client = _Reading(_reading())
    host = _Host()
    shell = _App({"weather_city": "鹿城"}, win=host)
    service = WeatherService(shell, client=client,
                             run_async=lambda work: pending.append(work))
    service.show_weather(host)
    shell.config["weather_city"] = ""
    service.show_weather(host)
    assert host.texts()[-1] == W.NO_CITY_TEXT
    shell.config["weather_city"] = "鹿城"
    service.show_weather(host)
    assert len(pending) == 1, "上一次取数还在飞，不该再起一次"


def test_default_executor_fetches_off_the_gui_thread():
    done = threading.Event()
    seen: dict = {}

    class _ThreadProbe(_Reading):
        def fetch(self, city, *, code="", deadline=None):
            self.calls.append((city, threading.current_thread().name))
            seen["thread"] = threading.current_thread()
            done.set()
            return _reading()

    client = _ThreadProbe()
    service, host = _service({"weather_city": "鹿城"}, client=client)
    service.show_weather(host)
    assert done.wait(5.0), "取数线程必须在预算内跑起来"
    assert seen["thread"] is not threading.main_thread()
    assert seen["thread"].name == "pet-weather"
    assert _pump_until(lambda: bool(host.bubbles)), "结果必须回 GUI 线程冒泡"
    assert "鹿城 晴 16.6℃" in host.texts()[0]


def test_bubble_is_skipped_when_the_host_is_already_destroyed():
    """异步回调可能晚于窗口销毁（切角色/退出）：先探活再触碰 Qt 对象。

    回归的是余额桥那一族的崩溃（``_show`` 里直接 ``win.show_bubble``）：迟到的
    queued 回调撞上已销毁的宿主会直接段错误，而不是抛异常。
    """
    import shiboken6
    from PySide6.QtWidgets import QWidget

    class _WidgetHost(QWidget):
        def __init__(self):
            super().__init__()
            self.bubbles: list[str] = []

        def show_bubble(self, text, duration_ms=3200, **_kwargs):
            self.bubbles.append(str(text))

    host = _WidgetHost()
    client = _Reading(_reading())
    service = WeatherService(_App({"weather_city": "鹿城"}, win=host), client=client)
    service.show_weather(host)
    bridge = service._bridge
    shiboken6.delete(host)
    bridge.done.emit(True, "鹿城 晴 16.6℃")  # 迟到回调：不得触碰已销毁对象


def test_late_result_after_host_destroyed_releases_the_busy_gate():
    """M6：宿主销毁也必须结算请求——否则忙门永锁，换新宿主后点不动。"""
    import shiboken6
    from PySide6.QtWidgets import QWidget

    class _WidgetHost(QWidget):
        def __init__(self):
            super().__init__()
            self.bubbles: list[str] = []

        def show_bubble(self, text, duration_ms=3200, **_kwargs):
            self.bubbles.append(str(text))

    pending: list = []
    client = _Reading(_reading())
    host = _WidgetHost()
    service = WeatherService(_App({"weather_city": "鹿城"}, win=host), client=client,
                             run_async=lambda work: pending.append(work))
    service.show_weather(host)
    bridge = service._bridge
    shiboken6.delete(host)
    bridge.done.emit(False, "天气获取失败：宿主没了")     # 结果回来时宿主已死

    assert service._busy is False, "请求已结束，忙门必须释放"
    assert service._bridge is None, "旧 bridge 必须断开释放"

    new_host = _WidgetHost()
    service.show_weather(new_host)
    assert len(pending) == 2, "换新宿主后必须还能再发起取数"
    pending[1]()
    assert new_host.bubbles == ["鹿城 晴 16.6℃，北风 1级，湿度 67%（中国天气网）"]


def test_thread_start_failure_also_releases_the_busy_gate():
    client = _Reading(_reading())
    host = _Host()
    shell = _App({"weather_city": "鹿城"}, win=host)

    def _boom(_work):
        raise RuntimeError("no threads left")

    service = WeatherService(shell, client=client, run_async=_boom)
    service.show_weather(host)
    assert service._busy is False
    assert "天气获取失败" in host.texts()[-1]


def test_known_area_code_is_passed_to_the_client():
    """m1 落到服务层：配置里的地区码要一路传到取数客户端（同名城市靠它认人）。"""
    client = _Reading(_reading())
    host = _Host()
    shell = _App({
        "weather_city": "朝阳 · 辽宁",
        "weather_city_list": ["朝阳 · 辽宁"],
        "weather_city_codes": {"朝阳 · 辽宁": "101071201"},
    }, win=host)
    service = WeatherService(shell, client=client, run_async=_sync)
    service.show_weather(host)
    assert client.calls[0][0] == "朝阳 · 辽宁"
    assert client.codes == ["101071201"]


def test_client_is_created_lazily_and_reused():
    service, _host = _service({"weather_city": ""})
    first = service.client()
    assert first is service.client()
    assert isinstance(first, W.WeatherClient)
