# -*- coding: utf-8 -*-
"""功能模块清单：激活层的纯数据地基（零 Qt、只吃 stdlib）。

设计稿见 `.scratch/feature-modules/SPEC.md`（§2 架构、§3 模块清单、§4 配置语义），
逐模块的入口证据见同目录 `ENTRY-INVENTORY.md`。本模块只做三件事：**报清单**、
**归一化启用集合**、**解依赖闭包**——不读配置、不写配置、不碰界面。它必须在
启动早期（任何可选服务创建之前）就能 import，所以依赖面只允许 stdlib：一旦
把 Qt 拖进来，「未激活零活动」就不再是清单的承诺。

三条口径（对齐 SPEC §4，写在这里是为了让调用方不必再猜）：

- **模块激活 ≠ 功能开关**。`FeatureSpec.config_keys` 列的是「该模块拥有的键」；
  模块激活后这些键的行为语义原样保留（`weather_enabled` 关着 = 有菜单但不自动
  出气泡）。两层语义不合并。
- **合法空集不回落**。`normalize_enabled([])` 返回空启用集，绝不回退成「全部
  启用」——那是用户明确选择的纯核心。
- **损坏不冒充新装**。形状不认识（dict / str / int / 含非字符串元素的 list /
  `None`）一律抛 `ValueError`：本函数永不拿默认集顶掉用户的启用集合，调用方
  保留原始数据 + 给可见诊断。「字段缺失」不是本函数的输入，缺失由迁移判定处理
  （`MIGRATION_BASELINE` 是那次判定的依据）。

`config_keys` 用点号表示嵌套路径（`"agent_link.dsh"` 即 `config.json` 里的
`{"agent_link": {"dsh": ...}}`）；把一整棵字典当一档登记的（`chat`、
`dynamic_island`）在旁注里指到 ENTRY-INVENTORY 的逐键小节——那种字典的子键有
二十余个，逐个抄进本文件只会制造两份会漂移的真相。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple

# ---------------------------------------------------------------- 分组与批次
#: 功能页的分组（顺序即 `all_features()` 组内排序的依据）。
#: 分组只影响卡片落在哪一栏，与门控无关——模块的可运行性只看 `requires`。
CATEGORY_LIFE = "生活"
CATEGORY_VOICE = "声音"
CATEGORY_INTELLIGENCE = "智能"
CATEGORY_LINK = "联动"
CATEGORY_SYSTEM = "系统"
#: 分组全集（新增分组必须登记在这里，否则 `all_features()` 排到末尾）。
CATEGORY_ORDER: tuple[str, ...] = (
    CATEGORY_LIFE,
    CATEGORY_VOICE,
    CATEGORY_INTELLIGENCE,
    CATEGORY_LINK,
    CATEGORY_SYSTEM,
)

#: 批次（SPEC §3 的分批就是实施与评审顺序）：1=天气/抽签/待办/节日，
#: 2=语音/音乐，3=AI/文件解读/手动视觉/主动识屏/Agent/灵动岛，4=快捷启动/系统工具。
BATCH_FIRST = 1
BATCH_SECOND = 2
BATCH_THIRD = 3
BATCH_TOOLS = 4


@dataclass(frozen=True)
class FeatureSpec:
    """一个**可停用**功能模块的静态契约。

    核心能力（渲染动画/素材角色/多宠身份/拖拽缩放/基础行为碰撞/点击文字反馈/
    气泡音效展示原语/托盘/基础菜单/设置宿主/配置迁移/日志诊断/更新/功能管理
    自身/退出）**不在**本清单里：它们没有「停用」这一态，写进来只会让门控长出
    一堆恒真的分支。

    字段含义按用途分四组：

    - 呈现：`name` / `desc`（用户向一句话）/ `category`（功能页分组）/
      `search_aliases`（设置搜索的别名，命中未激活模块时导向功能页卡片而不是死链）。
    - 依赖：`requires`（硬依赖，缺一个该模块就不可运行）/ `optional_deps`
      （可选增强，缺了只是少一块能力，**不**影响激活——见 `resolve_dependencies`）。
    - 门控落点：`menu_action_ids`（`pet/context_menus/registry.py` 的动作 id）/
      `config_keys`（该模块拥有的配置键）。
    - 构建与平台约束：`needs_network`（运行期会发网络请求）/ `needs_paid`
      （主路径要用户自备付费凭据）/ `needs_external_process`（会拉起子进程或
      外部程序）/ `batch`（分批实施的编号）。
    """

    id: str
    name: str
    desc: str
    category: str
    search_aliases: tuple[str, ...]
    requires: tuple[str, ...]
    optional_deps: tuple[str, ...]
    menu_action_ids: tuple[str, ...]
    config_keys: tuple[str, ...]
    needs_network: bool
    needs_paid: bool
    needs_external_process: bool
    batch: int
    #: 用户能在右键菜单/设置里认出来的入口名（功能页卡片「对应：」行）——
    #: 告诉用户关掉这张卡后具体哪些东西会消失。没有显性入口的模块为空。
    entry_names: tuple[str, ...] = ()


#: 没有右键菜单动作的模块（`menu_action_ids` 刻意为空，不是抄漏）。
#:
#: SPEC §3 的「每个模块都要有菜单/设置入口」在代码里有一处例外：文件解读的
#: **唯一触发面是「把文件拖到桌宠身上」**——`pet/context_menus/` 全目录 grep 无
#: `file_interpret`（ENTRY-INVENTORY §文件解读·菜单动作 = 无），拖放链接线在
#: `pet/file_eater.py:262-320` 与 `pet/sprite_feeding.py:59-101`。
#:
#: 把它登记成白名单而不是编一个假的动作 id：编出来的 id 在注册表里查不到，
#: 门控会静默失效（菜单编辑器里也点不到），比空着更危险。
MENU_LESS_FEATURES = frozenset({"file_interpret", "stream_capture", "cross_screen"})


# ---------------------------------------------------------------- 模块清单
# 证据格式：`文件:行号`，全部取自 ENTRY-INVENTORY.md（2026-10-21 调查）。
# 行号会随代码漂移，所以这里只作为「从哪来的」的索引，不作为断言来源——
# 唯一被测试钉住的是本文件内部的自洽性（id 唯一、依赖存在、硬依赖无环等）。

FEATURES: dict[str, FeatureSpec] = {
    # ============================================================ 第一批：生活
    # 天气 weather —— ENTRY-INVENTORY §天气
    #   菜单：registry.py:394-405（weather / weather_cities）
    #   配置：config.py:1330-1336（4 键，全全局键）
    "weather": FeatureSpec(
        id="weather",
        name="天气",
        desc="按城市查看实时天气，右键菜单里切换城市。",
        category=CATEGORY_LIFE,
        search_aliases=("天气", "天气预报", "气温", "城市", "weather", "forecast"),
        requires=(),
        optional_deps=(),
        menu_action_ids=("weather", "weather_cities"),
        config_keys=(
            "weather_enabled",
            "weather_city",
            "weather_city_list",
            "weather_city_codes",
        ),
        needs_network=True,  # 中国天气网检索/实况 + open-meteo 兜底（pet/weather.py:75-83）
        needs_paid=False,
        needs_external_process=False,
        batch=BATCH_FIRST,
        entry_names=("「查看天气」「天气城市」",),
    ),
    # 抽签 divination —— ENTRY-INVENTORY §抽签
    #   菜单：registry.py:389-391（divination_draw）
    #   配置：config.py:1313-1318（4 键；divination_state 是运行时状态，无设置行）
    #   「念出来」经共用音频通道，网络在 voice 模块（divination_service.py:279-288），
    #   所以本模块自己不计联网。
    "divination": FeatureSpec(
        id="divination",
        name="抽签",
        desc="每天抽一支签，可以念出来，也可以每天提醒一次。",
        category=CATEGORY_LIFE,
        search_aliases=("抽签", "每日一签", "求签", "运势", "divination", "fortune"),
        requires=(),
        optional_deps=("voice",),
        menu_action_ids=("divination_draw",),
        config_keys=(
            "divination_enabled",
            "divination_speak",
            "divination_hint",
            "divination_state",
        ),
        needs_network=False,
        needs_paid=False,
        needs_external_process=False,
        batch=BATCH_FIRST,
        entry_names=("「抽支签」",),
    ),
    # 待办 todo —— ENTRY-INVENTORY §待办
    #   菜单：registry.py:374-376（todo_panel）
    #   配置：config.py:1257-1261（3 键）；条目不落配置，在 todo_items-<slot>.json
    "todo": FeatureSpec(
        id="todo",
        name="待办提醒",
        desc="到点提醒待办事项，可提前几分钟通知并语音播报。",
        category=CATEGORY_LIFE,
        search_aliases=("待办", "提醒", "任务", "todo", "reminder"),
        requires=(),
        optional_deps=("voice",),
        menu_action_ids=("todo_panel",),
        config_keys=(
            "todo_reminder_enabled",
            "todo_reminder_lead_minutes",
            "todo_reminder_speak",
        ),
        needs_network=False,
        needs_paid=False,
        needs_external_process=False,
        batch=BATCH_FIRST,
        entry_names=("「待办提醒」面板与到点通知",),
    ),
    # 节日 festival —— ENTRY-INVENTORY §节日
    #   菜单：registry.py:383-388（festival_now / festival_toggle，默认布局 visible:false）
    #   配置：config.py:1292-1308（13 键，全全局键）
    #   ⚠️ festival_reminder_animation 与 festival_birthday 是既有「死开关」
    #   （清单附录 1：除设置页与默认值外零读点）——仍如实登记，停用时要连行一起藏。
    "festival": FeatureSpec(
        id="festival",
        name="节日提醒",
        desc="节日与节气提醒，支持自定义文案和生日提醒。",
        category=CATEGORY_LIFE,
        search_aliases=("节日", "节气", "生日", "农历", "festival", "holiday"),
        requires=(),
        optional_deps=("voice",),
        menu_action_ids=("festival_now", "festival_toggle"),
        config_keys=(
            "festival_reminder_enabled",
            "festival_reminder_cn",
            "festival_reminder_solar_terms",
            "festival_reminder_west",
            "festival_reminder_mode",
            "festival_reminder_count",
            "festival_reminder_times",
            "festival_reminder_show_quote",
            "festival_reminder_speak",
            "festival_reminder_animation",
            "festival_birthday",
            "festival_custom_quotes_cn",
            "festival_custom_quotes_west",
        ),
        needs_network=False,
        needs_paid=False,
        needs_external_process=False,
        batch=BATCH_FIRST,
        entry_names=("「节日提醒」设置组与提醒气泡",),
    ),
    # ============================================================ 第二批：声音
    # 语音 voice —— ENTRY-INVENTORY §语音报时 + §点击朗读 + §TTS 注册表
    #   菜单：registry.py:377-382（voice_chime_now / voice_chime_toggle）
    #   配置：config.py:1263-1289（20 个 voice_chime_*，含各 TTS 后端字段键）
    #         + config.py:1155-1157/1116（点击朗读四键）
    #   点击朗读的「使能半边」click_show_self_talk 本体属互动域的核心行为，
    #   朗读部分归本模块（SPEC §5.2 点名的那类拆行）；预缓存名单第二来源是
    #   character_profiles.<角色>.click_talk_bindings（每宠嵌套，不登记为配置键）。
    #   needs_paid：默认引擎 edge 免费，MiMo/豆包要用户自备凭据——按主路径记 False。
    "voice": FeatureSpec(
        id="voice",
        name="语音",
        desc="语音报时与台词朗读，可切换多种语音引擎。",
        category=CATEGORY_VOICE,
        search_aliases=("语音", "报时", "朗读", "朗读台词", "音色", "TTS", "voice", "speech"),
        requires=(),
        optional_deps=(),
        menu_action_ids=("voice_chime_now", "voice_chime_toggle"),
        config_keys=(
            "voice_chime_enabled",
            "voice_chime_schedule",
            "voice_chime_custom_times",
            "voice_chime_volume",
            "voice_chime_show_bubble",
            "voice_chime_show_quote",
            "voice_chime_custom_quotes_zh",
            "voice_chime_custom_quotes_en",
            "voice_chime_tts_backend",
            "voice_chime_tts_fallback",
            "voice_chime_voice",
            "voice_chime_rate",
            "voice_chime_pitch",
            "voice_chime_mimo_model",
            "voice_chime_mimo_voice",
            "voice_chime_mimo_style",
            "voice_chime_sapi_voice",
            "voice_chime_sapi_rate",
            "voice_chime_doubao_speaker",
            "voice_chime_doubao_pitch",
            "self_talk_speak_enabled",
            "click_show_self_talk",
            "self_talk_voice_precache_enabled",
            "self_talk_texts",
        ),
        needs_network=True,  # edge / MiMo / 豆包三个后端都在合成线程里联网
        needs_paid=False,
        needs_external_process=True,  # 豆包本地变调拉 ffmpeg（pet/tts/doubao.py:199-216）
        batch=BATCH_SECOND,
        entry_names=("「语音报时」「点击台词朗读」「助手回复朗读」",),
    ),
    # 音乐 music —— ENTRY-INVENTORY §音乐
    #   菜单：registry.py:327-343（9 个注册动作）。`music`「音乐」子菜单容器只是
    #         布局节点（menu_templates/modern-default-v1.json:15-26），不在注册表
    #         SPECS 里，所以不登记为 menu_action_id。
    #   配置：config.py:1163-1184（9 键，全全局键）
    "music": FeatureSpec(
        id="music",
        name="音乐",
        desc="跟着音乐唱歌、显示同步歌词，并能联动常见播放器。",
        category=CATEGORY_VOICE,
        search_aliases=("音乐", "唱歌", "歌词", "播放器", "foobar", "music", "lyrics"),
        requires=(),
        optional_deps=(),
        menu_action_ids=(
            "music_pause",
            "music_next",
            "music_prev",
            "music_quit",
            "music_lyric_align",
            "music_open_netease",
            "music_open_qqmusic",
            "music_open_kugou",
            "music_open_soda",
        ),
        config_keys=(
            "music_sing_enabled",
            "music_sing_grace_seconds",
            "music_lyric_enabled",
            "music_lyric_lead_seconds",
            "music_lyric_cache_limit",
            "music_browser_media_enabled",
            "foobar_lyrics_enabled",
            "foobar_title_format",
            "lxmusic_enabled",
            "music_player_paths",
        ),
        needs_network=True,  # 取词三源并发（pet/music_lyric.py:772-775）
        needs_paid=False,
        needs_external_process=True,  # 拉起外部播放器（shared.py:1237-1242）
        batch=BATCH_SECOND,
        entry_names=("「音乐」子菜单（唱歌/歌词/播放器）",),
    ),
    # ============================================================ 第三批：智能
    # AI 对话 chat —— ENTRY-INVENTORY §AI 对话
    #   菜单：registry.py:303（chat）
    #   配置：config.py:414-445（`chat` 整棵字典：provider/历史预算/联网搜索）
    #         + config.py:1206-1216、1254-1256（聊天窗 UI 键）+ 1327（hotkey_open_chat）
    #   chat 不依赖 voice（SPEC §3：免打扰/音频是被别人消费的通道，不是它的前置）。
    "chat": FeatureSpec(
        id="chat",
        name="AI 对话",
        desc="和桌宠聊天，支持多套模型服务与联网搜索。",
        category=CATEGORY_INTELLIGENCE,
        search_aliases=("对话", "AI", "聊天", "模型", "API", "chat", "llm"),
        requires=(),
        optional_deps=(),
        menu_action_ids=("chat",),
        config_keys=(
            "chat",
            "chat_background",
            "modern_chat_background",
            "chat_background_opacity",
            "chat_background_fill",
            "modern_chat_background_opacity",
            "modern_chat_background_fill",
            "modern_chat_card_opacity",
            "chat_bg_crops",
            "chat_ui_style",
            "chat_follow_pet",
            "chat_always_on_top",
            "system_notifications_enabled",
            "hotkey_open_chat",
        ),
        needs_network=True,
        needs_paid=True,  # 主路径必须自备模型服务凭据
        needs_external_process=False,
        batch=BATCH_THIRD,
        entry_names=("「AI 对话」聊天窗口与快捷聊天",),
    ),
    # 文件解读 file_interpret —— ENTRY-INVENTORY §文件解读
    #   菜单：**无**（唯一入口是拖文件，见 MENU_LESS_FEATURES）
    #   配置：config.py:397-404（`file_interpret` 字典：enabled / progress_interval_seconds）
    #   硬依赖 chat：它自建 ChatService（file_interpret.py:236-296），chat 缺失时
    #   只降级成一句气泡（:236-244）——所以模块层面是硬依赖，不是可选增强。
    "file_interpret": FeatureSpec(
        id="file_interpret",
        name="文件解读",
        desc="把文件拖到桌宠身上，让它读一遍并告诉你里面写了什么。",
        category=CATEGORY_INTELLIGENCE,
        search_aliases=("文件解读", "拖文件", "读文件", "文件识别", "file", "document"),
        requires=("chat",),
        optional_deps=(),
        menu_action_ids=(),
        config_keys=(
            "file_interpret.enabled",
            "file_interpret.progress_interval_seconds",
        ),
        needs_network=True,
        needs_paid=True,  # 复用 chat 的 provider
        needs_external_process=False,
        batch=BATCH_THIRD,
        entry_names=("拖文件到桌宠身上解读",),
    ),
    # 手动视觉 vision（看看屏幕 / 截图提问）—— ENTRY-INVENTORY §看看屏幕
    #   菜单：registry.py:304（look_screen）、307-309（screen_capture）
    #   配置：**没有自己的顶层键**，全部寄生在 chat provider 上
    #         （config.py 不写默认值，只有 chat/models.py:60-62 的 dataclass 默认）
    #         + config.py:1328-1329（hotkey_look_screen / hotkey_screen_capture）
    #   硬依赖 chat：翻译/问答走 chat 文本接口（screen_capture/translate.py:60-80）。
    #   ⚠️ pet/vision.py 同时提供「前台窗口/光标查询」，被全屏自动隐藏与光标穿透
    #   复用（platform_win.py:380、window_screen.py:70），门控只能切「截图→模型」
    #   那一段，不能整模块挡——本清单只声明模块边界，切点留给执行层。
    "vision": FeatureSpec(
        id="vision",
        name="看看屏幕",
        desc="看一眼屏幕或框选一块区域截图，让 AI 描述、翻译或回答问题。",
        category=CATEGORY_INTELLIGENCE,
        search_aliases=("看看屏幕", "截图", "识屏", "视觉", "看图", "vision", "screenshot", "OCR"),
        requires=("chat",),
        optional_deps=(),
        menu_action_ids=("look_screen", "screen_capture"),
        config_keys=(
            "chat.providers.<id>.vision_same_as_chat",
            "chat.providers.<id>.vision_model",
            "chat.providers.<id>.vision_base_url",
            "chat.providers.<id>.vision_api_key_ref",
            "chat.providers.<id>.vision_api_key",
            "hotkey_look_screen",
            "hotkey_screen_capture",
        ),
        needs_network=True,
        needs_paid=True,  # 复用 chat 的 provider（视觉模型按 provider 配置）
        needs_external_process=True,  # OCR 子进程 python -m pet.screen_capture.ocr_worker
        batch=BATCH_THIRD,
        entry_names=("「看看屏幕」「截图提问」及对应全局快捷键",),
    ),
    # 主动识屏 proactive —— ENTRY-INVENTORY §主动识屏
    #   菜单：registry.py:364-367（proactive_screen）
    #   配置：config.py:234-250（`proactive_screen` 字典，14 键）
    "proactive": FeatureSpec(
        id="proactive",
        name="主动识屏",
        desc="你在某个窗口停留久了，它会主动看一眼并给出建议。",
        category=CATEGORY_INTELLIGENCE,
        search_aliases=("主动识屏", "主动", "看一眼", "建议", "proactive"),
        requires=("chat",),
        optional_deps=(),
        menu_action_ids=("proactive_screen",),
        config_keys=(
            "proactive_screen.enabled",
            "proactive_screen.dry_run",
            "proactive_screen.preset",
            "proactive_screen.allow_when_mouse_through",
            "proactive_screen.whitelist",
            "proactive_screen.dwell_seconds",
            "proactive_screen.require_idle",
            "proactive_screen.min_idle_seconds",
            "proactive_screen.cooldown_minutes",
            "proactive_screen.daily_cap",
            "proactive_screen.min_request_interval_seconds",
            "proactive_screen.change_threshold",
            "proactive_screen.prefer_free_provider",
            "proactive_screen.pre_cue",
        ),
        needs_network=True,
        needs_paid=True,  # 视觉请求走 chat provider
        needs_external_process=False,
        batch=BATCH_THIRD,
        entry_names=("「主动识屏」与停留建议气泡",),
    ),
    # ============================================================ 第三批：联动
    # Agent 联动 agent_link —— ENTRY-INVENTORY §agent_link
    #   菜单：registry.py:363（agent_link）；消费统计 agent_cost 按清单归属本模块
    #         （registry.py:326，恒可用），位置在「桌宠控制」子菜单。
    #   配置：config.py:253-272（`agent_link` 字典，14 个子键）
    #         + config.py:1185（agent_cost_enabled）+ config.py:1204（pnpm_bin，与
    #         系统工具的 Harness 共用）
    #   needs_network：监视器本身只读本地，但消费统计线程会直接拉余额
    #   （agent_link.py:5547-5579），所以记 True。
    "agent_link": FeatureSpec(
        id="agent_link",
        name="Agent 联动",
        desc="把 DSH、Claude Code、Cursor、OpenCode 的工作进度接到桌宠身上。",
        category=CATEGORY_LINK,
        search_aliases=("Agent", "联动", "DSH", "Claude", "Cursor", "OpenCode", "harness"),
        requires=(),
        optional_deps=(),
        menu_action_ids=("agent_link", "agent_cost"),
        config_keys=(
            "agent_link.dsh",
            "agent_link.claude",
            "agent_link.cursor",
            "agent_link.opencode",
            "agent_link.custom_agents",
            "agent_link.sound_enabled",
            "agent_link.sound_start_path",
            "agent_link.sound_done_path",
            "agent_link.sound_error_path",
            "agent_link.sound_volume",
            "agent_link.sound_cooldown_seconds",
            "agent_link.sound_start_enabled",
            "agent_link.sound_done_enabled",
            "agent_link.sound_error_enabled",
            "agent_cost_enabled",
            "pnpm_bin",
        ),
        needs_network=True,
        needs_paid=False,
        needs_external_process=True,  # 装桥走 pnpm 子进程（agent_link.py:2622-2711）
        batch=BATCH_THIRD,
        entry_names=("「Agent 联动」与状态气泡、「消费统计」",),
    ),
    # 灵动岛 dynamic_island（含 FPS、余额两个子能力）—— ENTRY-INVENTORY §dynamic_island
    #   菜单：registry.py:357（balance）、406-411（network_info）。岛本身是进程级窗口，
    #         不是菜单动作；这两条是它暴露在菜单上的两个入口。
    #         ⚠️ network_info 在清单里被标为「相邻项，待裁决」（与岛指标同一实现
    #         settings_network，但落在「菜单」域）——暂按岛的入口登记，裁决后再动。
    #   配置：config.py:608-632（`dynamic_island` 整棵字典，20+ 子键，逐键见清单）
    #         + island_metrics / network_probe_interval_seconds /
    #         spawn_inherit_dynamic_island / click_show_balance（唯一 per-slot 键）
    #         + FPS 两键（game_fps_target_mode / game_fps_process）
    #         + 余额五键（balance_refresh_minutes / balance_tier_*）
    #   `island_network_enabled` 已废弃为写入来源（只在归一化时单向迁进 island_metrics），
    #   不作为模块键登记。`dynamic_island.enabled` 两处缺省值不一致（config.py:611=True
    #   vs 设置页 .get(...,False)），门控不得用它判「模块是否激活」。
    #   类别归「联动」：岛的读数是系统指标，但它存在的价值是把 AI/Agent/余额状态
    #   聚合到桌面，与 agent_link 同组；FPS/余额是它的子能力，不单独成模块。
    "dynamic_island": FeatureSpec(
        id="dynamic_island",
        name="灵动岛",
        desc="桌面胶囊，显示时间、网速、CPU/内存/显卡、游戏帧率和服务余额。",
        category=CATEGORY_LINK,
        search_aliases=("灵动岛", "胶囊", "桌面组件", "网速", "帧率", "余额", "island", "FPS", "balance"),
        requires=(),
        optional_deps=("chat", "agent_link"),
        menu_action_ids=("balance", "network_info"),
        config_keys=(
            "dynamic_island",
            "island_metrics",
            "network_probe_interval_seconds",
            "network_info_menu_enabled",
            "spawn_inherit_dynamic_island",
            "click_show_balance",
            "game_fps_target_mode",
            "game_fps_process",
            "balance_refresh_minutes",
            "balance_tier_labels_mode",
            "balance_tier_label_peak",
            "balance_tier_label_idle",
            "balance_tier_color_enabled",
        ),
        needs_network=True,  # UDP 延迟探测 + 余额查询
        needs_paid=False,
        needs_external_process=True,  # PresentMon 子进程 + 帧率授权 PowerShell
        batch=BATCH_THIRD,
        entry_names=("桌面胶囊（灵动岛）、「DeepSeek 余额」「网络信息」",),
    ),
    # ============================================================ 第四批：工具
    # 快捷启动 quick_launch —— ENTRY-INVENTORY §快捷启动
    #   菜单：registry.py:352-356（quick_launch）
    #   配置：config.py:1137（quick_launch_apps，单键，上限 20 条）
    "quick_launch": FeatureSpec(
        id="quick_launch",
        name="快捷启动",
        desc="在右键菜单里一键打开常用程序。",
        category=CATEGORY_SYSTEM,
        search_aliases=("快捷启动", "启动器", "打开程序", "launcher", "quick launch"),
        requires=(),
        optional_deps=(),
        menu_action_ids=("quick_launch",),
        config_keys=("quick_launch_apps",),
        needs_network=False,
        needs_paid=False,
        needs_external_process=True,  # os.startfile / QProcess.startDetached
        batch=BATCH_TOOLS,
        entry_names=("「快捷启动」子菜单",),
    ),
    # 直播捕获 stream_capture —— 重启级渲染拓扑开关（legacy 单窗供 OBS 捕获）。
    #   不是服务，关闭无后台成本；模块化藏的是设置行与切换入口（设置页 clutter 治理）。
    #   现有捕获模式用户经迁移基线继承本模块，行为不变；停用后拓扑判定按未开启处理。
    "stream_capture": FeatureSpec(
        id="stream_capture",
        name="直播捕获",
        desc="让 OBS 等直播软件能单独捕获桌宠窗口（兼容模式，重启后生效）。",
        category=CATEGORY_SYSTEM,
        search_aliases=("直播", "捕获", "OBS", "推流", "capture", "stream"),
        requires=(),
        optional_deps=(),
        menu_action_ids=(),
        config_keys=("stream_capture_mode",),
        needs_network=False,
        needs_paid=False,
        needs_external_process=False,
        batch=BATCH_TOOLS,
        entry_names=("常规页「直播捕获（高级）」与捕获兼容模式",),
    ),
    # 多屏跨屏 cross_screen —— 多显示器活动区开关（默认关、重启级、进程快照）。
    #   同样无后台成本；藏的是设置行。既有跨屏用户经迁移基线继承，行为不变。
    "cross_screen": FeatureSpec(
        id="cross_screen",
        name="多屏跨屏",
        desc="允许桌宠被拖到其他显示器、在多个屏幕之间活动（重启后生效）。",
        category=CATEGORY_SYSTEM,
        search_aliases=("跨屏", "多屏", "副屏", "显示器", "多显示器", "cross screen"),
        requires=(),
        optional_deps=(),
        menu_action_ids=(),
        config_keys=("allow_cross_screen",),
        needs_network=False,
        needs_paid=False,
        needs_external_process=False,
        batch=BATCH_TOOLS,
        entry_names=("常规页「允许桌宠跨屏」",),
    ),
    # 系统工具 system_tools —— ENTRY-INVENTORY §系统工具（D1 回收内存 + D2 Harness）
    #   菜单：registry.py:346（memory_trim，非 Windows 整条不出现）、
    #         358（harness）。⚠️ harness 的可用性今天绑在 on_open_chat 上
    #         （registry.py:358）——chat 一停连带 harness 消失，这是现存耦合，
    #         模块化时要显式解绑（SPEC §6 期 4 的验收项）。
    #   配置：config.py:1192-1193（mem_*，无设置行，只存在于 config.json）
    #         + config.py:1195/1200（harness_autostart / harness_launch_target）
    #         + config.py:1204（pnpm_bin，与 agent_link 共用）
    "system_tools": FeatureSpec(
        id="system_tools",
        name="系统工具",
        desc="回收内存、启动或停止 DSH Harness 服务。",
        category=CATEGORY_SYSTEM,
        search_aliases=("系统工具", "回收内存", "内存", "harness", "dsh 服务", "memory"),
        requires=(),
        optional_deps=(),
        menu_action_ids=("memory_trim", "harness"),
        config_keys=(
            "mem_skip_foreground",
            "mem_min_size_mb",
            "harness_autostart",
            "harness_launch_target",
        ),  # pnpm_bin 唯一归 agent_link（归属守卫：一键一主）
        needs_network=False,
        needs_paid=False,
        needs_external_process=True,  # 拉起 dsh 子进程（harness_launcher.py:503-556）
        batch=BATCH_TOOLS,
        entry_names=("「回收内存」「DeepSeek Harness」",),
    ),
}


#: 迁移基线：老用户升级时继承的模块全集（**写死**，不随 `FEATURES` 增长）。
#:
#: SPEC §4 的产品决策：新装 `enabled=[]`（纯核心），老用户升级「继承基线里的全部
#: 模块」。基线必须是固定历史集合——如果它是从 `FEATURES` 推出来的，以后每加一个
#: 模块，老用户就会在升级时被静默塞一个新功能（本该默认关）；而如果按「非默认值才
#: 保留」去猜，又会误伤天气/抽签/待办这些本来就默认开的模块（没有「肯定没用」的
#: 负证据）。所以这张表只增不改：新模块进来时**不动它**。
MIGRATION_BASELINE: tuple[str, ...] = (
    "weather",
    "divination",
    "todo",
    "festival",
    "voice",
    "music",
    "chat",
    "file_interpret",
    "vision",
    "proactive",
    "agent_link",
    "dynamic_island",
    "quick_launch",
    # 直播捕获/跨屏是**既有**设置项不是新功能：升级用户必须继承（开着捕获
    # 模式/跨屏的老用户被静默关掉就是事故）；「新增模块默认关」只适用于
    # 老版本里不存在的能力。
    "stream_capture",
    "cross_screen",
    "system_tools",
)


# ---------------------------------------------------------------- 查询面
def _sort_key(spec: FeatureSpec) -> tuple[int, int]:
    """`(批次, 分组序)`——`sorted` 是稳定排序，组内保持清单声明顺序。

    组内不按 id 重排：声明顺序就是 SPEC §3 的分批顺序（天气/抽签/待办/节日、
    报时/音乐……），功能页照着它排，用户看到的第一栏与实施顺序一致。
    """
    try:
        group = CATEGORY_ORDER.index(spec.category)
    except ValueError:  # 未登记的新分组排到末尾，不静默归入任一组
        group = len(CATEGORY_ORDER)
    return (spec.batch, group)


def all_features() -> tuple[FeatureSpec, ...]:
    """全部可停用模块，按批次、分组稳定排序（组内保持清单声明顺序）。

    排序只服务呈现（功能页卡片的分栏与实施顺序）。它**不**表达可运行性——
    「这个模块现在能不能跑」是 :func:`resolve_dependencies` 的事。
    """
    return tuple(sorted(FEATURES.values(), key=_sort_key))


_ACTION_TO_FEATURE: dict[str, str] | None = None


def feature_for_action(action_id: str) -> str | None:
    """右键菜单动作 id → 所属模块 id；不属于任何可停用模块返回 None。

    菜单投影（registry.available_ids）按它做激活过滤：返回 None 的动作
    （核心动作、MIGRATION 外的）不受模块门控影响。
    """
    global _ACTION_TO_FEATURE
    if _ACTION_TO_FEATURE is None:
        _ACTION_TO_FEATURE = {
            action_id: spec.id
            for spec in FEATURES.values()
            for action_id in spec.menu_action_ids
        }
    return _ACTION_TO_FEATURE.get(action_id)


def get(feature_id: str) -> FeatureSpec | None:
    """按 id 取模块；未知 id 返回 `None`（未知是常态：降级/再升级会让它回来）。"""
    return FEATURES.get(feature_id)


# ---------------------------------------------------------------- 启用集合
def normalize_enabled(value: object) -> tuple[list[str], list[str]]:
    """把磁盘上的启用集合归一化成 ``(合法启用集, 未知 ID)``。

    输入就是 `config.json` 里 `feature_activation.enabled` 的**原始 JSON 值**
    （任意形状都可能出现：手改、旧版本写入、别人写的工具写坏）。行为：

    - **合法数组**：逐项校验为字符串后去重（保序，第一次出现的位置生效）；
      认识的 id 进启用集，不认识的进未知列表（同样保序去重）。
    - **形状不认识**（dict / str / int / bool / `None` / 含非字符串元素的 list）：
      抛 :class:`ValueError`。调用方应保留原始数据并给可见诊断，**不要**用默认集
      覆盖——SPEC §4 的「损坏保留原数据 + 诊断，不冒充新装」。
    - **空数组**：返回 `([], [])`，即用户明确选择的纯核心。绝不回退成全部启用。
    - **字段缺失**（`feature_activation` 或 `enabled` 不存在）：**不是**本函数的
      输入。缺失要走一次性新装/升级判定（老用户 → `MIGRATION_BASELINE`），
      把 `None` 塞进这里只会得到一条 ValueError 而不是一个默认集——这是刻意的，
      默认集只能由迁移路径产生。

    未知 ID 单独返回而不是直接丢弃：它们「不执行但保留」（降级回旧版本、再升级
    回来时需要原样写回）。
    """
    if not isinstance(value, list):
        raise ValueError(
            "feature_activation.enabled 必须是数组："
            f"实际拿到 {type(value).__name__}，原始数据应原样保留并给诊断"
        )
    enabled: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                "feature_activation.enabled 里有非字符串元素："
                f"{type(item).__name__}，原始数据应原样保留并给诊断"
            )
        if item in seen:
            continue
        seen.add(item)
        (enabled if item in FEATURES else unknown).append(item)
    return enabled, unknown


class ResolvedFeatures(NamedTuple):
    """依赖解算结果。

    - ``active``：本次真正可运行的模块集合（启用集里硬依赖全部满足的那些）。
    - ``blocked``：硬依赖没满足的模块 → 它自己缺的**直接**硬依赖（元组）。
      只列直接原因，不把传递链平铺进来：调用方要提示「开 A 还差什么」时，
      逐层往下走 `blocked` 就是完整解释，平铺会把一句提示变成一串。

    两者都是不可变口径：`active` 是 frozenset（顺序无意义），`blocked` 按解算顺序
    （启用集合顺序，然后是依赖的广度优先序）插入——同一输入恒得同一份，便于诊断
    文案稳定。
    """

    active: frozenset[str]
    blocked: dict[str, tuple[str, ...]]


def resolve_dependencies(enabled: Iterable[str]) -> ResolvedFeatures:
    """按硬依赖解算「哪些模块能跑、哪些卡在谁身上」。

    规则（对齐 SPEC §3 的依赖边与验收矩阵「每条依赖边」）：

    - **硬依赖必须同样被启用**：`requires` 里的模块没在启用集里，本模块进
      `blocked`。启用 A（依赖 B）不等于替用户启用 B——激活集合是用户的选择。
    - **闭包传递**：A 硬依赖 B、B 硬依赖 C，只开 A 时 A 缺 B、B 缺 C，两条都进
      `blocked`（B 是 A 的依赖，被一起拉进解算范围；C 的依赖全满足，所以它既不在
      `active`（没启用）也不在 `blocked`（没缺东西））。
    - **可选增强不参与**：`optional_deps` 既不会被拉进解算，也不会让 active 变少。
      少了增强只是少一块能力（例如没有 voice，抽签只是不出声）。
    - **未知 id 忽略**：这里只解算清单内的模块；未知 id 的保留与诊断归
      :func:`normalize_enabled`（它会把未知 id 单独返回）。

    纯函数，无副作用，不读配置。
    """
    requested: list[str] = []
    seen_requested: set[str] = set()
    for feature_id in enabled:
        if not isinstance(feature_id, str) or feature_id not in FEATURES:
            continue
        if feature_id in seen_requested:
            continue
        seen_requested.add(feature_id)
        requested.append(feature_id)

    # 广度优先把硬依赖闭包拉进来（可选增强不进队列）。
    pulled: list[str] = []
    visited: set[str] = set()
    queue = list(requested)
    while queue:
        feature_id = queue.pop(0)
        if feature_id in visited:
            continue
        visited.add(feature_id)
        pulled.append(feature_id)
        for dep in FEATURES[feature_id].requires:
            if dep not in visited:
                queue.append(dep)

    blocked: dict[str, tuple[str, ...]] = {}
    for feature_id in pulled:
        missing = tuple(dep for dep in FEATURES[feature_id].requires if dep not in seen_requested)
        if missing:
            blocked[feature_id] = missing

    active = frozenset(feature_id for feature_id in requested if feature_id not in blocked)
    return ResolvedFeatures(active=active, blocked=blocked)
