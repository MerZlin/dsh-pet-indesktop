"""批6-6：Chat 双 UI 共享行为层（geometry.py / utils.py）纯函数测试。

覆盖 R6 验收「共享纯函数测试」的确定性部分：
- _short_title：空会话/自定义标题/长标题截断/空白压缩/坏时间戳；
  批10 产品修复后 Modern 与 Legacy 调用点均走默认 True（本地时间显示），
  纯函数保留 localize_time=False 路径（历史行为/API 兼容）由本文件钉住。
- 定位：四个屏幕边缘、超大窗口/小屏 available geometry、clamp 与 overlap 选择。

定位纯函数只操作 QtCore 值类型（QRect/QPoint/QSize），无需 QApplication。
"""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize

from pet.chat.geometry import best_position_near_pet, candidate_points_near_pet, clamp_point
from pet.chat.models import ChatMessage, ChatSession
from pet.chat.utils import _short_title

AVAILABLE = QRect(0, 0, 1280, 800)


def _session(created_at: str = "2026-08-27T01:05:00+00:00", **kwargs) -> ChatSession:
    return ChatSession("id", "shenshen", "provider", "", created_at=created_at, **kwargs)


# ---------- _short_title ----------


def test_short_title_custom_title_wins_and_strips():
    session = _session(custom_title="  自定义标题  ")
    assert _short_title(session) == "自定义标题"


def test_short_title_blank_custom_title_falls_through():
    session = _session(custom_title="   ")
    session.messages = [ChatMessage("user", "第一条用户消息")]
    assert _short_title(session) == "第一条用户消息"


def test_short_title_uses_first_user_message_and_compresses_whitespace():
    session = _session()
    session.messages = [ChatMessage("assistant", "助手消息不应采用"), ChatMessage("user", "多   个    空格")]
    assert _short_title(session) == "多 个 空格"


def test_short_title_truncates_long_user_message_to_24_chars():
    session = _session()
    long_text = "这是一段很长很长很长的用户消息超过二十四个字符需要截断"
    session.messages = [ChatMessage("user", f"  {long_text}  ")]
    title = _short_title(session)
    assert title.endswith("…")
    assert len(title) == 25  # 24 字符 + …
    assert title[:-1] == " ".join(session.messages[0].content.split())[:24]


def test_short_title_fallback_new_session_modern_converts_to_local():
    session = _session(created_at="2026-08-27T01:05:00+00:00")
    expected_local = datetime.fromisoformat("2026-08-27T01:05:00+00:00").astimezone().strftime("%H:%M")
    assert _short_title(session) == f"新会话 · {expected_local}"


def test_short_title_fallback_legacy_false_path_keeps_stored_wall_clock():
    session = _session(created_at="2026-08-27T01:05:00+00:00")
    # 纯函数 localize_time=False 路径（批10 前 Legacy 的历史行为，现无生产调用方，
    # 作为 API 兼容路径钉住）：不做本地时区转换，按 UTC 存储钟点原样显示。
    assert _short_title(session, localize_time=False) == "新会话 · 01:05"


def test_short_title_naive_timestamp_same_for_both_modes():
    session = _session(created_at="2026-08-27T09:05:00")
    assert _short_title(session) == "新会话 · 09:05"
    assert _short_title(session, localize_time=False) == "新会话 · 09:05"


def test_short_title_bad_timestamp_falls_back():
    assert _short_title(_session(created_at="not-a-date")) == "新会话"
    assert _short_title(_session(created_at=None)) == "新会话"


def test_short_title_empty_session():
    assert _short_title(_session(created_at="2026-08-27T09:05:00")) == "新会话 · 09:05"


# ---------- 定位：candidate_points_near_pet ----------


def test_candidate_points_order_right_left_below_above():
    pet = QRect(100, 100, 200, 100)
    size = QSize(300, 400)
    points = candidate_points_near_pet(pet, size, gap=10)
    assert len(points) == 4
    assert points[0] == QPoint(pet.right() + 11, pet.center().y() - size.height() // 2)
    assert points[1] == QPoint(pet.left() - size.width() - 10, pet.center().y() - size.height() // 2)
    assert points[2] == QPoint(pet.center().x() - size.width() // 2, pet.bottom() + 11)
    assert points[3] == QPoint(pet.center().x() - size.width() // 2, pet.top() - size.height() - 10)


def test_clamp_point_stays_inside_available():
    available = QRect(100, 50, 800, 600)
    assert clamp_point(QPoint(50, 20), QSize(360, 520), available) == QPoint(100, 50)
    assert clamp_point(QPoint(5000, 5000), QSize(360, 520), available) == QPoint(540, 130)
    # y=200 时窗口底边 719 > 649 → clamp 到 130（底边恰为 649）
    assert clamp_point(QPoint(300, 200), QSize(360, 520), available) == QPoint(300, 130)


# ---------- 定位：best_position_near_pet ----------


def test_side_placement_prefers_right_when_pet_is_centered():
    pet = QRect(540, 320, 200, 160)
    size = QSize(360, 520)
    assert best_position_near_pet(pet, size, AVAILABLE) == QPoint(
        pet.right() + 14 + 1, pet.center().y() - size.height() // 2
    )


def test_side_placement_moves_to_left_of_pet_at_right_edge():
    pet = QRect(1060, 300, 120, 140)
    size = QSize(360, 520)
    point = best_position_near_pet(pet, size, AVAILABLE)
    # 右候选越界（pet.right()+15+360 > 1280），左候选完整落位。
    assert point == QPoint(pet.left() - size.width() - 14, pet.center().y() - size.height() // 2)


def test_side_placement_moves_right_of_pet_at_left_edge():
    pet = QRect(0, 300, 120, 140)
    size = QSize(360, 520)
    assert best_position_near_pet(pet, size, AVAILABLE) == QPoint(
        pet.right() + 14 + 1, pet.center().y() - size.height() // 2
    )


def test_side_placement_moves_above_pet_at_bottom_edge():
    pet = QRect(500, 640, 200, 160)
    size = QSize(360, 520)
    # 右侧/左侧候选的 y 落点窗口底边超出工作区，下方候选也越界 → 上方候选胜出。
    assert best_position_near_pet(pet, size, AVAILABLE) == QPoint(
        pet.center().x() - size.width() // 2, pet.top() - size.height() - 14
    )


def test_side_placement_moves_below_pet_at_top_edge():
    pet = QRect(500, 0, 200, 100)
    size = QSize(360, 520)
    assert best_position_near_pet(pet, size, AVAILABLE) == QPoint(
        pet.center().x() - size.width() // 2, pet.bottom() + 14 + 1
    )


def test_clamp_fallback_minimizes_overlap_when_window_too_tall():
    available = QRect(0, 0, 800, 600)
    pet = QRect(700, 240, 100, 120)  # 贴右边缘
    size = QSize(500, 700)  # 比工作区高 → 任何候选都无法完全放入
    point = best_position_near_pet(pet, size, available)
    # 返回的左上角必须 clamp 在可用工作区内。
    assert available.contains(point)
    # 历史实现的选择：左候选 clamp 后与宠物重叠为 0，胜过其余候选。
    assert point == QPoint(186, 0)
    assert point.x() + size.width() - 1 < pet.left()  # 不压到宠物


def test_clamp_fallback_tiny_available_stays_at_corner():
    available = QRect(0, 0, 200, 100)
    pet = QRect(40, 20, 60, 40)
    point = best_position_near_pet(pet, QSize(400, 300), available)
    assert point == QPoint(0, 0)


# ---------- 调用点契约（批6-6 盲审 P2，源码轻量断言） ----------
# 钉的是调用契约而非行为：批10 产品修复后 Legacy 与 Modern 调用点统一走默认
# localize_time=True（本地时间显示，产品 bug 修复）；纯函数保留 False 路径
# 供 API 兼容。这两类断言是本批例外允许的源码断言——直接断言 UI 调用点，
# 防止未来重构时漏传/错传该旗标（纯函数行为测试无法覆盖调用点接线）。


def _chat_src(module_name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "pet" / "chat" / f"{module_name}.py").read_text(encoding="utf-8")


def test_legacy_call_sites_use_default_localize_time_true():
    """Legacy（legacy_widgets）所有 _short_title 调用点不得传 localize_time，
    走默认 True（批10 产品修复：与 Modern 一致显示本地时间）。"""
    calls = [ln for ln in _chat_src("legacy_widgets").splitlines() if "_short_title(" in ln]
    assert calls, "legacy_widgets.py 应存在 _short_title 调用点"
    for ln in calls:
        assert "localize_time" not in ln, f"Legacy 调用点不得传 localize_time（走默认 True）: {ln.strip()}"
    # 默认值本身为 True（本地时间显示，见 utils.py 签名）。
    assert "def _short_title(session, *, localize_time: bool = True)" in _chat_src("utils")


def test_modern_call_sites_use_default_localize_time_true():
    """Modern（widgets）所有 _short_title 调用点不得传 localize_time，走默认 True。"""
    calls = [ln for ln in _chat_src("widgets").splitlines() if "_short_title(" in ln]
    assert calls, "widgets.py 应存在 _short_title 调用点"
    for ln in calls:
        assert "localize_time" not in ln, f"Modern 调用点不得传 localize_time（走默认 True）: {ln.strip()}"
    # 默认值本身为 True（Modern 历史行为，见 utils.py 签名）。
    assert "def _short_title(session, *, localize_time: bool = True)" in _chat_src("utils")
