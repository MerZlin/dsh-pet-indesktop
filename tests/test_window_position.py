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
        _screen_restore_armed = False
        _screen_retry_deadline = float("inf")

        def _screen_available(self, screen_name=None):
            if screen_name:
                for s in screens:
                    if s.name() == screen_name:
                        return s
            return primary

        def move(self, x, y):
            self.position = (x, y)

        def isVisible(self):
            return True  # 可见态：恢复后无需补 show

        def show(self):
            self.visible = True

        def _disarm_screen_restore_retry(self):
            self._awaiting_saved_screen = None

        _restore_position = PetWindow._restore_position
        _ensure_visible_after_restore = PetWindow._ensure_visible_after_restore
        _screen_retry_tick = PetWindow._screen_retry_tick

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


def test_disarmed_restore_does_not_move_on_late_screen():
    """用户手动接管（拖动/回右下角/超时）撤防后，目标屏上线也不再自动移动。"""
    primary = _Screen("primary", QRect(0, 0, 1920, 1080))
    secondary = _Screen("secondary", QRect(1920, 0, 1920, 1080))

    class FakePet:
        cfg = _Config(rx=0.5, ry=0.5, screen_name="secondary")
        _awaiting_saved_screen = None
        _screen_restore_armed = False
        _screen_retry_deadline = float("inf")

        def move(self, x, y):
            self.position = (x, y)

        _screen_retry_tick = PetWindow._screen_retry_tick
        _disarm_screen_restore_retry = PetWindow._disarm_screen_restore_retry

    pet = FakePet()
    pet.position = (100, 100)
    # 撤防后目标屏上线：不应触发任何移动
    PetWindow._on_screen_added_restore(pet, secondary)
    assert pet.position == (100, 100)


def test_save_position_skipped_while_awaiting_saved_screen():
    """等待副屏上线期间：_save_position 不得把临时落脚屏的坐标/屏名写回配置。"""
    primary = _Screen("primary", QRect(0, 0, 1920, 1080))

    class FakePet:
        cfg = _Config(rx=0.5, ry=0.5, screen_name="secondary")
        facing = "left"
        scale = 1.0
        _w = 220
        _h = 260
        _awaiting_saved_screen = "secondary"  # 正在等副屏上线

        def _screen_available(self):
            return primary

        def x(self):
            return 100

        def y(self):
            return 200

    pet = FakePet()
    PetWindow._save_position(pet)
    # 副屏坐标与屏名必须原样保留
    assert pet.cfg["screen_name"] == "secondary"
    assert pet.cfg["rx"] == 0.5 and pet.cfg["ry"] == 0.5

    pet._awaiting_saved_screen = None  # 恢复完成后正常保存
    PetWindow._save_position(pet)
    assert pet.cfg["screen_name"] == "primary"


def test_retry_timeout_forces_restore_onto_primary_and_visible():
    """幻影屏兜底：等目标屏超时后不允许放弃——窗口必须强制落到当前主屏并可见。

    真实案例：打包 smoke 启动时 Qt 枚举到空名字/假几何（799x799）的占位屏，
    窗口 show() 到幻影坐标系后桌面不可见（MainWindowHandle=0），旧逻辑超时
    直接放弃，窗口永远不出现。"""
    primary = _Screen("primary", QRect(0, 0, 1920, 1080))

    class FakePet:
        cfg = _Config(rx=0.5, ry=0.5, screen_name="secondary")
        _w = 220
        _h = 260
        _awaiting_saved_screen = "secondary"
        _screen_restore_armed = False
        _screen_retry_deadline = -1.0  # 已超时
        shown = 0

        def _screen_available(self, screen_name=None):
            return primary

        def move(self, x, y):
            self.position = (x, y)

        def isVisible(self):
            return False

        def show(self):
            self.shown += 1

        def _disarm_screen_restore_retry(self):
            self._awaiting_saved_screen = None

        _restore_position = PetWindow._restore_position
        _force_show_on_primary = PetWindow._force_show_on_primary
        _ensure_visible_after_restore = PetWindow._ensure_visible_after_restore
        _screen_retry_tick = PetWindow._screen_retry_tick

    import time as _time
    pet = FakePet()
    pet.position = None
    PetWindow._screen_retry_tick(pet)
    assert pet._awaiting_saved_screen is None  # 兜底后撤防
    assert pet.position is not None            # 已落到主屏
    assert pet.shown == 1                      # 隐藏态下被强制显示


def test_retry_success_ensures_visibility_after_restore():
    """目标屏上线恢复后，若窗口此前不可见（自动隐藏/未显示），必须补 show()。"""
    primary = _Screen("primary", QRect(0, 0, 1920, 1080))
    secondary = _Screen("secondary", QRect(1920, 0, 1920, 1080))
    screens = [primary, secondary]

    class FakePet:
        cfg = _Config(rx=0.5, ry=0.5, screen_name="secondary")
        _w = 220
        _h = 260
        _awaiting_saved_screen = "secondary"
        _screen_restore_armed = False
        _screen_retry_deadline = float("inf")
        shown = 0

        def _screen_available(self, screen_name=None):
            if screen_name:
                for s in screens:
                    if s.name() == screen_name:
                        return s
            return primary

        def move(self, x, y):
            self.position = (x, y)

        def isVisible(self):
            return False

        def show(self):
            self.shown += 1

        def _disarm_screen_restore_retry(self):
            self._awaiting_saved_screen = None

        _restore_position = PetWindow._restore_position
        _ensure_visible_after_restore = PetWindow._ensure_visible_after_restore
        _screen_retry_tick = PetWindow._screen_retry_tick

    pet = FakePet()
    pet.position = None
    PetWindow._screen_retry_tick(pet)
    assert pet._awaiting_saved_screen is None
    assert pet.position == (2770, 410)
    assert pet.shown == 1
