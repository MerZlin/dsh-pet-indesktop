import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QRect
from pet.speech_bubble import PetSpeechBubble


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("style", ["classic_top", "breath_bubble"])
@pytest.mark.parametrize("scale", [1, 2, 3])
def test_card_layout(app, style, scale):
    b = PetSpeechBubble(style_id=style)
    b.set_text_scale(scale)
    b.show_text("检查布局与文字截断。" * 60, QRect(300, 400, 100, 100), agent_title="一个很长的会话名称" * 8, agent_status="运行命令")
    app.processEvents()
    card = b._agent_content
    assert card.isVisible() and not b.label.isVisible()
    assert len(card.lines) == 2 and card.overflow
    assert card.lines[-1].endswith("…")
    assert b.rect().contains(card.geometry())
    assert not b._pages
    b.show_text("普通消息", QRect(300, 400, 100, 100))
    app.processEvents()
    assert not card.isVisible() and b.label.isVisible()
    b.close()


def test_short_final_and_plain_restore(app):
    b = PetSpeechBubble()
    anchor = QRect(300, 400, 100, 100)
    b.show_text("短消息", anchor, agent_title="桌宠开发", agent_status="思考中")
    short = b.height()
    assert not b._agent_content.overflow
    b.show_text("long command /very/long/path " + "text " * 100, anchor, agent_title="桌宠开发", agent_status="回复结束", agent_final=True)
    assert b.height() > short
    assert len(b._agent_content.lines) == 3
    b.show_text("普通消息", anchor)
    app.processEvents()
    fresh = PetSpeechBubble()
    fresh.show_text("普通消息", anchor)
    app.processEvents()
    assert b.size() == fresh.size()
    b.close()
    fresh.close()


def test_readable_body_line_spacing(app):
    from PySide6.QtGui import QFontMetrics

    b = PetSpeechBubble()
    b.show_text("验证正文行间距与末行完整显示。" * 20, QRect(300, 400, 100, 100), agent_title="桌宠开发", agent_status="思考中", agent_final=True)
    card = b._agent_content
    fm = QFontMetrics(card.body_font)
    assert card.line_height >= fm.lineSpacing() * 1.25
    assert card.gap >= 8
    assert card.height() >= card.body_top + len(card.lines) * card.line_height
    b.close()


from types import SimpleNamespace
from pet.agent_link import AgentLinkManager
from pet.config import Config
from pet.multi_window_shared import MultiWindowProxy
from tests.test_sprite_menu_facade import _make_shell
from tests.codex_helpers import poll_until


def test_real_shared_overlay_agent_bubble(tmp_path):
    app = QApplication.instance() or QApplication([])
    shell, lib = _make_shell(tmp_path)
    bubble = PetSpeechBubble()
    shell._bubble_follower.bubble = bubble
    cfg = Config(base=tmp_path / "cfg")
    proxy = MultiWindowProxy(SimpleNamespace(config=cfg, _overlay_shell=shell))
    manager = AgentLinkManager(proxy, cfg, min_interval=0)
    gen = manager.monitors["codex"]._emit_gen
    detail = dict(key="turn", title="桌宠开发", text="第一条进展", kind="commentary", state="thinking", activity="思考中")
    summary = dict(busy=1, thinking=1, waiting=0, unknown=0, tool="", ended=[], detail=detail)
    try:
        shell.overlay.show()
        manager._on_session_snapshot("codex", summary, gen)
        poll_until(lambda: bubble._agent_data is not None)
        assert bubble._agent_data[:3] == ("桌宠开发", "思考中", "第一条进展")
        assert proxy._speech_bubble is bubble
        manager._on_session_snapshot("codex", dict(summary, detail=dict(detail, text="最新进展")), gen)
        poll_until(lambda: bubble._agent_data[2] == "最新进展")
        # Activity changes must not replay the cached commentary, including
        # when another session temporarily takes over the bubble.
        for key, state, activity, text, expected in (
            ("turn", "working", "运行命令", "最新进展", ""),
            ("other-turn", "thinking", "思考中", "另一会话", "另一会话"),
            ("turn", "thinking", "思考中", "最新进展", ""),
            ("turn", "working", "读取文件", "新的回复", "新的回复"),
            ("next-turn", "thinking", "思考中", "新的回复", "新的回复"),
        ):
            updated = dict(detail, key=key, state=state, activity=activity, text=text)
            manager._on_session_snapshot("codex", dict(summary, detail=updated), gen)
            poll_until(lambda: manager._snapshot_bubble_last["codex"][0] == updated)
            assert bubble._agent_data[:3] == ("桌宠开发", activity, expected)
        # Final replies remain visible even when identical to commentary.
        manager._on_session_snapshot("codex", dict(
            summary, busy=0, thinking=0, detail={},
            final=dict(key="next-turn", title="桌宠开发", text="新的回复")), gen)
        poll_until(lambda: bubble._agent_data[1] == "回复结束")
        assert bubble._agent_data[2] == "新的回复"
    finally:
        manager.shutdown()
        bubble.close()
        shell.overlay.close()
        shell._delete_runtime_marker()
        app.processEvents()


def test_declined_bubble_does_not_consume_progress(tmp_path):
    app = QApplication.instance() or QApplication([])
    shell, lib = _make_shell(tmp_path)
    cfg = Config(base=tmp_path / 'cfg')
    proxy = MultiWindowProxy(SimpleNamespace(config=cfg, _overlay_shell=shell))
    manager = AgentLinkManager(proxy, cfg, min_interval=0)
    try:
        shell.overlay.show()
        shell._bubble_suppressed = True
        assert proxy.show_bubble('尚未显示', agent_title='开发') is False
        assert manager._show_link_bubble(
            '尚未显示', important=False,
            agent_content=dict(title='开发', status='思考中', body='尚未显示')) is False
    finally:
        manager.shutdown()
        shell.overlay.close()
        shell._delete_runtime_marker()
        app.processEvents()


def test_bubble_anchor_tracks_actual_overlay_position(tmp_path):
    shell, lib = _make_shell(tmp_path)
    try:
        shell.overlay.move(100, 50)
        follower = shell._bubble_follower
        assert follower.anchor().topLeft() == shell.overlay.mapToGlobal(shell.sprite.body_rect().topLeft())
    finally:
        shell.overlay.close()
        shell._delete_runtime_marker()


def test_status_only_reflow_uses_current_text_scale(app):
    bubble = PetSpeechBubble()
    anchor = QRect(300, 400, 100, 100)
    try:
        bubble.show_text('', anchor, agent_title='开发', agent_status='思考中')
        old_width = bubble.width()
        bubble.set_text_scale(2)
        bubble.reflow(anchor)
        app.processEvents()
        assert bubble.width() > old_width
        assert bubble.rect().contains(bubble._agent_content.geometry())
    finally:
        bubble.close()
