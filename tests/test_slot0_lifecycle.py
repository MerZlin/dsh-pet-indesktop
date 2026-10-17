import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication
from pet.config import APP_DIR_NAME
from pet import pet_profiles as profiles
from tests.test_overlay_spawn import _make_persistent_shell

app = QApplication.instance() or QApplication([])


def test_close_zero_preserves_children_and_restores_identity(tmp_path):
    shell, _ = _make_persistent_shell(tmp_path)
    shell.spawn_pet()
    shell.spawn_pet()
    children = list(shell._spawned)
    directory = tmp_path / APP_DIR_NAME
    before = {n: profiles.slot_profile_path(directory, n).read_bytes() for n in (1, 2)}
    try:
        assert shell.exit_pet(shell.sprite)
        assert shell.sprite is None
        assert shell._spawned == children
        assert shell._active_slots() == [1, 2]
        shell.overlay.show()
        assert shell.alert_target_sprite() is children[0]
        shell.handle_geometry_changed()
        assert all(profiles.slot_profile_path(directory, n).read_bytes() == before[n] for n in before)
        shell.spawn_pet()
        assert shell.sprite is not None
        assert shell._active_slots() == [0, 1, 2]
        shell.spawn_pet()
        assert shell._spawned_slots[shell._spawned[-1]] == 3
    finally:
        shell.stop()


def test_tray_close_of_the_only_pet_keeps_process_tray_and_services(tmp_path, monkeypatch):
    """单宠经托盘「退出这只」关掉后 = "0 只"这个合法状态：进程/托盘/壳级服务都在，
    宠物驱动停、活跃集合留空、不自动复活，恢复入口仍可达（规范 §四）。

    入口与被修对象是同一件事，所以全过程**从托盘 QAction 触发**进入，不直接
    call ``exit_pet``（UI 入口闭环另见 tests/test_single_pet_close_entry.py）。
    """
    import time

    shell, _ = _make_persistent_shell(tmp_path)
    quits: list = []
    monkeypatch.setattr(shell.app, 'quit', lambda *a, **k: quits.append(1))
    try:
        assert shell.sprite is not None, '前提：单宠（slot0）在'
        menu = shell.tray.contextMenu()
        sub = next(a.menu() for a in menu.actions()
                   if a.text() == '主肥鱼 [slot-0]')
        action = next(a for a in sub.actions() if a.text() == '退出这只')
        action.trigger()

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and shell.sprite is not None:
            app.processEvents()
            time.sleep(0.01)
        assert shell.sprite is None, '菜单关闭后这只必须真的关掉'
        assert quits == [], '关闭最后一只不许退出应用'
        assert shell.overlay.sprites == []
        assert shell._active_slots() == []
        assert profiles.load_active_set(tmp_path / APP_DIR_NAME).slots == (), (
            '关闭 = 该槽留空（落盘为空），不是"退出时清掉整个集合"')
        # 壳级服务照旧：托盘、D12 指令通道、全屏 watcher 都不随最后一只宠关掉
        assert shell.tray is not None, '无宠时托盘保留（唯一恢复入口）'
        assert shell._command_watcher is not None and shell._command_timer.isActive()
        assert shell._watcher is not None
        assert not shell.driver._timer.isActive(), '0 只时宠物驱动停（没像素要上屏）'
        menu = shell.tray.contextMenu()
        labels = [a.text() for a in menu.actions()]
        assert '生成 / 恢复 slot0' in labels, labels
        assert not [a for a in menu.actions() if a.menu() is not None], '不许留空逐只菜单'
    finally:
        shell.stop()


def test_v2_empty_builds_no_pet(tmp_path):

    directory = tmp_path / APP_DIR_NAME
    directory.mkdir()
    (directory / profiles.ACTIVE_SET_FILENAME).write_text(json.dumps({'version': 2, 'slots': []}), encoding='utf8')
    from tests.test_overlay_window_capabilities import CapInstance, FakeScreen
    from pet.config import Config
    from pet.overlay_shell import OverlayShell
    shell = OverlayShell(app, CapInstance(Config(base=tmp_path, instance_id='')),
                         screen=FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040)))
    try:
        assert shell.sprite is None
        assert shell.overlay.sprites == []
        assert profiles.load_active_set(directory).slots == ()
        assert not shell.driver._timer.isActive()
    finally:
        shell.stop()


def test_spawn_failure_leaves_no_partial_sprite(tmp_path, monkeypatch):
    shell, _ = _make_persistent_shell(tmp_path)
    before = list(shell.overlay.sprites)
    def fail(*args):
        raise RuntimeError('apply failed')
    monkeypatch.setattr(shell, '_apply_sprite_settings_to', fail)
    shell.spawn_pet()
    assert shell.overlay.sprites == before
    assert shell._active_slots() == [0]
    assert shell._spawned == []


def test_capture_empty_zero_restores_only_zero(tmp_path, monkeypatch):
    from pet.app import AppShell
    from pet.config import Config
    from tests.test_single_process_spawn import _FakeWindow
    from pet import overlay_settings_command

    cfg = Config(base=tmp_path, instance_id='')
    profiles.write_profile_settings(cfg.dir, 0, {'character': 'shenshen', 'rx': 0.3})
    profiles.save_active_set(cfg.dir, [1, 128])
    monkeypatch.setenv('PET_RENDER_TOPOLOGY', 'legacy')
    overlay_settings_command.reset_topology_snapshot()
    shell = AppShell(app, cfg, enable_chat=False)
    made = []
    def create(character):
        made.append(character)
        shell.instance.win = _FakeWindow()
        shell.instance.win.show_bubble = lambda *args, **kwargs: None
    monkeypatch.setattr(shell, '_create_ui_with_character_fallback', create)
    monkeypatch.setattr(shell, '_refresh_tray_menu', lambda: None)
    assert shell.instance.win is None
    shell.spawn_pet()
    assert len(made) == 1
    assert profiles.load_active_set(cfg.dir).slots == (1, 128, 0)
    shell.spawn_pet()
    assert len(made) == 1


def test_slot_focus_does_not_activate_inactive_profile(tmp_path):
    shell, _ = _make_persistent_shell(tmp_path)
    shell.spawn_pet()
    child = shell._spawned[0]
    shell.set_sprite_visible(child, False)
    assert shell.select_slot(1)
    assert child.visible and shell.overlay.isVisible()
    before = shell._active_slots()
    assert not shell.select_slot(128)
    assert shell._active_slots() == before


def test_late_reply_keeps_shared_history_without_retargeting(tmp_path):
    from pet.overlay_shell import _SpriteChatAnchor
    from pet.quick_chat import QuickChatBubble
    shell, _ = _make_persistent_shell(tmp_path)
    shell.spawn_pet()
    old = shell._spawned[0]
    anchor = _SpriteChatAnchor(shell, old)
    bubble = QuickChatBubble(shell._sprite_config(shell.sprite), pet_window=anchor)
    bubble._active_request_id = 'request-old'
    bubble._request_pet_window = anchor
    shell.exit_pet(old)
    shell.spawn_pet()
    assert shell._spawned[0] is not old
    bubble._finished('request-old', 'shared fact')
    assert bubble.session.messages[-1].content == 'shared fact'
    assert not bubble.isVisible()
    assert not anchor.presentation_valid()
    bubble.close()


def test_late_reply_after_close_touches_nothing_but_presentation(tmp_path):
    """R3/5：关槽后的迟到回复只取消宠物呈现——别宠状态、原配置、会话消息都不变。"""
    from pet.overlay_shell import _SpriteChatAnchor
    from pet.quick_chat import QuickChatBubble
    shell, _ = _make_persistent_shell(tmp_path)
    shell.overlay.show()
    shell.spawn_pet()
    child = shell._spawned[0]
    main = shell.sprite
    directory = shell._config.dir
    anchor = _SpriteChatAnchor(shell, child)
    bubble = QuickChatBubble(shell._sprite_config(main), pet_window=anchor)
    bubble._active_request_id = 'request-late'
    bubble._request_pet_window = anchor

    shell.show_alert('别宠正挂着的提醒', alert_id='keep-me')
    main_bubble = shell._alert_bubble
    assert shell.exit_pet(child) is True
    shell.spawn_pet()
    assert shell._spawned[0] is not child

    # 迟到回复到达前，记录别宠现场与全部 slot 档案
    assert shell.alert_target_sprite() is main
    before_text = main_bubble._raw_text
    before_profiles = {n: profiles.slot_profile_path(directory, n).read_bytes()
                       for n in (0, 1)}
    alerts_before = list(shell._alert_queue)
    try:
        bubble._finished('request-late', 'shared fact')

        assert bubble.session.messages[-1].content == 'shared fact', \
            '共享会话里已生成的消息是会话事实，不得因关槽丢弃'
        assert not bubble.isVisible()
        assert not anchor.presentation_valid(), '只取消宠物呈现'
        assert shell._alert_bubble is main_bubble and main_bubble.isVisible(), \
            '迟到回复不得动别宠的提醒呈现'
        assert main_bubble._raw_text == before_text
        assert list(shell._alert_queue) == alerts_before, '迟到回复不得改别宠的提醒队列'
        assert {n: profiles.slot_profile_path(directory, n).read_bytes()
                for n in (0, 1)} == before_profiles, '迟到回复不得改写任何槽档案'
    finally:
        bubble.close()


def test_three_distinct_profiles_close_restore_generate_restart(tmp_path):
    directory = tmp_path / APP_DIR_NAME
    for slot, role, rx, speed in ((0, 'shenshen', 0.3, 0.5), (1, 'nova', 0.5, 1.5), (2, 'cat', 0.7, 2.0)):
        profiles.write_profile_settings(directory, slot, {'character': role, 'rx': rx, 'ry': 0.4,
            'scale': 0.4, 'playback_speed': speed})
    profiles.save_active_set(directory, [0, 1, 2])
    shell, _ = _make_persistent_shell(tmp_path)
    before = {n: profiles.slot_profile_path(directory, n).read_bytes() for n in (1, 2)}
    assert shell.exit_pet(shell.sprite)
    assert all(profiles.slot_profile_path(directory, n).read_bytes() == before[n] for n in before)
    shell.spawn_pet()
    assert shell._sprite_config(shell.sprite).get('character') == 'shenshen'
    shell.spawn_pet()
    assert shell._spawned_slots[shell._spawned[-1]] == 3
    shell.save_position()
    shell.save_spawned_positions()
    restarted, _ = _make_persistent_shell(tmp_path)
    assert restarted._active_slots() == [0, 1, 2, 3]
    for n, role, speed in ((0, 'shenshen', 0.5), (1, 'nova', 1.5), (2, 'cat', 2.0)):
        sprite = restarted._sprite_for_slot(n)
        assert restarted._sprite_config(sprite).get('character') == role
        assert sprite.playback_speed == speed


def test_no_tray_recovery_button_is_visible_and_restores(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon, QPushButton
    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: False)
    directory = tmp_path / APP_DIR_NAME
    profiles.save_active_set(directory, [])
    from pet.config import Config
    from pet.overlay_shell import OverlayShell
    from tests.test_overlay_window_capabilities import CapInstance, FakeScreen
    shell = OverlayShell(app, CapInstance(Config(base=tmp_path, instance_id='')),
        screen=FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040)))
    assert shell._recovery_window.isVisible()
    button = next(b for b in shell._recovery_window.findChildren(QPushButton) if '生成' in b.text())
    button.click()
    assert shell.sprite is not None
    assert shell._active_slots() == [0]
    shell._recovery_window.close()


def test_legacy_profile_reset_keeps_body_anchor_and_reloads_role(tmp_path, monkeypatch):
    from pet.app import AppShell
    from pet.config import Config
    from pet.window import PetWindow
    from pet import window_placement
    from tests.test_window_pause import FakeLibrary
    monkeypatch.setenv('PET_RENDER_TOPOLOGY', 'legacy')
    cfg = Config(base=tmp_path, instance_id='')
    profiles.write_profile_settings(cfg.dir, 0, {'character': 'nova', 'scale': 0.5})
    shell = AppShell(app, cfg, enable_chat=False)
    win = PetWindow(FakeLibrary(), shell.instance.config)
    win._profile_character = 'nova'
    win.move(250, 200)
    shell.instance.win = win
    center = window_placement.virtual_pos(win) + win._stable_body_local_rect().center()
    assert profiles.reset_slot_profile(cfg.dir, 0).ok
    made = []
    monkeypatch.setattr(shell.instance, '_create_library', lambda role: made.append(role) or FakeLibrary())
    def build(role, lib=None):
        new = PetWindow(lib, shell.instance.config)
        shell.instance.win = new
        return new
    monkeypatch.setattr(shell.instance, '_build_window', build)
    assert shell._apply_legacy_profile(0)
    new = shell.instance.win
    assert made == ['shenshen']
    assert window_placement.virtual_pos(new) + new._stable_body_local_rect().center() == center
    assert new._profile_generation == profiles.slot_generation(cfg.dir, 0)
    new.close()


def test_legacy_window_scale_survives_the_reload_its_own_save_triggers(tmp_path, monkeypatch):
    """#304：直播捕获兼容（legacy 单窗）下右键改大小，不许被自存触发的那次 reload 回滚。

    真实链路：``change_scale`` 改 ``self.scale``（用户看到的"闪一下"）→ 置
    ``user_customized`` → ``_save_position`` 直写档案 → QFileSystemWatcher 回调
    ``_apply_legacy_profile(0)`` → ``config.reload()`` → ``refresh_pet_settings``
    再读 ``scale``。旧实现里那条草稿把档案快照冻住，reload 读回**改之前**的尺寸，
    于是窗口弹回去、盘上也被写回旧值。这里按这条链逐段真跑（不 mock 配置层）。
    """
    from pet.app import AppShell
    from pet.config import Config
    from pet.window import PetWindow
    from tests.test_window_pause import FakeLibrary
    monkeypatch.setenv('PET_RENDER_TOPOLOGY', 'legacy')
    cfg = Config(base=tmp_path, instance_id='')
    profiles.write_profile_settings(cfg.dir, 0, {'character': 'nova', 'scale': 0.5})
    shell = AppShell(app, cfg, enable_chat=False)
    win = PetWindow(FakeLibrary(), shell.instance.config)
    # 与 _build_window 同一接缝：几何自存要带代次 token 才落盘
    win.set_profile_identity('nova', shell.instance.config.profile_generation())
    win.move(250, 200)
    shell.instance.win = win
    try:
        win.change_scale(0.85)
        assert win.scale == pytest.approx(0.85), '前提：窗口当场接受了新尺寸'

        assert shell._apply_legacy_profile(0)

        assert shell.instance.win.scale == pytest.approx(0.85), (
            '自存触发的那次 reload 不许把尺寸弹回改之前的值')
        raw = json.loads(profiles.slot_profile_path(cfg.dir, 0).read_text(encoding='utf-8'))
        assert raw['scale'] == pytest.approx(0.85), '盘上必须停在用户选的尺寸'
        assert raw['user_customized'] is True, (
            '右键改大小 = 用户在这只鱼自己的界面里主动设置过，旗标必须当场落盘')
    finally:
        win.close()


# ---------------------------------------------------------------- ops D1/D2（2026-10-10 终审）

def test_migration_statuses_separate_slot_only_from_global(tmp_path):
    """ops D2：迁移失败按**结果类型**处置——单槽损坏可降级，其它一律不许吞。

    "一份 slot0 档案坏了"与"全局配置读不出来 / 备份失败 / 锁超时"是两种事故：
    前者只牵连那一个槽（保留原文件、该槽空着，用户显式动作再恢复），后者涉及全局面
    或迁移基础设施，必须照旧硬失败。把两者都当"异常"一起吞掉，等于用不可信的全局配置
    继续跑。
    """
    slot_only = profiles.Slot0Migration(status="target-corrupt")
    assert slot_only.ok is False and slot_only.slot_only_problem is True
    for status in ("source-corrupt", "source-unreadable", "refused-slot-source",
                   "backup-invalid", "backup-failed", "write-failed", "lock-timeout"):
        migration = profiles.Slot0Migration(status=status)
        assert migration.ok is False
        assert migration.slot_only_problem is False, status


def test_corrupt_slot0_profile_is_reported_as_target_corrupt(tmp_path):
    """真生产者：坏 slot0 档案 → ``target-corrupt``（带路径），不覆盖、不清源。"""
    from pet.config import Config

    cfg = Config(base=tmp_path, instance_id='')
    cfg.dir.mkdir(parents=True, exist_ok=True)
    (cfg.dir / 'config.json').write_text(json.dumps({"version": 4, "scale": 0.8}), encoding='utf8')
    broken = cfg.dir / profiles.slot_profile_path(cfg.dir, 0).name
    broken.write_bytes(b'{ "scale": 0.8, broken')
    before = broken.read_bytes()

    migration = profiles.migrate_slot0_profile(cfg.dir)

    assert migration.status == 'target-corrupt'
    assert migration.slot_only_problem is True
    assert migration.profile_path == broken
    assert broken.read_bytes() == before


def test_corrupt_slot0_profile_does_not_break_the_overlay_shell(tmp_path):
    """ops D2（真壳）：坏 slot0 档案不许让整个 overlay 壳起不来。

    旧处置是 ``raise RuntimeError`` → 启动失败对话框 → 全局配置、其它槽、提醒、托盘
    全不可用，对话框里连文件路径都没有。现在只降级那一个槽：主 sprite 不复活、
    其它 active 子照常、原文件一个字节不动、活跃集合也不被启动期改写。
    """
    from pet.config import Config
    from pet.overlay_shell import OverlayShell
    from tests.test_overlay_window_capabilities import CapInstance, FakeScreen

    directory = tmp_path / APP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps({"version": 4}), encoding='utf8')
    profiles.save_active_set(directory, [0, 1])
    assert profiles.write_profile_settings(directory, 1, {'character': 'nova'}).status == 'ok'
    broken = directory / profiles.slot_profile_path(directory, 0).name
    broken.write_bytes(b'{broken')
    before_broken = broken.read_bytes()
    active_path = directory / profiles.ACTIVE_SET_FILENAME
    before_active = active_path.read_bytes()

    shell = OverlayShell(
        app, CapInstance(Config(base=tmp_path, instance_id='')),
        screen=FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040)))
    try:
        assert shell.sprite is None, '坏档案的那个槽先空着'
        assert shell._remembered_active == [1], '只摘掉坏掉的那个槽'
        assert [shell._spawned_slots[s] for s in shell._spawned] == [1], '其它 active 子照常复活'
    finally:
        shell.stop()

    assert broken.read_bytes() == before_broken, '坏档案一个字节都不许动（不自动修复）'
    assert active_path.read_bytes() == before_active, '启动不替用户改活跃集合'


def test_corrupt_slot0_profile_degrades_the_app_shell_with_a_visible_path(tmp_path):
    """ops D2（真 AppShell）：以前这里直接 raise，现在降级并给出**含路径**的诊断。"""
    from pet.app import AppShell
    from pet.config import Config

    cfg = Config(base=tmp_path, instance_id='')
    directory = cfg.dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps({
        "version": 4, "scale": 0.8, "character": "nova", "pet_opacity": 55,
    }), encoding='utf8')
    profiles.save_active_set(directory, [0, 2])
    assert profiles.write_profile_settings(directory, 2, {'character': 'cat'}).status == 'ok'
    broken = directory / profiles.slot_profile_path(directory, 0).name
    broken.write_bytes(b'{broken')
    before_broken = broken.read_bytes()

    shell = AppShell(app, cfg, enable_chat=False)     # 旧行为：RuntimeError
    try:
        assert shell._slot0_profile_unusable is True
        assert 0 not in shell._active_pet_slots, '坏掉的槽只在内存里退出活跃集合'
        assert 2 in shell._active_pet_slots, '别的 active 子照常'
        assert shell.slot0_profile_diagnosis(), '降级必须有诊断'
        assert str(broken) in shell.slot0_profile_diagnosis(), '诊断必须点名文件路径'
    finally:
        shell._shared.stop_all()

    assert broken.read_bytes() == before_broken, '坏档案一个字节都不许动'
    assert profiles.load_active_set(directory).slots == (0, 2), (
        '启动期不得替用户改活跃集合（改集合只有用户显式的生成/关闭）')


def _corrupt_slot0_app(tmp_path):
    """真 AppShell + 坏 slot0 档案（只断言诊断口径时用）。"""
    from pet.config import Config

    cfg = Config(base=tmp_path, instance_id='')
    directory = cfg.dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps({"version": 4}), encoding='utf8')
    profiles.save_active_set(directory, [0])
    broken = profiles.slot_profile_path(directory, 0)
    broken.write_bytes(b'{broken')
    return cfg, broken


def test_overlay_diagnosis_points_at_in_place_restore(tmp_path, monkeypatch):
    """overlay：修好档案 → 点「生成 / 恢复 slot0」**免重启**恢复（实机已验证）。

    旧文案两个拓扑共用一句"修好该文件后重启即可恢复"，overlay 用户读日志会以为
    必须重启（实测发现，见 .scratch/product-close-approved-gui-20261011/REPORT.md
    §4.4）。这条把 overlay 的口径钉住。
    """
    from pet import overlay_settings_command
    from pet.app import AppShell

    monkeypatch.setenv('PET_RENDER_TOPOLOGY', 'overlay')
    overlay_settings_command.reset_topology_snapshot()
    cfg, broken = _corrupt_slot0_app(tmp_path)
    shell = AppShell(app, cfg, enable_chat=False)
    try:
        diagnosis = shell.slot0_profile_diagnosis()
        assert str(broken) in diagnosis
        assert '生成 / 恢复 slot0' in diagnosis, diagnosis
        assert '重启即可恢复' not in diagnosis, (
            'overlay 免重启即可原地恢复，不许只写"重启"：%s' % diagnosis)
    finally:
        shell._shared.stop_all()
        overlay_settings_command.reset_topology_snapshot()


def test_legacy_diagnosis_keeps_the_restart_path(tmp_path, monkeypatch):
    """legacy（捕获模式/显式 legacy）：本进程内不再重读该档案 → 口径仍是重启。

    与上一条成对：两个拓扑的恢复方式确实不同，谁都不许被"顺手统一"成另一种。
    """
    from pet import overlay_settings_command
    from pet.app import AppShell

    monkeypatch.setenv('PET_RENDER_TOPOLOGY', 'legacy')
    overlay_settings_command.reset_topology_snapshot()
    cfg, broken = _corrupt_slot0_app(tmp_path)
    shell = AppShell(app, cfg, enable_chat=False)
    try:
        diagnosis = shell.slot0_profile_diagnosis()
        assert str(broken) in diagnosis
        assert '重启即可恢复' in diagnosis, diagnosis
        assert '生成 / 恢复 slot0' not in diagnosis, (
            'legacy 侧点"恢复"仍会被拒（档案不可用），不许指错路：%s' % diagnosis)
    finally:
        shell._shared.stop_all()
        overlay_settings_command.reset_topology_snapshot()


def test_explicit_spawn_over_a_corrupt_active_set_backs_up_and_writes(tmp_path):
    """ops D1：坏活跃集合 + 用户显式生成 → 先备份再覆盖，不许"永远落不进盘"。

    旧行为：默认 ``refuse`` 让每次 save 都 ``refused-corrupt``，只有一条 warning——
    用户看到的是「生成了 → 重启又没了」，UI 里没有任何解释，也永远修不好。
    """
    shell, _ = _make_persistent_shell(tmp_path)
    directory = tmp_path / APP_DIR_NAME
    active_path = directory / profiles.ACTIVE_SET_FILENAME
    active_path.write_text('{not json', encoding='utf8')
    try:
        shell.spawn_pet()

        result = profiles.load_active_set(directory)
        assert result.status == 'ok', '显式生成必须真的把活跃集合写成可信状态'
        assert set(result.slots) == {0, 1}
        backups = list(directory.glob(
            f'{profiles.ACTIVE_SET_FILENAME}{profiles.CORRUPT_BACKUP_MARKER}*'))
        assert backups, '坏文件必须先备份再覆盖（不静默销毁）'
        assert backups[0].read_text(encoding='utf8') == '{not json'
    finally:
        shell.stop()


def test_active_set_write_failure_is_visible_and_not_faked(tmp_path, monkeypatch):
    """ops D1：写不进去必须**可见**（气泡点名路径）+ 返回值如实 False，不许假成功。"""
    from pet import pet_profiles as profiles_mod

    shell, _ = _make_persistent_shell(tmp_path)
    feedback: list = []
    shell.show_bubble = lambda text, **kw: feedback.append(text) or True
    seen_kwargs: list = []
    target = shell._spawn_config_dir() / profiles_mod.ACTIVE_SET_FILENAME

    def fake_save(config_dir, slots, **kwargs):
        seen_kwargs.append(kwargs)
        return profiles_mod.ProfileResult(status='lock-timeout', path=target,
                                          reason='controlled contention')

    monkeypatch.setattr(profiles_mod, 'save_active_set', fake_save)
    try:
        assert shell._persist_active_slots() is False, '失败不许假报成功'
        assert seen_kwargs and seen_kwargs[0].get('on_broken') == 'backup-overwrite', (
            '显式生成/关闭这条路必须走先备份再覆盖的既有 API')
        assert feedback and str(target) in feedback[0], '失败必须可见并点名文件路径'
    finally:
        shell.stop()


# ---------------------------------------------------------------- ops D2-新（2026-10-10 终审复核）
def _broken_slot0_shell(base, *, active):
    """真 OverlayShell + 坏 ``config-slot-0.json``（原文件保留、其它槽照常）。"""
    from pet.config import Config
    from pet.overlay_shell import OverlayShell
    from tests.test_overlay_window_capabilities import CapInstance, FakeScreen

    directory = base / APP_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps({"version": 4}), encoding='utf8')
    profiles.save_active_set(directory, list(active))
    broken = profiles.slot_profile_path(directory, 0)
    broken.write_bytes(b'{broken')
    shell = OverlayShell(
        app, CapInstance(Config(base=base, instance_id='')),
        screen=FakeScreen((0, 0, 1920, 1080), (0, 0, 1920, 1040)))
    return shell, directory, broken


def test_spawn_over_a_corrupt_slot0_is_visible_and_keeps_children(tmp_path):
    """ops D2-新：坏 slot0 期间生成**不许静默失败**，健康子照常活。

    终审链条：启动降级后 ``_remembered_active`` 不含 0 → 分配器仍按**最小非活跃槽**
    回到 0（这是用户的明确取舍：不静默跳号、不改分配语义）→ ``_build_slot_sprite(0)``
    读坏档案抛错 → 旧实现只有一条 ``logging.exception``。用户眼里就是"点了没反应"，
    而且**每次点都一样**（正是 B01 要消灭的那类失败）。

    本用例钉住：失败必须带**路径**可见、健康子不受牵连、活跃集合不被改写、坏档案
    一个字节不动；档案修好后再生成照常恢复 slot0。
    """
    shell, directory, broken = _broken_slot0_shell(tmp_path, active=[0, 1])
    assert profiles.write_profile_settings(directory, 1, {'character': 'nova'}).status == 'ok'
    before = broken.read_bytes()
    spoken: list = []
    try:
        (child,) = shell._spawned
        assert shell._spawned_slots[child] == 1
        shell.overlay.show()
        shell._show_bubble_text = lambda text, duration_ms=0, **kw: (
            spoken.append(str(text)), True)[1]

        shell.spawn_pet()                       # 目标仍是 slot0（不跳号）

        assert spoken, '生成失败必须可见（旧实现只 logging.exception）'
        assert str(broken) in spoken[0], '可见反馈必须点名坏档案路径'
        assert 'slot-0' in spoken[0]
        assert shell.sprite is None, '失败的 slot0 不许留下半只'
        assert shell._spawned == [child] and shell._spawned_slots[child] == 1, '健康子继续活'
        assert shell._active_slots() == [1], '失败不改活跃集合'
        assert broken.read_bytes() == before, '坏档案一个字节都不许动（不自动修复）'

        # 修好档案 → 同一进程里再次生成恢复 slot0（失败是可恢复的，不是死状态）
        broken.write_text(json.dumps({"version": 4, "character": "nova"}), encoding='utf8')
        shell.spawn_pet()
        assert shell.sprite is not None, '档案修好后生成必须恢复 slot0'
        assert shell._active_slots() == [0, 1]
    finally:
        shell.stop()


def test_spawn_failure_reaches_the_recovery_window_when_no_pet_is_visible(tmp_path):
    """无宠时冒不出泡：同一份带路径的说明必须落到**可见恢复窗**，并留修复入口。

    无宠期（刚关掉最后一只 / 全隐藏）气泡没有落点，反馈只剩日志就等于又回到静默。
    恢复窗此时是唯一的可见入口，故失败说明要写进它，并且「打开配置目录」这条
    "去修档案"的入口必须在（本批次不做自动修复，只给路）。
    """
    from PySide6.QtWidgets import QPushButton

    shell, directory, broken = _broken_slot0_shell(tmp_path, active=[])
    try:
        assert shell.sprite is None and not shell._spawned

        shell.spawn_pet()

        window = getattr(shell, '_recovery_window', None)
        assert window is not None, '无宠失败必须给出可见恢复窗'
        assert window.isVisible(), '恢复窗必须真的显示出来（不是建了不 show）'
        label = getattr(shell, '_recovery_diagnosis_label', None)
        assert label is not None and str(broken) in label.text(), '说明必须带档案路径'
        buttons = [b.text() for b in window.findChildren(QPushButton)]
        assert any('配置目录' in text for text in buttons), (
            '必须给出"去打开配置目录修档案"的入口：%s' % buttons)
        assert any('设置' in text for text in buttons), '保留该槽设置入口'
    finally:
        window = getattr(shell, '_recovery_window', None)
        if window is not None:
            window.close()
        shell.stop()


def test_capture_restore_refuses_a_corrupt_slot0_instead_of_faking_defaults(tmp_path, monkeypatch):
    """legacy（捕获模式）下的"恢复 slot0"：拒写 + 明确告知，不许拿默认视图充数。

    实测（``.scratch/product-final-fix-wiring2-20261010/probe_d2_chain.py`` 修复前）：
    旧链 ``begin_slot_generation`` → ``config.reload()`` → 用**默认角色**建窗 → 把 0
    写回活跃集合；而档案层所有写都是 ``refused-corrupt``（坏文件确实没被覆盖）。用户
    得到一只看着正常、设置永远存不进去的宠，全程零提示——比明说"这份档案坏了"更糟。
    两个拓扑对同一次事故必须同源处置：overlay 侧本来就会失败（上面两条用例），
    legacy 侧这里改成**同样失败 + 说清路径**，坏文件仍然一个字节不动。
    """
    from pet import overlay_settings_command
    from pet.app import AppShell
    from pet.config import Config

    cfg = Config(base=tmp_path, instance_id='')
    directory = cfg.dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'config.json').write_text(json.dumps({"version": 4}), encoding='utf8')
    profiles.save_active_set(directory, [0])
    broken = profiles.slot_profile_path(directory, 0)
    broken.write_bytes(b'{broken')
    before = broken.read_bytes()

    monkeypatch.setenv('PET_RENDER_TOPOLOGY', 'legacy')
    overlay_settings_command.reset_topology_snapshot()
    shell = AppShell(app, cfg, enable_chat=False)
    made: list = []
    toasts: list = []
    monkeypatch.setattr(shell, '_create_ui_with_character_fallback',
                        lambda character: made.append(character))
    monkeypatch.setattr(shell, '_refresh_tray_menu', lambda: None)
    monkeypatch.setattr(shell, 'system_notify',
                        lambda title, message, **kw: toasts.append((title, message)))
    try:
        shell.spawn_pet()

        assert made == [], '坏档案期间不许拿默认值建一只"看着正常"的宠'
        assert 0 not in shell._active_pet_slots, '拒写就不许把 0 记回活跃集合'
        assert toasts, '必须有可见告知（气泡不可用时走右下角通知）'
        assert str(broken) in toasts[0][1], '告知必须点名坏档案路径'
        assert shell.slot0_profile_diagnosis() in toasts[0][1], '并说明处置'
    finally:
        shell._shared.stop_all()
        overlay_settings_command.reset_topology_snapshot()

    assert broken.read_bytes() == before, '坏档案一个字节都不许动'
    assert profiles.load_active_set(directory).slots == (0,), (
        '被拒的动作不许改写活跃集合')

