# -*- coding: utf-8 -*-
"""音效配置与迁移测试、真实 dt 与长帧物理积分测试。"""
import json
from pathlib import Path

from pet.config import Config
from pet import physics as physics_mod
from pet import window as window_mod


def test_legacy_click_sound_path_migration(tmp_path: Path):
    """旧配置迁移：只包含旧字段 click_sound_path 时迁移到 click_sound_pack。"""
    root = tmp_path / "appdata"
    cfg_dir = root / "dsh-pet-standalone"
    cfg_dir.mkdir(parents=True)
    custom_path = "C:/path/to/custom_sound.mp3"
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, "click_sound_path": custom_path}),
        encoding="utf-8",
    )

    cfg = Config(root)
    pack = cfg.get("click_sound_pack")
    assert pack == {
        "kind": "file",
        "id": "custom",
        "path": custom_path,
    }
    assert cfg.get("click_sound_volume") == 0.70
    assert cfg.get("slingshot_enabled") is True
    assert cfg.get("throw_strength") == "standard"
    assert cfg.get("throw_max_speed") == 4800.0


def test_legacy_click_sound_path_empty_migrates_to_builtin(tmp_path: Path):
    """旧配置 click_sound_path 为空时迁移为 builtin default。"""
    root = tmp_path / "appdata"
    cfg_dir = root / "dsh-pet-standalone"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({"version": 4, "click_sound_path": ""}),
        encoding="utf-8",
    )

    cfg = Config(root)
    pack = cfg.get("click_sound_pack")
    assert pack == {
        "kind": "builtin",
        "id": "default",
        "path": "",
    }


def test_explicit_click_sound_pack_precedence(tmp_path: Path):
    """新配置已有合法 click_sound_pack 时不受 click_sound_path 影响。"""
    root = tmp_path / "appdata"
    cfg_dir = root / "dsh-pet-standalone"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({
            "version": 4,
            "click_sound_path": "C:/old.mp3",
            "click_sound_pack": {"kind": "builtin", "id": "duck", "path": ""},
        }),
        encoding="utf-8",
    )

    cfg = Config(root)
    pack = cfg.get("click_sound_pack")
    assert pack == {
        "kind": "builtin",
        "id": "duck",
        "path": "",
    }


def test_invalid_sound_and_physics_config_normalization(tmp_path: Path):
    """异常或脏配置的归一化处理。"""
    root = tmp_path / "appdata"
    cfg_dir = root / "dsh-pet-standalone"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(
        json.dumps({
            "version": 4,
            "click_sound_volume": "invalid",
            "click_sound_pack": "bad_type",
            "throw_strength": "super_crazy",
        }),
        encoding="utf-8",
    )

    cfg = Config(root)
    assert cfg.get("click_sound_volume") == 0.70
    assert cfg.get("click_sound_pack") == {"kind": "builtin", "id": "default", "path": ""}
    assert cfg.get("throw_strength") == "standard"
    assert cfg.get("throw_max_speed") == 4800.0


def test_throw_physics_substep_long_frame():
    """测试长帧（如 dt=0.1s）时的子步积分稳定性：不超过边界、多个子步拆分。"""
    step_dts = []
    orig_throw_step = physics_mod.throw_step

    def instrumented_throw_step(px, py, vx, vy, dt, left, top, right, bottom, gravity=physics_mod.GRAVITY):
        step_dts.append(dt)
        return orig_throw_step(px, py, vx, vy, dt, left, top, right, bottom, gravity)

    class FakeScreen:
        def availableGeometry(self):
            class Geometry:
                def left(self): return 0
                def top(self): return 0
                def right(self): return 1920
                def bottom(self): return 1080
            return Geometry()

    class FakePetWindow:
        _w = 300
        _h = 300
        _phys_pos = [500.0, 500.0]
        _phys_vel = [1200.0, -800.0]
        _moved_to = None
        _stopped = False
        _saved = False

        def _screen_available(self):
            return FakeScreen()

        def move(self, x, y):
            self._moved_to = (x, y)

        def _stop_physics(self):
            self._stopped = True

        def _save_position(self):
            self._saved = True

    pet = FakePetWindow()
    # 模拟 0.1s 长帧
    # 猴子补丁 instrumented_throw_step
    saved_fn = physics_mod.throw_step
    physics_mod.throw_step = instrumented_throw_step
    try:
        window_mod.PetWindow._tick_throw_physics(pet, dt=0.1)
    finally:
        physics_mod.throw_step = saved_fn

    # 验证子步：0.1s 被拆分成不超过 0.008s 的子步
    assert len(step_dts) == 13  # 12 * 0.008 + 0.004 = 0.1
    for sub_dt in step_dts:
        assert sub_dt <= 0.008 + 1e-6
    assert abs(sum(step_dts) - 0.1) < 1e-6
    assert pet._moved_to is not None
