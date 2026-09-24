# -*- coding: utf-8 -*-
from pet.config import Config


def test_dialogue_modes_and_custom_phrases_persist(tmp_path):
    cfg = Config(base=tmp_path)
    assert cfg.get("dialogue_mode") == "legacy"
    cfg.set("dialogue_mode", "whale_maid")
    cfg.set("dialogue_phrases", {"start": "你好", "thinking": ["想想"]})
    cfg.save()
    loaded = Config(base=tmp_path)
    assert loaded.get("dialogue_mode") == "whale_maid"
    assert loaded.get("dialogue_phrases")["start"] == "你好"
    assert loaded.get("dialogue_phrases")["thinking"] == ["想想"]


def test_dialogue_last_scope_roundtrip(tmp_path):
    """设置页「记住上次编辑层」：dialogue_last_scope 走默认值/白名单持久化。"""
    cfg = Config(base=tmp_path)
    assert cfg.get("dialogue_last_scope") == ""
    cfg.set("dialogue_last_scope", "dsh")
    cfg.save()
    loaded = Config(base=tmp_path)
    assert loaded.get("dialogue_last_scope") == "dsh"


def test_bad_mode_and_phrase_types_are_repaired(tmp_path):
    cfg = Config(base=tmp_path)
    cfg.set("dialogue_mode", "bad")
    cfg.set("dialogue_phrases", {"start": 42, "thinking": [], "unknown": ["ok"]})
    cfg._normalize_pet_settings()
    assert cfg.get("dialogue_mode") == "legacy"
    assert "start" not in cfg.get("dialogue_phrases")
    assert cfg.get("dialogue_phrases")["unknown"] == ["ok"]


def test_unified_preset_phrases_survive_normalize(tmp_path):
    """双层统一预设 {global, agents} 在 config 清洗后原样保留（ticket 05）。

    旧清洗逻辑只认 {event: [...]} 单层，会把 global/agents 这两个 dict 值
    当作非法条目丢掉；升级后须保留结构且逐事件校验值类型。
    """
    cfg = Config(base=tmp_path)
    preset = {
        "global": {
            "start": ["全局开始"],
            "thinking": ["全局思考"],
        },
        "agents": {
            "dsh": {"start": ["DSH 专属开始"]},
            "claude": {"thinking": 42},  # 非法值须被清掉，但结构保留
        },
    }
    cfg.set("dialogue_phrases", preset)
    cfg._normalize_pet_settings()
    cleaned = cfg.get("dialogue_phrases")
    assert isinstance(cleaned.get("global"), dict)
    assert cleaned["global"]["start"] == ["全局开始"]
    assert isinstance(cleaned.get("agents"), dict)
    assert cleaned["agents"]["dsh"]["start"] == ["DSH 专属开始"]
    # claude 仅含非法 42 → 该 agent 无任何有效覆盖，整键被清理（delta 空无意义）
    assert "claude" not in cleaned["agents"]


def test_legacy_placeholder_fields_migrate_to_new_names(tmp_path):
    """用户自定义文案里的旧占位符一次性迁移：{source}→{failureType}、
    {errorText}→{errorMessage}（词表改名后不留兼容别名；新配置幂等 no-op）。

    覆盖单层 flat 与双层 global/agents 两种形状、str/list 两种值。
    """
    cfg = Config(base=tmp_path)
    cfg.set(
        "dialogue_phrases",
        {
            "global": {
                "failure.retry": "本轮失败，来源是{source}",
                "failure.tool": ["工具错误正文：{errorText}，码 {errorCode}"],
            },
            "agents": {
                "dsh": {"failure.generic": "来源 {source} / {errorText}"},
            },
        },
    )
    cfg._normalize_pet_settings()
    phrases = cfg.get("dialogue_phrases")
    assert phrases["global"]["failure.retry"] == "本轮失败，来源是{failureType}"
    assert phrases["global"]["failure.tool"] == ["工具错误正文：{errorMessage}，码 {errorCode}"]
    assert phrases["agents"]["dsh"]["failure.generic"] == "来源 {failureType} / {errorMessage}"


def test_legacy_event_keys_migrate_to_semantic_names(tmp_path):
    """用户自定义文案里的旧事件键一次性迁移：rate_limit.one/many → model_access.*。

    事件键按语义命名（不留状态码痕迹）；内置 preset JSON 直接改源文件，
    用户已保存的 dialogue_phrases 由加载期迁移兜底——否则旧键文案会变成
    永远取不到的死键（用户看到的仍是旧文案，改新文案却不生效）。
    """
    cfg = Config(base=tmp_path)
    cfg.set(
        "dialogue_phrases",
        {
            "global": {
                "rate_limit.one": ["被限流了老文案"],
                "rate_limit.many": "已连续限流 {count} 次",
            },
            "agents": {
                "dsh": {"rate_limit.one": "DSH 专属限流文案"},
            },
        },
    )
    cfg._normalize_pet_settings()
    phrases = cfg.get("dialogue_phrases")
    assert "rate_limit.one" not in phrases["global"]
    assert "rate_limit.many" not in phrases["global"]
    assert phrases["global"]["model_access.one"] == ["被限流了老文案"]
    assert phrases["global"]["model_access.many"] == "已连续限流 {count} 次"
    assert "rate_limit.one" not in phrases["agents"]["dsh"]
    assert phrases["agents"]["dsh"]["model_access.one"] == "DSH 专属限流文案"


def test_event_key_migration_prefers_new_key_when_both_present(tmp_path):
    """新旧键并存时以新配置为准：旧键丢弃，不合并、不留别名。"""
    cfg = Config(base=tmp_path)
    cfg.set(
        "dialogue_phrases",
        {
            "global": {
                "model_access.one": ["新文案"],
                "rate_limit.one": ["旧文案"],
            },
        },
    )
    cfg._normalize_pet_settings()
    phrases = cfg.get("dialogue_phrases")
    assert phrases["global"]["model_access.one"] == ["新文案"]
    assert "rate_limit.one" not in phrases["global"]


def test_placeholder_migration_idempotent_and_noop_on_fresh(tmp_path):
    """迁移幂等：新文案（已用 failureType/errorMessage）重复清洗不再变化。"""
    cfg = Config(base=tmp_path)
    fresh = {"global": {"failure.retry": "重试失败：{failureType}"}}
    cfg.set("dialogue_phrases", fresh)
    cfg._normalize_pet_settings()
    first = cfg.get("dialogue_phrases")
    cfg._normalize_pet_settings()
    assert cfg.get("dialogue_phrases") == first, "迁移应幂等（旧占位符不存在即 no-op）"


# --- ticket 02：统一预设（global + agents delta）渲染路由 ---


def test_phrase_lookup_prefers_agent_delta_over_global():
    """agents[agent_key][key] 优先于 global[key]；缺省回退 global；global 缺失返回 None。"""
    from pet.persona_phrases import phrase_for_agent

    preset = {
        "global": {
            "start": ["全局 start"],
            "thinking": ["全局 thinking"],
        },
        "agents": {
            "dsh": {"start": ["DSH start"]},
        },
    }
    assert phrase_for_agent(preset, "dsh", "start") == ["DSH start"]
    assert phrase_for_agent(preset, "dsh", "thinking") == ["全局 thinking"]
    assert phrase_for_agent(preset, "claude", "start") == ["全局 start"]
    assert phrase_for_agent(preset, "claude", "missing_event") is None


def test_phrase_lookup_accepts_flat_legacy_phrases():
    """旧单层 {key: [...]} 视为 global（preset 无 global 键时整包即 global）。"""
    from pet.persona_phrases import phrase_for_agent

    flat = {"start": ["旧 start"], "thinking": ["旧 thinking"]}
    assert phrase_for_agent(flat, "dsh", "start") == ["旧 start"]
    assert phrase_for_agent(flat, "claude", "thinking") == ["旧 thinking"]


def test_phrase_lookup_non_agent_only_reads_global():
    """非 Agent 场景（agent_key=""）绝不查 agents 层，只查 global。

    非 Agent 事件（self_talk/balance.* 等）只应存在于 global；运行时它们由
    agent_key="" 的调用点渲染，天然绕过 agents 层。agents 层误存非 Agent 事件
    由 UI/导入层约束（ticket 04），不在渲染层维护事件分类。
    """
    from pet.persona_phrases import phrase_for_agent

    preset = {
        "global": {"self_talk": ["只有 global"]},
        "agents": {"dsh": {"start": ["DSH start"]}},
    }
    assert phrase_for_agent(preset, "", "self_talk") == ["只有 global"]


# --- ticket 05：PhrasePicker 的 agent 维度渲染（custom 分支） ---


def test_picker_custom_for_agent_renders_delta_then_global():
    """custom_for_agent：agents[agent_key][key] → global[key] → fallback；
    渲染占位符注入 values。"""
    from pet.persona_phrases import PhrasePicker

    picker = PhrasePicker()
    preset = {
        "global": {
            "start": ["全局 {name} start"],
            "thinking": ["全局 thinking"],
        },
        "agents": {
            "dsh": {"start": ["DSH {name} start"]},
        },
    }
    assert picker.custom_for_agent(preset, "dsh", "start", "fb", name="DSH") == "DSH DSH start"
    assert picker.custom_for_agent(preset, "claude", "start", "fb", name="Claude Code") == "全局 Claude Code start"
    assert picker.custom_for_agent(preset, "claude", "thinking", "fb") == "全局 thinking"
    assert picker.custom_for_agent(preset, "claude", "no_such_event", "回退") == "回退"


def test_picker_custom_for_agent_non_agent_ignores_agents():
    """agent_key=""（非 Agent 场景）只渲染 global，忽略 agents 层。"""
    from pet.persona_phrases import PhrasePicker

    picker = PhrasePicker()
    preset = {
        "global": {"self_talk": ["global 自说自话"]},
        "agents": {"dsh": {"self_talk": ["不该命中"]}},
    }
    assert picker.custom_for_agent(preset, "", "self_talk", "fb") == "global 自说自话"
