from collections import deque

from PySide6.QtWidgets import QApplication

from pet.window import PetWindow


def _app():
    return QApplication.instance() or QApplication([])


class _Bubble:
    def __init__(self):
        self.shown = []

    def hide(self):
        pass

    def show_text(self, text, *args, **kwargs):
        self.shown.append((text, kwargs))

    def dismiss(self):
        pass


def _pet():
    pet = type("Pet", (), {})()
    pet._bubble_suppressed = False
    pet._alert_current = None
    pet._alert_queue = deque()
    pet._sticky_bubble_active = False
    pet._sticky_text = pet._sticky_subtitle = ""
    pet._sticky_buttons = None
    pet._speech_bubble = _Bubble()
    pet.scale = 1.0
    pet._pump_alerts = lambda: PetWindow._pump_alerts(pet)
    pet.isVisible = lambda: True
    pet.visible_content_rect = lambda: None
    return pet


def test_settings_suppression_keeps_interaction_alert_for_later_display():
    _app()
    pet = _pet()
    PetWindow.set_bubble_suppressed(pet, True)

    PetWindow.show_alert(
        pet, "需要批准", sticky=True, alert_id="interaction:approval:r1",
        alert_type="approval", priority=0,
    )

    assert len(pet._alert_queue) == 1
    assert pet._speech_bubble.shown == []

    PetWindow.set_bubble_suppressed(pet, False)
    assert pet._speech_bubble.shown == [
        ("需要批准", {"pet_scale": 1.0, "subtitle": "", "sticky": True, "buttons": None})
    ]


def test_settings_suppression_still_drops_ordinary_alerts():
    _app()
    pet = _pet()
    PetWindow.set_bubble_suppressed(pet, True)

    PetWindow.show_alert(
        pet, "普通提醒", duration_ms=1000, sticky=False, alert_type="watchdog"
    )

    assert pet._alert_current is None
    assert list(pet._alert_queue) == []
