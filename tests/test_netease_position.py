"""Native clocks use real parsing/selection; only Win32 and input files are fakes."""
from dataclasses import replace
import hashlib
import json
import math
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from pet.music import netease_layout as layout
from pet.music import netease_native as native
from pet.music import netease_position as source
from pet.music import now_playing

pytest_plugins = ['tests.test_netease_sync']


def image_fixture():
    image = bytearray(0xe00)
    image[:2] = b'MZ'
    struct.pack_into('<I', image, 0x3c, 0x80)
    image[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HH', image, 0x84, 0x8664, 2)
    struct.pack_into('<H', image, 0x94, 0xf0)
    struct.pack_into('<H', image, 0x98, 0x20b)
    struct.pack_into('<I', image, 0x98 + 56, 0x3000)
    for i, (rva, raw, size, flags) in enumerate([
        (0x1000, 0x200, 0x400, 0x60000020), (0x2000, 0x600, 0x800, 0xc0000040),
    ]):
        off = 0x188 + 40 * i
        struct.pack_into('<IIII', image, off + 8, size, rva, size, raw)
        struct.pack_into('<I', image, off + 36, flags)
    for offset, pattern, disp, target in [
        (0x220, layout.SCHEDULE_PATTERN, 4, 0x2020),
        (0x300, layout.PLAYER_PATTERN, 3, 0x2100),
    ]:
        code = bytes(0 if p == '?' else int(p, 16) for p in pattern.split())
        image[offset:offset + len(code)] = code
        instruction = offset - 0x200 + 0x1000
        struct.pack_into('<i', image, offset + disp, target - instruction - disp - 4)
    return image


class Win32:
    def __init__(self, path, resolved):
        self.path, self.layout = path, resolved
        self.pid = 10
        self.opens, self.closes, self.threads = [], [], []
        self.module_calls = 0
        self.position, self.duration, self.state = 299.0, 300.0, 1
        self.token = '123_local'
        self.denied = self.dead = self.bad_code = self.short_read = self.switch = False

    def player_pid(self):
        return self.pid

    def open(self, pid):
        if self.denied:
            raise OSError('denied')
        handle = len(self.opens) + 1
        self.opens.append(handle)
        self.dead = False
        return handle

    def close(self, handle):
        self.closes.append(handle)

    def alive(self, handle):
        return not self.dead

    def module(self, handle):
        self.module_calls += 1
        return str(self.path), 0x100000, self.layout.image_size

    def read(self, handle, address, size):
        self.threads.append(threading.get_ident())
        for offset, code in self.layout.instructions:
            if address == 0x100000 + offset:
                return b'x' * size if self.bad_code else code
        if self.short_read:
            return b''
        player = 0x100000 + self.layout.player_rva
        if address == player + 0x50:
            return struct.pack('<Q', 0x500000)
        if address == 0x500010:
            token = self.token.encode()
            obj = bytearray(32)
            if len(token) < 16:
                obj[:len(token)] = token
            else:
                struct.pack_into('<Q', obj, 0, 0x600000)
            struct.pack_into('<QQ', obj, 16, len(token), max(15, len(token)))
            if self.switch:
                self.token = '456_other'
            return bytes(obj)
        if address == 0x600000:
            return self.token.encode()
        if address == 0x100000 + self.layout.position_rva:
            return struct.pack('<d', self.position)
        if address == player + 0xa8:
            return struct.pack('<d', self.duration)
        if address == player + 0x60:
            return struct.pack('<i', self.state)
        raise AssertionError((address, size))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    image = bytes(image_fixture())
    path = tmp_path / 'cloudmusic.dll'
    path.write_bytes(image)
    monkeypatch.setattr(layout, 'SUPPORTED_IMAGES', {hashlib.sha256(image).hexdigest()})
    layout._layout_at.cache_clear()
    api = Win32(path, layout.inspect_image(image))
    monkeypatch.setattr(source, 'native_api', lambda: api)
    monkeypatch.setattr(source, 'IS_WINDOWS', True)
    metadata = source.Metadata(tmp_path)
    track = {'name': 'song', 'artists': [{'name': 'artist'}], 'album': {'name': 'album'}}
    (tmp_path / 'playingList').write_text(json.dumps({'list': [{'id': '123', 'track': track}]}))
    reader = source.NetEasePositionSource(metadata)
    playback = now_playing.Playback(now_playing.Track('song', 'artist', 'album', playing=True),
                                   None, time.monotonic(), 'cloudmusic.exe')
    yield api, reader, playback
    reader.close()
    layout._layout_at.cache_clear()


def test_verified_image_uses_relocated_references():
    resolved = layout.inspect_image(bytes(image_fixture()))
    assert (resolved.position_rva, resolved.player_rva) == (0x2020, 0x2100)
    assert len(resolved.instructions) == 2


@pytest.mark.parametrize('fault', ['truncated', 'x86', 'wrong-magic', 'outside-data', 'duplicate', 'missing'])
def test_bad_or_ambiguous_image_is_rejected(fault):
    image = image_fixture()
    if fault == 'truncated':
        image = image[:0x210]
    elif fault == 'x86':
        struct.pack_into('<H', image, 0x84, 0x14c)
    elif fault == 'wrong-magic':
        struct.pack_into('<H', image, 0x98, 0x10b)
    elif fault == 'outside-data':
        struct.pack_into('<i', image, 0x224, 0x20000)
    elif fault == 'duplicate':
        image[0x260:0x270] = image[0x220:0x230]
    else:
        image[0x220] = 0
    with pytest.raises((ValueError, struct.error)):
        layout.inspect_image(bytes(image))


def test_warm_samples_reuse_process_and_module_and_close_once(rig):
    api, reader, before = rig
    for position in [0, 23.5, 299]:
        api.position = position
        after = reader.enrich(before)
        assert after.position == position
        assert after.track.duration == 300
        assert after.track.key() == before.track.key()
        assert after.track.playing == before.track.playing
        assert after.app_id == before.app_id
    assert api.opens == [1] and api.module_calls == 1 and not api.closes
    assert reader.status == 'native'
    reader.close()
    reader.close()
    assert api.closes == [1]
    assert native.READ_ACCESS == 0x410


@pytest.mark.parametrize('fault', ['unknown-image', 'bad-code', 'short-read', 'denied'])
def test_rejected_reads_keep_original_smtc_and_release_resources(rig, fault):
    api, reader, before = rig
    if fault == 'unknown-image':
        api.path.write_bytes(bytes(image_fixture()) + b'changed')
    else:
        setattr(api, {'bad-code': 'bad_code', 'short-read': 'short_read', 'denied': 'denied',
                      'track-switch': 'switch'}[fault], True)
    assert reader.enrich(before) is before
    assert api.closes == api.opens
    count = len(api.opens)
    assert reader.enrich(before) is before
    assert len(api.opens) == count  # retry cooldown does not reopen a failed source


@pytest.mark.parametrize('field,value', [('position', -1), ('position', math.nan), ('position', math.inf),
                                        ('position', 301), ('duration', 0), ('duration', math.nan),
                                        ('duration', 604801), ('state', 0), ('state', 4)])
def test_invalid_native_values_cannot_become_true_progress(rig, field, value):
    api, reader, before = rig
    setattr(api, field, value)
    assert reader.enrich(before) is before


def test_transient_loop_snapshot_recovers_on_next_sample_without_reopening(rig):
    api, reader, before = rig
    assert reader.enrich(before).position == 299
    api.state = 0
    assert reader.enrich(before) is before
    api.position, api.state = 0, 1
    assert reader.enrich(before).position == 0
    assert api.opens == [1] and not api.closes


def test_track_switch_during_read_is_withheld_then_immediately_retried(rig):
    api, reader, before = rig
    api.switch = True
    assert reader.enrich(before) is before
    api.switch, api.token = False, '123_local'
    assert reader.enrich(before).position == 299
    assert api.opens == [1] and not api.closes


@pytest.mark.parametrize('token', ['123_local', '123_very_long_track_token'])
def test_sso_and_heap_track_ids_are_supported(rig, token):
    api, reader, before = rig
    api.token = token
    assert reader.enrich(before).position == 299


def test_restart_closes_old_process_before_attaching_new_one(rig):
    api, reader, before = rig
    reader.enrich(before)
    api.pid = 20
    assert reader.enrich(before).position == 299
    assert api.opens == [1, 2] and api.closes == [1]
    api.dead = True
    assert reader.enrich(before).position == 299
    assert api.opens == [1, 2, 3] and api.closes == [1, 2]


def test_no_unique_window_discards_previous_clock(rig):
    api, reader, before = rig
    reader.enrich(before)
    api.pid = None
    assert reader.enrich(before) is before
    assert api.closes == [1]


@pytest.mark.parametrize('second_session,expected', [(1, None), (2, 10), (None, 'error')])
def test_window_ownership_rejects_ambiguous_or_unqueryable_player(second_session, expected):
    # Exercise the actual selector, including an inaccessible second instance;
    # renderer processes are not independent player windows.
    api = native.Native.__new__(native.Native)
    api.session = native.w.DWORD(1)
    api.callback_type = lambda callback: callback

    def enumerate_windows(callback, unused):
        for hwnd in [10, 20, 30]:
            callback(hwnd, 0)
        return True

    def class_name(hwnd, buffer, size):
        buffer.value = 'OrpheusBrowserHost' if hwnd != 30 else 'Chrome_WidgetWin_0'
        return len(buffer.value)

    def window_pid(hwnd, pointer):
        pointer._obj.value = hwnd
        return 1

    def session_id(pid, pointer):
        session = 1 if pid == 10 else second_session
        if session is None:
            return False
        pointer._obj.value = session
        return True

    api.u = SimpleNamespace(EnumWindows=enumerate_windows, GetClassNameW=class_name,
                            GetWindowThreadProcessId=window_pid)
    api.k = SimpleNamespace(ProcessIdToSessionId=session_id)
    if expected == 'error':
        with pytest.raises(OSError, match='window_query_failed'):
            api.player_pid()
    else:
        assert api.player_pid() == expected


@pytest.mark.parametrize('case', ['none', 'browser', 'other-player', 'smtc-position', 'non-windows'])
def test_non_targets_never_attach_and_release_previous_reader(rig, monkeypatch, case):
    api, reader, before = rig
    reader.enrich(before)
    if case == 'none':
        before = None
    elif case in ('browser', 'other-player'):
        before = replace(before, app_id='chrome.exe' if case == 'browser' else 'QQMusic.exe')
    elif case == 'smtc-position':
        before = replace(before, position=0)
    else:
        monkeypatch.setattr(source, 'IS_WINDOWS', False)
    assert reader.enrich(before) is before
    assert api.opens == [1] and api.closes == [1]


@pytest.mark.parametrize('field,value', [('title', 'another song'), ('artist', 'guest'), ('album', 'live')])
def test_native_clock_is_not_attached_to_other_smtc_metadata(rig, field, value):
    _, reader, before = rig
    before = replace(before, track=replace(before.track, **{field: value}))
    assert reader.enrich(before) is before
    assert reader.status == 'metadata_mismatch'


def test_mismatched_play_pause_snapshot_is_not_merged(rig):
    api, reader, before = rig
    api.state = 2
    assert reader.enrich(before) is before
    paused = replace(before, track=replace(before.track, playing=False))
    assert reader.enrich(paused).position == 299


def test_metadata_retries_truncated_write_and_uses_exact_fm_id(rig):
    api, reader, before = rig
    playlist = api.path.parent / 'playingList'
    playlist.write_text('{')
    fm = api.path.parent / 'fmPlay'
    fm.write_text(json.dumps({'currentIndex': 0, 'queue': [
        {'id': 'wrong', 'name': 'song', 'artists': [{'name': 'artist'}]},
    ]}))
    assert reader.enrich(before) is before
    fm.write_text(json.dumps({'queue': [{'id': '123', 'name': 'song', 'artists': [{'name': 'artist'}]}]}))
    assert reader.enrich(before).position == 299


def test_real_sampling_thread_uses_native_clock_and_closes_reader(controller, rig, monkeypatch):
    app, window, ctrl, _ = controller
    api, reader, before = rig
    monkeypatch.setattr(source, 'NetEasePositionSource', lambda: reader)
    monkeypatch.setattr(now_playing, 'get_now_playing', lambda *a, **kw: before)
    ctrl._start_sample_thread()
    try:
        for index, (position, expected) in enumerate([(299, 'LAST'), (0, 'TITLE'), (2, 'FIRST'),
                                                     (299, 'LAST'), (0, 'TITLE'), (2, 'FIRST')]):
            api.position = position
            api.token = f'123_instance_{index}'  # replay instances differ, song ID does not
            count = len(window.shown)
            ctrl._on_tick()
            deadline = time.monotonic() + 10
            while len(window.shown) == count and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.001)
            assert len(window.shown) > count
            assert window.shown[-1] == expected
        assert api.opens == [1]
        assert all(t != threading.get_ident() for t in api.threads)
    finally:
        ctrl.shutdown()
        ctrl._sample_thread.join(timeout=10)
        assert not ctrl._sample_thread.is_alive()
    assert api.closes == [1]
