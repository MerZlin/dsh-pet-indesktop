# -*- coding: utf-8 -*-
"""DeepSeek 余额查询模块测试。"""

import io
import json
import urllib.error
from datetime import date, datetime, timedelta, timezone

import pytest

from pet.island import balance
from pet.chat.models import ChatSession

_BJ = timezone(timedelta(hours=8))


def _bj(hour: int, day=31, month=8, year=2026, weekday_override=None):
    """构造北京时间 datetime；默认 2026-08-31 是周一。"""
    return datetime(year, month, day, hour, tzinfo=_BJ)


def test_format_balance_variants():
    assert balance.format_balance({"total": "12.34", "granted": "2.34", "topped_up": "10.00"}) == \
        "余额 ¥12.34（充值 ¥10.00 / 赠送 ¥2.34）"
    assert balance.format_balance({"total": "5.00", "granted": "", "topped_up": "5.00"}) == \
        "余额 ¥5.00"
    assert balance.format_balance({"total": "", "granted": "", "topped_up": ""}) == "余额信息为空"


def test_fetch_balance_parses_response(monkeypatch):
    body = json.dumps({
        "is_available": True,
        "balance_infos": [{
            "currency": "CNY",
            "total_balance": "12.34",
            "granted_balance": "2.34",
            "topped_up_balance": "10.00",
        }],
    }).encode()

    def fake_urlopen(req, *args, **kwargs):
        # 校验端点与认证头
        assert req.full_url.endswith("/user/balance")
        assert req.get_header("Authorization") == "Bearer sk-test"
        assert req.get_header("User-agent", "").startswith("Mozilla/")
        assert req.get_header("Accept-language", "") != ""
        return io.BytesIO(body)

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_urlopen)
    info = balance.fetch_balance("https://api.deepseek.com", "sk-test")
    assert info["total"] == "12.34"
    assert info["granted"] == "2.34"
    assert info["topped_up"] == "10.00"
    assert info["is_available"] is True


def test_fetch_balance_errors(monkeypatch):
    # 无 Key
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "")

    # HTTP 错误（如 401）
    def fake_http(req, *args, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_http)
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "sk-x")

    # 网络失败
    def fake_net(req, *args, **kwargs):
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_net)
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "sk-x")

    # 响应无 balance_infos
    def fake_empty(req, *args, **kwargs):
        return io.BytesIO(json.dumps({"is_available": True}).encode())

    monkeypatch.setattr(balance.urllib.request, "urlopen", fake_empty)
    with pytest.raises(balance.BalanceError):
        balance.fetch_balance("https://api.deepseek.com", "sk-x")


def test_fetch_balance_multi_currency_picks_positive_cny(monkeypatch):
    """issue #106：balance_infos 多币种且顺序不保证时，不得盲取首条。"""
    cny = {"currency": "CNY", "total_balance": "66.47",
           "granted_balance": "0.00", "topped_up_balance": "66.47"}
    usd = {"currency": "USD", "total_balance": "0.00",
           "granted_balance": "0.00", "topped_up_balance": "0.00"}

    def run_with(infos):
        body = json.dumps({"is_available": True, "balance_infos": infos}).encode()
        monkeypatch.setattr(balance.urllib.request, "urlopen",
                            lambda req, *a, **k: io.BytesIO(body))
        return balance.fetch_balance("https://api.deepseek.com", "sk-test")

    # USD 排前（复现原 bug：旧实现取 infos[0] 得到 0.00）
    assert run_with([usd, cny])["total"] == "66.47"
    # CNY 排前不回归
    assert run_with([cny, usd])["total"] == "66.47"
    # 全为 0：退回首条
    assert run_with([usd, {**cny, "total_balance": "0.00"}])["total"] == "0.00"
    # 首条非法/非数值：仍选有余额的一条
    bad = {"currency": "USD", "total_balance": None}
    assert run_with([bad, cny])["total"] == "66.47"
    # 非 list 的 balance_infos（单条 dict）原样兼容
    assert run_with(cny)["total"] == "66.47"


def test_balance_percent_and_event_index():
    # 余额 20 元 → 未消耗 0%；10 元 → 50%；0/负数 → 100%；非法 → None
    assert balance.balance_percent("20") == 0
    assert balance.balance_percent("10") == 50
    assert balance.balance_percent("0") == 100
    assert balance.balance_percent("-1") == 100
    assert balance.balance_percent("abc") is None
    assert balance.balance_percent("") is None

    # 档位：0..4 对应 [0,20) [20,40) [40,60) [60,80) [80,100)，100 单独第 5 档
    assert balance.balance_event_index(0) == 0
    assert balance.balance_event_index(19.9) == 0
    assert balance.balance_event_index(20) == 1
    assert balance.balance_event_index(59.9) == 2
    assert balance.balance_event_index(80) == 4
    assert balance.balance_event_index(100) == 5


def test_deepseek_pricing_tier():
    # 2026-08-31 是周一：9-12 / 14-18 高峰，其余空闲
    assert balance.deepseek_pricing_tier(_bj(10)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(11)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(13)) == "idle"
    assert balance.deepseek_pricing_tier(_bj(15)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(20)) == "idle"
    # 周六/周日全天空闲
    assert balance.deepseek_pricing_tier(_bj(10, day=29, month=8, year=2026)) == "idle"
    assert balance.deepseek_pricing_tier(_bj(15, day=29, month=8, year=2026)) == "idle"


def test_deepseek_pricing_hint_and_next_switch():
    hint_peak = balance.deepseek_pricing_hint(_bj(10))
    assert "当前高峰" in hint_peak
    assert "下一空闲 12:00" in hint_peak

    hint_idle_midday = balance.deepseek_pricing_hint(_bj(13))
    assert "当前空闲" in hint_idle_midday
    assert "下一高峰 14:00" in hint_idle_midday

    # 周末全天空闲，下一高峰为周一 09:00
    hint_weekend = balance.deepseek_pricing_hint(_bj(15, day=29, month=8, year=2026))
    assert "当前空闲" in hint_weekend
    assert "下一高峰 下周一 09:00" in hint_weekend


def test_resolve_tier_labels_and_custom_hint():
    # 默认
    assert balance.resolve_tier_labels("default") == ("高峰", "空闲")
    # 梁文
    assert balance.resolve_tier_labels("liangwen") == ("梁文峰", "梁文谷")
    # 自定义，留空回退默认
    assert balance.resolve_tier_labels("custom", "自定义峰", "自定义谷") == ("自定义峰", "自定义谷")
    assert balance.resolve_tier_labels("custom", "", "") == ("高峰", "空闲")

    # 自定义文案会反映到提示里
    hint = balance.deepseek_pricing_hint(
        _bj(10), peak_label="梁文峰", idle_label="梁文谷"
    )
    assert "当前梁文峰" in hint
    assert "下一梁文谷" in hint


def test_deepseek_pricing_hint_html_colors():
    # 默认高峰红、低谷绿，且包含对应文本
    html = balance.deepseek_pricing_hint_html(
        _bj(10), peak_label="高峰", idle_label="空闲"
    )
    assert "#e5484d" in html
    assert "高峰" in html
    assert "空闲" in html
    # 自定义标签会转义，避免破坏 HTML
    html_custom = balance.deepseek_pricing_hint_html(
        _bj(10), peak_label="<峰>", idle_label="谷"
    )
    assert "&lt;峰&gt;" in html_custom


def test_friday_evening_next_peak_skips_weekend():
    # 2026-08-28 是周五，20:00 后下一高峰应为周一 09:00，而不是周六 09:00
    hint = balance.deepseek_pricing_hint(_bj(20, day=28, month=8, year=2026))
    assert "当前空闲" in hint
    assert "下一高峰 下周一 09:00" in hint
    next_tier, next_time = balance._next_pricing_switch(
        _bj(20, day=28, month=8, year=2026)
    )
    assert next_tier == "peak"
    assert next_time.weekday() == 0  # Monday
    assert next_time.hour == 9


# ------------------------------------------------- 法定节假日峰谷（issue #217）
# 官方口径（DeepSeek 开放平台）：高峰 = 北京时间周一至周五（**不含中国法定
# 节假日**）9-12 / 14-18；周末与法定节假日全天空闲。调休补班的周六/周日按
# 官方口径仍是空闲——不是工作日高峰。


def test_statutory_holiday_name_reports_free_days_only():
    """festival_calendar 的节假日边界：只认放假日，补班日与表外日期返回 None。"""
    from pet.festival.calendar import statutory_holiday_name

    assert statutory_holiday_name(date(2026, 10, 1)) == "国庆节"
    assert statutory_holiday_name(date(2026, 2, 16)) == "春节"
    # 调休补班的周六（2026-10-10）是上班日，不属于放假日
    assert statutory_holiday_name(date(2026, 10, 10)) is None
    # 内嵌安排表之外（2026-10-11 起）降级为 None = 「未知」，不是「确认上班」
    assert statutory_holiday_name(date(2027, 1, 1)) is None


def test_statutory_holidays_are_idle_all_day():
    """法定节假日全天空闲：工作日高峰时段（9-12 / 14-18）也判空闲。"""
    for day in (date(2026, 10, 1), date(2026, 10, 5),
                date(2026, 6, 19), date(2026, 2, 16)):
        assert day.weekday() < 5, f"{day} 应是工作日，否则本用例不构成反向闸门"
        for hour in (10, 15):
            assert balance.deepseek_pricing_tier(
                _bj(hour, day=day.day, month=day.month, year=day.year)
            ) == "idle"
    # 反向闸门：假期外的相邻工作日仍是高峰
    assert balance.deepseek_pricing_tier(_bj(10, day=8, month=10, year=2026)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(10, day=9, month=10, year=2026)) == "peak"


def test_makeup_workdays_on_weekend_stay_idle():
    """调休补班的周六/周日仍空闲（官方口径，与「补班即高峰」的建议相反）。"""
    assert date(2026, 10, 10).weekday() == 5  # 周六补班
    assert date(2026, 9, 20).weekday() == 6   # 周日补班
    for day in (date(2026, 10, 10), date(2026, 9, 20)):
        for hour in (10, 15):
            assert balance.deepseek_pricing_tier(
                _bj(hour, day=day.day, month=day.month, year=day.year)
            ) == "idle"


def test_holiday_next_peak_scans_past_whole_holiday():
    """国庆假期中（10-06 10:00）下一高峰是假期结束后的第一个工作日 09:00。

    10-06（周二）/ 10-07（周三）仍是国庆假期，故 10-08（周四）09:00 才是高峰；
    按 09/12/14/18 点边界逐点扫描才能跨过整段假期。
    """
    tier, when = balance._next_pricing_switch(_bj(10, day=6, month=10, year=2026))
    assert tier == "peak"
    assert when.date() == date(2026, 10, 8)
    assert (when.hour, when.minute) == (9, 0)

    hint = balance.deepseek_pricing_hint(_bj(10, day=6, month=10, year=2026))
    assert "当前空闲" in hint
    assert "下一高峰 周四 09:00" in hint


def test_format_switch_time_same_iso_week_uses_bare_weekday():
    """跨天文案按 ISO 周判定：同周用「周四」，跨周才用「下周一」。"""
    # 周二 -> 周四：同一 ISO 周
    assert balance.format_switch_time(
        _bj(10, day=6, month=10, year=2026), _bj(9, day=8, month=10, year=2026)
    ) == "周四 09:00"
    # 周五 20:00 -> 下周一 09:00：跨 ISO 周，保持「下周一」
    assert balance.format_switch_time(
        _bj(20, day=28, month=8, year=2026), _bj(9, day=31, month=8, year=2026)
    ) == "下周一 09:00"
    # 周六 -> 下周一：跨周（周末不是「同周周一」）
    assert balance.format_switch_time(
        _bj(15, day=29, month=8, year=2026), _bj(9, day=31, month=8, year=2026)
    ) == "下周一 09:00"


def test_pricing_degrades_without_holiday_calendar_data():
    """2027 起 HolidayUtil 无内嵌数据：不抛异常，退回星期规则。"""
    # 2027-01-01 是周五，表外日期按工作日高峰判
    assert balance.deepseek_pricing_tier(_bj(10, day=1, month=1, year=2027)) == "peak"
    assert balance.deepseek_pricing_tier(_bj(13, day=1, month=1, year=2027)) == "idle"
    # 周末规则与表无关
    assert balance.deepseek_pricing_tier(_bj(10, day=2, month=1, year=2027)) == "idle"
    # 切换扫描同样可用（不因表耗尽而抛错）
    tier, when = balance._next_pricing_switch(_bj(10, day=1, month=1, year=2027))
    assert tier == "idle"
    assert when.date() == date(2027, 1, 1)


def test_chat_session_title_roundtrip():
    session = ChatSession.create("cat", "provider", "prompt")
    assert session.title == ""
    session.title = "自定义备注"
    loaded = ChatSession.from_dict(session.to_dict())
    assert loaded.title == "自定义备注"
    # 旧数据无 title 字段 → 默认空串
    data = session.to_dict()
    data.pop("title")
    assert ChatSession.from_dict(data).title == ""


def test_balance_worker_start_failure_never_leaves_busy(monkeypatch, tmp_path):
    from PySide6.QtWidgets import QApplication
    from pet.app import PetApp
    from pet.config import Config
    app = QApplication.instance() or QApplication([])
    owner = PetApp(app, Config(base=tmp_path))
    owner.win = type("Win", (), {
        "isVisible": lambda self: True,
        # 线程启动失败路径会排队 singleShot(done.emit(错误文案))：
        # 弹窗桩方法必须存在，且队列必须在本测试内清空（见结尾 processEvents），
        # 否则事件泄漏到下一个测试的 processEvents 里引爆（Win 桩无 show_bubble）。
        "show_bubble": lambda self, *a, **k: None,
        "show_alert": lambda self, *a, **k: None,
    })()
    monkeypatch.setattr(owner, "_read_balance_file_cache", lambda *_: None)
    class Settings:
        active_config = type("Provider", (), {"id":"x", "base_url":"https://x", "api_key":"k", "verify_ssl":True})()
    monkeypatch.setattr(owner.config, "chat_settings", lambda: Settings())
    monkeypatch.setattr(owner.config, "resolve_api_key", lambda p: "k")
    class BrokenThread:
        def __init__(self, *a, **k): pass
        def start(self): raise RuntimeError("cannot start")
    monkeypatch.setattr("pet.app.threading.Thread", BrokenThread)
    owner.show_balance(owner.win)
    assert owner._balance_busy is False
    # 清空本测试排队的 singleShot(done.emit(...))，不泄漏给下一个测试
    app.processEvents()


def test_format_balance_display_joins_amounts_without_changing_visible_text():
    """显示态把「余额/充值/赠送 + 金额」焊成一段；明文、空格和再次调用都不变。"""
    raw = balance.format_balance(
        {"total": "12.34", "granted": "2.34", "topped_up": "10.00"},
    )
    assert "\u2060" not in raw
    shown = balance.format_balance_display(raw)
    assert shown.replace("\u2060", "") == raw
    assert "\u00a0" not in shown
    assert balance.format_balance_display(shown) == shown
    for phrase in ("余额 ¥12.34", "充值 ¥10.00", "赠送 ¥2.34"):
        assert "\u2060".join(phrase) in shown
    # 人设前缀里的「余额」不是金额短语，不能跟着被焊上。
    wrapped = balance.format_balance_display(f"当前余额：{raw}")
    assert wrapped.replace("\u2060", "") == f"当前余额：{raw}"
    assert "\u2060" not in wrapped.split("：", 1)[0]


def test_format_balance_display_amounts_and_unmatched_text():
    cases = (
        "余额 ¥0",
        "余额 ¥0.00",
        "余额 ¥-0",
        "余额 ¥-0.00",
        "赠送 ¥-2.50",
        "充值 -¥3",
        "余额 ¥1234567890123.99",
        "余额¥5.99",
        "余额：¥5.99",
        "充值: ¥10.5",
        "余额  ¥8",
    )
    for raw in cases:
        shown = balance.format_balance_display(raw)
        assert shown.replace("\u2060", "") == raw, raw
        assert shown != raw
        assert balance.format_balance_display(shown) == shown
    for raw in ("", "余额信息为空", "赠送一份礼物", "余额情况很好", "今天天气不错", "让我看看余额…"):
        assert balance.format_balance_display(raw) == raw


def test_menu_balance_action_calls_bound_window_callback():
    from PySide6.QtWidgets import QApplication, QMenu
    from pet.context_menus.shared import add_balance
    app = QApplication.instance() or QApplication([])
    calls = []
    pet = type("Pet", (), {"on_show_balance": lambda self, parent=None: calls.append(parent)})()
    menu = QMenu()
    action = add_balance(menu, pet, icons=False)
    assert action is not None
    action.trigger()
    app.processEvents()
    assert calls == [pet]
