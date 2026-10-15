import json

from pet.config import APP_DIR_NAME, Config
from pet import pet_profiles as profiles
from pet.settings_slot_config import SlotRoutedConfig, route_settings_config


def test_migration_cleans_source_and_normalizes(tmp_path):
    directory = tmp_path / APP_DIR_NAME
    directory.mkdir()
    source = directory / 'config.json'
    source.write_text(json.dumps({'version': 1, 'scale': 2, 'character': 'cat',
                                  'voice_chime_enabled': True}), encoding='utf8')
    result = profiles.migrate_slot0_profile(directory)
    assert result.ok
    original = source.with_name(source.name + profiles.PROFILE_MIGRATION_BACKUP_SUFFIX).read_bytes()
    assert 'character' not in json.loads(source.read_text(encoding='utf8'))
    assert profiles.migrate_slot0_profile(directory).ok
    assert source.with_name(source.name + profiles.PROFILE_MIGRATION_BACKUP_SUFFIX).read_bytes() == original


def test_explicit_global_ignores_instance_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('DSH_PET_INSTANCE', 'slot-128')
    assert Config(base=tmp_path, instance_id='').path.name == 'config.json'


def test_profile_draft_cannot_cross_reset(tmp_path):
    directory = tmp_path / APP_DIR_NAME
    profiles.write_profile_settings(directory, 0, {'scale': 0.8, 'rx': 0.2, 'ry': 0.3})
    cfg = route_settings_config(Config(base=tmp_path, instance_id='slot-0'))
    cfg.set('scale', 1.4)
    assert profiles.reset_slot_profile(directory, 0).ok
    cfg.reload()
    assert not cfg.save()
    assert profiles.read_slot_profile(directory, 0).value('rx') == 0.2


def test_global_save_never_resurrects_migrated_pet_keys(tmp_path):
    cfg = Config(base=tmp_path, instance_id='')
    cfg.set('character', 'cat')
    cfg.save()
    assert profiles.migrate_slot0_profile(cfg.dir).ok
    cfg.set('voice_chime_enabled', True)
    assert cfg.save()
    assert not (profiles.PER_PET_KEYS & json.loads(cfg.path.read_text(encoding='utf8')).keys())


def test_global_reset_preserves_all_profiles_and_active(tmp_path):
    cfg = Config(base=tmp_path, instance_id='')
    for n in (0, 1, 128):
        profiles.write_profile_settings(cfg.dir, n, {'rx': n / 128, 'scale': 0.9})
    profiles.save_active_set(cfg.dir, [1, 128])
    paths = [profiles.slot_profile_path(cfg.dir, n) for n in (0, 1, 128)]
    paths.append(profiles.active_set_path(cfg.dir))
    before = {path: path.read_bytes() for path in paths}
    assert cfg.reset_to_defaults()
    assert all(path.read_bytes() == before[path] for path in paths)


def test_raw_slot_config_cannot_write(tmp_path):
    cfg = Config(base=tmp_path, instance_id='slot-128')
    cfg.set('scale', 1.2)
    assert not cfg.save()
    assert not cfg.path.exists()


def test_partial_reset_blocks_new_reader_save(tmp_path, monkeypatch):
    from pet import config_write_lock as cwl
    directory = tmp_path / APP_DIR_NAME
    profiles.write_profile_settings(directory, 0, {'scale': 1.2})
    real = cwl.atomic_write_json
    def fail(path, data):
        if path == profiles.slot_profile_path(directory, 0):
            raise cwl.AtomicWriteFailed('injected')
        real(path, data)
    with monkeypatch.context() as patch:
        patch.setattr(cwl, 'atomic_write_json', fail)
        assert profiles.reset_slot_profile(directory, 0).status == 'partial'
    snapshot = profiles.read_slot_snapshot(directory, 0)
    result = profiles.write_profile_settings(directory, 0, {'scale': 1.4},
                                             generation=snapshot.generation)
    assert not result.ok
    assert profiles.reset_slot_profile(directory, 0).ok



def test_main_identity_settings_session_routes_to_slot0(tmp_path):
    """#271：空身份（主宠）的设置会话按 slot0 分流——每宠键落 config-slot-0.json、
    主配置保持干净、重开设置读回新值（修复前：整表保存把每宠键当退役键 pop 掉，
    写入凭空蒸发且 save 报成功，重开显示出厂默认）。"""
    Config(base=tmp_path, instance_id='').save()  # 建主配置 + 迁移落种 slot0 档案

    view = route_settings_config(Config(base=tmp_path, instance_id=''))
    assert isinstance(view, SlotRoutedConfig)
    view.set('self_talk_image_chance', 7)
    assert view.save()

    directory = tmp_path / APP_DIR_NAME
    assert profiles.read_slot_profile(directory, 0).value('self_talk_image_chance') == 7
    assert 'self_talk_image_chance' not in json.loads(
        (directory / 'config.json').read_text(encoding='utf8'))
    reopened = route_settings_config(Config(base=tmp_path, instance_id=''))
    assert reopened.get('self_talk_image_chance') == 7


def test_main_identity_session_global_keys_stay_global(tmp_path):
    """#271：空身份会话里的全局键仍落 config.json，不污染 slot0 档案。"""
    Config(base=tmp_path, instance_id='').save()
    view = route_settings_config(Config(base=tmp_path, instance_id=''))
    view.set('system_notifications_enabled', False)
    assert view.save()

    directory = tmp_path / APP_DIR_NAME
    data = json.loads((directory / 'config.json').read_text(encoding='utf8'))
    assert data.get('system_notifications_enabled') is False
    assert profiles.read_slot_profile(directory, 0).value('system_notifications_enabled') is None


def test_main_identity_session_untouched_slot0_keys_preserved(tmp_path):
    """#271：空身份会话只动了一个键时，档案里既有自定义值不被默认回灌。"""
    directory = tmp_path / APP_DIR_NAME
    Config(base=tmp_path, instance_id='').save()
    assert profiles.write_profile_settings(directory, 0, {'self_talk_bubble_style': 'soft_blue_top'}).ok

    view = route_settings_config(Config(base=tmp_path, instance_id=''))
    view.set('self_talk_image_chance', 7)
    assert view.save()
    assert profiles.read_slot_profile(directory, 0).value('self_talk_bubble_style') == 'soft_blue_top'


def test_unmigrated_legacy_config_is_migrated_before_slot0_routing(tmp_path):
    """#271 审查 P1：未迁移目录（只有旧 config.json、无 slot0 档案）直接进设置
    会话——路由前必须先跑 migrate_slot0_profile；否则路由视图读到的是出厂默认，
    保存时默认档案抢先落盘、Config.save() 的迁移分支被跳过，旧每宠值被当退役键
    清掉且没有迁移备份。"""
    directory = tmp_path / APP_DIR_NAME
    directory.mkdir()
    legacy = {'version': 1, 'scale': 0.5, 'character': 'cat', 'self_talk_image_chance': 55}
    (directory / 'config.json').write_text(json.dumps(legacy), encoding='utf8')

    view = route_settings_config(Config(base=tmp_path, instance_id=''))
    assert isinstance(view, SlotRoutedConfig)
    assert view.get('self_talk_image_chance') == 55, "旧值必须先迁移进档案，而不是读出默认 30"
    assert view.get('character') == 'cat'
    # scale 属屏幕相对几何口径，迁移按同源默认重落地（normalize_pet_profile_snapshot），
    # 不随旧配置搬迁——与「落种永不写位置键」同一哲学，不算丢失。
    backup = (directory / 'config.json').with_name(
        'config.json' + profiles.PROFILE_MIGRATION_BACKUP_SUFFIX)
    assert backup.is_file(), "迁移必须留下备份"

    view.set('self_talk_bubble_style', 'soft_blue_top')
    assert view.save()
    profile = profiles.read_slot_profile(directory, 0)
    assert profile.value('self_talk_image_chance') == 55, "未编辑的旧每宠值不许丢"
    assert profile.value('self_talk_bubble_style') == 'soft_blue_top'
    main_data = json.loads((directory / 'config.json').read_text(encoding='utf8'))
    assert 'self_talk_image_chance' not in main_data
