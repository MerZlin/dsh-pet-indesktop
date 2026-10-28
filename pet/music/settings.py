# -*- coding: utf-8 -*-
"""设置页「音乐播放器路径」：控件创建 / 行装配 / 保存 / 后台自动检测。

**为什么单独一个模块**：``modern_settings_dialog.py`` 的行数预算已无余量，
本组控件与行全部在此构建（与 ``settings_file_interpret.py`` 同口径）。

**为什么需要这个设置**：右键菜单「音乐 → 打开网易云/QQ音乐/酷狗/汽水给主人放歌」需要
知道播放器装在哪。``pet/music_players.py`` 会先自动搜常见目录，但此前实测本机网易云在
``D:\\CloudMusic``、QQ音乐在 ``D:\\QQ音乐\\QQMusic``，都不在默认位置；搜不到时菜单项
置灰，提示是「可在配置文件中手动指定路径」——在本次之前**没有任何 UI 能写这个键**
（``music_players.clear_cache`` 的 docstring 一直登记着这个缺口）。

**四个播放器一张表**：``PLAYER_KEYS`` 取注册表 ``music_players.player_keys()``——那边是
"有哪些播放器"的真源（显示名、可执行文件名、搜索目录、config 的路径清洗都在那边），
设置页**不另抄键表**。行键与控件属性名都从键派生（``music_player_<key>`` /
``music_player_<key>_picker``），注册表里每个播放器都会拿到自己的行：此前这里写死两个
键，于是注册表加了酷狗 / 汽水也没人给它建行（用户报的正是这个）。漂移由
``tests/test_music_player_settings.py::test_key_set_matches_the_backend_player_registry``
盯着。

**这些路径只管启动，不管歌词**：它们只被右键菜单的「打开…给主人放歌」用来启动程序；
歌词取自系统媒体会话（现在正在播的那首歌），**不读这里的路径**。行文案里必须写明这一条
——否则歌词取不到时用户会去改路径，改完还是没歌词。

**自动检测为什么这么写**：目录浅扫实测 6s+，绝不能放 GUI 线程（曾把菜单冻住）。
这里复用 ``music_players`` 自己的后台预热与只读缓存：点按钮 → **先作废旧缓存再**
触发预热 → 定时轮询只读缓存 → 出结果就回填并弹一次说明。轮询超时也会收尾（每个
播放器一份预算，见 :data:`DETECT_TIMEOUT_PER_PLAYER_SECONDS`），不会留一个永远转的
定时器。

**"再点一次就好"必须当真**：预热是幂等的——缓存里已有值（含 ``None`` 负结果）时它
根本不扫盘，所以显式点检测必须先清缓存（否则用户"装好播放器再点检测"永远看到上一次
的「未找到」，唯一出路是重启）；清缓存不碰在飞扫描，那一格交给
:func:`_rescan_cold_player` 补扫。检测期间用户手填的行不回填
（:func:`_apply_detected_paths`），超时仍没扫完的格子报「未完成」而非「未找到」
（:func:`poll_detect`）——still cold 不是 missing。
"""

from __future__ import annotations

import logging
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox, QPushButton

from . import players as music_players
from ..settings_widgets import ResourcePathPicker, SettingRow

log = logging.getLogger("dsh-pet-standalone")

#: 设置页展示的播放器（次序 = 行序）。**取注册表，不另抄键表**：``music_players``
#: 是"有哪些播放器"的唯一真源（右键菜单与 config 的路径清洗都从这里取键）。此前这里
#: 写死 ``("netease", "qqmusic")``，于是注册表里加了播放器也没人给它建行——用户报的
#: 「只有网易云 / QQ音乐，没有酷狗 / 汽水」就是这个漂移。
PLAYER_KEYS = music_players.player_keys()
EXE_NAME_FILTER = "可执行文件 (*.exe);;所有文件 (*)"
DETECT_POLL_MS = 300
#: 单个播放器一次目录浅扫的预算（秒）。**总预算要按播放器数放大**：四个 key 的预热扫描
#: 被 ``music_players._search_lock`` 串行化，多播放器串行可能超过单项预算——原先写死的
#: 单份总预算在四播放器下会先超时，把找得到的播放器报成「未找到」（用户再点一次才会
#: 看到，因为那时缓存已经暖了）。超时只是兜底：出齐结果就立刻收尾，不等满预算。
DETECT_TIMEOUT_PER_PLAYER_SECONDS = 20.0
DETECT_TIMEOUT_SECONDS = DETECT_TIMEOUT_PER_PLAYER_SECONDS * len(PLAYER_KEYS)
#: 单个播放器在一轮检测里**额外补起**几次扫描的上界（见 :func:`_rescan_cold_player`）：
#: 扫描线程异常退出（缓存仍冷、也不在飞）时补扫是有意义的，但"每次都补"会退化成
#: 每 300ms 起一条线程；封顶之后剩下的时间如实报「未完成」，不在 GUI 线程扫盘。
DETECT_MAX_RESCANS_PER_PLAYER = 2
#: 键 → 对话框属性名 / 设置行键（从 PLAYER_KEYS 派生：注册表里每个播放器都不会漏建）。
_PICKER_ATTR = {key: f"music_player_{key}_picker" for key in PLAYER_KEYS}
_ROW_KEY = {key: f"music_player_{key}" for key in PLAYER_KEYS}

_DETECT_BUTTON_LABEL = "自动检测"


def _player_row_hint(label: str) -> str:
    """单个播放器路径行的说明（每个播放器同口径，只有名字不同）。"""
    return (
        f"{label}的安装位置（指向可执行文件）。留空 = 自动搜索常见安装目录；"
        f"填了就以它为准（文件不存在时右键菜单会提示找不到，而不是偷偷回退）。"
        f"只影响右键菜单里的「打开{label}给主人放歌」；歌词显示走系统媒体会话，"
        f"与本路径无关。"
    )


_DETECT_ROW_HINT = (
    "在后台按常见目录名搜一遍（几个盘符 + 用户目录，最多三层），每次点击都会重新搜"
    "一遍（上次没找到的也会再找），找到就填进上面四行、覆盖那里原有的内容；检测期间"
    "你自己改过的行会保留、不会被覆盖（结果里会说明）。搜索在后台做，不会卡住设置页。"
    "这些路径只影响右键菜单的启动入口，歌词显示走系统媒体会话，与本路径无关。"
)


def create_music_player_controls(dialog) -> None:
    """创建四个路径选择器与「自动检测」按钮（幂等）。"""
    if getattr(dialog, "music_player_detect_btn", None) is not None:
        return
    configured = dialog.config.get("music_player_paths", {})
    configured = configured if isinstance(configured, dict) else {}
    for key in PLAYER_KEYS:
        label = music_players.player_label(key)
        picker = ResourcePathPicker(
            str(configured.get(key, "") or ""),
            name_filter=EXE_NAME_FILTER,
            dialog_title=f"选择{label}的程序",
            parent=dialog,
        )
        setattr(dialog, _PICKER_ATTR[key], picker)
    button = QPushButton(_DETECT_BUTTON_LABEL, dialog)
    button.setObjectName("musicPlayerDetectButton")
    button.clicked.connect(lambda: start_detect(dialog))
    dialog.music_player_detect_btn = button
    # 四个路径键都是**进程级**（不在 PER_SLOT_SETTING_KEYS，右键菜单读 host 配置面、
    # 由 _SpriteConfig 转发到主配置）：子宠页保存由设置进程的配置路由直写主配置
    # （pet/settings_slot_config.py），选了就生效——此前按死开关口径把选择器与
    # 「自动检测」按钮一并置灰（搜到的路径填进没人读的控件），现摘除。


def build_music_player_rows(dialog) -> list[SettingRow]:
    """本组的设置行（由「桌宠 · 音乐关联」组直接取用，不走 claim）。"""
    rows: list[SettingRow] = []
    for key in PLAYER_KEYS:
        label = music_players.player_label(key)
        rows.append(
            SettingRow(
                _ROW_KEY[key],
                f"{label}程序",
                _player_row_hint(label),
                getattr(dialog, _PICKER_ATTR[key]),
                stacked=True,
            )
        )
    rows.append(
        SettingRow(
            "music_player_detect",
            "自动检测播放器",
            _DETECT_ROW_HINT,
            dialog.music_player_detect_btn,
        )
    )
    return rows


def browser_media_supported() -> bool:
    """「浏览器媒体会话参与歌词」是否在本平台可用——系统媒体控制是 Windows 专属。"""
    return sys.platform == "win32"


def browser_media_rows(dialog) -> list[SettingRow]:
    """「浏览器媒体会话参与歌词」设置行（**仅 Windows**）。

    系统媒体控制是 Windows 专属能力：非 Windows 上这个开关既无效也不该出现
    （SETTINGS-CHANGE-GATES §4「永久不支持的能力不创建控件」）。控件本体也只在
    Windows 创建（``settings_pet_controls``），所以这里以「控件在不在」为准——
    与 ``cursor_hidden_passthrough`` 的处置一致，不会出现"控件在、行不在"的
    游离开关（那会被孤儿开关测试拦下）。

    hint 里如实写清代价：浏览器把网页视频也上报成音乐类型，开着就会对着视频
    显示「我在唱」并取词；网页版音乐平台需要它才有歌词。
    """
    check = getattr(dialog, "music_browser_media_check", None)
    if check is None or not browser_media_supported():
        return []
    # 与 foobar 两键同规（同一 _SOURCE_SWITCHES 组、同一读取形态）：进程级歌词源
    # 开关，子宠页保存由设置进程的配置路由直写主配置（pet/settings_slot_config.py），
    # 改了真生效——此前按死开关口径置灰，现摘除。控件本体建在 settings_pet_controls。
    return [
        SettingRow(
            "music_browser_media",
            "浏览器媒体会话参与歌词",
            "关闭时，浏览器（Edge、Chrome、Firefox 等）里播放的内容不会被当作正在"
            "播放的歌曲——浏览器把网页视频也上报成音乐类型，开着会对着视频显示"
            "「我在唱」并取词。网页版音乐平台（如网页版网易云）需要打开它才能显示歌词。",
            check,
        )
    ]


def foobar_lyrics_supported() -> bool:
    """「foobar2000 歌词」是否在本平台可用——窗口标题源走 Win32 窗口枚举。"""
    return sys.platform == "win32"


def foobar_lyrics_rows(dialog) -> list[SettingRow]:
    """「foobar2000 歌词」两行（**仅 Windows**，与 browser_media_rows 同规）。

    行文案里必须如实写清三条，否则用户会以为是"功能坏了"：

    1. **推荐路线**是给 foobar 装媒体控制组件（如 foo_mediacontrol）——那条路走现有
       的系统媒体控制采样（机制见 ``pet/now_playing.py``），比窗口标题可靠得多；
       本开关是给不想装组件的人用的替代路线。
    2. 窗口标题分辨不出播放状态：foobar 开着但没在播时，可能仍显示上一首。
    3. 只在没有其它播放器（系统媒体控制在播会话）时才生效，不会抢走正在听的那首歌。
    """
    check = getattr(dialog, "foobar_lyrics_check", None)
    edit = getattr(dialog, "foobar_title_format_edit", None)
    if check is None or edit is None or not foobar_lyrics_supported():
        return []
    # 两个键都是**进程级**（不在 PER_SLOT_SETTING_KEYS，歌词源解析读主配置）：子宠页
    # 保存由设置进程的配置路由直写主配置（pet/settings_slot_config.py），改了真生效
    # ——此前按死开关口径置灰，现摘除（控件本体建在 settings_pet_controls）。
    return [
        SettingRow(
            "foobar_lyrics",
            "foobar2000 歌词",
            "foobar2000 默认不会告诉系统正在播什么，所以读不到歌词。本开关通过读取"
            "播放器窗口标题来获取歌曲信息，只在没有其它播放器在播时生效。更推荐的"
            "路线：给 foobar 安装「媒体控制插件」（让系统能识别播放信息，可搜索 "
            "foo_mediacontrol 插件）——装好后不需要打开本开关。局限：窗口标题看不出"
            "是否正在播放，停止播放后可能仍显示上一首。",
            check,
        ),
        SettingRow(
            "foobar_title_format",
            "播放器标题格式（高级）",
            "通常留空即可。如果歌词经常对不上，把 foobar「显示 → 默认用户界面 → "
            "窗口标题」里配置的那串格式表达式原样复制到这里（类似 %artist% - %title% "
            "这样的格式），帮助识别歌名与歌手；匹配不上时仍会自动按原样尝试。",
            edit,
            stacked=True,
        ),
    ]


def lxmusic_supported() -> bool:
    """「洛雪音乐歌词」是否在本平台可用——读它桌面版自带的本地服务（HTTP 回环）。

    接口本身跨平台，但洛雪桌面版的分发以 Windows 为主；与相邻两个来源开关同规，
    只在 Windows 建控件与设置行，避免出现"控件/行只在部分平台"的不对称。
    """
    return sys.platform == "win32"


def lxmusic_rows(dialog) -> list[SettingRow]:
    """「洛雪音乐歌词」设置行（**仅 Windows**，与 browser_media_rows 同规）。

    文案里要如实写清三件事，否则用户会以为是"功能坏了"：

    1. 需要先在洛雪里打开「开放服务」开关（设置 → 其他），默认端口 23330；
    2. 它比系统媒体控制**更准**（洛雪在系统媒体接口里常不报时间轴，歌词只能按
       检测到切歌的时刻估算，快进/中途起播会错位）；
    3. 关掉或接口没开时**自动回落**到原来的系统媒体控制链路，不报错。

    文案纪律（settings 文案规范）：主视线里不出现缩写黑话，所以这里不写产品
    英文名、也不写接口缩写，只写用户看得懂的说法。
    """
    check = getattr(dialog, "lxmusic_check", None)
    if check is None or not lxmusic_supported():
        return []
    # 进程级歌词源开关（不在 PER_SLOT_SETTING_KEYS，歌词源解析读主配置）：子宠页
    # 保存由设置进程的配置路由直写主配置（pet/settings_slot_config.py）。
    return [
        SettingRow(
            "lxmusic",
            "洛雪音乐歌词",
            "让桌宠直接读洛雪音乐自己上报的播放进度，歌词逐句对齐，快进或中途开始"
            "播放也不会错位。使用前请在洛雪里打开「设置 → 其他 → 开放服务」"
            "（默认端口 23330）。关闭本开关、洛雪没在播或接口没开时，会自动回落到"
            "系统媒体控制那条路，不影响其它播放器。",
            check,
        )
    ]


#: 「音乐关联」里的高级折叠组名——播放器路径与标题格式是**故障恢复入口**（歌词对不上
#: /换了安装位置时来改），组名必须直说，不藏。
PLAYER_GROUP_TITLE = "播放器路径与标题格式（高级）"
#: 非 Windows 上不存在「标题格式」行：组名跟着退化，不留一个提不到的行名。
PLAYER_GROUP_TITLE_NO_FOOBAR = "播放器路径（高级）"


def advanced_group_title(dialog) -> str:
    """高级折叠组标题：按平台上有没有「标题格式」这一行决定（无该行就不提它）。"""
    if not foobar_lyrics_supported() or getattr(dialog, "foobar_title_format_edit", None) is None:
        return PLAYER_GROUP_TITLE_NO_FOOBAR
    return PLAYER_GROUP_TITLE


def save_music_player_settings(dialog) -> None:
    """写回 ``music_player_paths``；四个播放器都留空 = 回到自动搜索，并清掉路径缓存。"""
    if getattr(dialog, "music_player_detect_btn", None) is None:
        return
    paths = {}
    for key in PLAYER_KEYS:
        value = getattr(dialog, _PICKER_ATTR[key]).text().strip()
        if value:
            paths[key] = value
    previous = dialog.config.get("music_player_paths", {})
    if not isinstance(previous, dict):
        previous = {}
    dialog.config.set("music_player_paths", paths)
    if paths != previous:
        # 路径变了：让右键菜单下次拿到确定态（自动搜索的负缓存也要作废）。
        music_players.clear_cache()


# 「音乐关联」域里两个**来源开关**的控件名 → 配置键。控件在
# pet/settings_pet_controls 里按平台条件创建（非 Windows 为 None）。
_SOURCE_SWITCHES = (
    ("music_browser_media_check", "music_browser_media_enabled"),
    ("foobar_lyrics_check", "foobar_lyrics_enabled"),
    ("lxmusic_check", "lxmusic_enabled"),
)


def save_source_settings(dialog) -> None:
    """写回歌词来源开关与 foobar 窗口标题格式串。

    集中在这里的原因与控件/行同源：``modern_settings_dialog._save`` 的行数预算已无
    余量，这一组键的写回语义（"控件不存在就整键跳过"，非 Windows 上没有该控件）
    放进对话框本体只会多出几行重复的 getattr 样板。
    """
    for attr, key in _SOURCE_SWITCHES:
        widget = getattr(dialog, attr, None)
        if widget is not None:
            dialog.config.set(key, widget.isChecked())
    edit = getattr(dialog, "foobar_title_format_edit", None)
    if edit is not None:
        dialog.config.set("foobar_title_format", edit.text().strip())


def start_detect(dialog, *, warm=None, lookup=None, clear=None) -> None:
    """「自动检测」入口：作废旧缓存 → 后台重扫 → 定时轮询回填（GUI 线程只读缓存）。

    **为什么显式检测要先清缓存**：``warm_cache_async`` 是幂等的——缓存里已有值
    （哪怕是上一次扫描留下的 ``None`` 负结果）时它直接返回 False、根本不扫盘。
    "用户装好播放器 → 点自动检测 → 报未找到"这个缺陷就出在这里：用户点的是
    "现在再找一遍"，吃到的却是旧结论，唯一的出路是重启。所以这里先用后端既有的
    :func:`music_players.clear_cache` 作废旧结果再预热，不在本模块自造"刷新"。
    清缓存不碰 ``_warm_inflight``，在飞扫描写回的是**真扫描**的结果而非旧缓存值；
    竞态窗口漏写的格子由 :func:`_rescan_cold_player` 补扫。

    **为什么记一份原文本**：检测是异步的（四个 key 串行扫描，耗时随机器而异），用户
    很可能一边等一边手填。回填只写那些从检测开始到现在**文本没变过**的行——否则会
    把用户刚填的路径盖掉，而用户看不出是谁改的。检测开始前就有的旧路径**允许**被
    结果替换（那正是点检测的目的），替换与否由结果文案说清，不加确认弹窗。

    ``warm`` / ``lookup`` / ``clear`` 是测试注入缝；默认都用后端真身。
    """
    from .. import feature_activation

    if not feature_activation.is_active("music"):
        # 音乐模块未激活：路径探测链整条不做（不清缓存、不起扫描线程、不起轮询表）。
        # 行本身也没挂出（设置层），这里是绕过 UI 直接调用的兜底。
        log.info("音乐模块未激活，忽略播放器路径自动检测")
        return
    warm = warm or music_players.warm_cache_async
    clear = clear or music_players.clear_cache
    dialog._music_detect_lookup = lookup
    dialog._music_detect_warm = warm
    dialog._music_detect_deadline = time.monotonic() + DETECT_TIMEOUT_SECONDS
    dialog._music_detect_results = {}
    dialog._music_detect_states = {}
    dialog._music_detect_rescans = {}
    dialog._music_detect_start_text = {
        key: getattr(dialog, _PICKER_ATTR[key]).text() for key in PLAYER_KEYS
    }

    clear()
    for key in PLAYER_KEYS:
        warm(key, "")

    button = dialog.music_player_detect_btn
    button.setEnabled(False)
    button.setText("检测中…")

    timer = getattr(dialog, "_music_detect_timer", None)
    if timer is None:
        timer = QTimer(dialog)
        timer.setInterval(DETECT_POLL_MS)
        timer.timeout.connect(lambda: poll_detect(dialog))
        dialog._music_detect_timer = timer
    timer.start()
    poll_detect(dialog)


def poll_detect(dialog) -> None:
    """轮询一次：有冷缓存就继续等，出结果（或超时）就回填并收尾。

    冷 ≠ 没找到：扫描还在飞（或超时仍没扫完）时这一格是**未完成**，不能当
    :data:`music_players.CACHED_MISSING` 报「未找到」——那会把"这次只是没扫完"
    说成"你这台机器没有"。
    """
    lookup = getattr(dialog, "_music_detect_lookup", None) or music_players.cached_player
    results: dict[str, str] = getattr(dialog, "_music_detect_results", {})
    states: dict[str, str] = getattr(dialog, "_music_detect_states", {})
    pending = False
    for key in PLAYER_KEYS:
        state, path = lookup(key, "")
        if path:
            results[key] = path
            states[key] = music_players.CACHED_FOUND
        elif state == music_players.CACHED_COLD:
            states[key] = music_players.CACHED_COLD
            pending = True
            _rescan_cold_player(dialog, key)
        else:
            states[key] = music_players.CACHED_MISSING
    if pending and time.monotonic() < getattr(dialog, "_music_detect_deadline", 0.0):
        return

    timer = getattr(dialog, "_music_detect_timer", None)
    if timer is not None:
        timer.stop()
    button = dialog.music_player_detect_btn
    button.setEnabled(True)
    button.setText(_DETECT_BUTTON_LABEL)

    start_text = getattr(dialog, "_music_detect_start_text", None) or {}
    filled = _apply_detected_paths(dialog, results, start_text)
    unfinished = [
        key for key in PLAYER_KEYS if states.get(key) == music_players.CACHED_COLD
    ]
    if unfinished:
        log.info("音乐播放器路径自动检测：找到 %s；仍在扫（未完成）%s", results, unfinished)
    elif results:
        log.info("音乐播放器路径自动检测：找到 %s", results)
    else:
        log.info("音乐播放器路径自动检测：四个播放器都没找到")
    _report_results(dialog, results, states, filled, start_text)


def _rescan_cold_player(dialog, key: str) -> None:
    """对"仍冷"的格子补一次预热——覆盖"清缓存"与"在飞扫描"的竞态窗口。

    竞态具体是什么：点检测前可能已经有一条扫描在飞（例如右键菜单刚触发过预热）。
    ``clear_cache`` 清掉缓存，但 ``warm_cache_async`` 因为在飞会幂等返回 False、
    补不上新线程；若那条在飞扫描**在清缓存之前**就已经把结果写下（写缓存与移出
    ``_warm_inflight`` 之间有一小段窗口），它写下的值被清掉、又没有扫描接手，这一格
    会一直冷到超时。所以每轮轮询对"仍冷"的格子补一次预热：

    - 在飞 → ``warm_cache_async`` 返回 False，不起线程（既不叠线程，也不把"还在
      扫"当成"没找到"）；
    - 扫描线程已异常退出（缓存仍冷、也不在飞）→ 真的补起一条线程。

    每 key 每轮**获准的补扫次数**有上界（:data:`DETECT_MAX_RESCANS_PER_PLAYER`）：
    扫描持续失败时最多多起两条线程，剩下的时间如实报「未完成」——既不在 GUI 线程
    扫盘，也不无限起线程。没有轮次状态（直接调 :func:`poll_detect` 的调试/测试路径）
    时按旧语义只等不补。
    """
    rescans = getattr(dialog, "_music_detect_rescans", None)
    if rescans is None:
        return
    if rescans.get(key, 0) >= DETECT_MAX_RESCANS_PER_PLAYER:
        return
    warm = getattr(dialog, "_music_detect_warm", None) or music_players.warm_cache_async
    if warm(key, ""):
        rescans[key] = rescans.get(key, 0) + 1


def _apply_detected_paths(dialog, results: dict[str, str], start_text: dict[str, str]) -> dict[str, bool]:
    """把检测到的路径填回各行；返回 key → 是否真的填了。

    只有"从检测开始到现在文本没变过"的行才允许回填：检测是异步的，用户在等待期间
    手填 / 改过的路径不能被结果盖掉（没填进去还能在结果里说明原因，静默覆盖不行）。
    快照缺失（只可能来自直接调 :func:`poll_detect` 的调试 / 测试路径）按"没被改过"
    处理，保持旧语义。
    """
    filled: dict[str, bool] = {}
    for key, path in results.items():
        picker = getattr(dialog, _PICKER_ATTR[key])
        baseline = start_text.get(key)
        if baseline is not None and picker.text() != baseline:
            filled[key] = False
            continue
        picker.setText(path)
        filled[key] = True
    return filled


def _report_results(
    dialog,
    results: dict[str, str],
    states: dict[str, str],
    filled: dict[str, bool],
    start_text: dict[str, str],
) -> None:
    """一次说明：逐项交代结果，并写清"替换了原来的什么 / 哪些没动"。

    三种结局用词不同，用户据此能判断下一步：**未完成**（本轮没扫完，可重试一次）、
    **未找到**（确定没有，去手动指定）、**找到**（填了；若替换了原来填的就说清）。

    冷格只报「未完成」，**不承诺后台还在扫**：补扫有上界
    （:data:`DETECT_MAX_RESCANS_PER_PLAYER`），耗尽后一条线程都没有，那时说"后台
    还在扫描 / 再点一次就能看到"是假承诺。
    """
    lines = []
    replaced = False
    kept = False
    unfinished = False
    missing = False
    for key in PLAYER_KEYS:
        label = music_players.player_label(key)
        path = results.get(key)
        if states.get(key) == music_players.CACHED_COLD:
            lines.append(f"{label}：未完成（本次检测未完成，可稍后重试）")
            unfinished = True
            continue
        if not path:
            lines.append(f"{label}：未找到")
            missing = True
            continue
        if not filled.get(key, True):
            # 检测期间用户改过这一行：绝不覆盖，但要让他知道检测到了什么。
            lines.append(f"{label}：检测到 {path}，但你检测期间改过这一行，已保留你填的内容")
            kept = True
            continue
        original = str(start_text.get(key) or "")
        if original and original != path:
            lines.append(f"{label}：{path}（替换了检测前填的 {original}）")
            replaced = True
        else:
            lines.append(f"{label}：{path}")
    notes = []
    if unfinished:
        notes.append(
            "「未完成」的行本次没得出结果，不保证后台还有扫描在跑；"
            "可以稍后再点一次「自动检测」重试。"
        )
    if replaced and missing:
        notes.append("被替换的行可以点右侧「选择…」改回你原来的路径；没找到的同样可以手动指定程序位置。")
    elif replaced:
        notes.append("被替换的行可以点右侧「选择…」改回你原来的路径。")
    elif missing:
        notes.append("没找到的可以点右侧「选择…」手动指定程序位置。")
    if kept:
        notes.append("你自己填的行已保留，检测结果没有写进去。")
    QMessageBox.information(
        dialog,
        "自动检测播放器",
        "\n".join(lines) + "\n\n" + "\n".join(notes),
    )
