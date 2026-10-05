"""YouTube Music via Windows media sessions and DSH's existing timed lyrics."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
import math
import os
import re
from types import SimpleNamespace

from .language_ui import install

MUSIC_URL = "https://music.youtube.com/"
LYRIC_HINT = (
    "支援 YouTube Music 網頁與桌面版，以及支援 Windows 媒體控制的播放器。"
    "播放時依進度逐句顯示歌詞；暫停會停止更新，快轉與切歌會跟著同步。"
    "歌詞由公開歌詞來源取得，不需要 GPT；無同步歌詞時只顯示歌名。"
)
OPEN_HINT = "使用預設瀏覽器開啟 YouTube Music。請同時開啟「顯示歌詞」，播放歌曲後會自動連動。"
AUTO_HINT = (
    "自動搜尋 YouTube Music 與瀏覽器的播放資訊，優先連結 YouTube Music 桌面版。請先播放歌曲；開啟此開關也會啟用「顯示歌詞」。關閉後停止瀏覽器音樂自動連結。"
)
SIZE_HINT = "調整氣泡與歌詞的文字大小，範圍 50%～300%。100% 為預設；下方立即預覽，儲存後生效。"
_preference_cache = {}
_VIDEO_WORDS = r"(?:official\s*(?:music\s*)?(?:video|audio|mv)|music\s*video|lyrics?|lyric\s*video|MV|HD|4K|動態歌詞|动态歌词|字幕|中字|pinyin)"
_VIDEO_BLOCK = re.compile(r"[\[【(（][^\]】)）]*" + _VIDEO_WORDS + r"[^\]】)）]*[\]】)）]", re.I)
_THEME_BLOCK = re.compile(r"[（(][^）)]*(?:主題曲|主题曲|片尾曲|片頭曲|片头曲|電視劇插曲|电视剧插曲)[^）)]*[）)]")


def normalize_track(title, artist, app_id):
    """Clean browser video decorations, retaining live/remix/recording versions."""
    title, artist = str(title or "").strip(), str(artist or "").strip()
    app_id = str(app_id or "").lower()
    if not any(name in app_id for name in ("chrome", "msedge", "youtube", "ytmusic", "ytmdesktop")):
        return title, artist
    original = title
    title = re.sub(r"\s*[-|]\s*YouTube(?:\s+Music)?\s*$", "", title, flags=re.I)
    decoration = _VIDEO_BLOCK.search(title)
    if decoration:
        # Some uploads append whole quoted lyric excerpts after this label.
        title = title[: decoration.start()].strip()
    title = _THEME_BLOCK.sub("", title).strip()
    title = re.sub(r"\s*[-|]\s*" + _VIDEO_WORDS + r"\s*$", "", title, flags=re.I).strip()
    artist = re.sub(r"\s*[-–]\s*Topic\s*$", "", artist, flags=re.I).strip()
    pair = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
    channel = bool(re.search(r"official|vevo|lyrics?|\bmusic\b|records|音樂|音乐", artist, re.I))
    if len(pair) == 2 and all(pair) and (not artist or channel or decoration or pair[0].casefold() == artist.casefold()):
        artist, title = pair[0].strip(), pair[1].strip()
    return title or original, artist


def normalize_playback(playback):
    if playback is None:
        return None
    title, artist = normalize_track(playback.track.title, playback.track.artist, playback.app_id)
    if (title, artist) == (playback.track.title, playback.track.artist):
        return playback
    return replace(playback, track=replace(playback.track, title=title, artist=artist))


def extrapolate_position(position, timeline, *, is_playing, end, original):
    """Chromium reports positions at transport events, often over 5 seconds apart."""
    fallback = original(position, timeline, is_playing=is_playing, end=end)
    if not is_playing or end <= 0:
        return fallback
    try:
        from .now_playing import _utc_now

        stamp = timeline.last_updated_time
        if stamp is None or stamp.year < 2000:
            return fallback
        elapsed = _utc_now().timestamp() - stamp.timestamp()
        # Keep the original clock checks, accepting sparse reports within one
        # track's duration. Reject old/stale sessions and timestamps in the future.
        if 5 <= elapsed <= end + 60:
            return max(0.0, min(position + elapsed, end))
    except Exception:
        pass
    return fallback


def quota_popup_visible(window):
    controller = getattr(window, "_codex_quota_controller", None)
    return bool(controller is not None and controller.popup.isVisible())


def load_preferences(config):
    """Compatibility seam; Config now owns persistent preference normalization."""
    return config.get("ytmusic_auto_connect", True)


def auto_connect_enabled():
    from .config import _default_base, APP_DIR_NAME

    instance_id = os.environ.get("DSH_PET_INSTANCE", "").strip()
    name = "config-" + instance_id + ".json" if instance_id else "config.json"
    path = _default_base() / APP_DIR_NAME / name
    key = str(path)
    previous = _preference_cache.get(key, (None, True))
    try:
        stamp = path.stat().st_mtime_ns
        if stamp == previous[0]:
            return previous[1]
        value = json.loads(path.read_text(encoding="utf-8")).get("ytmusic_auto_connect", True)
        value = value if isinstance(value, bool) else True
        _preference_cache[key] = (stamp, value)
        return value
    except (OSError, ValueError, AttributeError):
        return previous[1]


def is_browser_music(app_id):
    app_id = str(app_id or "").lower()
    return any(name in app_id for name in ("chrome", "msedge", "youtube", "ytmusic", "ytmdesktop"))


def is_youtube_music(app_id):
    app_id = str(app_id or "").lower()
    return any(name in app_id for name in ("youtube", "ytmusic", "ytmdesktop", "cinhimbnkkghhklpknlkffjgod"))


_controlled_app_id = ""


def is_game_replay(app_id):
    return "xboxgamingoverlay" in str(app_id or "").casefold()


def music_sessions(manager, auto_connect):
    from .now_playing import _session_app_id

    return [
        session
        for session in manager.get_sessions()
        if not is_game_replay(_session_app_id(session)) and (auto_connect or not is_browser_music(_session_app_id(session)))
    ]


async def pick_media_session(manager, tracked_app_id, original):
    from .now_playing import _session_app_id, _session_status

    enabled = auto_connect_enabled()
    sessions = music_sessions(manager, enabled)
    if enabled:
        wanted = str(tracked_app_id or "").strip().casefold()
        youtube = [session for session in sessions if is_youtube_music(_session_app_id(session))]
        # A paused music session remains the lyric source. Never switch to a
        # replay or another browser merely because YouTube Music was paused.
        for session in youtube:
            if wanted and _session_app_id(session).strip().casefold() == wanted:
                return session
        for session in youtube:
            if _session_status(session) == 4:
                return session
        if not any(_session_status(session) == 4 for session in sessions):
            for session in youtube:
                if _session_status(session) == 5:
                    return session
    return await original(SimpleNamespace(get_sessions=lambda: sessions), tracked_app_id)


async def pick_control_session(tracked_app_id=None):
    """Keep all transport actions on the session chosen before pause."""
    global _controlled_app_id
    from .now_playing import _import_winrt, _session_app_id, _session_status, _pick_playing_session

    manager_cls = _import_winrt()
    if manager_cls is None:
        return None
    manager = await manager_cls.request_async()
    enabled = auto_connect_enabled()
    sessions = music_sessions(manager, enabled)
    wanted = str(tracked_app_id or _controlled_app_id or "").strip().casefold()
    if wanted and not enabled and is_browser_music(wanted):
        # Respect the user's auto-connect switch immediately.
        _controlled_app_id = ""
        wanted = ""
    if wanted:
        for session in sessions:
            if _session_app_id(session).strip().casefold() == wanted:
                _controlled_app_id = _session_app_id(session)
                return session
        # The chosen player was closed or its session vanished. A failed music
        # command is preferable to starting an unrelated video or game replay.
        logging.info("Music transport target unavailable; command ignored: %s", wanted)
        return None
    selected = None
    if enabled:
        youtube = [session for session in sessions if is_youtube_music(_session_app_id(session))]
        for status in (4, 5):
            selected = next((session for session in youtube if _session_status(session) == status), None)
            if selected is not None:
                break
    if selected is None:
        selected = await _pick_playing_session(SimpleNamespace(get_sessions=lambda: sessions))
    if selected is not None:
        _controlled_app_id = _session_app_id(selected)
        logging.info("Music transport target retained: %s", _controlled_app_id)
    return selected


def show_lyric(controller, lyric, title, force, original):
    if is_browser_music(controller._tracked_app_id) and not auto_connect_enabled():
        return
    key = controller._current_key
    if getattr(controller, "_ytmusic_title_key", None) != key:
        controller._ytmusic_title_key = key
        controller._ytmusic_lyrics_started = False
    actual_lyric = bool(str(lyric or "").strip() and controller._tracker.has_lyrics and not controller._instrumental)
    if (actual_lyric or controller._ytmusic_lyrics_started) and not controller._instrumental:
        title = ""
    bubble = getattr(controller.win, "_speech_bubble", None)
    if bubble is not None:
        bubble._ytmusic_lyric_text = str(lyric) if actual_lyric else None
        bubble._ytmusic_pending_owner = id(controller)
        bubble._ytmusic_pending_message = str(lyric or title or "").strip()
    try:
        result = original(controller, lyric, title=title, force=force)
        if actual_lyric and controller._last_shown == (str(lyric).strip(), ""):
            controller._ytmusic_lyrics_started = True
        return result
    finally:
        if bubble is not None:
            bubble._ytmusic_lyric_text = None
            bubble._ytmusic_pending_owner = None
            bubble._ytmusic_pending_message = None


def bubble_taken_by_other(controller, original):
    bubble = getattr(controller.win, "_speech_bubble", None)
    if (
        bubble is not None
        and bubble.isVisible()
        and getattr(bubble, "_ytmusic_owner", None) == id(controller)
        and getattr(bubble, "_content_kind", "") == "text"
        and getattr(bubble, "_raw_text", "") == getattr(bubble, "_ytmusic_owned_message", None)
    ):
        # The controller has already assigned the NEXT lyric to _last_lyric.
        # Match who displayed the current bubble, rather than comparing it with
        # the next line or requiring the song subtitle we intentionally removed.
        return False
    return original(controller)


def accept_lyrics(controller, key, lyrics, original):
    if lyrics is not None and lyrics.lines and not lyrics.instrumental:
        lines = tuple(line for line in lyrics.lines if str(line.text).strip())
        if len(lines) != len(lyrics.lines):
            logging.info("YTM skipped %d empty lyric timestamps", len(lyrics.lines) - len(lines))
            lyrics = replace(lyrics, lines=lines)
    return original(controller, key, lyrics)


def wrap_lyric_text(text, font, width):
    """Use Qt's Unicode word boundaries and UTF-16 indexes without dropping text."""
    from PySide6.QtGui import QTextLayout, QTextOption

    wrapped = []
    for paragraph in str(text).splitlines() or [""]:
        layout = QTextLayout(paragraph, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        encoded = paragraph.encode("utf-16-le")
        layout.beginLayout()
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            start, length = line.textStart(), line.textLength()
            wrapped.append(encoded[start * 2 : (start + length) * 2].decode("utf-16-le").strip())
        layout.endLayout()
        if not paragraph:
            wrapped.append("")
    return wrapped or [""]


def render_full_lyric(bubble, text, anchor_rect):
    if getattr(bubble, "_ytmusic_lyric_text", None) != text or bubble._interactive_active:
        return
    if bubble._preset.get("shape") == "breath_bubble":
        # The regular text frame adapts to the complete lyric, including large fonts.
        bubble._layout.setContentsMargins(13, 10, 13, 17)
        bubble.setMinimumSize(0, 0)
        bubble.setMaximumSize(16777215, 16777215)
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontMetrics

    bubble._ytmusic_original_text_format = bubble.label.textFormat()
    bubble.label.setTextFormat(Qt.TextFormat.PlainText)
    bubble.label.ensurePolished()
    column = bubble._locked_column or bubble._column_for_text(text, anchor_rect)
    available = bubble._available_geometry(anchor_rect)
    margins = bubble._layout.contentsMargins()
    if available is not None:
        column = min(column, available.width() - margins.left() - margins.right() - 8)
    column = max(24, int(column))
    lines = wrap_lyric_text(text, bubble.label.font(), column - 8)
    metrics = QFontMetrics(bubble.label.font())
    bubble._reset_paging()
    bubble._subtitle_label.hide()
    bubble.label.show()
    bubble.label.setText("\n".join(lines))
    bubble.label.setFixedSize(column, math.ceil(metrics.lineSpacing() * len(lines)) + 4)
    bubble._locked_column = column
    bubble._layout.activate()
    bubble.adjustSize()
    bubble._place(anchor_rect, animate=False)
    # Keep a line visible through a slow media sample; normal 1 s updates renew
    # it, and pause/stop still expires it. Do not make the bubble sticky.
    bubble._hide_timer.start(5000)
    render_key = (text, bubble.label.font().pixelSize(), column)
    if getattr(bubble, "_ytmusic_last_render", None) != render_key:
        bubble._ytmusic_last_render = render_key
        logging.info("YTM lyric-only bubble displayed: %d characters, %d lines, %d px font", len(text), len(lines), bubble.label.font().pixelSize())


def prepare_bubble(bubble):
    bubble._ytmusic_owner = getattr(bubble, "_ytmusic_pending_owner", None)
    bubble._ytmusic_owned_message = getattr(bubble, "_ytmusic_pending_message", None)
    previous = getattr(bubble, "_ytmusic_original_text_format", None)
    if previous is not None:
        bubble.label.setTextFormat(previous)
        del bubble._ytmusic_original_text_format


def write_settings(dialog, original):
    config = dialog.config
    previous_override = config.__dict__.get("save")
    save = config.save

    def commit():
        config.set("ytmusic_auto_connect", dialog.ytmusic_auto_switch.isChecked())
        return save()

    config.save = commit
    try:
        return original(dialog)
    finally:
        if previous_override is None:
            del config.save
        else:
            config.save = previous_override


def append_row(dialog, card, row):
    from PySide6.QtWidgets import QFrame
    from .settings_widgets import SettingsSection

    separator = QFrame(card)
    separator.setObjectName("cardSeparator")
    separator.setFixedHeight(1)
    card.layout().addWidget(separator)
    card.layout().addWidget(row)
    card.rows.append(row)
    card.separators.append(separator)
    card.refresh_separators()
    section = card.parentWidget()
    if isinstance(section, SettingsSection):
        section.rows.append(row)
    dialog._search_rows.append(row)


def replace_hint(row, text):
    row.hint_label.setText(text)
    row.hint_label.setProperty("_dsh_source_text", None)
    row.hint_label.setProperty("_dsh_rendered_text", None)
    row.control.setAccessibleDescription(text)
    row.control.setProperty("_dsh_source_accessibleDescription", None)
    row.control.setProperty("_dsh_rendered_accessibleDescription", None)


def add_settings_control(dialog):
    from PySide6.QtWidgets import QPushButton, QLabel
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from .settings_widgets import SettingRow, ToggleSwitch

    lyric_row = dialog.findChild(SettingRow, "settingRow_music_lyric")
    if lyric_row is None:
        return
    replace_hint(lyric_row, LYRIC_HINT)
    card = lyric_row.parentWidget()
    auto = ToggleSwitch(dialog)
    auto.setObjectName("dshYouTubeMusicAutoConnect")
    auto.setChecked(bool(dialog.config.get("ytmusic_auto_connect", True)))
    auto.toggled.connect(lambda on: dialog.music_lyric_check.setChecked(True) if on else None)
    dialog.ytmusic_auto_switch = auto
    append_row(dialog, card, SettingRow("ytmusic_auto_connect", "自動搜尋 YouTube Music", AUTO_HINT, auto))
    button = QPushButton("開啟 YouTube Music", dialog)
    button.setObjectName("dshOpenYouTubeMusic")
    button.setMinimumWidth(165)
    button.clicked.connect(lambda _checked=False: QDesktopServices.openUrl(QUrl(MUSIC_URL)))
    row = SettingRow("ytmusic_open", "YouTube Music", OPEN_HINT, button)
    append_row(dialog, card, row)
    dialog.ytmusic_open_button = button
    size_row = dialog.findChild(SettingRow, "settingRow_bubble_text_scale")
    if size_row is not None:
        size_row.label.setText("文字大小")
        size_row.label.setProperty("_dsh_source_text", None)
        size_row.label.setProperty("_dsh_rendered_text", None)
        replace_hint(size_row, SIZE_HINT)
        preview = QLabel("歌詞與氣泡文字預覽", dialog)
        preview.setObjectName("dshBubbleTextSizePreview")
        preview.setWordWrap(True)
        preview_row = SettingRow("bubble_text_preview", "文字預覽", "", preview, stacked=True)
        append_row(dialog, size_row.parentWidget(), preview_row)
        from .speech_bubble import BUBBLE_BODY_FONT_PX, scale_bubble_font_px

        def resize_preview(value):
            pixels = scale_bubble_font_px(BUBBLE_BODY_FONT_PX, value / 100)
            preview.setStyleSheet("QLabel { font-size: %dpx; padding: 10px; }" % pixels)
            preview.updateGeometry()

        dialog.bubble_text_scale_spin.setSingleStep(10)
        dialog.bubble_text_scale_spin.valueChanged.connect(resize_preview)
        resize_preview(dialog.bubble_text_scale_spin.value())
        dialog.bubble_text_size_preview = preview
    manager = install(dialog.config)
    if manager is not None:
        manager.translate_tree(dialog)
