"""设置元数据：给外部 AI 的稳定 schema（键/作用域/类型/范围/默认值）。

``DEFAULTS`` 仍是运行时唯一真值；本模块只描述它，不参与配置读写。
范围/类型/枚举尽量由 ``scripts/gen_settings_meta.py`` 从 ``_normalize`` 生成，
人工只补中文名、常用范围和枚举选项。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import DEFAULTS, GLOBAL_CONFIG_KEYS, ISLAND_ACCENTS
from .contracts.bubble_styles import STYLE_IDS
from .net.github_mirrors import ALL_MIRROR_POLICIES
from .settings_schema_ranges import CHOICES as _GENERATED_CHOICES
from .settings_schema_ranges import KINDS as _GENERATED_KINDS
from .settings_schema_ranges import RANGES as _GENERATED_RANGES

_STATE_KEYS = frozenset(
    {
        "schema",
        "screen_name",
        "rx",
        "ry",
        "body_rx",
        "body_ry",
        "pos_x",
        "pos_y",
        "island_x",
        "island_y",
        "island_dock_edge",
        "menu_hidden",
        "menu_order",
        "menu_layout",
        "last_settings_page",
        "positions",
        "active_pets",
        "todo_items",
        "weather_city_codes",
    }
)
_RESTART_KEYS = frozenset(
    {
        "frameseq",
        "stream_capture_mode",
        "allow_cross_screen",
        "hotkey_toggle_pet",
        "hotkey_keyboard_move",
        "hotkey_open_chat",
        "hotkey_look_screen",
        "hotkey_screen_capture",
    }
)

_LABELS = {
    "character": "当前角色",
    "scale": "桌宠大小",
    "always_on_top": "窗口置顶",
    "click_through": "鼠标穿透（仅左键）",
    "multi_screen": "多显示器",
    "click_sound": "点击音效",
    "volume": "音量",
    "ui_font_size": "界面字号",
    "bubble_text_scale": "气泡字号",
    "edge_probe_enabled": "边缘探头",
    "auto_hide_fullscreen": "全屏时自动隐藏",
    "stream_capture_mode": "直播捕获模式",
    "stream_capture_keycolor": "直播色键",
    "stream_key_color": "色键颜色",
    "allow_cross_screen": "允许跨屏拖动",
    "frameseq": "帧序列模式",
    "character_aliases": "角色别名",
    "hotkey_toggle_pet": "显示/隐藏快捷键",
    "hotkey_keyboard_move": "键盘控制快捷键",
    "hotkey_open_chat": "AI 对话快捷键",
    "hotkey_look_screen": "看看屏幕快捷键",
    "hotkey_screen_capture": "截图提问快捷键",
    "ai_service": "AI 服务",
    "ai_model": "模型",
    "ai_system_prompt": "系统提示词",
    "ai_persona": "人格卡",
    "ai_vision_enabled": "视觉能力",
    "ai_temperature": "温度",
    "ai_look_screen_auto_open": "看看屏幕后自动开窗",
    "ai_thinking": "深度思考",
    "ai_reasoning_effort": "思考强度",
    "ai_animation_enabled": "对话动作",
    "ai_animation_thinking": "思考动作",
    "ai_animation_min_seconds": "动作最短间隔",
    "ai_memory_enabled": "长期记忆",
    "ai_memory_inject": "记忆注入条数",
    "ai_memory_max_entries": "记忆条数上限",
    "ai_memory_share_channels": "AI/DSH 共享记忆",
    "ai_memory_import_dsh": "导入 DSH 记忆",
    "ai_memory_identity_dir": "身份接力目录",
    "ai_memory_feiyu_root": "飞鱼记忆根目录",
    "ai_memory_feiyu_session": "飞鱼会话",
    "ai_web_search": "联网搜索",
    "ai_web_engine": "搜索服务",
    "ai_web_results": "搜索结果条数",
    "ai_web_fetch": "阅读网页正文",
    "ai_proactive_enabled": "主动识屏",
    "ai_proactive_whitelist": "主动识屏白名单",
    "ai_proactive_dwell_seconds": "停留判定秒数",
    "ai_proactive_change_threshold": "画面变化阈值",
    "ai_proactive_daily_cap": "每日次数上限",
    "ai_proactive_cooldown_seconds": "冷却秒数",
    "ai_proactive_min_interval_seconds": "最小间隔秒数",
    "ai_proactive_require_idle": "只在闲置时",
    "ai_proactive_min_idle_seconds": "闲置秒数",
    "ai_proactive_dry_run": "只记录不调用",
    "ai_proactive_pre_cue": "先提示再看",
    "ai_proactive_sync_chat": "写入会话",
    "ai_agent_enabled": "Agent 联动",
    "ai_agent_dsh_enabled": "DSH 联动",
    "ai_agent_events_enabled": "事件联动",
    "ai_agent_custom_agents": "自定义 Agent",
    "ai_agent_animation_enabled": "Agent 动画",
    "ai_agent_command_enabled": "Agent 指令",
    "ai_agent_activity_bubbles": "过程气泡",
    "ai_agent_done_notice": "完成提示",
    "ai_agent_min_interval_seconds": "动画最小间隔",
    "ai_agent_sound_enabled": "Agent 提示音",
    "ai_agent_sound_volume": "提示音音量",
    "ai_agent_sound_cooldown_seconds": "提示音冷却",
    "island_enabled": "灵动岛",
    "island_show_name": "岛显示名字",
    "island_show_status": "岛显示状态点",
    "island_show_messages": "岛显示最近消息",
    "island_info_mode": "岛信息模式",
    "island_custom_text": "岛自定义文本",
    "island_style": "岛配色",
    "island_opacity": "岛不透明度",
    "island_accent": "岛强调色",
    "island_click_action": "岛点击动作",
    "island_show_toggle_button": "岛卡片显隐桌宠按钮",
    "island_hide_fullscreen": "全屏时隐藏岛",
    "island_metrics": "岛显示指标",
    "island_edge_dock": "岛贴边停靠",
    "island_collision": "岛与桌宠碰撞",
    "balance_tier_labels_mode": "余额峰谷文案",
    "balance_tier_color_enabled": "余额颜色",
    "chat_always_on_top": "对话窗置顶",
    "chat_follow_pet": "对话窗跟随桌宠",
    "chat_layout": "对话窗布局",
    "chat_trash_retention_days": "回收站保留天数",
    "chat_dsh_mode": "DSH 模式",
    "chat_dark_mode": "对话窗深色",
    "chat_background": "对话窗背景",
    "chat_accent": "对话窗强调色",
    "chat_font_size": "对话窗字号",
    "chat_font_family": "对话窗字体",
    "click_squash_enabled": "点击 Q 弹",
    "click_squash_ms": "Q 弹时长",
    "click_squash_bounces": "Q 弹次数",
    "click_squash_strength": "Q 弹强度",
    "drag_physics_enabled": "拖动物理",
    "drag_lean_enabled": "拖动倾斜",
    "drag_lean_strength": "倾斜强度",
    "drop_gravity_enabled": "静止放手下落",
    "throw_strength": "甩飞力度",
    "slingshot_enabled": "弹弓",
    "collision_enabled": "桌宠碰撞",
    "collision_sound": "碰撞音效",
    "golden_spin_on_click": "点击黄金回旋",
    "golden_spin_direct": "直接黄金回旋",
    "throw_egg_enabled": "探头击飞旋转",
    "file_feeding_enabled": "拖文件投喂",
    "no_move": "不自动移动",
    "lock_position": "锁定位置",
    "shift_drag": "Shift 拖动",
    "playback_speed": "播放速率",
    "idle_low_fps_enabled": "闲置降帧",
    "work_animations_only": "工作时只播工作动画",
    "animation_gap_seconds": "动作等待间隔",
    "head_pat_enabled": "摸头互动",
    "head_pat_animation_mode": "摸头动画",
    "head_pat_animation_names": "摸头动画名单",
    "anim_weight_idle": "待机权重",
    "anim_weight_turn": "转向权重",
    "anim_weight_act": "动作权重",
    "anim_weight_move": "移动权重",
    "anim_random_auto": "随机动作总开关",
    "anim_auto_off": "仅手动动画",
    "anim_blocked": "禁用动画",
    "anim_facing_default": "素材默认朝向",
    "anim_facing_groups": "分动画组朝向",
    "spawn_inherit_size": "子宠继承大小",
    "spawn_scale": "子宠大小",
    "self_talk_enabled": "自言自语",
    "self_talk_texts": "自定义台词",
    "self_talk_daily_enabled": "每日一句",
    "self_talk_daily_texts": "每日一句文本",
    "self_talk_daily_count": "每日句数",
    "self_talk_daily_sequence": "按顺序说",
    "self_talk_min": "最短间隔",
    "self_talk_max": "最长间隔",
    "self_talk_duration": "气泡时长",
    "self_talk_bubble_style": "气泡风格",
    "self_talk_image_dir": "配图目录",
    "self_talk_image_scale": "配图大小",
    "self_talk_image_chance": "配图概率",
    "self_talk_voice_enabled": "自语语音",
    "self_talk_champion_enabled": "自语彩蛋",
    "self_talk_champion_chance": "彩蛋概率",
    "click_talk_enabled": "点击台词",
    "click_talk_texts": "点击台词文本",
    "click_talk_voice_enabled": "点击台词语音",
    "quick_launch_apps": "快捷启动应用",
    "quiet_minutes_default": "免打扰默认分钟",
    "network_info_enabled": "网络信息",
    "memory_trim_enabled": "回收内存",
    "divination_enabled": "占卜",
    "divination_speak": "占卜朗读",
    "divination_hint": "占卜提示",
    "festival_enabled": "节日提醒",
    "festival_reminder_enabled": "定时节日提醒",
    "festival_reminder_cn": "中国节日",
    "festival_reminder_solar_terms": "二十四节气",
    "festival_reminder_west": "西方节日",
    "festival_reminder_mode": "提醒方式",
    "festival_reminder_count": "提醒次数",
    "festival_reminder_times": "提醒时间",
    "festival_reminder_show_quote": "显示诗句",
    "festival_reminder_speak": "朗读提醒",
    "festival_custom_quotes_cn": "自定义中国节日文案",
    "festival_custom_quotes_west": "自定义西方节日文案",
    "festival_birthday": "生日",
    "weather_enabled": "天气",
    "weather_city": "当前城市",
    "weather_city_list": "城市列表",
    "weather_city_codes": "城市编码",
    "file_reader_enabled": "文件解读",
    "file_reader_progress_seconds": "文件解读进度间隔",
    "file_reader_record": "文件解读记录",
    "todo_reminder_enabled": "待办提醒",
    "todo_reminder_lead_minutes": "提前分钟",
    "todo_reminder_speak": "朗读待办",
    "voice_chime_enabled": "报时",
    "voice_chime_schedule": "报时频率",
    "voice_chime_times": "报时时间",
    "voice_chime_show_bubble": "报时气泡",
    "voice_chime_quotes": "报时台词",
    "voice_chime_anim": "报时动画",
    "voice_chime_provider": "报时语音服务",
    "voice_chime_voice": "报时音色",
    "voice_chime_rate": "报时语速",
    "voice_chime_volume": "报时音量",
    "music_enabled": "音乐",
    "music_sing_enabled": "唱歌",
    "music_buddy_wine_mode": "朋友的酒模式",
    "music_buddy_wine_bpm": "朋友的酒 BPM",
    "music_sing_grace_seconds": "唱歌宽限",
    "music_lyric_enabled": "歌词",
    "music_lyric_lead_seconds": "歌词提前量",
    "music_lyric_cache_limit": "歌词缓存上限",
    "music_lyric_offset_seconds": "歌词偏移",
    "music_browser_media_enabled": "浏览器媒体",
    "music_player_paths": "播放器路径",
    "music_player": "当前播放器",
    "music_filter_enabled": "只识别指定播放器",
    "music_filter_extra_ids": "额外识别名",
    "music_title_format": "播放器标题格式",
    "music_audio_device": "音乐输出设备",
    "speech_backend": "语音服务",
    "speech_fallback": "语音兜底",
    "speech_voice": "音色",
    "speech_rate": "语速",
    "speech_volume": "语音音量",
    "speech_cache_limit": "语音缓存上限",
    "speech_doubao_voice": "豆包音色",
    "speech_mimo_voice": "MiMo 音色",
    "speech_local_endpoint": "本地 TTS 地址",
    "speech_local_protocol": "本地 TTS 协议",
    "speech_local_path": "本地 TTS 路径",
    "speech_local_model": "本地 TTS 模型",
    "speech_local_voice": "本地 TTS 音色",
    "speech_local_format": "本地 TTS 格式",
    "speech_local_timeout": "本地 TTS 超时",
    "speech_local_ref_audio": "GPT-SoVITS 参考音频",
    "speech_local_prompt_text": "GPT-SoVITS 参考文本",
    "speech_local_prompt_lang": "GPT-SoVITS 参考语言",
    "speech_local_text_lang": "GPT-SoVITS 合成语言",
    "speech_sapi_voice": "SAPI 音色",
    "asr_enabled": "语音输入",
    "asr_engine": "语音识别引擎",
    "asr_endpoint": "语音识别地址",
    "asr_model": "语音识别模型",
    "asr_language": "识别语言",
    "asr_device": "录音设备",
    "asr_stream_enabled": "流式识别",
    "asr_partial_interval_ms": "流式间隔",
    "asr_auto_send": "识别后自动发送",
    "asr_max_seconds": "最长录音",
    "asr_timeout": "识别超时",
    "ai_voice_reply": "语音回答",
    "ai_meme_reply_enabled": "表情包回复",
    "menu_animation_icons": "动画缩略图",
    "market_download_connections": "下载连接数",
    "market_download_sources": "下载源",
    "pack_mirror_policy": "镜像策略",
    "pack_mirror_custom_url": "自定义镜像前缀",
    "market_index_url": "市场索引地址",
    "allow_feature_mods": "允许第三方扩展",
}

_CHOICES = {
    "anim_facing_default": ("left", "right"),
    "self_talk_bubble_style": STYLE_IDS,
    "throw_strength": ("gentle", "standard", "strong", "crazy"),
    "head_pat_animation_mode": ("default_click", "custom", "all_single"),
    "festival_reminder_mode": ("times", "custom"),
    "island_style": ("dark", "light", "glass"),
    "island_accent": tuple(ISLAND_ACCENTS),
    "island_info_mode": ("time", "custom"),
    "island_click_action": ("expand", "toggle_pet"),
    "island_dock_edge": ("none", "left", "right", "top", "bottom"),
    "balance_tier_labels_mode": ("default", "liangwen", "custom"),
    "chat_layout": ("desktop", "phone"),
    "frameseq": ("auto", "off", "only"),
    "market_download_sources": ("auto", "ordered", "race"),
    "pack_mirror_policy": ALL_MIRROR_POLICIES,
    "speech_backend": ("edge", "sapi", "doubao", "mimo", "local"),
    "speech_fallback": ("", "edge", "sapi", "doubao", "mimo", "local"),
    "speech_local_protocol": ("openai", "simple"),
    "speech_local_format": ("mp3", "wav"),
    "asr_engine": ("sapi", "http"),
    "ai_thinking": ("auto", "enabled", "disabled"),
    "ai_reasoning_effort": ("low", "high", "max"),
    "ai_web_engine": ("baidu", "bocha", "tavily", "duckduckgo"),
    "voice_chime_schedule": (
        "hourly",
        "every_30",
        "every_15",
        "every_5",
        "every_minute",
        "custom",
    ),
}

_RANGES = dict(_GENERATED_RANGES)
_RANGES.update(
    {
        "scale": (0.1, 3.0),
        "volume": (0.0, 1.0),
        "playback_speed": (0.5, 2.0),
        "ui_font_size": (10, 18),
        "bubble_text_scale": (50, 300),
        "click_squash_ms": (80, 1000),
        "click_squash_bounces": (1, 5),
        "click_squash_strength": (30, 200),
        "drag_lean_strength": (0, 100),
        "animation_gap_seconds": (0, 3600),
        "anim_weight_idle": (0, 100),
        "anim_weight_turn": (0, 100),
        "anim_weight_act": (0, 100),
        "anim_weight_move": (0, 100),
        "self_talk_min": (1, 3600),
        "self_talk_max": (1, 3600),
        "self_talk_duration": (1, 120),
        "self_talk_image_scale": (50, 300),
        "self_talk_image_chance": (0, 100),
        "self_talk_champion_chance": (0, 100),
        "self_talk_daily_count": (1, 24),
        "self_talk_daily_texts": None,
        "click_squash_enabled": None,
        "easter_egg_image_scale": (50, 300),
        "spawn_scale": (0.1, 3.0),
        "chat_font_size": (0, 24),
        "chat_trash_retention_days": (0, 365),
        "ai_temperature": (0.0, 2.0),
        "ai_memory_inject": (1, 8),
        "ai_memory_max_entries": (50, 2000),
        "ai_web_results": (1, 10),
        "ai_web_search_max_sources": (1, 8),
        "ai_web_search_page_chars": (2000, 40000),
        "ai_proactive_dwell_seconds": (15, 600),
        "ai_proactive_change_threshold": (0, 32),
        "ai_proactive_daily_cap": (1, 9999),
        "ai_proactive_cooldown_seconds": (30, 7200),
        "ai_proactive_min_interval_seconds": (30, 3600),
        "ai_proactive_min_idle_seconds": (0, 3600),
        "ai_agent_min_interval_seconds": (0.5, 10.0),
        "ai_agent_sound_volume": (0.0, 1.0),
        "ai_agent_sound_cooldown_seconds": (0.0, 10.0),
        "island_opacity": (0.4, 1.0),
        "market_download_connections": (1, 8),
        "music_buddy_wine_bpm": (60, 200),
        "music_sing_grace_seconds": (0.0, 120.0),
        "music_lyric_lead_seconds": (-2.0, 3.0),
        "music_lyric_cache_limit": (1, 2000),
        "music_lyric_offset_seconds": (-3.0, 3.0),
        "speech_rate": (0.5, 2.0),
        "speech_volume": (0.0, 1.0),
        "speech_cache_limit": (10, 2000),
        "speech_local_timeout": (5.0, 120.0),
        "asr_partial_interval_ms": (400, 3000),
        "asr_max_seconds": (3, 60),
        "asr_timeout": (5.0, 120.0),
        "voice_chime_rate": (0.5, 2.0),
        "voice_chime_volume": (0.0, 1.0),
        "quiet_minutes_default": (1, 1440),
        "file_reader_progress_seconds": (5, 120),
        "todo_reminder_lead_minutes": (0, 60),
    }
)


def _type_of(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "str"


def _group_for(key: str) -> str:
    if key.startswith(("ai_", "chat_")):
        return "AI 对话"
    if key.startswith("island_"):
        return "灵动岛"
    if key.startswith(("speech_", "asr_", "voice_chime")):
        return "语音"
    if key.startswith("music_"):
        return "音乐"
    if key.startswith("weather_"):
        return "天气"
    if key.startswith("hotkey_"):
        return "快捷键"
    if key.startswith(("click_", "drag_", "throw_", "golden_", "collision_", "file_feeding", "no_move", "lock_position", "shift_drag")):
        return "互动与物理"
    if key.startswith(("anim_", "animation_", "playback_", "idle_", "work_", "head_pat", "bubble_")):
        return "动画与气泡"
    if key.startswith(("easter_", "self_talk_", "click_talk")):
        return "彩蛋与自言自语"
    if key.startswith("menu_"):
        return "菜单"
    if key.startswith(("todo_", "festival_", "divination_")):
        return "待办与节日"
    if key.startswith(("market_", "pack_")):
        return "素材包"
    return "其他"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    scope: str
    type: str
    default: Any
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] | None = None
    label_zh: str = ""
    description: str = ""
    group: str = ""
    kind: str = "preference"
    restart_required: bool = False
    sensitive: bool = False
    reset_exempt: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "scope": self.scope,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "choices": list(self.choices) if self.choices else None,
            "label_zh": self.label_zh,
            "description": self.description,
            "group": self.group,
            "kind": self.kind,
            "restart_required": self.restart_required,
            "sensitive": self.sensitive,
            "reset_exempt": self.reset_exempt,
            "aliases": list(self.aliases),
        }


def _build() -> dict[str, SettingSpec]:
    settings: dict[str, SettingSpec] = {}
    for key, default in DEFAULTS.items():
        kind = "state" if key in _STATE_KEYS else "preference"
        spec_type = _GENERATED_KINDS.get(key) or _type_of(default)
        limits = _RANGES.get(key)
        minimum = maximum = None
        if isinstance(limits, tuple) and len(limits) == 2:
            minimum, maximum = float(limits[0]), float(limits[1])
        choices = _CHOICES.get(key) or _GENERATED_CHOICES.get(key)
        settings[key] = SettingSpec(
            key=key,
            scope="global" if key in GLOBAL_CONFIG_KEYS else "pet",
            type=spec_type,
            default=default,
            min=minimum,
            max=maximum,
            choices=tuple(choices) if choices else None,
            label_zh=_LABELS.get(key, key),
            description="",
            group=_group_for(key),
            kind=kind,
            restart_required=key in _RESTART_KEYS,
            reset_exempt=key in _STATE_KEYS,
        )
    return settings


SETTINGS: dict[str, SettingSpec] = _build()


def spec_for(key: str) -> SettingSpec | None:
    return SETTINGS.get(str(key or "").strip())


def as_dicts(*, writable_only: bool = False) -> list[dict[str, Any]]:
    items = []
    for spec in SETTINGS.values():
        if writable_only and spec.kind != "preference":
            continue
        items.append(spec.as_dict())
    return items


__all__ = ["SETTINGS", "SettingSpec", "as_dicts", "spec_for"]
