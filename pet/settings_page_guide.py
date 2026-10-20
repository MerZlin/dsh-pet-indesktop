# -*- coding: utf-8 -*-
"""设置页「按区直找」：旧页分区顺序 + 页内分区跳转条 + 组描述。

**为什么单独一个模块**：``modern_settings_dialog.py`` 的行数预算（``tests/
test_architecture.py``）没有余量，而本功能的主体是一段页内跳转条装配逻辑、一个换行
用的流式布局和一张「组标题 → 一句人话」的描述表，与对话框框体无关——按本仓库既有
口径（``settings_network`` / ``settings_quiet`` / ``settings_search`` /
``settings_default_marks`` / ``settings_music`` 都是同一个原因拆出来的）落在这里，
对话框只留一行 ``install(self)`` 接线。

口径：

- **布局顺序而不是创建顺序**：域装配把旧页的行整表捞出来塞进共享卡片时，
  ``findChildren`` 给的是**创建顺序**——两处偶然一致，一处不一致就静默错位。
  ``sections_in_layout_order`` 走真实布局树，与用户在旧页看到的顺序同源。
- **跳转条只做「页内直达到区」**：不做新导航层、不改侧栏 10 域契约、不动任何行键。
  点一下把目标区顶到视口上沿（``setValue``），不用 ``ensureWidgetVisible``——后者
  只保证「看得见」，长卡片会把目标压在屏幕下沿，等于没跳。显隐判据（区数够 **且**
  内容真的超过视口）必须量在**这一态的落定几何**上：跳转条自己占的那 28px 让出去之后，
  视口要等下一轮事件循环才长回来，当场重判会把它又显示回来（``_apply_visibility``
  里的同步推平就是为这件事）。
- **页尾留白是「顶对齐」这条口径的必要条件**：滚动范围的尽头是「内容底边贴视口
  底边」，页尾那一组最多滚到 ``视口高 - 该组高``，够不到 y=8。展开时序再对也没用
  ——范围本身就不够（``_PastEndPadding``）。
- **组描述是「这里是什么」的一句人话**：文案全部集中在本模块的 ``SECTION_GUIDE``，
  控件只负责呈现；高级组收起时描述照常可见（否则要先展开才知道里面是什么）。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

from .settings_widgets import CollapsibleGroup, SettingRow, SettingsSection, SettingsTabContainer

#: 少于这么多「区」就不装跳转条：两三个区一眼扫得完，多一条横条只是噪音。
#: 另一半判据是「内容真的超过视口」（短页上这条横条没有任何位移可言）：见
#: :meth:`_SettingsAnchorBar._content_overflows`。
ANCHOR_MIN_BLOCKS = 4


def section_title(section: SettingsSection) -> str:
    """分区的正式标题：高级组在折叠按钮上，普通组在 ``sectionTitle`` 标签上。"""
    if section.toggle is not None:
        return section.toggle.text()
    label = section.findChild(QLabel, "sectionTitle")
    return label.text() if label is not None else ""


def _layout_children(widget) -> list[QWidget]:
    """布局顺序的直接子控件（不是 ``findChildren`` 的创建顺序）。"""
    layout = widget.layout()
    if layout is None:
        return []
    children = []
    for position in range(layout.count()):
        child = layout.itemAt(position).widget()
        if child is not None:
            children.append(child)
    return children


def _is_plain_container(widget) -> bool:
    """没有自己的标题、只是布局层的普通 ``QWidget``（域装配可能先起一层再放卡片）。

    2026-10-14 起「语音」域就是这样：顶层一张「语音报时」平卡 + 节日页自建的卡片组，
    中间隔了一层自建 ``QWidget``。跳转条与组描述都按布局树取材，不穿透这层就会
    「新卡片看不见、跳转条少一格」。
    """
    return type(widget) is QWidget and widget.layout() is not None


def blocks_in_layout_order(page) -> list[tuple[str, QWidget]]:
    """页内**顶层块**（分区 / 折叠框）按布局顺序；跳转条一格对一块。"""
    blocks: list[tuple[str, QWidget]] = []

    def walk(node) -> None:
        for child in _layout_children(node):
            if isinstance(child, CollapsibleGroup):
                blocks.append((child.toggle.text(), child))
            elif isinstance(child, SettingsSection):
                blocks.append((section_title(child), child))
            elif _is_plain_container(child):
                walk(child)

    walk(page)
    return blocks


def sections_in_layout_order(page) -> list[tuple[str, list[SettingRow]]]:
    """页内所有分区（含折叠框里的子分区）按布局顺序，返回 (标题, 行表)。"""
    found: list[tuple[str, list[SettingRow]]] = []

    def walk(node) -> None:
        for child in _layout_children(node):
            if isinstance(child, SettingsSection):
                found.append((section_title(child), list(child.rows)))
            elif isinstance(child, CollapsibleGroup):
                walk(child.body)
            elif _is_plain_container(child):
                walk(child)

    walk(page)
    return found


def rows_in_layout_order(page) -> list[SettingRow]:
    """页内全部设置行按布局顺序；没被任何分区收走的行按创建顺序垫在后面。"""
    rows = [row for _title, section_rows in sections_in_layout_order(page) for row in section_rows]
    seen = {id(row) for row in rows}
    rows.extend(row for row in page.findChildren(SettingRow) if id(row) not in seen)
    return rows


def tab_container(widget) -> SettingsTabContainer | None:
    """页内容里的页内标签容器（没有就是 ``None``）。"""
    if isinstance(widget, SettingsTabContainer):
        return widget
    return widget.findChild(SettingsTabContainer, "settingsTaskTabs") if widget is not None else None


def current_page_content(scroll: QScrollArea) -> QWidget:
    """跳转条要列的那一屏内容：页内标签域取当前标签页，否则就是滚动内容本身。"""
    content = scroll.widget()
    tabs = tab_container(content)
    return tabs.stack.currentWidget() if tabs is not None else content


def _tab_chrome(tabs: SettingsTabContainer | None) -> int:
    """标签容器在**当前标签页**之上多占的那一层（``None`` = 不是标签域 ⇒ 0）。

    给"手里已经有容器"的调用者用（跳转条 / 页尾留白构造时都找过一次），避免每拍重找。
    """
    if tabs is None:
        return 0
    layout = tabs.layout()
    spacing = layout.spacing() if layout is not None else 0
    return max(0, tabs.tab_bar.sizeHint().height() + spacing)


def tab_chrome_height(scroll: QScrollArea) -> int:
    """页内标签域在**当前标签页**之上多占的那一层（标签栏 + 容器布局间距）。

    滚动的视口是**整块滚动内容**（= 标签容器）的视口，而 ``current_page_content``
    给的是容器里那一页：只拿页高去比视口就漏了这一层——``页高 <= 视口 < 页高 + 这一层``
    这一小段里判据说「装得下」，而内容其实要滚（实测 1100 宽、桌宠／动作与碰撞这一页的
    层高 52px = 标签栏 40 + 间距 12，对应窗口高 1222…1278）。

    取**结构量**（标签栏自己的 hint + 容器布局的 spacing），不取
    ``容器.minimumSizeHint() − 当前标签页.minimumSizeHint()``：后者在"刚把留白归零、
    布局还没落定"的那一拍会被上一拍的缓存值污染（实测同一态下差分量到 1336，真值 52）
    —— 而留白判据恰恰是在那一拍"归零后重量"的。标签栏纵向是 Fixed、按钮高度也是 Fixed，
    所以它的 hint 就是它落版后的高，且不用等第一遍布局。
    """
    return _tab_chrome(tab_container(scroll.widget()))


def current_page_height(scroll: QScrollArea, tabs: SettingsTabContainer | None = None) -> int:
    """当前这一屏真实要占的高度：当前标签页的高（扣掉页尾留白）**加上**标签那一层。

    页尾留白（``_PastEndPadding``）挂在标签页自己布局的下外边距上、不算内容，故扣掉；
    留白是按「最小高」1:1 长出来的，所以扣得掉。

    ``tabs`` 传调用者手里的标签容器（不是标签域就传 ``None``）：不传则自己找一次。
    两个调用者（跳转条 / 页尾留白）构造时都已经找过，传进来就省掉每拍的子树查找——
    非标签域那一次 ``findChild`` 实测 **11.6 µs**（页面上百个控件，判据每拍都调）。
    """
    content = scroll.widget()
    if tabs is None:
        tabs = tab_container(content)
    root = tabs.stack.currentWidget() if tabs is not None else content
    layout = root.layout() if root is not None else None
    if layout is None:
        return 0
    width = scroll.viewport().width()
    needed = max(root.minimumSizeHint().height(), root.heightForWidth(width))
    return needed - layout.contentsMargins().bottom() + _tab_chrome(tabs)


# --------------------------------------------------------------------------- 组描述
#: 组标题 → 一句人话（显示在组标题 / 折叠头下面，12px 灰字）。
#:
#: 写法：说「这里是什么、改了会怎样」，一句话，不出现黑话（同一判据见
#: ``tests/test_settings_copy_lint.py``）。新增组必须在这里补一行，
#: ``tests/test_settings_page_guide.py`` 的覆盖率守卫会盯住。
SECTION_GUIDE: dict[str, str] = {
    # 角色包不是「设置项」而是素材清单 + 下载动作，文案按用户视角写（不含实现术语）。
    "已安装的角色包": "当前已经装好的角色素材，以及它们占用的空间。",
    "可下载": "可以下载到本机的动画分件；装好后对应的动画就能播放。",
    "作者账号": "BiliBili、抖音与 GitHub。",
    # 常规
    "应用启动": "开机后要不要自动把桌宠叫起来。",
    "窗口与系统": "它压在别的窗口上面还是能被盖住，全屏玩游戏时让不让位，光标被系统藏起来时要不要自动穿透点击，以及桌宠能不能被拖到别的显示器上。",
    # 收起时只留一句"这一项做什么"：重启级切换、OBS 捕获方式与作用域全文都在行内，
    # 展开即见（分层是"要不要现在读"，不是删说明）。摘要里不写缩写（黑话 lint 同尺子）。
    "直播捕获（高级）": "让直播工具能枚举并捕获桌宠窗口。",
    "启动项（高级）": "随桌宠启动（不是开机）时顺手把 dsh 服务拉起来；可以只起服务，也可以连界面一起打开。",
    # 桌宠
    "显示": "桌宠和它的气泡显示多大、多透明。",
    "音乐关联": "听到音乐就唱歌、显示歌词；按你在用的播放器挑对应开关。",
    "播放器路径与标题格式（高级）": "播放器装在哪儿、歌名从哪儿取。默认值对大多数人是好的。",
    # 非 Windows 上不存在「标题格式」行，组名跟着退化（见 settings_music）。
    "播放器路径（高级）": "播放器装在哪儿。默认值对大多数人是好的。",
    "动画与移动": "动作播放多快、待机时自己走动还是原地待着。",
    "拖拽与弹射": "拖它、甩它、把它弹出去时的手感。",
    "边缘探头": "拖到屏幕边上，它会探出身子看一眼再缩回去。",
    "生小肥鱼": "再生成一只时它多大，以及随不随主桌宠的设置。",
    "多开碰撞": "多只桌宠之间撞来撞去，撞出声音。",
    "碰撞参数（高级）": "调撞击的弹跳、摩擦和力度；数值越小越文静。",
    # 互动
    "输入": "鼠标点在它身上时，点击要不要穿到底下的窗口。",
    "点击反馈": "点它一下会发生什么：音效、说句话、转个圈、报余额。",
    "自言自语": "没人理它的时候，它多久冒一句话、说什么。",
    "天气": "在气泡里带上你所在城市的天气。",
    # 菜单
    "内容与布局": "右键菜单长什么样：有哪些项、按什么顺序排。",
    "已配置应用": "菜单里「快捷启动」子菜单放哪些应用。",
    "菜单外观": "菜单的配色、圆角、字体和透明度。",
    "高级配色": "逐项指定浅色、深色菜单的底色与文字色。",
    "彩蛋入口": "菜单里那个小彩蛋，以及它弹出来的图和话。",
    # 桌面组件
    "桌面胶囊（灵动岛）": "屏幕边上那条小胶囊：显示什么、点它做什么、怎么收起来。",
    "网络延迟检测（高级）": "胶囊多久测一次网络延迟（开了「网络」这条信息才有用）；速率监测不产生额外流量，和这一项无关。",
    # AI 与对话
    "API 列表": "你配过的服务商清单，在这里增删和切换。",
    "模型与连接": "当前用哪家、哪个模型、密钥填什么、人设提示词写什么。",
    "深度思考与联网": "让模型先想再答（思考过程可折叠），以及回答前要不要先搜资料。",
    "系统通知": "你没在看聊天窗时（切去干别的），回复和失败会弹到桌面右下角；点一下跳回聊天窗。",
    "视觉能力": "看图的活儿交给谁：跟聊天用同一家，还是单独配一个。",
    "生成参数（高级）": "等待上限、随机程度、回复长度这些底层旋钮。",
    "对话窗口": "聊天窗的配色、背景图和透明度。",
    "余额与服务状态": "余额多久刷一次，以及高峰、空闲时段各显示什么话。",
    # 自动化与联动
    "待办提醒": "到点了提醒你该做什么；待办条目在右键菜单「待办提醒」面板里增删改。",
    "免打扰": "开启后普通提醒先攒着，退出免打扰时统一汇报；审批、报错这类要你回应的提示照常冒出来。"
              "语音报时与节日语音在免打扰期间也不出声（过了点补播没意义，所以直接丢掉，只留气泡）。",
    "主动识屏 · 开关与节奏": "它偶尔看看你正在用的软件，说句话。截图不保存。",
    "主动识屏 · 什么时候看": "什么时候才看：要不要先闲置才看，以及桌宠处于鼠标穿透状态时算不算数。",
    "主动识屏 · 看哪些软件": "只看你点名的软件；看过留下的痕迹随时能清掉。",
    "主动识屏 · 细调（高级）": "看得多勤、多久搭一次话、一天最多看几次，以及不花钱的试运行。"
                        "预设选「自定义参数」时这些参数才生效；手动改参数保存后会自动落成自定义预设。",
    "消费统计": "每一轮结束时报一报这轮花了多少钱。",
    "Agent 联动": "编程助手开始干活、干完活、出错时的提示音。",
    "提示音效": "三种时机各用哪段声音、多大声、多久内不重复。",
    "文案风格与模板": "它说话是什么风格，以及一份可以整体替换的文案模板。",
    "自定义台词（逐事件文案）": "每个事件各说什么，逐条改；不改也能正常用。",
    "事件气泡文案": "按事件分组的气泡台词，一行一条。",
    # 互动 · 趣味（卡片名仍是「抽签」）
    "抽签": "右键「抽支签」出当天的签，一天一支；这里管开关、要不要念、要不要每天提醒。",
    "快捷键": "给「对话 / 看看屏幕 / 截图提问」各录一个全局组合键；空着就是不绑，右键菜单照常能用。",
    # 语音（2026-10-14 起两页各自自建卡片，标题跟着从「语音报时 · X」变成卡片名）
    # 「语音报时」是域里唯一那张语音卡：语音页自己的四张小卡（基础设置 / 合成后端 /
    # 语音 / 台词歌词）**不进域**——它们的行被收进这张平卡，容器本身被丢弃，所以
    # 那四个标题在界面上永远不会出现，不能在这里留条目（会被防腐烂用例判为过期）。
    "语音报时": "几点说话、多久说一次；用哪家的合成引擎、各引擎的音色与参数；"
              "以及报完时间加不加台词、冒不冒气泡。",
    "何时提醒": "一天提醒几次，或者自己排几个时间点。",
    "提醒哪些日子": "开不开节日提醒、提醒哪些节日。",
    "怎么提醒": "出声念、播动画、附一句诗词，各自开不开。",
    "你的生日": "填了就在当天有专属祝福。",
    "少用项": "自定义节日文案与预览这类偶尔才用的项。",
    # 文件识别
    "拖文件解读": "把文件拖到桌宠身上，它会读一遍并说点什么。",
}


def apply_descriptions(root) -> None:
    """把 ``SECTION_GUIDE`` 注入 ``root``（一个域页的滚动内容）里的分区 / 折叠框。

    ``root`` 是域页 ``settingsScroll`` 的 widget，不是整个对话框：域装配末尾旧的散页
    已被 ``deleteLater``，整框遍历会顺手碰这些待销毁的壳（无害，但没有一处需要它们）。
    标题做键，缺项留空。
    """
    for section in root.findChildren(SettingsSection):
        section.set_description(SECTION_GUIDE.get(section_title(section), ""))
    for box in root.findChildren(CollapsibleGroup):
        box.set_description(SECTION_GUIDE.get(box.toggle.text(), ""))


# ------------------------------------------------------------------------- 跳转条
class _FlowLayout(QLayout):
    """把 chip 横向排开、放不下就换行的简易流式布局（Qt 没有内置 FlowLayout）。"""

    def __init__(self, parent=None, *, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = int(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt API
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - Qt API
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._reflow(QRect(0, 0, int(width), 0), dry_run=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._reflow(rect, dry_run=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize(0, 0)
        for item in self._visible_items():
            size = size.expandedTo(item.minimumSize())
        return size

    def _visible_items(self) -> list:
        # 整组被依赖隐藏时 chip 跟着藏起来，藏起来的不能继续占位（否则留空洞）。
        return [item for item in self._items
                if item.widget() is None or not item.widget().isHidden()]

    def _reflow(self, rect: QRect, *, dry_run: bool) -> int:
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self._visible_items():
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right():
                x, y, line_height = rect.x(), y + line_height + self._spacing, 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


class _SettingsAnchorBar(QWidget):
    """页头标题下方那一排 chip：一个可见分区一格，点一下顶对齐滚过去。

    **短页不摆**：页内每个区都已在视口里时，这条横排 chip 只是把页头重复一遍——点它
    没有任何位移。显隐判据因此是「区数够 **且** 内容真的超过视口」，见
    :meth:`_apply_visibility` 与 :meth:`_content_overflows`；判据量必须在**这一态的
    落定几何**上做（显隐刚改时视口还差自己那段），故显隐变更当场推平布局再复核。
    """

    def __init__(self, heading_host: QWidget, scroll: QScrollArea, padding: _PastEndPadding):
        super().__init__(heading_host)
        self.setObjectName("settingsPageAnchors")
        self._scroll = scroll
        self._padding = padding
        self._layout = _FlowLayout(self)
        self._chips: list[QPushButton] = []
        self._blocks: list[tuple[str, QWidget]] = []
        #: 跳转条自己占掉的那段高度（见 ``_content_overflows``）：只在真正落过版之后
        #: 记下来，取值 0 表示"还没量过"，那时判据退回当前视口（偏向显示，不会把长页藏掉）。
        self._bar_height = 0
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(0)
        self._sync_timer.timeout.connect(self._apply_visibility)
        self._tabs = tab_container(scroll.widget())
        if self._tabs is not None:
            # 页内标签各是一组区：换标签就把跳转条换成那一组的。
            self._tabs.currentKeyChanged.connect(self._on_tab_changed)
        heading_host.layout().addWidget(self)
        # 视口尺寸/显隐变了要重判；滚动范围变了（展开折叠组、依赖放出整组行…）同样
        # 会改变"够不够装"，所以两边都盯。重判推到下一拍：这些信号都可能发在布局过程
        # 里，当场改自己的可见性会重入布局流程（同 ``SettingsSidebarPane`` 的先例）。
        # 而显隐一旦真的改了，就当场把布局推平（``_apply_visibility`` 里的同步复核）。
        scroll.viewport().installEventFilter(self)
        scroll.verticalScrollBar().rangeChanged.connect(lambda *_args: self._schedule_visibility())
        self.rebuild()

    # ---------------------------------------------------------------- 装配
    def rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._chips = []
        self._blocks = blocks_in_layout_order(current_page_content(self._scroll))
        for title, block in self._blocks:
            chip = QPushButton(title, self)
            chip.setObjectName("settingsPageAnchor")
            chip.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            chip.setAccessibleName(f"跳到「{title}」分区")
            chip.clicked.connect(lambda _checked=False, target=block: self.jump_to(target))
            block.installEventFilter(self)
            self._chips.append(chip)
            self._layout.addWidget(chip)
        self._apply_visibility()

    def _on_tab_changed(self, _key: str) -> None:
        self.rebuild()

    def _sync_visibility(self) -> None:
        """整组被依赖隐藏时 chip 同步隐藏（``isHidden`` 才是「显式藏了」）。"""
        for chip, (_title, block) in zip(self._chips, self._blocks):
            chip.setVisible(not block.isHidden())

    # ------------------------------------------------------------ 显隐（短页不摆）
    def _schedule_visibility(self) -> None:
        if not self._sync_timer.isActive():
            self._sync_timer.start()

    def _apply_visibility(self) -> None:
        """跳转条自己的显隐：**区数够** 且 **内容真的超过视口**。

        显隐变更之后要**当场**把页头那几层布局推平再复核一次（:meth:`_settle_geometry`）：
        跳转条让出一行之后，滚动视口要等下一轮事件循环才长回来，而判据按「藏掉之后装不装
        得下」回答——若在这一拍就重判，量到的还是矮视口（``hidden`` 分支又不补自己那段），
        于是「装不下」⇒ 又把自己显示回来。两个稳态里会停在错的显态：1100×556 实测停在
        显态 + 页尾 381px 空白，而把窗口再拉高 28px 就装得下（544…568 整段都是这样）。
        推平之后两个方向量到的都是这一态的落定几何，最多两拍就收敛：不排队、不用定时器
        兜、也不靠放大窗口掩盖。
        """
        self._sync_visibility()
        for _pass in range(2):
            want = len(self._blocks) >= ANCHOR_MIN_BLOCKS and self._content_overflows()
            if want == (not self.isHidden()):
                return
            self.setVisible(want)
            self._settle_geometry()

    def _settle_geometry(self) -> None:
        """从自己往上逐层 ``activate()`` 到滚动内容所在的那一页。

        同步推平（同 :meth:`_settle` 的手法，不排定时器）：跳转条占掉/让出的那段高度要
        同一拍落进视口，紧接着的复核才量得到这一态的真值。
        """
        target = self._scroll.parentWidget()
        node: QWidget | None = self
        while node is not None:
            layout = node.layout()
            if layout is not None:
                layout.activate()
            if node is target:
                return
            node = node.parentWidget()

    def _content_overflows(self) -> bool:
        """真实内容是否超过视口——判据与调用者的显隐状态无关，只有一个不动点。

        三笔都要算对：

        - **量的是整块滚动内容，不是标签页自己**：页内标签域多一层「标签栏 + 间距」
          （:func:`current_page_height`）。拿页高直接比视口，``页高 <= 视口 < 页高 + 层高``
          这一小段就成了漏网：判据说装得下、跳转条收起来，而用户明明要滚才看得完。
        - **页尾留白不算内容**（``_PastEndPadding`` 挂在内容布局的下外边距上）：它由
          "内容超不超过视口"反推出来（装得下就是 0），把它算进去等于拿结论当前提。
        - **跳转条自己占的那段高度要加回来**：显着的时候视口已经被它压矮了一截，直接
          比会让短页"因为自己摆着一条跳转条"而永远显示不足（两种状态各自自洽 ⇒ 来回
          抖）。加回自己那份之后，判据在两个状态下取到同一个可用高，因此是幂等的。
          还没落过版（``_bar_height == 0``）时不加：偏向显示，宁可多一条也不把长页藏掉。

        还有一件不在这个函数里、但同样属于判据前提的事：量到的视口必须是**这一态的落定
        几何**。显隐刚改、布局还没跟上时量到的是上一态的视口（矮 28px），"加回自己那段"
        也补不回来——那一路由 :meth:`_apply_visibility` 的同步推平负责。
        """
        viewport = self._scroll.viewport()
        if viewport.height() <= 0:
            return False
        budget = viewport.height() + (0 if self.isHidden() else self._bar_height)
        return current_page_height(self._scroll, self._tabs) > budget

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if self.isHidden():
            return
        # 记下自己占的那段高度（判据要用），并重判一次：可见性变化本身也要等布局
        # 落定之后才量得准尺寸，这一拍的重判把"刚才量到的"喂回判据。
        self._bar_height = self.height()
        self._schedule_visibility()

    # ---------------------------------------------------------------- 跳转
    def jump_to(self, block: QWidget) -> None:
        """顶对齐滚到该区；目标是高级组 / 折叠框时先展开（口径同搜索跳转）。"""
        self._reveal(block)
        content = self._scroll.widget()
        self._settle(block, content)
        # 从上次重算到现在，内容可能变过（展开、依赖开关放出一整组行…）：留白不够
        # 就会被旧 maximum 截住，先按当前内容补足。
        self._padding.sync()
        self._settle(block, content)
        y = block.mapTo(content, QPoint(0, 0)).y() - 8
        self._scroll.verticalScrollBar().setValue(max(0, y))

    def _settle(self, block: QWidget, content: QWidget) -> None:
        """把布局推平到「量坐标即最终坐标」。

        两步都要：

        - ``adjustSize()``：让滚动内容按 sizeHint 真 resize。只 ``activate()`` 布局不够
          ——它按内容**旧的**高度重排，量出来的 y 落在半更新的位置上（实测展开收起组时
          少算 74px；页面刚被展示出来时「模型与连接」少算 100px，720×500 下一次点击
          落不到 y=8）。同步、不需要定时器，也就没有「延后一拍时目标已被销毁」的悬空。
        - 从块往上逐层 ``activate()``：``adjustSize()`` 只保证**滚动内容**这一层的尺寸
          落定，下面每一层（页内标签的 stack 页、页里的块）要等事件循环才重排，此时量
          y 会落在半更新的布局上：实测 1600×1000 的标签页多算 252px，一次点击直接越过
          目标滚到空白处（目标视口 y 从 8 变成 -244）。
        """
        content.adjustSize()
        self._activate_layouts(block, content)

    def _reveal(self, block: QWidget) -> None:
        """目标是收起的高级组 / 折叠框就先展开，否则滚过去是一片空白。"""
        if isinstance(block, CollapsibleGroup):
            if not block.is_expanded():
                block.set_expanded(True)
        elif isinstance(block, SettingsSection) and block.advanced:
            if not block.toggle.isChecked():
                block.toggle.setChecked(True)

    @staticmethod
    def _activate_layouts(block: QWidget, content: QWidget) -> None:
        """从 ``block`` 往上到 ``content``（含）逐层重排，量坐标前先把布局推平。"""
        node = block.parentWidget()
        while node is not None:
            layout = node.layout()
            if layout is not None:
                layout.activate()
            if node is content:
                return
            node = node.parentWidget()

    # ---------------------------------------------------------------- 跟手
    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self._scroll.viewport():
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self._schedule_visibility()
        elif event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            # 整组出现/消失会改变页高（也改变 chip 该不该在）：chip 当场同步，
            # 跳转条自己的显隐推到下一拍。
            self._sync_visibility()
            self._schedule_visibility()
        return super().eventFilter(watched, event)


# ------------------------------------------------------------------- 页尾留白
#: 页尾留白的高度上限（屏数）。实际上限由公式自动保证（``<= 视口高 - 8 - 尾组高``），
#: 这个常量只是「离谱值不许出现」的兜底。
PAST_END_MAX_SCREENS = 1.0


class _PastEndPadding(QObject):
    """在滚动内容末尾挂一块留白，让**页尾那一组**也能被顶到视口 y=8。

    **为什么需要**：不用这块留白时，滚动范围的尽头是「内容底边贴视口底边」，于是页尾
    那一组的顶部最多只能滚到 ``视口高 - 该组高``——900×700 实测「碰撞参数（高级）」
    只能到 y=255、「余额与服务状态」只能到 y=191，目标是 8。这不是展开时序问题：
    ``_SettingsAnchorBar`` 的 ``_settle()`` 已经能让布局与滚动范围落定，是**范围本身
    小于目标**（``desired=1189`` > ``maximum=942``）。

    **高度按「刚好够最后一组顶对齐」算**，不拍固定值：

        P = 目标上限 - 布局最小高

    其中「目标上限」= ``尾组顶 y - 8 + 视口高``（滚动范围要够到这个值），「布局最小高」
    是留白归零后 ``root.minimumSizeHint()``。**基准取最小高而不是渲染高**，是因为
    ``QScrollArea`` 把内容高定成 ``max(视口高, 布局最小高, 按视口宽渲染的高)``：
    只有「最小高」会随留白 1:1 增长，拿渲染高当基准会自欺——页内标签域的内容高会被
    「按视口宽渲染的高」永久钉在一个比真实布局大的值上（切标签/缩窗口都不会回退），
    用渲染高算出来的留白加到布局里根本抬不动滚动范围（实测 1200×900 下补 440px，
    内容高纹丝不动）。

    - 只补差额，不凭空造空白；``P <= 视口高 - 8 - 尾组高``，天然不超过一屏。
    - **留白是页内容布局的下外边距，不是一块占位控件**：控件会额外吃掉一段布局间距
      （``QVBoxLayout`` 的 ``spacing``），于是留白为 0 的页面也会凭空长高一段——那只
      会把「刚好装得下」的页面挤出一个滚动条。外边距只改布局自己的尺寸，不留痕迹。
    - **内容本来就装得下时补 0**：那种页面根本不滚动，每一组都已经看得见，再挂留白
      只会凭空变出滚动条（短页面的显隐不受本功能影响）。「装得下」按**整块滚动内容**
      判：页内标签域要连标签栏那一层一起算（:func:`tab_chrome_height`），否则
      ``页高 <= 视口 < 整块内容高`` 这一小段会被答成"装得下"——跳转条摆着、点下去却
      顶不到 y=8（实测 1100 宽、桌宠／动作与碰撞、窗口高 1264：只滚到 52，目标 979）。
    - 视口尺寸变了要重算：事件过滤器盯 viewport 的 Resize / Show。
    - 页内标签各是一份页内容（``SettingsTabContainer`` 的 stack），每份各挂一块，
      换标签时一并重算（``currentKeyChanged``）。
    - **手滚到页尾会多出一块空白，这是有意代价**（编辑器式 scroll-past-end）：
      「任何一组都能一次点击顶对齐」与「滚到底正好是内容底边」不可兼得。
    """

    def __init__(self, scroll: QScrollArea):
        super().__init__(scroll)
        content = scroll.widget()
        tabs = tab_container(content)
        roots = (
            [tabs.stack.widget(i) for i in range(tabs.stack.count())]
            if tabs is not None else [content]
        )
        self._scroll = scroll
        #: 页内标签容器（没有就是 None）：判据只在这里找一次，之后每拍直接用。
        self._tabs = tabs
        self._roots = [root for root in roots if root is not None and root.layout() is not None]
        self._syncing = False
        if not self._roots:
            return
        if tabs is not None:
            tabs.currentKeyChanged.connect(self.sync)
        scroll.viewport().installEventFilter(self)
        self.sync()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.sync()
        return super().eventFilter(watched, event)

    def sync(self, *_args) -> None:
        """按当前视口尺寸重算每块留白（重入保护：改留白会让滚动条出现/消失）。"""
        if self._syncing:
            return
        self._syncing = True
        try:
            viewport = self._scroll.viewport().size()
            for root in self._roots:
                self._apply(root, self._height_for(root, viewport))
        finally:
            self._syncing = False
        for root in self._roots:
            root.updateGeometry()

    @staticmethod
    def _apply(root: QWidget, height: int) -> None:
        layout = root.layout()
        margins = layout.contentsMargins()
        if margins.bottom() != height:
            layout.setContentsMargins(margins.left(), margins.top(), margins.right(), height)

    def _height_for(self, root: QWidget, viewport: QSize) -> int:
        """这块留白该多高；页面本来就装得下就给 0（见类说明）。

        「装得下」这一问要按**整块滚动内容**回答：页内标签域的内容是标签容器，比标签页
        自己多一层「标签栏 + 间距」（:func:`tab_chrome_height`）。只拿标签页去比视口，
        ``页高 <= 视口 < 页高 + 层高`` 这一小段就会答成"装得下"⇒ 留白 0 ⇒ 页尾那几组
        点了也顶不到 y=8（实测 1100 宽、桌宠／动作与碰撞：窗口高 1264 时点「多开碰撞」
        只滚到 52，目标 979）。
        """
        layout = root.layout()
        _PastEndPadding._apply(root, 0)
        layout.activate()
        minimum_height = root.minimumSizeHint().height()
        # 「本来就装得下」= 两个口径都不超过视口：布局最小高（不换行也算得下的高度）与
        # 按视口宽渲染出来的高（``heightForWidth``，换行标签的实际高度）。这里**不能**
        # 用「当前有没有滚动条」来放行：刚构造时布局未落定、或被留白自己顶出一个滚动条，
        # 都会让判据自证成立，短页面从此长出一屏空白还退不回去（实测把 1600×900 的
        # 「常规」页挤出滚动条，页面标题与内容不再居中对齐）。
        rendered_height = root.heightForWidth(viewport.width())
        laid_out = _tab_chrome(self._tabs) + max(minimum_height, rendered_height)
        if viewport.height() <= 0 or laid_out <= viewport.height():
            return 0
        blocks = [child for child in _layout_children(root)
                  if isinstance(child, (SettingsSection, CollapsibleGroup))]
        if not blocks:
            return 0
        last_top = blocks[-1].mapTo(root, QPoint(0, 0)).y() - 8
        # 留白加在末尾、不改任何块的位置，所以「尾组顶 y」不受留白影响：一次算准，
        # 不需要迭代收敛。基准取最小高（随留白 1:1 增长的那个），见类说明。
        return max(0, min(last_top - minimum_height + viewport.height(),
                           int(viewport.height() * PAST_END_MAX_SCREENS)))


def install(dialog) -> None:
    """给每个设置域页装跳转条、注入组描述、挂页尾留白（域装配末尾调一次）。

    页尾留白不依赖跳转条：内容够长的页面都该能滚到底，短页面按公式补 0、不受影响。

    没有 ``settingsScroll`` 的页（如「更新」的命令面板）自然跳过：那不是设置表单，
    没有「区」可直达，没有分区要写描述，也没有页尾要留白。
    """
    for index in range(dialog.pages.count()):
        page = dialog.pages.widget(index)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        if scroll is None:
            continue
        apply_descriptions(scroll.widget())
        padding = _PastEndPadding(scroll)
        heading_host = getattr(page, "heading_host", None)
        if heading_host is None:
            continue
        _SettingsAnchorBar(heading_host, scroll, padding)
