# -*- coding: utf-8 -*-
"""设置搜索的候选集扩展（设置页第四刀，2026-10-08）：折叠组标题 + 用户口语别名。

**为什么单独一个模块**：``modern_settings_dialog.py`` 的行数预算（``tests/
test_architecture.py``）只剩 3 行余量，而本功能的主体是一张「口语词 → 行键」别名表
加一段组标题归纳逻辑，与对话框框体无关——按本仓库既有口径（``settings_default_marks``
``settings_network`` / ``settings_quiet`` / ``settings_music`` 都是同一个原因拆出来的）
落在这里，对话框只留一行接线。

第一刀遗留的两个 0 命中问题，本模块一次解决：

1. **折叠组标题不在索引里**：搜索索引原本只有「行 label + hint + objectName」，
   于是搜「启动项」/「碰撞参数」/「网络延迟检测」这类**组名** 0 命中——组名存在，
   但从来没进过索引。
2. **用户口语与行文案不一致**：行标题写「灵动岛指标 · 网络」「开机自启」「API Key」，
   用户说「网速」「开机启动」「密钥」，两边对不上就 0 命中。

口径（主代理裁决）：

- **候选集三源**：① 行自身 label / hint / objectName 的关键词命中（既有行为，一字
  未改）；② **可折叠组标题**命中（``SettingsSection(advanced=True)`` 与
  ``CollapsibleGroup``）→ 该组**全部行**入候选，回车逐行可达（展开折叠、切页内标签
  仍走 ``_search_settings`` 里既有的祖先链机制，本模块只决定「哪些行进候选」）；
  ③ 口语别名命中 → 别名指向的行。
- **次序**：关键词 → 组标题 → 别名，三源各自保持 ``rows`` 的原次序，跨源**去重保序**
  （组标题命中的行常常同时是关键词命中，同一行只算一次）。关键词永远排最前，所以
  「首次命中」以及既有的域跳转 / 页内标签切换与改动前逐位一致——新增两源只会**追加**
  候选，不会顶掉已有首次命中。
- **只索引可折叠组**：普通小标题（未 ``advanced`` 的 ``SettingsSection``）不入索引。
  它们本来就在首屏可见，不存在「搜到了但看不见」的问题，纳进来只会让「显示」「背景」
  这类半截词把整组行灌进候选。
- **别名表人工策展**：不做拼音 / 分词引擎、不引入新依赖。命中方向是**别名 ⊂ 查询**
  （「怎么看网速」也能命中），反向不算——否则「网」这种单字会把命中放大到失控。
- 行键取 ``SettingRow.objectName()`` 去掉 ``settingRow_`` 前缀；别名目标行在本次
  ``rows`` 里不存在时静默跳过（no-chat 打包变体的设置窗没有 AI 页，密钥行就不在）。
- 本模块**不碰任何配置**：只读行的文案与控件归属，返回候选行列表，与配置键 / 默认值 /
  依赖语义无关。
"""

from __future__ import annotations

from .settings_widgets import CollapsibleGroup, SettingRow, SettingsSection

#: 别名 → 目标行键（``settingRow_`` 之后的部分）。一个口语词可以指向多行。
#:
#: 每条都要有「真实用户会这么说」的理由，且**只收关键词命中覆盖不到的词**——
#: 行文案里已经写着的说法（「穿透」「唱歌」「歌词」「背景」「提示音」）加了也只是
#: 空转，留着反而让这张表看起来比实际覆盖更广。已考察并**有意不收**的词与理由：
#:
#: - 穿透：「鼠标穿透」「光标隐藏时自动穿透」「鼠标穿透时仍识屏」三行文案里都有
#:   「穿透」，关键词已命中；别名不能替用户挑其中一行，加了等于没加。
#: - 唱歌 / 歌词：行标题就是「音乐自动唱歌」「显示歌词」，关键词已命中。
#: - 背景：已有「自定义背景图片」「背景色」「填充方式」等行文案含「背景」；真正
#:   关键词覆盖不到的是「壁纸」（见下表）。
#: - 重置 / 恢复默认：「恢复默认设置…」是侧栏按钮而不是设置行，搜索候选集只装
#:   ``SettingRow``，给不出行的落点——本刀不做（要做得先把按钮纳入候选模型，
#:   那是另一件事，不该顺手塞进搜索）。
ROW_ALIASES: dict[str, tuple[str, ...]] = {
    # 岛上那一行的 label 是「灵动岛指标 · 网络」，文案写「↓下行 / ↑上行速率」，
    # 用户张口就是「网速」——关键词 0 命中。
    "网速": ("island_network",),
    # 同一行的半截说法：用户只想看一个方向。文案里成对写成「↓下行 / ↑上行速率」，
    # 单搜「上行」本来只能靠 hint 侥幸命中，语义上要的就是这一行。
    "上行": ("island_network",),
    "下行": ("island_network",),
    # 行 label 是「开机自启」，用户写「开机启动」「自启动」都搜不到——「自启」与
    # 「启动」同义不同序，子串匹配认不出。
    "开机启动": ("autostart",),
    "自启动": ("autostart",),
    # 对话窗背景主题在代码里、在用户嘴里都叫「壁纸」，设置页行 label 写的是
    # 「自定义背景图片」——「壁纸」二字整份设置页文本里没有。
    "壁纸": ("chat_background_file",),
    # 行 label 是「气泡鼠标穿透」（issue #367），用户张口就是「气泡穿透」——
    # 子串匹配认不出省掉「鼠标」的半截说法。
    "气泡穿透": ("bubble_click_through",),
    # 设置页统一写「API Key」（行 label 就是「API Key」），用户说「密钥」；
    # 「密钥」只出现在「恢复默认」确认框的说明文字里，不在任何设置的文案里。
    "密钥": ("api_key",),
    # 2026-10-08 行标题重写（14 行）后，老用户记忆里的旧说法在新文案里已经一个字都
    # 不剩，关键词命中覆盖不到——逐条补回来，免得改完标题老用户搜不着。
    # 「dry-run 验证模式」→「试运行（只记日志、不花钱）」
    "dry-run": ("proactive_dry_run",),
    "验证模式": ("proactive_dry_run",),
    # 「窗口停留门限（秒）」→「在同一窗口停多久才看（秒）」（「停留」还在 hint 里）
    "门限": ("proactive_dwell",),
    # 「关怀冷却间隔」→「两次搭话至少间隔」（「关怀」还在 hint 里）
    "冷却": ("proactive_cooldown",),
    # 「最小请求间隔（秒）」→「两次识屏最短间隔（秒）」
    "最小请求": ("proactive_min_interval",),
    # 「识屏优先用独立视觉配置」→「识屏用单独的看图模型」（「独立视觉」还在 hint 里）
    "视觉配置": ("proactive_free",),
    # 「菜单模式」别名随「菜单版本」行一起退役（2026-10-08 旧版菜单删除）：
    # 设置页不再有菜单模式这一项，别名留着只会给一个不存在的行空转。
    # 「质量倍率」→「体重倍率」（hint 写的是「质量加权倍率」，连不成词）
    "质量倍率": ("collision_mass_scale",),
    # 「弹性系数」→「撞后弹力」
    "弹性": ("collision_restitution",),
    # 「显示信息槽」→「显示中间信息栏」、「信息槽内容」→「信息栏显示什么」：两行都从
    # 「信息槽」来，别名得同时指向它们——只指开关行的话，老用户搜旧标题「信息槽内容」
    # 拿到的还是那个开关，兼容承诺等于没兑现（终审确认问题 2）。
    "信息槽": ("dynamic_island_info", "dynamic_island_info_mode"),
    # 「信息槽」这条**有意不收** ``dynamic_island_custom_text``：它从来没叫过「信息槽…」
    # （旧标题就是「自定义短文本」，本次改名也没动它），label 里又自带「自定义短文本」，
    # 关键词直接命中——别名只兜「老说法」，不替用户把候选放大到语义不同的行。
    # 「峰谷提示文案」→「高峰 / 空闲时段提示」
    "峰谷": ("balance_tier_mode",),
    # 「Agent 音效联动」→「AI 编程助手提示音」
    "音效联动": ("agent_sound_enabled",),
    # 游戏帧率段：行 label 写「游戏帧率」、hint 写「每秒真正呈现到屏幕上的帧数」，
    # 用户三种说法（FPS / fps / 帧数）都要能找到它。**帧数**这条别名与「上行 / 下行」
    # 同一取舍：行文案里确实含着这个词（关键词本来就命中），但它指向哪一行是**语义**
    # 决定的——文案改写一次关键词路径就可能断，别名把"用户指的就是这一行"钉下来。
    "帧数": ("island_fps",),
    # 2026-10-18 用户视角实测的 0 命中词。分三类：
    # ① 新功能的叫法：行标题只写「AI 对话 / 看看屏幕 / 截图提问」，说明里写「组合键」，
    #    用户张口就是「快捷键」「热键」——整个设置页文本里没有这两个词。三个动作是三
    #    行，别名必须**全指**：只指一行等于替用户挑了一个动作。
    "快捷键": ("hotkey_open_chat", "hotkey_look_screen", "hotkey_screen_capture"),
    "热键": ("hotkey_open_chat", "hotkey_look_screen", "hotkey_screen_capture"),
    # ② 「跨屏」说明里的词是「显示器 / 那块屏」，用户说的是「副屏」「多屏」——单字
    #    「屏」在不收之列（会把「屏幕」类行全灌进来），两个词都指向唯一那张跨屏开关。
    "副屏": ("cross_screen",),
    "多屏": ("cross_screen",),
    # ③ **域名的搜索**：侧栏域的名字不进索引（`SETTINGS_DOMAIN_NAV` 是导航标签，
    #    不是行文案），于是照着侧栏念域名的人 0 命中。每个域名指到该域里最有代表性的
    #    那一行，理由逐条写：
    #    桌面组件 = 灵动岛主开关（该域 25 行里 21 行是岛的外观与指标、3 行游戏帧率采集、
    #    1 行延迟探测，主开关是唯一能当入口的那一行）；
    "桌面组件": ("dynamic_island_enabled",),
    #    AI 与对话 = 「API 列表」（该域首行，也是「模型与连接」组的抬头，落在域的最上方，
    #    同时避开已由「密钥」占着的 `api_key`）；域名里的「对话」半边由「对话窗口」行兜住。
    #    键写小写：查询在 ``find_matches`` 里已 lowered，别名表按小写比对（同 ``dry-run``）。
    "ai 与对话": ("provider_list", "chat_ui_style"),
    #    自动化与联动 = 「开启主动识屏」（该域里最大的一块自动化，也是它的主开关；
    #    待办提醒、免打扰、编程助手提示音都是并列的配角，选谁当落点都不如它代表"自动"）。
    #    **但「主动识屏」页只在 Windows 建**（`modern_settings_dialog.py` 的
    #    `sys.platform == "win32" and include_ai` 闸门），macOS/Linux 上那一行根本不存在、
    #    查询会 0 命中。所以并列补上该域第一组「待办提醒」作跨平台落点。
    "自动化与联动": ("proactive_enabled", "todo_reminder_enabled"),
    #    文件识别 = 「拖文件后询问解读」（该域只有 2 行，它就是整个域的功能本体，
    #    「进度汇报间隔」是它的从属参数）。
    "文件识别": ("file_interpret_enabled",),
    # 「检查更新」**有意不收**：它是「更新」页上的按钮（`pet/update_settings.py` 的
    # `check_button`），不是 `SettingRow`——命令类控件不进搜索候选集（与侧栏的
    # 「恢复默认设置 / 保存并退出」同一口径）。要让它可搜得先给按钮建落点，另一件事。
}

_ROW_PREFIX = "settingRow_"


def row_key(row: SettingRow) -> str:
    """行的键（``settingRow_`` 之后的部分），与别名表 / 配置归属用的是同一套。"""
    return row.objectName()[len(_ROW_PREFIX):]


def fold_group_titles(row: SettingRow, memo: dict | None = None) -> list[str]:
    """该行（含其上层 widget）所属的**可折叠组**标题，由内到外；普通小标题不算。

    普通小标题（未 ``advanced`` 的 ``SettingsSection``）不入索引，理由见模块 docstring。

    ``memo`` 是可选缓存（一次搜索内共享的 ``widget → 标题表``）：同一组的几十行共享
    同一条上半段祖先链，逐行重走全程在 200+ 行的设置页上要花几毫秒（每一跳都要跨到
    C++ 问一次 ``parentWidget``）。不传就逐行真走，语义完全相同。
    """
    cache = {} if memo is None else memo
    path = []
    node = row
    while node is not None and node not in cache:
        path.append(node)
        node = node.parentWidget()
    titles: list[str] = list(cache[node]) if node is not None else []
    for step in reversed(path):
        title = _own_fold_title(step)
        if title:
            titles = [title, *titles]
        cache[step] = list(titles)
    return titles


def _own_fold_title(widget) -> str:
    """widget 自己是不是可折叠组；是就给出它的标题（否则 ``""``）。"""
    if isinstance(widget, CollapsibleGroup):
        return widget.toggle.text()
    if isinstance(widget, SettingsSection) and widget.advanced:
        return widget.toggle.text()
    return ""


def _row_text(row: SettingRow) -> str:
    """既有索引口径：行 label + hint + objectName（小写，供子串匹配）。

    **作用域标注也算进来**（``scope_note``，「影响所有桌宠」这类）：它原本拼在 hint
    末尾、因此本来就在索引里（搜「所有桌宠」能命中那几行），拆成独立标注只是换了呈现
    位置，不该从搜索里掉出去。标注不存在时是空串，索引口径逐字不变。
    """
    note = getattr(row, "scope_note", None)
    note_text = note.text() if note is not None else ""
    return f"{row.label.text()} {row.hint_label.text()} {note_text} {row.objectName()}".lower()


def _alias_targets(query: str) -> set[str]:
    """查询里说到的口语别名指向的行键（别名 ⊂ 查询；反向不算，见模块 docstring）。"""
    return {
        target
        for token, targets in ROW_ALIASES.items()
        if token in query
        for target in targets
    }


def find_matches(rows, query: str) -> list[SettingRow]:
    """候选行（关键词 → 折叠组标题 → 口语别名，去重保序）；空查询返回空表。

    ``rows`` 传对话框构建期取的那一份 ``_search_rows``：候选集与域导航重建前的行
    登记表保持同源，别名目标也因此不会指向已被替换的行壳。
    """
    lowered = str(query or "").strip().lower()
    if not lowered:
        return []
    targets = _alias_targets(lowered)
    keyword_hits = [row for row in rows if lowered in _row_text(row)]
    group_memo: dict = {}  # 一次搜索内共享：同组几十行的祖先链只走一次
    group_hits = [
        row for row in rows
        if any(lowered in title.lower() for title in fold_group_titles(row, group_memo))
    ]
    alias_hits = [row for row in rows if row_key(row) in targets]
    ordered: list[SettingRow] = []
    seen: set[int] = set()
    for row in keyword_hits + group_hits + alias_hits:
        if id(row) in seen:
            continue
        seen.add(id(row))
        ordered.append(row)
    return ordered
