from __future__ import annotations


class PetChatLink:
    def __init__(self, pet_window=None):
        self.pet_window = pet_window
        self.on_status = None

    def set_window(self, w):
        self.pet_window = w

    def _notify(self, state, text=''):
        if self.on_status:
            self.on_status(state, text)
        if self.pet_window and hasattr(self.pet_window, 'set_chat_status'):
            self.pet_window.set_chat_status(state, text)

    def thinking(self, text='正在想怎么回答…'):
        self._notify('thinking', text)

    def streaming(self, text):
        self._notify('streaming', text[:60])

    def success(self):
        self._notify('success', '')

    def error(self, text):
        self._notify('error', text[:80])
