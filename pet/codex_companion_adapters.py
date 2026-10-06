"""Named source seams of the opt-in companion; feature state stays in controllers."""

from functools import wraps
from PySide6.QtCore import Qt


def _with_music_lyric_name_matches(_original):
    @wraps(_original)
    def wrapped(candidate, wanted):
        from pet.ytmusic import artist_matches

        return artist_matches(candidate, wanted, _original)

    return wrapped


def _with_now_playing_read_async(_original):

    @wraps(_original)
    async def wrapped(tracked_app_id=None):
        playback = await _original(tracked_app_id)
        from pet.ytmusic import normalize_playback

        return normalize_playback(playback)

    return wrapped


def _with_now_playing_extrapolate(_original):

    @wraps(_original)
    def wrapped(position, timeline, *, is_playing, end):
        from pet.ytmusic import extrapolate_position

        return extrapolate_position(position, timeline, is_playing=is_playing, end=end, original=_original)

    return wrapped


def _with_music_lyric_controller_MusicLyricController_bubble_blocked(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.ytmusic import quota_popup_visible

        return quota_popup_visible(self.win) or _original(self)

    return wrapped


def _with_now_playing_pick_playing_session(_original):

    @wraps(_original)
    async def wrapped(manager, tracked_app_id=None):
        from pet.ytmusic import pick_media_session

        return await pick_media_session(manager, tracked_app_id, _original)

    return wrapped


def _with_music_lyric_controller_MusicLyricController_show(_original):

    @wraps(_original)
    def wrapped(self, lyric, *, title="", force=False):
        from pet.ytmusic import show_lyric

        return show_lyric(self, lyric, title, force, _original)

    return wrapped


def _with_speech_bubble_PetSpeechBubble_show_text(_original):

    @wraps(_original)
    def wrapped(self, text, anchor_rect, duration_ms=3200, *, pet_scale=None, subtitle="", sticky=False, buttons=None, title_first=False, width_locked=False):
        from pet.ytmusic import prepare_bubble, render_full_lyric

        prepare_bubble(self)
        _original(
            self,
            text,
            anchor_rect,
            duration_ms,
            pet_scale=pet_scale,
            subtitle=subtitle,
            sticky=sticky,
            buttons=buttons,
            title_first=title_first,
            width_locked=width_locked,
        )
        render_full_lyric(self, text, anchor_rect)

    return wrapped


def _with_music_lyric_controller_MusicLyricController_bubble_taken_by_other(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.ytmusic import bubble_taken_by_other

        return bubble_taken_by_other(self, _original)

    return wrapped


def _with_music_lyric_controller_MusicLyricController_on_lyrics_ready(_original):

    @wraps(_original)
    def wrapped(self, key, lyrics):
        from pet.ytmusic import accept_lyrics

        return accept_lyrics(self, key, lyrics, _original)

    return wrapped


def _with_now_playing_pick_playback_session(_original):

    @wraps(_original)
    async def wrapped(tracked_app_id=None):
        from pet.ytmusic import pick_control_session

        return await pick_control_session(tracked_app_id)

    return wrapped


def _with_dynamic_island_DynamicIsland_init(_original):

    @wraps(_original)
    def wrapped(self, config, parent=None):
        _original(self, config, parent)
        from pet.island_music import attach

        attach(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_rest_size(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_music import rest_size

        return rest_size(self, _original(self))

    return wrapped


def _with_dynamic_island_DynamicIsland_target_rect(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_music import target_rect

        return target_rect(self, _original(self))

    return wrapped


def _with_dynamic_island_DynamicIsland_update_size(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_music import update_size

        return update_size(self, _original)

    return wrapped


def _with_dynamic_island_DynamicIsland_resizeEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        _original(self, event)
        from pet.island_music import layout

        layout(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_enterEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        _original(self, event)
        from pet.island_music import enter

        enter(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_leaveEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        _original(self, event)
        from pet.island_music import leave

        leave(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_hideEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        from pet.island_music import suspend

        suspend(self)
        _original(self, event)

    return wrapped


def _with_dynamic_island_DynamicIsland_expand_card(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_music import expand_card

        return expand_card(self, _original)

    return wrapped


def _with_dynamic_island_DynamicIsland_collapse_card_music(_original):

    @wraps(_original)
    def wrapped(self, *, animate=True):
        from pet.island_music import collapse_card

        return collapse_card(self, _original, animate=animate)

    return wrapped


def _with_dynamic_island_DynamicIsland_refresh_from_config(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_music import reset

        reset(self)
        _original(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_mouseMoveEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        _original(self, event)
        from pet.island_music import drag

        drag(self)

    return wrapped


def _with_modern_settings_dialog_ModernSettingsDialog_apply_selected_theme(_original):

    @wraps(_original)
    def wrapped(self, *_args):
        _original(self, *_args)
        from pet.ui_polish import settings_theme

        settings_theme(self)

    return wrapped


def _with_chat_widgets_ChatWindow_style(_original):

    @wraps(_original)
    def wrapped(self):
        _original(self)
        from pet.ui_polish import chat_style

        chat_style(self)

    return wrapped


def _with_chat_widgets_ChatWindow_add(_original):

    @wraps(_original)
    def wrapped(self, role, text):
        bubble = _original(self, role, text)
        from pet.ui_polish import polish_chat_icons, colors, style_of

        polish_chat_icons(bubble, colors(style_of(self.config)))
        return bubble

    return wrapped


def _with_quick_chat_QuickChatBubble_build(_original):

    @wraps(_original)
    def wrapped(self):
        _original(self)
        from pet.ui_polish import quick_style

        quick_style(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_style_palette(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.ui_polish import island_palette

        return island_palette(self, _original(self))

    return wrapped


def _with_dynamic_island_DynamicIsland_sync_card_labels(_original):

    @wraps(_original)
    def wrapped(self):
        _original(self)
        from pet.ui_polish import island_card

        island_card(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_rest_size_2(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.ui_polish import island_rest_size

        return island_rest_size(self, _original(self))

    return wrapped


def _with_settings_widgets_ModernSelect_paintEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        from pet.ui_polish import paint_select

        paint_select(self)

    return wrapped


def _with_settings_widgets_settings_popup_stylesheet(_original):

    @wraps(_original)
    def wrapped(widget=None):
        from pet.ui_polish import settings_popup

        return settings_popup(widget, _original(widget))

    return wrapped


def _with_context_menu_populate_context_menu(_original):

    @wraps(_original)
    def wrapped(menu, pet):
        _original(menu, pet)
        from pet.independent_ui import menu_polish

        menu_polish(menu, pet)
        menu.aboutToShow.connect(lambda: menu_polish(menu, pet))

    return wrapped


def _with_dynamic_island_DynamicIsland_init_2(_original):

    @wraps(_original)
    def wrapped(self, config, parent=None):
        _original(self, config, parent)
        from pet.codex_work_status import attach

        attach(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_info_text(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.codex_work_status import capsule_info

        return capsule_info(self, _original(self))

    return wrapped


def _with_dynamic_island_DynamicIsland_status_dot_color(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.codex_work_status import status_dot

        return status_dot(self, _original(self))

    return wrapped


def _with_music_lyric_controller_MusicLyricController_bubble_blocked_2(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.codex_work_status import notice_active

        return notice_active(self.win) or _original(self)

    return wrapped


def _with_speech_bubble_PetSpeechBubble_set_style(_original):

    @wraps(_original)
    def wrapped(self, style_id):
        _original(self, style_id)
        from pet.bubble_polish import apply

        apply(self, force=True)

    return wrapped


def _with_speech_bubble_PetSpeechBubble_show_text_2(_original):

    @wraps(_original)
    def wrapped(self, text, anchor_rect, duration_ms=3200, *, pet_scale=None, subtitle="", sticky=False, buttons=None, title_first=False, width_locked=False):
        from pet.bubble_polish import apply

        apply(self)
        result = _original(
            self,
            text,
            anchor_rect,
            duration_ms,
            pet_scale=pet_scale,
            subtitle=subtitle,
            sticky=sticky,
            buttons=buttons,
            title_first=title_first,
            width_locked=width_locked,
        )
        apply(self, force=True)
        from pet.overlay_layout import sync

        sync()
        return result

    return wrapped


def _with_speech_bubble_PetSpeechBubble_paintEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        from pet.bubble_polish import paint

        if paint(self):
            return
        return _original(self, event)

    return wrapped


def _with_speech_bubble_PetSpeechBubble_place(_original):

    @wraps(_original)
    def wrapped(self, anchor_rect, *, animate=True):
        _original(self, anchor_rect, animate=animate)
        from pet.overlay_layout import avoid_chat

        avoid_chat(self)

    return wrapped


def _with_modern_settings_dialog_ModernSettingsDialog_reject(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.ui_preview import cancel

        cancel(self)
        from PySide6.QtWidgets import QDialog

        return QDialog.reject(self)

    return wrapped


def _with_modern_settings_dialog_ModernSettingsDialog_closeEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        from pet.ui_preview import cancel

        cancel(self)
        from PySide6.QtWidgets import QDialog

        return QDialog.closeEvent(self, event)

    return wrapped


def _with_dynamic_island_DynamicIsland_on_card_toggle(_original):

    @wraps(_original)
    def wrapped(self):
        self.toggle_pet_requested.emit()
        self._sync_card_labels()

    return wrapped


def _with_dynamic_island_DynamicIsland_hidden_chat_enabled(_original):

    @wraps(_original)
    def wrapped(self):
        return False

    return wrapped


def _with_dynamic_island_DynamicIsland_set_pet_visible(_original):

    @wraps(_original)
    def wrapped(self, visible):
        callback = self.on_pet_visibility_changed
        self.on_pet_visibility_changed = None
        try:
            _original(self, visible)
        finally:
            self.on_pet_visibility_changed = callback

    return wrapped


def _with_dynamic_island_DynamicIsland_status_dot_color_2(_original):

    @wraps(_original)
    def wrapped(self):
        visible = self._pet_visible
        self._pet_visible = True
        try:
            return _original(self)
        finally:
            self._pet_visible = visible

    return wrapped


def _with_dynamic_island_DynamicIsland_icon_pixmap(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.custom_avatar import pixmap

        return pixmap(self, _original)

    return wrapped


def _with_island_chat_IslandChatBubble_show_for_island(_original):

    @wraps(_original)
    def wrapped(self, island, *, activate=True, reply_text=None):
        from pet.island_embedded_chat import show

        return show(self, island, activate, reply_text, _original)

    return wrapped


def _with_island_chat_IslandChatBubble_show_feedback(_original):

    @wraps(_original)
    def wrapped(self, island, text, *, subtitle="", duration_ms=None):
        from pet.island_embedded_chat import embedded

        if embedded(self):
            return
        return _original(self, island, text, subtitle=subtitle, duration_ms=duration_ms)

    return wrapped


def _with_island_chat_IslandChatBubble_position_near_pet(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_embedded_chat import embedded

        if embedded(self):
            return
        return _original(self)

    return wrapped


def _with_quick_chat_QuickChatBubble_paintEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        from pet.island_embedded_chat import embedded

        if embedded(self):
            return
        return _original(self, event)

    return wrapped


def _with_quick_chat_QuickChatBubble_event(_original):

    @wraps(_original)
    def wrapped(self, event):
        from pet.island_embedded_chat import embedded

        if embedded(self):
            from PySide6.QtWidgets import QFrame

            return QFrame.event(self, event)
        return _original(self, event)

    return wrapped


def _with_quick_chat_QuickChatBubble_close_if_still_inactive(_original):

    @wraps(_original)
    def wrapped(self):
        self._deactivate_check_pending = False
        from pet.island_embedded_chat import embedded

        if embedded(self):
            return
        return _original(self)

    return wrapped


def _with_quick_chat_QuickChatBubble_closeEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        result = _original(self, event)
        from pet.island_embedded_chat import closed

        closed(self)
        return result

    return wrapped


def _with_quick_chat_QuickChatBubble_set_reply_text(_original):

    @wraps(_original)
    def wrapped(self, full_text):
        _original(self, full_text)
        from pet.island_embedded_chat import embedded

        if embedded(self):
            self._reply_text = self._reply_full
            self._reply_truncated = False

    return wrapped


def _with_quick_chat_QuickChatBubble_render_reply(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_embedded_chat import render

        if render(self):
            return
        return _original(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_on_card_chat(_original):

    @wraps(_original)
    def wrapped(self):
        if not self._pet_visible:
            self.open_chat_requested.emit()
            return
        return _original(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_rest_size_3(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.island_embedded_chat import rest_size

        return rest_size(self, _original(self))

    return wrapped


def _with_dynamic_island_DynamicIsland_sync_card_labels_2(_original):

    @wraps(_original)
    def wrapped(self):
        _original(self)
        from pet.island_embedded_chat import refresh

        refresh(self)

    return wrapped


def _with_dynamic_island_DynamicIsland_collapse_card(_original):

    @wraps(_original)
    def wrapped(self, *, animate=True):
        from pet.island_embedded_chat import close

        close(self)
        return _original(self, animate=animate)

    return wrapped


def _with_dynamic_island_DynamicIsland_set_pet_visible_2(_original):

    @wraps(_original)
    def wrapped(self, visible):
        from pet.island_embedded_chat import close

        if visible:
            close(self)
        return _original(self, visible)

    return wrapped


def _with_window_PetWindow_init(_original):

    @wraps(_original)
    def wrapped(self, lib, config, broker_facade=None, *, clock=None, agent_link_manager=None, proactive_watcher=None):
        _original(self, lib, config, broker_facade, clock=clock, agent_link_manager=agent_link_manager, proactive_watcher=proactive_watcher)
        from pet.codex_companion import initialize_pet

        initialize_pet(self)

    return wrapped


def _with_window_PetWindow_mouseReleaseEvent(_original):

    @wraps(_original)
    def wrapped(self, event):
        _original(self, event)
        from pet.codex_usage import toggle_for

        if event.button() == Qt.MouseButton.LeftButton:
            toggle_for(self)

    return wrapped


def _with_modern_settings_dialog_ModernSettingsDialog_init(_original):

    @wraps(_original)
    def wrapped(self, config, parent=None, *, include_ai=True, standalone=False, initial_page=None):
        _original(self, config, parent, include_ai=include_ai, standalone=standalone, initial_page=initial_page)
        from pet.codex_companion import initialize_settings

        initialize_settings(self)

    return wrapped


def _with_modern_settings_dialog_ModernSettingsDialog_write_config(_original):

    @wraps(_original)
    def wrapped(self):
        from pet.codex_companion import commit_settings

        return commit_settings(self, _original)

    return wrapped


ADAPTERS = (
    ("pet.music_lyric", None, "_name_matches", _with_music_lyric_name_matches),
    ("pet.now_playing", None, "_read_async", _with_now_playing_read_async),
    ("pet.now_playing", None, "_extrapolate", _with_now_playing_extrapolate),
    ("pet.music_lyric_controller", "MusicLyricController", "_bubble_blocked", _with_music_lyric_controller_MusicLyricController_bubble_blocked),
    ("pet.now_playing", None, "_pick_playing_session", _with_now_playing_pick_playing_session),
    ("pet.music_lyric_controller", "MusicLyricController", "_show", _with_music_lyric_controller_MusicLyricController_show),
    ("pet.speech_bubble", "PetSpeechBubble", "show_text", _with_speech_bubble_PetSpeechBubble_show_text),
    ("pet.music_lyric_controller", "MusicLyricController", "_bubble_taken_by_other", _with_music_lyric_controller_MusicLyricController_bubble_taken_by_other),
    ("pet.music_lyric_controller", "MusicLyricController", "_on_lyrics_ready", _with_music_lyric_controller_MusicLyricController_on_lyrics_ready),
    ("pet.now_playing", None, "_pick_playback_session", _with_now_playing_pick_playback_session),
    ("pet.dynamic_island", "DynamicIsland", "__init__", _with_dynamic_island_DynamicIsland_init),
    ("pet.dynamic_island", "DynamicIsland", "_rest_size", _with_dynamic_island_DynamicIsland_rest_size),
    ("pet.dynamic_island", "DynamicIsland", "_target_rect", _with_dynamic_island_DynamicIsland_target_rect),
    ("pet.dynamic_island", "DynamicIsland", "_update_size", _with_dynamic_island_DynamicIsland_update_size),
    ("pet.dynamic_island", "DynamicIsland", "resizeEvent", _with_dynamic_island_DynamicIsland_resizeEvent),
    ("pet.dynamic_island", "DynamicIsland", "enterEvent", _with_dynamic_island_DynamicIsland_enterEvent),
    ("pet.dynamic_island", "DynamicIsland", "leaveEvent", _with_dynamic_island_DynamicIsland_leaveEvent),
    ("pet.dynamic_island", "DynamicIsland", "hideEvent", _with_dynamic_island_DynamicIsland_hideEvent),
    ("pet.dynamic_island", "DynamicIsland", "expand_card", _with_dynamic_island_DynamicIsland_expand_card),
    ("pet.dynamic_island", "DynamicIsland", "collapse_card", _with_dynamic_island_DynamicIsland_collapse_card_music),
    ("pet.dynamic_island", "DynamicIsland", "refresh_from_config", _with_dynamic_island_DynamicIsland_refresh_from_config),
    ("pet.dynamic_island", "DynamicIsland", "mouseMoveEvent", _with_dynamic_island_DynamicIsland_mouseMoveEvent),
    ("pet.modern_settings_dialog", "ModernSettingsDialog", "_apply_selected_theme", _with_modern_settings_dialog_ModernSettingsDialog_apply_selected_theme),
    ("pet.chat.widgets", "ChatWindow", "_style", _with_chat_widgets_ChatWindow_style),
    ("pet.chat.widgets", "ChatWindow", "_add", _with_chat_widgets_ChatWindow_add),
    ("pet.quick_chat", "QuickChatBubble", "_build", _with_quick_chat_QuickChatBubble_build),
    ("pet.dynamic_island", "DynamicIsland", "_style_palette", _with_dynamic_island_DynamicIsland_style_palette),
    ("pet.dynamic_island", "DynamicIsland", "_sync_card_labels", _with_dynamic_island_DynamicIsland_sync_card_labels),
    ("pet.dynamic_island", "DynamicIsland", "_rest_size", _with_dynamic_island_DynamicIsland_rest_size_2),
    ("pet.settings_widgets", "ModernSelect", "paintEvent", _with_settings_widgets_ModernSelect_paintEvent),
    ("pet.settings_widgets", None, "settings_popup_stylesheet", _with_settings_widgets_settings_popup_stylesheet),
    ("pet.context_menu", None, "populate_context_menu", _with_context_menu_populate_context_menu),
    ("pet.dynamic_island", "DynamicIsland", "__init__", _with_dynamic_island_DynamicIsland_init_2),
    ("pet.dynamic_island", "DynamicIsland", "_info_text", _with_dynamic_island_DynamicIsland_info_text),
    ("pet.dynamic_island", "DynamicIsland", "_status_dot_color", _with_dynamic_island_DynamicIsland_status_dot_color),
    ("pet.music_lyric_controller", "MusicLyricController", "_bubble_blocked", _with_music_lyric_controller_MusicLyricController_bubble_blocked_2),
    ("pet.speech_bubble", "PetSpeechBubble", "set_style", _with_speech_bubble_PetSpeechBubble_set_style),
    ("pet.speech_bubble", "PetSpeechBubble", "show_text", _with_speech_bubble_PetSpeechBubble_show_text_2),
    ("pet.speech_bubble", "PetSpeechBubble", "paintEvent", _with_speech_bubble_PetSpeechBubble_paintEvent),
    ("pet.speech_bubble", "PetSpeechBubble", "_place", _with_speech_bubble_PetSpeechBubble_place),
    ("pet.modern_settings_dialog", "ModernSettingsDialog", "reject", _with_modern_settings_dialog_ModernSettingsDialog_reject),
    ("pet.modern_settings_dialog", "ModernSettingsDialog", "closeEvent", _with_modern_settings_dialog_ModernSettingsDialog_closeEvent),
    ("pet.dynamic_island", "DynamicIsland", "_on_card_toggle", _with_dynamic_island_DynamicIsland_on_card_toggle),
    ("pet.dynamic_island", "DynamicIsland", "_hidden_chat_enabled", _with_dynamic_island_DynamicIsland_hidden_chat_enabled),
    ("pet.dynamic_island", "DynamicIsland", "set_pet_visible", _with_dynamic_island_DynamicIsland_set_pet_visible),
    ("pet.dynamic_island", "DynamicIsland", "_status_dot_color", _with_dynamic_island_DynamicIsland_status_dot_color_2),
    ("pet.dynamic_island", "DynamicIsland", "_icon_pixmap", _with_dynamic_island_DynamicIsland_icon_pixmap),
    ("pet.island_chat", "IslandChatBubble", "show_for_island", _with_island_chat_IslandChatBubble_show_for_island),
    ("pet.island_chat", "IslandChatBubble", "show_feedback", _with_island_chat_IslandChatBubble_show_feedback),
    ("pet.island_chat", "IslandChatBubble", "position_near_pet", _with_island_chat_IslandChatBubble_position_near_pet),
    ("pet.quick_chat", "QuickChatBubble", "paintEvent", _with_quick_chat_QuickChatBubble_paintEvent),
    ("pet.quick_chat", "QuickChatBubble", "event", _with_quick_chat_QuickChatBubble_event),
    ("pet.quick_chat", "QuickChatBubble", "_close_if_still_inactive", _with_quick_chat_QuickChatBubble_close_if_still_inactive),
    ("pet.quick_chat", "QuickChatBubble", "closeEvent", _with_quick_chat_QuickChatBubble_closeEvent),
    ("pet.quick_chat", "QuickChatBubble", "_set_reply_text", _with_quick_chat_QuickChatBubble_set_reply_text),
    ("pet.quick_chat", "QuickChatBubble", "_render_reply", _with_quick_chat_QuickChatBubble_render_reply),
    ("pet.dynamic_island", "DynamicIsland", "_on_card_chat", _with_dynamic_island_DynamicIsland_on_card_chat),
    ("pet.dynamic_island", "DynamicIsland", "_rest_size", _with_dynamic_island_DynamicIsland_rest_size_3),
    ("pet.dynamic_island", "DynamicIsland", "_sync_card_labels", _with_dynamic_island_DynamicIsland_sync_card_labels_2),
    ("pet.dynamic_island", "DynamicIsland", "collapse_card", _with_dynamic_island_DynamicIsland_collapse_card),
    ("pet.dynamic_island", "DynamicIsland", "set_pet_visible", _with_dynamic_island_DynamicIsland_set_pet_visible_2),
    ("pet.window", "PetWindow", "__init__", _with_window_PetWindow_init),
    ("pet.window", "PetWindow", "mouseReleaseEvent", _with_window_PetWindow_mouseReleaseEvent),
    ("pet.modern_settings_dialog", "ModernSettingsDialog", "__init__", _with_modern_settings_dialog_ModernSettingsDialog_init),
    ("pet.modern_settings_dialog", "ModernSettingsDialog", "_write_config", _with_modern_settings_dialog_ModernSettingsDialog_write_config),
)
