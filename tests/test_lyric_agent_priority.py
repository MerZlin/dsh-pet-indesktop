"""Lyrics are passive; real overlay + lyric + Agent Link retain priority."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from pet.agent_link import AgentLinkManager
from pet.config import Config
from pet.multi_window_shared import MultiWindowProxy
from pet.music.lyric_controller import MusicLyricController
from tests.test_sprite_menu_facade import _make_shell

app = QApplication.instance() or QApplication([])


@pytest.fixture
def pipeline(tmp_path):
    shell, _lib = _make_shell(tmp_path / 'sprite')
    shell.overlay.show()
    cfg = Config(base=tmp_path / 'manager')
    cfg.data['agent_link'] = {'custom_agents': [
        {'key': 'hermes', 'name': 'Hermes', 'path': str(tmp_path / 'events.jsonl')},
    ]}
    proxy = MultiWindowProxy(SimpleNamespace(config=cfg, _overlay_shell=shell))
    manager = AgentLinkManager(proxy, cfg, min_interval=0)
    lyrics = MusicLyricController(shell)
    lyrics._title_line = '我在唱《测试歌曲》'
    yield shell, manager, lyrics
    manager.shutdown()
    lyrics._timer.stop()
    shell.overlay.close()
    shell._delete_runtime_marker()


def test_lyric_does_not_reserve_the_notification_slot(pipeline):
    shell, _manager, lyrics = pipeline
    before = shell._bubble_busy_until
    lyrics._show('一句歌词', title=lyrics._title_line, force=True)
    assert shell._speech_bubble._raw_text == '一句歌词'
    assert shell._bubble_busy_until == before


def test_start_and_done_preempt_lyrics_and_lyrics_resume(pipeline):
    shell, manager, lyrics = pipeline
    lyrics._show('一句歌词', title=lyrics._title_line, force=True)
    manager._on_agent_state('hermes', 'thinking')
    assert 'Hermes 正在思考' in shell._speech_bubble._raw_text
    # The next periodic lyric refresh must not overwrite the notification.
    lyrics._show('下一句歌词', title=lyrics._title_line, force=True)
    assert 'Hermes 正在思考' in shell._speech_bubble._raw_text
    # Expire the real bubble's display/reservation, without timing sleeps.
    shell._speech_bubble.hide()
    shell._bubble_busy_until = time.monotonic() - 1
    lyrics._lyric_yield_until = 0
    lyrics._show('下一句歌词', title=lyrics._title_line, force=True)
    assert shell._speech_bubble._raw_text == '下一句歌词'
    manager._on_agent_state('hermes', 'idle')
    manager._fire_done('hermes')
    assert 'Hermes 已完成本轮任务' in shell._speech_bubble._raw_text
    lyrics._show('不能覆盖完成提示', title=lyrics._title_line, force=True)
    assert 'Hermes 已完成本轮任务' in shell._speech_bubble._raw_text


def test_lyrics_do_not_release_an_existing_chat_reservation(pipeline):
    shell, manager, lyrics = pipeline
    shell.show_bubble('聊天回复', duration_ms=6000)
    reserved = shell._bubble_busy_until
    lyrics._show('不能覆盖聊天', title=lyrics._title_line, force=True)
    assert shell._speech_bubble._raw_text == '聊天回复'
    assert shell._bubble_busy_until == reserved
    manager._on_agent_state('hermes', 'thinking')
    assert shell._speech_bubble._raw_text == '聊天回复'


def test_approval_still_blocks_lyrics_and_normal_status(pipeline):
    shell, manager, lyrics = pipeline
    shell.show_alert('待管理员审批', alert_type='approval', alert_id='test-approval',
                     priority=1, sticky=True)
    lyrics._show('不能覆盖审批', title=lyrics._title_line, force=True)
    manager._on_agent_state('hermes', 'thinking')
    assert shell._alert_current['id'] == 'test-approval'
    assert '待管理员审批' in shell._speech_bubble._raw_text
    shell.resolve_alert('test-approval')
