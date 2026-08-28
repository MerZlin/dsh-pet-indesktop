from PySide6.QtCore import QRect

from pet.window import PetWindow


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value

    def save(self):
        pass


class _Screen:
    def __init__(self, name, geometry):
        self._name = name
        self._geometry = geometry

    def name(self):
        return self._name

    def availableGeometry(self):
        return self._geometry

    def devicePixelRatio(self):
        return 1.0


def test_save_position_records_secondary_screen():
    secondary = _Screen("secondary", QRect(1920, 0, 1920, 1080))

    class FakePet:
        cfg = _Config()
        facing = "left"
        scale = 1.0
        _w = 220
        _h = 260

        def _screen_available(self):
            return secondary

        def x(self):
            return 2500

        def y(self):
            return 500

    pet = FakePet()
    PetWindow._save_position(pet)

    assert pet.cfg["screen_name"] == "secondary"


def test_restore_position_uses_saved_secondary_screen_after_restart():
    primary = _Screen("primary", QRect(0, 0, 1920, 1080))
    secondary = _Screen("secondary", QRect(1920, 0, 1920, 1080))

    class FakePet:
        cfg = _Config(rx=0.5, ry=0.5, screen_name="secondary")
        _w = 220
        _h = 260

        def _screen_available(self, screen_name=None):
            return secondary if screen_name == "secondary" else primary

        def move(self, x, y):
            self.position = (x, y)

    pet = FakePet()
    PetWindow._restore_position(pet)

    assert pet.position == (2770, 410)


def test_named_screen_lookup_and_config_persistence(tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    from pet.config import Config

    primary = _Screen("primary", QRect(0, 0, 1920, 1080))
    secondary = _Screen("secondary", QRect(1920, 0, 1920, 1080))
    monkeypatch.setattr(
        QGuiApplication, "screens", staticmethod(lambda: [primary, secondary])
    )

    class FakePet:
        def screen(self):
            return primary

    assert PetWindow._screen_available(FakePet(), "secondary") is secondary

    config = Config(tmp_path)
    config.set("screen_name", "secondary")
    config.save()
    assert Config(tmp_path).get("screen_name") == "secondary"


def test_restore_defers_until_saved_screen_comes_online():
    """issue #8：开机自启时副屏未就绪，先落主屏；副屏上线后自动恢复。"""
    primary = _Screen("primary", QRect(0, 0, 1920, 1080))
    secondary = _Screen("secondary", QRect(1920, 0, 1920, 1080))
    screens = [primary]  # 模拟开机自启瞬间：副屏还没被枚举到

    class FakePet:
        cfg = _Config(rx=0.5, ry=0.5, screen_name="secondary")
        _w = 220
        _h = 260
        _awaiting_saved_screen = None

        def _screen_available(self, screen_name=None):
            if screen_name:
                for s in screens:
                    if s.name() == screen_name:
                        return s
            return primary

        def move(self, x, y):
            self.position = (x, y)

        def _disarm_screen_restore_retry(self):
            self._awaiting_saved_screen = None

        _restore_position = PetWindow._restore_position

    pet = FakePet()
    PetWindow._restore_position(pet)
    # 目标屏不在线：先落主屏，并记下目标屏等待
    assert pet._awaiting_saved_screen == "secondary"

    # 无关屏幕上线：不触发
    PetWindow._on_screen_added_restore(pet, _Screen("other", QRect(0, 0, 800, 600)))
    assert pet._awaiting_saved_screen == "secondary"

    # 副屏上线：自动恢复到副屏坐标，且撤防
    screens.append(secondary)
    PetWindow._on_screen_added_restore(pet, secondary)
    assert pet._awaiting_saved_screen is None
    assert pet.position == (2770, 410)
