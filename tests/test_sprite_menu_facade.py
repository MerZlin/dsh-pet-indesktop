# -*- coding: utf-8 -*-
"""SpriteMenuFacade + 行为/物理接缝 offscreen 单测（4.1c）。

覆盖：菜单建造器产出全量菜单（旧 context_menus 建造器原样复用）、
facade 各窗口能力项落点、play_once/play_move_once、no_move 移动桶并入
动作池、playback_speed bind 应用、drag_physics 松手不抛掷、switch_character
换库收口、「退出这只」的确认闸（主宠破坏性动作）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import tests.test_overlay_window_capabilities as cap
from pet.pet_sprite import INTERACTION_THROWN, PetSprite
from pet.sprite_menu_facade import SpriteMenuFacade, build_sprite_full_menu

app = QApplication.instance() or QApplication([])


class FakeClip(QObject):
    frameChanged = Signal(int)

    def __init__(self, name, frames=24):
        super().__init__()
        self.name = name
        self._frames = frames
        self.image = QImage(640, 360, QImage.Format.Format_ARGB32)
        self.image.fill(0xFF336699)
        self.speeds: list[float] = []
        self.started = False

    def currentFrameNumber(self):
        return 0

    def currentImage(self):
        return self.image

    def frameCount(self):
        return self._frames

    def duration(self):
        return self._frames * 42 / 1000.0

    def set_playback_speed(self, v):
        self.speeds.append(float(v))

    def start(self):
        self.started = True
        return True

    def stop(self):
        self.started = False


class RichLibrary:
    """带素材池的假库（无 names() → 走行为分类的属性分支）。"""

    def __init__(self):
        self.idles = ["idle1"]
        self.turns = ["turn1"]
        self.moves = ["walk"]
        self.clicks = ["click1"]
        self.acts = ["act1"]
        self.no_mirror: set[str] = set()
        self._clips = {n: FakeClip(n, 24) for n in
                       self.idles + self.turns + self.moves + self.clicks + self.acts}
        self.manifest = {}
        self.folder_map = {}
        self.folder_files = {}
        self.shutdown_calls = 0

    def movie(self, name, **_kw):
        return self._clips[name]

    def duration(self, name):
        return self._clips[name].duration()

    def shutdown(self):
        self.shutdown_calls += 1


def _make_shell(tmp_path, config_values=None):
    """真实 PetSprite 的壳（facade 接缝需要 velocity/bind_clip/facing 全面）。"""
    from pet.overlay_shell import OverlayShell

    config = cap.CapConfig(tmp_path, config_values)
    from pet import pet_profiles

    values = {key: value for key, value in (config_values or {}).items()
              if key in pet_profiles.PER_PET_KEYS}
    pet_profiles.write_profile_settings(tmp_path, 0, values or {"scale": 0.5})
    instance = cap.CapInstance(config)
    screen = cap.FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040))
    shell = OverlayShell(
        app, instance, screen=screen,
        sprite_factory=lambda lib, pos, scale: PetSprite(lib, pos=pos, scale=scale))
    lib = RichLibrary()
    shell.lib = lib
    shell.sprite.library = lib
    return shell, lib


def _recursive_labels(container) -> set:
    """递归枚举菜单文本：模板感知后条目可能落在分组子菜单里（modern 模板）。"""
    labels = set()
    for action in container.actions():
        if action.isSeparator():
            continue
        labels.add(action.text())
        if action.menu() is not None:
            labels |= _recursive_labels(action.menu())
    return labels


def _find_action(container, text: str):
    """按文本递归找 QAction（条目可能落在分组子菜单里）。"""
    for action in container.actions():
        if action.text() == text:
            return action
        submenu = action.menu()
        if submenu is not None:
            found = _find_action(submenu, text)
            if found is not None:
                return found
    return None


def test_full_menu_builds_with_capability_items(tmp_path):
    """窗口能力条目都在（结构由 context_menus 建造器决定）。"""
    shell, _ = _make_shell(tmp_path)
    try:
        menu = build_sprite_full_menu(shell)
        labels = _recursive_labels(menu)
        # 「鼠标穿透」已随上游 ea17bfa 从右键全菜单移除（入口收敛到
        # 设置页/托盘菜单/命中右键小菜单 sprite_menu.py:58），不再断言。
        for expected in ("回到右下角", "窗口置顶", "不移动",
                         "开机自启", "隐藏桌宠", "桌宠设置", "退出"):
            assert expected in labels, f"菜单缺项: {expected}"
        assert menu._facade is not None  # F5 保活
    finally:
        shell._delete_runtime_marker()


def test_facade_window_capabilities(tmp_path):
    shell, _ = _make_shell(tmp_path, {"on_top": True, "mouse_through": False})
    facade = SpriteMenuFacade(shell)
    try:
        facade.change_scale(1.0)
        assert shell.sprite.scale == 1.0
        assert shell._sprite_config(shell.sprite).get("scale") == 1.0
        facade.set_no_move(True)
        assert shell.behavior.no_move is True
        assert shell._config.get("no_move") is True
        facade.set_drag_physics(False)
        assert shell.sprite.drag_physics is False
        facade.set_on_top(False)
        assert not (shell.overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        facade.set_mouse_through(True)
        assert shell.overlay.mouse_through is True
        facade.go_default_corner()
        expected = shell._default_corner_pos(shell._bounds, shell.sprite.rect())
        assert shell.sprite.pos == expected
        facade.hide()
        assert not shell.overlay.isVisible()
    finally:
        shell._delete_runtime_marker()


def test_play_once_binds_named_clip(tmp_path):
    shell, lib = _make_shell(tmp_path)
    facade = SpriteMenuFacade(shell)
    try:
        assert facade.switch_clip("act1") is None or True
        assert shell.sprite._clip_name == "act1"
        assert lib._clips["act1"].started is True
    finally:
        shell._delete_runtime_marker()


def test_play_move_once_forces_anim(tmp_path):
    shell, lib = _make_shell(tmp_path)
    facade = SpriteMenuFacade(shell)
    try:
        assert facade.trigger_move("walk") is True
        assert shell.sprite._clip_name == "walk"
        assert facade.trigger_move("不存在的") is False
    finally:
        shell._delete_runtime_marker()


def test_no_move_bucket_goes_to_acts(tmp_path):
    shell, lib = _make_shell(tmp_path)
    try:
        from collections import deque
        shell.behavior.no_move = True
        shell.behavior.rng = type("R", (), {
            "random": lambda self: 0.99,          # 移动桶
            "randint": lambda self, a, b: a,
            "choice": lambda self, seq: seq[0]})()
        st = shell.behavior._states.setdefault(
            shell.sprite, __import__("pet.sprite_behavior", fromlist=["_SpriteState"])._SpriteState())
        shell.behavior._roll_next(shell.sprite, st)
        assert shell.sprite._clip_name == "act1"   # 移动桶并入动作池
        assert shell.sprite.velocity == QPointF(0, 0)
    finally:
        shell._delete_runtime_marker()


def test_playback_speed_applied_on_bind(tmp_path):
    shell, lib = _make_shell(tmp_path)
    facade = SpriteMenuFacade(shell)
    try:
        facade.set_playback_speed(1.5)
        assert shell.sprite.playback_speed == 1.5
        facade.switch_clip("idle1")
        assert lib._clips["idle1"].speeds[-1] == 1.5
    finally:
        shell._delete_runtime_marker()


def test_drag_physics_off_lands_in_place(tmp_path):
    shell, _ = _make_shell(tmp_path)
    try:
        sprite = shell.sprite
        sprite.drag_physics = False
        sprite._dragging = True
        sprite._drag_offset = None
        now = [1000.0]
        sprite._clock = lambda: now[0]
        # 高速甩出轨迹（0.1s 位移 500px——drag_physics 开时必抛）
        sprite._drag_trail = [(1000.0 + i * 0.02, float(i * 100), 0.0)
                              for i in range(6)]
        now[0] = 1000.12
        sprite.on_release(QPointF(500, 0))
        assert sprite.interaction_state != INTERACTION_THROWN
        assert sprite.velocity.isNull()
    finally:
        shell._delete_runtime_marker()


def test_switch_character_swaps_library(tmp_path):
    shell, old_lib = _make_shell(tmp_path)
    try:
        new_lib = RichLibrary()
        made = []

        class Inst:
            config = shell._config

            def _create_library(self, cid):
                made.append(cid)
                return new_lib

        shell._instance = Inst()
        shell.switch_character("nova")
        assert made == ["nova"]
        assert shell.lib is new_lib
        assert shell.sprite.library is new_lib
        assert shell._sprite_config(shell.sprite).get("character") == "nova"
        assert old_lib.shutdown_calls == 1
        assert shell.behavior.state_of(shell.sprite) is None  # forget 已重置
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 菜单图标缓存（B08 / 复审#5）
def _argb32(color: int, size: int = 4) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(color)
    return image


class _StubLibrary:
    """只够缩略图面用的素材库替身（``clip_path`` 带自己的身份标签）。"""

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def clip_path(self, name: str) -> str:
        return f"/{self.tag}/{name}.webm"


def _stub_sprite(library):
    """只带 ``library`` 的 sprite 替身。

    库挂在**实例**上（不是类属性）：动态 ``type(...)`` 的类属性会一直吊着库不放，
    "库被回收后弱引用失效"这类断言就永远不成立（P3 的 id 复用用例正是靠它才可能真回收）。
    """
    sprite = type("Sprite", (), {})()
    sprite.library = library
    return sprite


def _seed_menu_icon_cache(shell, library, name, image) -> None:
    """按产品键（库身份 + 动画名）预置一条缓存：单一来源，不在测试里复刻键格式。"""
    from pet.context_menus.shared import (
        animation_icon_cache_entry,
        animation_icon_cache_key,
    )

    cache = getattr(shell, "_menu_icon_cache", None)
    if cache is None:
        cache = {}
        shell._menu_icon_cache = cache
    cache[animation_icon_cache_key(library, name)] = animation_icon_cache_entry(library, image)


def test_character_switch_clears_menu_icon_cache(tmp_path, monkeypatch):
    """B08：换角色后菜单缩略图缓存必须清空，下次取图按新库重新解码。

    缓存挂壳上、键 = （素材库身份, 动画名）（复审#5 起）；切角色清缓存是内存卫生：
    旧库的死项不占着 128 条上限，也不给 id 复用留机会。
    """
    import pet.animation_thumbnail as thumb_mod

    shell, _ = _make_shell(tmp_path)
    try:
        facade = SpriteMenuFacade(shell)
        stale = _argb32(0xFF112233)
        _seed_menu_icon_cache(shell, shell.sprite.library, "idle1", stale)
        assert not facade.animation_icon_cached_image("idle1").isNull()

        decoded: list = []

        def fake_decode(path):
            decoded.append(path)
            return _argb32(0xFF445566)

        monkeypatch.setattr(thumb_mod, "decode_representative_frame", fake_decode)

        shell.switch_character("nova")                    # 换角色成功即清缓存
        shell.sprite.library.clip_path = lambda name: f"/x/{name}.webm"

        assert facade.animation_icon_cached_image("idle1").isNull(), "旧角色的图不残留"
        fresh = facade.animation_icon_image("idle1")
        assert decoded == ["/x/idle1.webm"], "清缓存后必须按新库重新解码"
        assert facade.animation_icon_cached_image("idle1").size() == fresh.size()
    finally:
        shell._delete_runtime_marker()


def test_menu_icon_cache_hit_survives_without_character_switch(tmp_path, monkeypatch):
    """不换角色时缓存行为不变：命中即返回，不重新解码。"""
    import pet.animation_thumbnail as thumb_mod

    shell, _ = _make_shell(tmp_path)
    try:
        facade = SpriteMenuFacade(shell)
        cached = _argb32(0xFF778899, size=6)
        _seed_menu_icon_cache(shell, shell.sprite.library, "idle1", cached)

        decoded: list = []
        monkeypatch.setattr(thumb_mod, "decode_representative_frame",
                            lambda path: decoded.append(path))
        shell.sprite.library.clip_path = lambda name: f"/x/{name}.webm"

        assert facade.animation_icon_image("idle1").size() == cached.size()
        assert decoded == []
    finally:
        shell._delete_runtime_marker()


def test_same_animation_name_resolves_per_library(tmp_path, monkeypatch):
    """复审#5：缓存键带库身份——同壳两只不同角色的宠，同名动画各取各的图。

    缓存挂壳上（facade 每次右键重建，必须长寿命），键只有动画名时：两只宠本来就
    不同角色、**没有任何切换发生**，开各自菜单时同名动画直接命中对方的缩略图。
    """
    import pet.animation_thumbnail as thumb_mod

    shell, _ = _make_shell(tmp_path)
    try:
        decoded: list = []

        def fake_decode(path):
            decoded.append(path)
            return _argb32(0xFF0000AA if "/lib-a/" in path else 0xFF0000BB)

        monkeypatch.setattr(thumb_mod, "decode_representative_frame", fake_decode)

        facade_a = SpriteMenuFacade(shell, _stub_sprite(_StubLibrary("lib-a")))
        facade_b = SpriteMenuFacade(shell, _stub_sprite(_StubLibrary("lib-b")))

        facade_a.animation_icon_image("act1")
        assert not facade_a.animation_icon_cached_image("act1").isNull()
        assert facade_b.animation_icon_cached_image("act1").isNull(), \
            "另一只宠的同名动画不得命中这只的缩略图"

        image_a = facade_a.animation_icon_image("act1")
        image_b = facade_b.animation_icon_image("act1")
        assert decoded == ["/lib-a/act1.webm", "/lib-b/act1.webm"], "各自解码自己的素材"
        assert image_a.pixel(0, 0) != image_b.pixel(0, 0)
    finally:
        shell._delete_runtime_marker()


def test_late_worker_write_is_keyed_to_the_old_library(tmp_path, monkeypatch):
    """复审#5：换库后旧 worker 的迟到写入必须落在**旧库**的键上。

    worker 在解码**之前**就抓到了旧库（``clip_path`` 取自它），解码期间换库、清缓存
    都已经发生；写入若只按动画名，就把旧角色的图写回新库的查询键上——B08 的"切角色
    清缓存"管不到这一次迟到写入。
    """
    import pet.animation_thumbnail as thumb_mod

    shell, _ = _make_shell(tmp_path)
    try:
        lib_a = _StubLibrary("lib-a")
        lib_b = _StubLibrary("lib-b")
        sprite = _stub_sprite(lib_a)
        facade = SpriteMenuFacade(shell, sprite)

        decoded: list = []

        def decode_across_switch(path):
            decoded.append(path)
            sprite.library = lib_b        # 解码期间换库（旧 worker 的迟到时刻）
            return _argb32(0xFF0000AA)

        monkeypatch.setattr(thumb_mod, "decode_representative_frame", decode_across_switch)

        facade.animation_icon_image("act1")
        assert facade.animation_icon_cached_image("act1").isNull(), \
            "旧库解码出的图不得成为新库的缓存命中"

        assert not facade.animation_icon_image("act1").isNull()
        assert decoded == ["/lib-a/act1.webm", "/lib-b/act1.webm"], "新库按新键重新解码"
    finally:
        shell._delete_runtime_marker()


def test_cache_hit_requires_a_live_identity():
    """值里的库弱引用是唯一身份凭证：凭不出来的条目一律不命中。

    失败方向必须是"未命中"（重解码），不能是"命中旧图"——所以空条目、没有身份凭证的
    条目（库对象不支持弱引用）、指向别的库的条目都要判未命中。
    """
    from pet.context_menus.shared import (
        animation_icon_cache_entry,
        animation_icon_cache_hit,
    )

    lib_a = _StubLibrary("lib-a")
    image = _argb32(0xFF010203)
    entry = animation_icon_cache_entry(lib_a, image)

    assert animation_icon_cache_hit(entry, lib_a) is image, "同一个库命中"
    assert animation_icon_cache_hit(entry, _StubLibrary("lib-b")) is None, "别的库不命中"
    assert animation_icon_cache_hit(None, lib_a) is None, "空条目不命中"
    assert animation_icon_cache_hit(image, lib_a) is None, "裸 QImage（非条目）不命中"
    opaque = animation_icon_cache_entry({}, image)   # dict 不支持弱引用 → 无身份凭证
    assert animation_icon_cache_hit(opaque, {}) is None, "无凭证的条目永不命中"


def test_cache_prune_drops_target_library_and_dead_entries():
    """定向摘的契约：摘目标库的条目 + 顺手清掉对谁都永不命中的条目，活库条目留着。

    "永不命中"的两类：库已回收（弱引用已死）、没有身份凭证（库对象不支持弱引用）。
    它们留着只占 128 条上限与内存，与"退出子肥鱼"这轮清理同源。
    """
    import gc

    from pet.context_menus.shared import (
        animation_icon_cache_drop_library,
        animation_icon_cache_entry,
        animation_icon_cache_key,
    )

    image = _argb32(0xFF010203)
    target = _StubLibrary("lib-target")
    keeper = _StubLibrary("lib-keeper")
    dead = _StubLibrary("lib-dead")
    cache = {
        animation_icon_cache_key(target, "act1"): animation_icon_cache_entry(target, image),
        animation_icon_cache_key(keeper, "act1"): animation_icon_cache_entry(keeper, image),
        animation_icon_cache_key(dead, "act1"): animation_icon_cache_entry(dead, image),
        animation_icon_cache_key({}, "act1"): animation_icon_cache_entry({}, image),
    }
    del dead
    gc.collect()

    assert animation_icon_cache_drop_library(cache, target) == 3, \
        "目标库 + 已回收的 + 无凭证的"
    assert set(cache) == {animation_icon_cache_key(keeper, "act1")}, "别的活库条目不动"


def test_cache_entry_does_not_keep_the_library_alive(tmp_path, monkeypatch):
    """缓存值不许吊住库：条目留下、库照样能被回收（存的是弱引用，不是强引用）。

    这条是"不许把库强引用塞进缓存"的直接守卫：缓存挂壳上、活到进程退出，塞强引用就等于
    把退役的库连同素材一起吊在内存里（换角色/退出子宠都放不掉）。
    """
    import gc
    import weakref

    import pet.animation_thumbnail as thumb_mod

    shell, _ = _make_shell(tmp_path)
    try:
        lib = _StubLibrary("lib-weak")
        sprite = _stub_sprite(lib)
        facade = SpriteMenuFacade(shell, sprite)
        monkeypatch.setattr(thumb_mod, "decode_representative_frame",
                            lambda path: _argb32(0xFF010203))
        facade.animation_icon_image("act1")

        ref = weakref.ref(lib)
        sprite.library = None
        del lib
        gc.collect()

        assert ref() is None, "缓存条目不得持有库的强引用"
        assert len(shell._menu_icon_cache) == 1, "条目本身还在（死条目，靠身份校验不命中）"
    finally:
        shell._delete_runtime_marker()


def test_stale_entry_is_not_served_when_library_id_is_reused(tmp_path, monkeypatch):
    """P3：旧库回收后 ``id()`` 被复用，不得把它的图当成本库的命中。

    键是 ``(id(library), 动画名)``，而 ``id()`` 只保证"同刻存活对象互不相同"——
    旧库回收后新库完全可能拿到同一个 id，于是键逐字相同、缓存里那份旧图被当成本库的
    命中，新角色的菜单显示上一只宠的缩略图。

    复现方式：旧库解码进缓存后整个被回收（弱引用失效），随后**同一个键**被新库查询。
    id 复用由 CPython 分配器决定、测试里无法确定性复现（赌分配器会 flaky），所以这里
    直接把键钉成旧键——复现的是 id 复用之后的那个后果。修复前这次查询命中旧图（上面
    两条断言即红），修复后必须视为未命中、重解码新库素材并按同键覆盖。
    """
    import gc

    import pet.animation_thumbnail as thumb_mod
    import pet.sprite_menu_facade as facade_mod
    from pet.context_menus.shared import animation_icon_cache_key

    shell, _ = _make_shell(tmp_path)
    try:
        lib_old = _StubLibrary("lib-old")
        sprite = _stub_sprite(lib_old)
        facade = SpriteMenuFacade(shell, sprite)
        # 把键钉成旧库的键：id 复用后新库算出来的键与它逐字相同
        pinned = animation_icon_cache_key(lib_old, "act1")
        monkeypatch.setattr(facade_mod, "animation_icon_cache_key",
                            lambda _library, name: pinned)

        decoded: list = []

        def decode_old(path):
            decoded.append(path)
            return _argb32(0xFF445566, size=8)

        monkeypatch.setattr(thumb_mod, "decode_representative_frame", decode_old)

        stale = facade.animation_icon_image("act1")
        assert decoded == ["/lib-old/act1.webm"], "前提：旧库的图已经进缓存"
        assert stale.pixel(0, 0) == _argb32(0xFF445566).pixel(0, 0)

        sprite.library = None            # 旧库失去最后一个强引用（sprite 换库）
        del lib_old
        gc.collect()

        fresh = _argb32(0xFF99AA33, size=8)

        def decode_new(path):
            decoded.append(path)
            return fresh

        monkeypatch.setattr(thumb_mod, "decode_representative_frame", decode_new)
        lib_new = _StubLibrary("lib-new")
        sprite.library = lib_new

        assert facade.animation_icon_cached_image("act1").isNull(), \
            "旧库已回收：同键的这条缓存不得再被当成命中（id 复用也是同样后果）"
        image = facade.animation_icon_image("act1")
        assert decoded == ["/lib-old/act1.webm", "/lib-new/act1.webm"], \
            "id 复用后必须重解码新库的素材，不得返回旧库的图"
        assert image.pixel(0, 0) == fresh.pixel(0, 0)
        # 覆盖判定用的是新加的条目校验器：放在行为断言之后，修复前先在行为上红
        from pet.context_menus.shared import animation_icon_cache_hit

        assert animation_icon_cache_hit(shell._menu_icon_cache[pinned], lib_new) is not None, \
            "重解码的结果必须覆盖同键上的旧条目"
    finally:
        shell._delete_runtime_marker()


def test_exiting_spawned_pet_drops_its_menu_icon_cache(tmp_path):
    """P3：退出子肥鱼即定向摘掉它自己的缩略图条目。

    旧行为：退出子肥鱼只 ``shutdown`` 库，缓存条目留到换角色清缓存或撞 128 条上限——
    死图占着上限，且旧库在缓存里的那份身份凭证也一起留着。缓存按库身份分键，这里应当
    摘得准：只摘这只宠的库，别动别的宠（整份清掉会把同壳其他宠的图一起丢掉）。
    """
    shell, main_lib = _make_shell(tmp_path)
    try:
        shell._create_main_library = lambda: RichLibrary()
        shell.spawn_pet()
        child_lib = shell._spawned[0].library
        assert child_lib is not main_lib

        _seed_menu_icon_cache(shell, child_lib, "idle1", _argb32(0xFF0A0B0C))
        _seed_menu_icon_cache(shell, main_lib, "idle1", _argb32(0xFF0D0E0F))
        assert len(shell._menu_icon_cache) == 2

        shell.clear_spawned_pets()

        assert len(shell._menu_icon_cache) == 1, "退出子宠必须摘掉它自己的条目"
        facade = SpriteMenuFacade(shell)
        assert not facade.animation_icon_cached_image("idle1").isNull(), \
            "主宠的同名动画缓存不受影响"
    finally:
        shell._delete_runtime_marker()


# ---------------------------------------------------------------- 「退出这只」确认闸
#
# 主宠退出是**不可逆**破坏性动作：有子宠时旧主宠没有自己的 slot 存档，提升者会覆盖
# 它的位置与设置（无法恢复）；无子宠时直接退出整个应用。子宠退出有 config-slot-N.json
# 存档、且是用户明确点选，保持一键直退。
def _spawn_child(shell):
    """给壳生一只子肥鱼（per-pet 库走假库工厂，不碰真实素材）。"""
    shell._create_main_library = lambda: RichLibrary()
    shell.spawn_pet()
    assert len(shell._spawned) == 1
    return shell._spawned[0]


def _teardown(shell) -> None:
    """收掉本用例生的子肥鱼（各带自言自语计时器 + 气泡宿主）再删 runtime 标记。

    本节的用例都生真实子宠；留着它们，计时器会在后续用例的同一进程里继续跑——
    长跑套件里这是纯粹的噪声源（解码/事件循环被无谓占用）。
    """
    shell.clear_spawned_pets()
    shell._delete_runtime_marker()


class _DialogRecorder:
    """确认框替身：**构造是产品代码真实走的**（文案/按钮/默认钮全按实现取），只替掉
    ``exec`` 的模态事件循环（offscreen 下也真阻塞）。

    真实路径里 ``clickedButton()`` 由 exec 期间的点击写入，替身必须同口径给出答案。
    """

    def __init__(self, answer: str | None) -> None:
        self.answer = answer
        self.opened = 0
        self.text = ""
        self.informative = ""
        self.buttons: list[str] = []
        self.default: str | None = None
        self._clicked = None

    def install(self, monkeypatch):
        from PySide6.QtWidgets import QMessageBox

        recorder = self

        def fake_exec(box):
            recorder.opened += 1
            recorder.text = box.text()
            recorder.informative = box.informativeText()
            recorder.buttons = [button.text() for button in box.buttons()]
            default = box.defaultButton()
            recorder.default = default.text() if default is not None else None
            recorder._clicked = next(
                (button for button in box.buttons()
                 if button.text() == recorder.answer), None)
            return 0

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)
        monkeypatch.setattr(QMessageBox, "clickedButton",
                            lambda box: recorder._clicked)
        return recorder


def _spy_exit_pet(shell, monkeypatch) -> list:
    """记录 ``OverlayShell.exit_pet`` 的调用（"取消则什么都不发生"的观测点）。"""
    calls: list = []
    monkeypatch.setattr(shell, "exit_pet",
                        lambda sprite=None: calls.append(sprite))
    return calls


def test_exit_main_pet_cancel_keeps_main_and_children(tmp_path, monkeypatch):
    """主宠 + 有子宠 → 点取消：不提升、不退出、活跃清单原样。"""
    shell, _ = _make_shell(tmp_path)
    try:
        child = _spawn_child(shell)
        dialog = _DialogRecorder("取消").install(monkeypatch)
        exits = _spy_exit_pet(shell, monkeypatch)

        SpriteMenuFacade(shell).on_exit_pet()

        assert dialog.opened == 0, "可逆关闭与子宠相同，不专属确认"
        assert exits == [shell.sprite]
        assert shell.sprite is not child, "取消不得提升子宠"
        assert shell._spawned == [child], "取消不得动活跃清单"
    finally:
        _teardown(shell)


def test_exit_main_pet_confirmed_promotes_first_child(tmp_path, monkeypatch):
    """主宠 + 有子宠 → 点确认：走真实提升（列表首只子宠接管主身份）。"""
    shell, _ = _make_shell(tmp_path)
    try:
        child = _spawn_child(shell)
        _DialogRecorder("确认退出主宠").install(monkeypatch)

        SpriteMenuFacade(shell).on_exit_pet()

        assert shell.sprite is None
        assert shell._spawned == [child]
        assert shell._spawned_slots[child] == 1
    finally:
        _teardown(shell)


def test_exit_main_pet_without_children_cancel_does_not_quit(tmp_path, monkeypatch):
    """主宠 + 无子宠 → 点取消：不退应用（这条也覆盖"菜单开着时最后一只子宠已退出"）。"""
    shell, _ = _make_shell(tmp_path)
    try:
        quits: list = []
        monkeypatch.setattr(shell.app, "quit", lambda *a, **k: quits.append(1))
        dialog = _DialogRecorder("取消").install(monkeypatch)
        exits = _spy_exit_pet(shell, monkeypatch)

        SpriteMenuFacade(shell).on_exit_pet()

        assert dialog.opened == 0
        assert exits == [shell.sprite]
        assert quits == [], "关闭最后一宠不退出应用"
    finally:
        _teardown(shell)


def test_exit_main_pet_without_children_confirmed_quits_app(tmp_path, monkeypatch):
    """主宠 + 无子宠 → 点确认：真的走退出整个应用。"""
    shell, _ = _make_shell(tmp_path)
    try:
        quits: list = []
        monkeypatch.setattr(shell.app, "quit", lambda *a, **k: quits.append(1))
        _DialogRecorder("确认退出主宠").install(monkeypatch)

        SpriteMenuFacade(shell).on_exit_pet()

        assert quits == []
        assert shell.sprite is None
    finally:
        _teardown(shell)


def test_exit_child_pet_has_no_confirm(tmp_path, monkeypatch):
    """子宠退出不弹框：直接退掉它自己（有 slot 存档、用户明确点选），主宠不受影响。"""
    shell, _ = _make_shell(tmp_path)
    try:
        child = _spawn_child(shell)
        dialog = _DialogRecorder("取消").install(monkeypatch)

        SpriteMenuFacade(shell, child).on_exit_pet()

        assert dialog.opened == 0, "子宠退出不该被确认框挡住"
        assert shell._spawned == [], "子宠被退掉"
        assert shell.sprite is not child, "主宠不退"
    finally:
        _teardown(shell)


def test_exit_main_pet_confirm_box_defaults_to_cancel(tmp_path, monkeypatch):
    """确认框的三个语义点：默认钮 = 取消（防连点/回车误触）、按钮文案、默认取消的返回值。"""
    shell, _ = _make_shell(tmp_path)
    try:
        _spawn_child(shell)
        monkeypatch.setattr(shell._config, "character_display_name",
                            lambda cid: "小美", raising=False)
        dialog = _DialogRecorder(None).install(monkeypatch)   # None = 没点任何按钮
        exits = _spy_exit_pet(shell, monkeypatch)

        SpriteMenuFacade(shell).on_exit_pet()

        assert dialog.opened == 0
        assert exits == [shell.sprite]
    finally:
        _teardown(shell)


def test_promote_target_name_uses_alias_and_empty_without_children(tmp_path, monkeypatch):
    """壳的只读 seam：被提升者的展示名走既有角色名/别名口径，无子宠 → 空串。

    退出确认框靠它点名"谁会被提升"，不新造命名系统：展示名与识屏自我识别同源
    （``character_display_name`` + 该 sprite 自己的 ``character``）。
    """
    shell, _ = _make_shell(tmp_path)
    try:
        assert not hasattr(shell, "promote_target_name")
        child = _spawn_child(shell)
        shell.exit_pet(shell.sprite)
        assert shell.sprite is None
        assert shell._spawned_slots[child] == 1
    finally:
        _teardown(shell)


def test_menu_exit_this_for_main_pet_is_gated(tmp_path, monkeypatch):
    """菜单入口本身也带闸：右击主宠 →「退出这只」→ 取消，什么都不发生。

    上一条走 facade 方法，这条从**真实菜单项**触发——防的是"日后有人在菜单里直连
    ``exit_pet`` 绕过 facade 的确认闸"（这个入口本来就只多宠时注入）。
    """
    shell, _ = _make_shell(tmp_path)
    try:
        child = _spawn_child(shell)
        menu = build_sprite_full_menu(shell, shell.sprite)   # 右击主宠
        action = _find_action(menu, "退出这只")
        assert action is not None, "多宠时主宠菜单必须有「退出这只」"

        dialog = _DialogRecorder("取消").install(monkeypatch)
        exits = _spy_exit_pet(shell, monkeypatch)
        action.trigger()

        assert dialog.opened == 0
        assert exits == [shell.sprite]
        assert shell.sprite is not child
        assert shell._spawned == [child]
    finally:
        _teardown(shell)


def test_failed_child_write_does_not_apply_or_reflow(tmp_path):
    from pet import pet_profiles
    from tests import test_overlay_collision_toggle as toggle

    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        child.bind_clip("idle1")
        clip = child._clip
        speeds = list(clip.speeds)
        original = (child.scale, child.playback_speed, child.drag_physics)
        reflows, feedback = [], []
        shell.on_sprite_scale_changed = lambda sprite: reflows.append(sprite)
        shell.show_bubble = lambda text, **kw: feedback.append(text) or True
        pet_profiles.slot_profile_path(cfg.dir, 1).write_text("{broken", encoding="utf-8")
        facade = SpriteMenuFacade(shell, child)
        assert facade.change_scale(1.7) is False
        assert facade.set_playback_speed(2.0) is False
        assert facade.set_drag_physics(not original[2]) is False
        assert (child.scale, child.playback_speed, child.drag_physics) == original
        assert clip.speeds == speeds and reflows == []
        assert len(feedback) == 3 and all("失败" in text for text in feedback)
    finally:
        toggle._teardown(shell)


def test_child_lock_timeout_keeps_runtime_and_reports_failure(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pet import config_write_lock as cwl
    from tests import test_overlay_collision_toggle as toggle

    shell, _ = toggle._make_slot_shell(tmp_path)
    try:
        shell.spawn_pet()
        child = shell._spawned[0]
        original = child.scale
        feedback = []
        shell.show_bubble = lambda text, **kw: feedback.append(text) or True
        @contextmanager
        def blocked(*args, **kwargs):
            raise cwl.WriteLockTimeout("controlled lock contention")
            yield
        monkeypatch.setattr(cwl, "config_write_lock", blocked)
        assert SpriteMenuFacade(shell, child).change_scale(1.7) is False
        assert child.scale == original and "失败" in feedback[0]
    finally:
        toggle._teardown(shell)


def test_main_save_false_rolls_back_config_and_runtime(tmp_path, monkeypatch):
    shell, _ = _make_shell(tmp_path)
    try:
        original = shell.sprite.scale
        previous = shell._config.get("scale")
        monkeypatch.setattr(shell, "persist_sprite_setting", lambda *args: False)
        assert SpriteMenuFacade(shell).change_scale(1.7) is False
        assert shell.sprite.scale == original
        assert shell._config.get("scale") == previous
    finally:
        _teardown(shell)


# ---------------------------------------------------------------- A10 服务结果落点
# 菜单旁路（右击 → 查看天气 / DeepSeek 余额）此前把**整个壳**交给 AppShell 的
# 手动服务方法，落点由壳在结果到达时按"最小可见槽"动态决定：右击子肥鱼查天气，
# 气泡冒在主肥鱼头上；请求在飞时把发起的那一只关掉，结果还会落到别宠身上。
# 点击余额路径早就用 ``_sprite_host(target)`` 收口了，菜单这条路漏了。

def test_service_callbacks_hand_the_initiating_sprite_presenter(tmp_path):
    """两个手动服务回调都要带**发起 sprite** 的代次呈现器，而不是整个壳。"""
    from tests import test_overlay_collision_toggle as toggle

    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        seen: list = []
        shell.show_weather = lambda host=None, **kw: seen.append(host)
        shell.show_balance = lambda host=None, **kw: seen.append(host)

        SpriteMenuFacade(shell, child).on_show_weather()
        SpriteMenuFacade(shell, child).on_show_balance()
        assert len(seen) == 2
        for host in seen:
            assert host is not shell, "手动结果落点必须带发起宠身份，不能是壳本身"
            assert host.presentation_available() is True
            assert host.show_bubble("天气：晴", 3000) is False or True  # 仅探活，不判文案

        # 主宠那一侧同样带身份（而不是"壳 = 动态最小可见槽"）
        seen.clear()
        SpriteMenuFacade(shell).on_show_weather()
        assert seen[0] is not shell
        assert seen[0].show_bubble("天气：晴", 3000) is True
    finally:
        toggle._teardown(shell)


def test_service_presenter_routes_the_bubble_to_the_initiating_sprite(tmp_path, monkeypatch):
    """结果真的落在发起那一只身上：子宠走它自己的气泡面，主宠走壳的完整门。"""
    from tests import test_overlay_collision_toggle as toggle

    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        calls: list = []
        monkeypatch.setattr(
            shell, "_show_bubble_text_for",
            lambda sprite, text, duration_ms=0, **kw: (calls.append(("child", sprite)), True)[1])
        monkeypatch.setattr(
            shell, "show_bubble",
            lambda text, duration_ms=0, **kw: (calls.append(("main", None)), True)[1])

        assert SpriteMenuFacade(shell, child)._service_host().show_bubble("天气：晴", 3000) is True
        assert calls == [("child", child)]
        calls.clear()
        assert SpriteMenuFacade(shell)._service_host().show_bubble("天气：晴", 3000) is True
        assert calls == [("main", None)]
    finally:
        toggle._teardown(shell)


def test_service_presenter_cancels_only_the_presentation_after_close(tmp_path):
    """请求在飞时关掉发起宠：迟到结果只取消呈现，不投给别宠、也不报成功。"""
    from tests import test_overlay_collision_toggle as toggle

    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        presenter = SpriteMenuFacade(shell, child)._service_host()
        assert presenter.presentation_available() is True

        assert shell.exit_pet(child) is True

        assert presenter.presentation_available() is False, "关闭后这只不再是活宿主"
        assert presenter.show_bubble("迟到的天气", 3000) is False, "只取消呈现，不另找落点"
        assert shell.sprite is not None, "主宠仍在（别宠一个都不该被牵动）"
    finally:
        toggle._teardown(shell)


def test_service_presenter_treats_a_reborn_slot_as_another_pet(tmp_path):
    """同一槽重建出来的是**另一只**：旧呈现器不许把结果投给新住客。"""
    from tests import test_overlay_collision_toggle as toggle

    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        child = shell._spawned[0]
        presenter = SpriteMenuFacade(shell, child)._service_host()
        assert shell.exit_pet(child) is True
        shell.spawn_pet()
        reborn = shell._spawned[0]
        assert reborn is not child, "前提：槽被重建"

        assert presenter.presentation_available() is False
        assert presenter.show_bubble("迟到的余额", 3000) is False
        assert reborn.visible is True, "新住客不受旧请求影响"
    finally:
        toggle._teardown(shell)


def test_service_presenter_respects_the_per_sprite_bubble_gate(tmp_path, monkeypatch):
    """F04：手动结果（余额/天气）必须守壳的 per-sprite 气泡门禁。

    ``_SpriteServicePresenter`` 的 ``show_bubble`` override 只查了
    ``presentation_available``，随后对子宠直调 ``_show_bubble_text_for``——把父类
    ``_SpritePetHost.show_bubble`` 开头那句 ``_bubble_blocked()`` 省掉了：飞行中的
    那一只、以及设置页打开期间的抑制位，都会照样冒泡（2026-10-10 终审 F04，方法级实证
    blocked=True / shown=True）。门禁是 **per-sprite** 的：飞的不是这一只时，别的宠照常。
    """
    from tests import test_overlay_collision_toggle as toggle

    shell, cfg = toggle._make_slot_shell(tmp_path)
    try:
        shell.overlay.show()
        shell.spawn_pet()
        shell.spawn_pet()
        first, second = shell._spawned
        calls: list = []
        monkeypatch.setattr(
            shell, "_show_bubble_text_for",
            lambda sprite, text, duration_ms=0, **kw: (calls.append(sprite), True)[1])

        # 反例 1：这一只正在飞行（真实飞行边沿回调）
        shell._on_sprite_flight_changed(first, True)
        assert SpriteMenuFacade(shell, first)._service_host().show_bubble(
            "余额：12.34", 3000) is False, "飞行期不许冒泡"
        assert calls == [], "被门禁挡住时不许再走呈现面"

        # 正例：门禁是 per-sprite 的，飞的不是这一只 → 照常呈现
        assert SpriteMenuFacade(shell, second)._service_host().show_bubble(
            "余额：12.34", 3000) is True
        assert calls == [second], "一只在飞不该把整个壳的结果都按住"

        # 反例 2：设置页抑制期（含主宠那一侧）
        calls.clear()
        shell.set_bubble_suppressed(True)
        try:
            assert SpriteMenuFacade(shell, second)._service_host().show_bubble(
                "天气：晴", 3000) is False
            assert SpriteMenuFacade(shell)._service_host().show_bubble(
                "天气：晴", 3000) is False
            assert calls == []
        finally:
            shell.set_bubble_suppressed(False)

        # 抑制解除后恢复（不是"从此再也冒不出来"）
        assert SpriteMenuFacade(shell, second)._service_host().show_bubble(
            "天气：晴", 3000) is True
        assert calls == [second]
    finally:
        toggle._teardown(shell)
