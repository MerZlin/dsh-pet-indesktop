# -*- coding: utf-8 -*-
"""「快速识屏」的编排：截图 → 框选动作条选动作 → 引擎/出口（本片的产品面）。

框选后由覆盖层动作条给出动作（对齐主流截图工具：松手 → 选做什么），四条链路
的差别只在中间那一步，前后两端共用同一套收口：

    提取文字：capture → 本地 OCR            → 结果浮层（可复制，纯本地）
    翻译    ：capture → OCR(原文) + 聊天模型文本互译 → 结果浮层（双语对照位）
    复制图像：capture → 系统剪贴板           → 岛/气泡一句状态（纯本地）
    截图问答：capture → 打开问答浮窗          → 图进输入栏，用户自己打字发问、可多轮
              （不弹系统确认框：发不发送、发什么，都由用户在浮窗里亲手点「发送」
              决定——打开窗口本身一个字节都不发）

为什么编排单独立一层（而不是塞进窗口类）：宿主有两条（``PetWindow`` 单窗拓扑与
``OverlayShell`` sprite 拓扑），窗口类里各写一遍必然漂移；这里只认一个 duck-typed
宿主面（``cfg`` / ``show_bubble`` / ``proactive_watcher`` / ``screen_capture_island``），
两条宿主各自只挂一个零参回调。整个文件不 import 任何窗口模块。

六条纪律（每条都有对应用例）：

1. **隐私红线**：提取文字/复制图像纯本地（不联网）；问答的像素只在用户在问答浮窗里
   亲手点「发送」时才离开本机——浮窗本身即确认面（发送与否、发送什么内容都由用户
   在发送前看得见、改得了），不再叠一层系统 Yes/No 对话框（2026-10-14 实机反馈：
    modal 确认框突兀且与操作条语义重复）。
2. **物理隔离**：采集会话期间暂停主动识屏 watcher，会话结束**只恢复到此前配置
   允许的状态**——此前没在跑就不许被这次截图唤醒（对称，不是无条件 resume）。
3. **失败也收口**：采集中途取消/失败/引擎抛错，watcher 与 busy 位都必须回到起点，
   不许留下"此后每次触发都被拒"的中间态。
4. **阻塞步骤不占 GUI 线程**：OCR 与网络都在后台线程跑，结果经 Qt 信号回到
   GUI 线程再动界面（Qt 对象只在拥有它的线程里碰）。
5. **用完就丢**：会话收口（交付 / 取消 / 失败）时调 ``ocr.release_engine``。切片 5
   起识别跑在**独立子进程**里，进程退出即把 ~108 MB 的 native 堆整份还回操作系统
   （进程内 ``unload`` 实测只回吐约 30 MB，见 ``ocr.py`` 与
   ``.scratch/screen-capture/probe_ocr_subprocess.txt``）；收口处这一次调用因此
   是一道**兜底清扫**：真取消/失败时把还在跑的识别子进程杀掉，不留孤儿。所有出口
   都汇到 ``_finish()``，清扫只挂在那里一处。
6. **上下文随操作走 + 展示归属只有一处**：一次操作的**图 / 选区 / 轮次身份**在**发起
   那一刻**捕获（:class:`_OpContext`），此后收口只读这份快照，绝不回头读"最近一次"的
   状态——用户在等待期间又截一张图，不会把新那张的图配到旧那张的原文上（跨截图串用，
   见 ``_retranslate`` 与 ``_on_translate_done``）。四条链路共用的展示面（岛 / 气泡 /
   结果面板）由**落点**一处判归属，且判在通知与面板**之前**：被顶下去的旧卡片整张跳过
   ——不发通知、不覆写正文、不碰新一轮还在跑的加载动画（见
   :meth:`ScreenCapturePresenter._claims_display`）。
"""

from __future__ import annotations

import functools
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from .controller import ScreenCaptureController, _weak_slot
from .overlay import (
    ACTION_ASK, ACTION_COPY, ACTION_OCR, ACTION_TRANSLATE, SelectionOverlay,
)
from .reasons import (
    OCR_PENDING_REASON,
    SCREEN_RECORDING_PERMISSION_REASON,
    TRANSLATION_UNAVAILABLE_REASON,
)
from .result_panel import SHORT_RESULT_LIMIT, ScreenResult, ScreenResultPanel

# ``_weak_slot``：slice-1 的 Qt 生命周期小工具（信号槽只持弱引用，避免
# ``controller ↔ actions`` 引用环把 Qt 销毁推到解释器退出期）。同包内复用一份，
# 不再抄第二遍。

log = logging.getLogger("dsh-pet-standalone")

__all__ = [
    "ACTION_ASK",
    "ACTION_COPY",
    "ACTION_OCR",
    "ACTION_TRANSLATE",
    "ACTIONS",
    "OCR_PENDING_REASON",
    "TRANSLATION_UNAVAILABLE_REASON",
    "ScreenCaptureActions",
    "ScreenCapturePresenter",
    "actions_for",
    "ocr_engine_available",
    "resolve_ocr_engine",
    "resolve_ocr_release",
    "resolve_translator",
    "translation_engine_available",
]

ACTIONS = (ACTION_OCR, ACTION_TRANSLATE, ACTION_ASK, ACTION_COPY)

TITLE_OCR = "截图提取文字"
TITLE_TRANSLATE = "截图翻译"
TITLE_ASK = "截图问答"


@dataclass(frozen=True)
class _OpContext:
    """一次用户操作的上下文快照：**发起时**捕获，此后收口只读它。

    跨截图串用（返修 P1）的根因就是"收口时回头读最近一次状态"：A 的翻译结果摆着 →
    用户切语言触发重翻 → 重翻还在路上，用户又截了第二张图。``_last_image`` /
    ``_last_selection`` 在第二次采集落地那一刻就换成第二张的了，而旧回调是在**收口
    那一刻**现读它们——于是 A 的原文配上了 B 的图，「继续提问」把 A 的文字交给 B 的图。

    归属（``round_key``，同时也是展示归属判据 `Presenter._claims_display` 认的那份身份）、
    图（``image``）、选区（``selection``）三件上下文在这里定下来，随回调闭包走完全程。
    """

    round_key: object
    image: object = None
    selection: object = None

#: 引擎缺席时的如实说明（不是"暂时没有数据"，也不摆假结果）。文案的真值在
#: ``pet/screen_capture/reasons.py``——菜单里那两条置灰 tooltip 用的是同一份
#: （本模块顶部 import 进来，这里只做转发，见 ``__all__``）。

#: 本地 OCR 引擎 seam 的契约：模块级 ``recognize(image: QImage) -> str``——同步、
#: 纯本地、不联网，没识别到文字返回空串，失败抛异常（``str(exc)`` 可直接展示给
#: 用户）。引擎模块（切片 2 的 ``ocr.py``）不在时解析结果为空，动作如实说明。
OCR_MODULE = "ocr"
OCR_ENTRY = "recognize"
#: 「用完就丢」的释放入口：``release_engine() -> bool``（同步、幂等；没有识别在跑
#: 时是零成本 no-op）。每次会话收口都调它——切片 5 起识别跑在独立子进程里，收口处
#: 这一次调用是**兜底清扫**：把还在跑的识别子进程杀掉，不留孤儿。
OCR_RELEASE_ENTRY = "release_engine"
#: 翻译引擎 seam 的契约：``translate_selection(image: QImage, source: str) -> str``
#: （留给"独立翻译引擎模块"的扩展位；当前产品链路不走它——翻译由
#: ``_build_translator`` 装配 ``translate.translate_text``，吃宿主配置面）。
TRANSLATOR_MODULE = "translate"
TRANSLATOR_ENTRY = "translate_selection"


# ---------------------------------------------------------------- seam 解析


def _resolve_engine(module_name: str, entry: str) -> Callable | None:
    """惰性解析同包内的引擎模块入口；不在/加载失败一律返回 None。"""
    import importlib

    try:
        module = importlib.import_module(f"{__package__}.{module_name}")
    except Exception as exc:  # 引擎缺失或它自己的依赖没装好，都不是本片的崩溃点
        log.debug("快速识屏：引擎 %s 不可用 (%s)", module_name, exc)
        return None
    entry_fn = getattr(module, entry, None)
    return entry_fn if callable(entry_fn) else None


def resolve_ocr_engine() -> Callable | None:
    """本地 OCR 引擎（``pet/screen_capture/ocr.py::recognize``），缺席返回 None。"""
    return _resolve_engine(OCR_MODULE, OCR_ENTRY)


def resolve_translator() -> Callable | None:
    """旧的翻译引擎 seam（``translate.py::translate_selection``）。

    当前翻译走 ``translate.translate_text``（编排层经 ``_build_translator`` 装配，
    需要宿主配置面）；这个按名解析的 seam 留给"独立翻译引擎模块"的后续扩展，
    没有那个入口时恒 None。
    """
    return _resolve_engine(TRANSLATOR_MODULE, TRANSLATOR_ENTRY)


def resolve_ocr_release() -> Callable | None:
    """本地 OCR 的释放入口（``ocr.py::release_engine``），缺席返回 None。

    缺席（旧版引擎模块）不是错误：没有它只是"不归还"，识别链路照常工作——
    所以调用点对 None 与异常一律静默放行，绝不因为归还失败而影响一次已经成功
    的识屏。
    """
    return _resolve_engine(OCR_MODULE, OCR_RELEASE_ENTRY)


def ocr_engine_available() -> bool:
    """菜单项可用性的判据：**引擎模块在不在**（切片 2 的 ``ocr.py``）。

    刻意只判"模块 + 入口可调用"，不在这里探模型文件/依赖装没装全：

    - 模型缺失、rapidocr 没装好这类问题只有真加载时才知道，而引擎自己抛的
      ``OcrError`` 文案是**面向用户写的**（"缺少本地 OCR 模型文件 … 请重新安装"）
      ——那条理由由动作的错误出口原样展示在结果浮层里，比在这里猜一个置灰理由准；
    - 这里若去 ``resolve_model_dir()`` 之类做深探，会把 rapidocr 的厚重导入搬到
      "每次开右键菜单"这条路径上（菜单卡顿换一个更准的置灰，不值）。
    """
    return resolve_ocr_engine() is not None


def translation_engine_available() -> bool:
    """翻译链路是否接线（translate 模块在 + 有 translate_text 入口）。

    注意语义：这回答的是"引擎在不在"，不是"用户配没配置 AI 服务"——后者只有
    真跑才知道（`_build_translator` 需要宿主的配置面），UI 闸门要配配置面判断
    一起用，别拿这条单独当置灰依据。
    """
    try:
        import importlib

        module = importlib.import_module(f"{__package__}.translate")
    except Exception:
        return False
    return callable(getattr(module, "translate_text", None))


# ---------------------------------------------------------------- watcher 隔离


class _WatcherGate:
    """截图会话期间的主动识屏物理隔离（对称暂停/恢复）。

    "对称"是这一层唯一的难点：无条件 ``resume()`` 会把用户此前显式暂停/配置
    不允许的 watcher 唤醒——一次截图就改了主人没让它改的东西。因此只在
    **此前确实在跑**时才走恢复路径，而恢复本身仍交给 ``resume()``
    （= 按最新配置重评估启停），不在这里直接起定时器。

    取用方式在 2026-10-13 交叉审查 m9 改过：watcher **不在构造期定死**，构造期只挂
    一个零参取用回调，``pause()`` 那一刻才问"现在是谁"——壳的共享 watcher 可能晚建、
    也可能被重建，构造期抓到的那一个可能早就不是当值的那一个（与
    ``ScreenCapturePresenter`` 现取岛同一条纪律）。恢复则落在**确实被暂停的那一个**
    实例上：现取到别的对象就恢复别的对象＝我们暂停的那个永远醒不过来。
    """

    def __init__(self, provider: Callable[[], Any] | None = None) -> None:
        self._provider = provider
        self._paused_watcher: Any = None
        self._was_running = False

    def _current(self):
        provider = self._provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:  # 取用回调本身出错＝这条面不可用，不是截图失败
            log.debug("快速识屏：取主动识屏 watcher 失败", exc_info=True)
            return None

    def pause(self) -> None:
        if self._paused_watcher is not None:
            return  # 已经在隔离中：同一轮里只暂停一次，不叠第二次
        watcher = self._current()
        if watcher is None:
            return
        try:
            self._was_running = bool(watcher.is_running())
            watcher.pause()
        except Exception:  # 隔离失败不能连累截图本身
            log.exception("快速识屏：暂停主动识屏失败")
            self._was_running = False
            return
        self._paused_watcher = watcher

    def restore(self) -> None:
        watcher, self._paused_watcher = self._paused_watcher, None
        was_running, self._was_running = self._was_running, False
        if watcher is None:
            return  # 没暂停成功过（或已经恢复过）：不留残余状态
        if not was_running:
            return  # 此前没在跑：绝不被这次截图唤醒（配置/用户才是启动者）
        try:
            watcher.resume()
        except Exception:
            log.exception("快速识屏：恢复主动识屏失败")


# ---------------------------------------------------------------- 宿主解析
#
# 宿主有两条拓扑（PetWindow / OverlayShell），面不完全一样；这里把"从宿主取
# 某样东西"的探测收成一小组函数，属性名按各自主流的写法依次尝试，缺就返回 None。
# 测试替身同样吃这套探测（给任一形态即可）。


def _host_config(host):
    for name in ("cfg", "_config", "config"):
        value = getattr(host, name, None)
        if value is not None and callable(getattr(value, "get", None)):
            return value
    return None


def _host_watcher(host):
    watcher = getattr(host, "proactive_watcher", None)
    if watcher is not None:
        return watcher
    shared = getattr(getattr(getattr(host, "_instance", None), "shell", None), "_shared", None)
    return getattr(shared, "proactive", None)


def _host_island(host):
    provider = getattr(host, "screen_capture_island", None)
    if callable(provider):
        try:
            island = provider()
        except Exception:
            log.debug("快速识屏：取灵动岛失败", exc_info=True)
            island = None
        if island is not None:
            return island
    island = getattr(host, "island", None) or getattr(host, "dynamic_island", None)
    if island is None:
        island = getattr(getattr(getattr(host, "_instance", None), "shell", None), "island", None)
    return island


def _host_bubble(host):
    fn = getattr(host, "show_bubble", None)
    return fn if callable(fn) else None


def _host_dialog_parent(host):
    from PySide6.QtWidgets import QWidget

    if isinstance(host, QWidget):
        return host
    return getattr(host, "overlay", None)


def _host_theme(host) -> str | None:
    """宿主主题（``"light"`` / ``"dark"``；跟随系统时返回 ``None``）。

    来源就是菜单那一条真值：主配置的 ``context_menu_appearance.theme``
    （``{"system", "light", "dark"}``，见 ``pet/config.py::_clean_menu_appearance``）。
    既然菜单是这一个键渲染的，结果浮层就不该自己猜一遍——两处口径不同的话，用户
    会看到"菜单是浅色的、浮层是深色的"。``system`` 与读不到配置一律返回 ``None``：
    那表示"跟着宿主调色板现判"，由 ``ScreenResultPanel`` 自己按调色板定。
    """
    config = _host_config(host)
    if config is None:
        return None
    appearance = config.get("context_menu_appearance")
    theme = appearance.get("theme") if isinstance(appearance, dict) else None
    return theme if theme in ("light", "dark") else None


# ---------------------------------------------------------------- 展示落点


class ScreenCapturePresenter:
    """状态与结果的落点：短的上灵动岛，长的落在岛旁的结果浮层。

    岛的用法只取既有两个入口（``set_last_message`` + ``notify_event``），
    不为本片给岛加新 API——岛是"一眼"面，本片没有非它不可的新状态通道。
    岛不可用（关掉/隐藏/纯桌宠变体）时回落到桌宠气泡，用户至少看得到反馈。

    ``style`` 是结果浮层的主题（``None`` = 跟着宿主调色板现判）。默认那一条由
    ``ScreenCaptureActions`` 从宿主配置里读出来（与菜单同一个键），所以浅色宿主
    上不会再弹出一块写死深色的浮层。

    **展示归属在这里，且只有这一处**（:meth:`_claims_display`）：四条链路共用岛 /
    气泡 / 同一块结果面板，所以"谁有权改这些面"必须是一个判据，并且在**任何展示
    之前**判——短结果虽然不上面板，照样会覆写岛/气泡的那行文案，一样要判。判据曾经
    设在面板里，两条毛病：① 判在 ``_notify`` **之后**（旧终态照样刷掉新轮次的岛/
    气泡文案），② 只挡"上面板"的那条路（短结果绕开面板、无人可挡）。放在这里之后，
    ``show`` 的第一行就判，拦下的轮次一个字都不写、连通知都不发。

    归属状态只有一份：已经放行的**最高轮次编号**（生产里就是 ``actions._op_seq``
    这个单调整数），O(1)、不按身份记历史（旧实现按身份登记进面板的字典，进程内
    永不清理＝每次操作漏 ≈85 B）；也**不做淘汰**——被淘汰掉的旧身份会重新"看起来
    最新"，让旧回调复活，那比内存增长更不可接受。
    """

    def __init__(self, *, island=None, bubble=None,
                 panel_factory=ScreenResultPanel, panel_parent=None,
                 style: str | None = None, continue_ask=None,
                 language_change=None) -> None:
        self._island = island
        self._bubble = bubble
        self._panel_factory = panel_factory
        self._panel_parent = panel_parent
        self._style = style
        self._continue_ask = continue_ask   # 「继续提问」回调 (image, text)；None=不提供
        self._language_change = language_change  # 翻译语言行回调 (target)；None=不提供
        self._panel: ScreenResultPanel | None = None
        #: 展示归属的**唯一**状态：已经放行的最高轮次编号（``None`` ＝ 还没有任何
        #: 带编号的轮次到过）。生产里轮次身份就是 ``actions._op_seq`` 那个单调递增
        #: 整数，所以一份 O(1) 状态足够——不按身份记历史，也不淘汰（见类文档）。
        self._accepted_round: int | None = None

    # ------------------------------------------------------------ 对外
    def status(self, text: str) -> None:
        """过程状态（"正在识别…"）：只借岛的动效/消息槽，不上面板。

        （这条通道不带轮次身份：它的调用点都在**当前会话**内——`已取消`/`已复制`/
        `第几步失败`——按 `_busy` 与后台槽位的代号已经排除了旧会话，不属于展示归属
        判的对象。）
        """
        self._notify(str(text), "generic")

    def show(self, result: ScreenResult, below=None,
             round_key: object = None) -> ScreenResultPanel | None:
        """结果落地：岛永远拿到一句短文案；长结果/双语结果/强制件再开面板。

        ``below``：采集选区——面板落在选区正下方（实机反馈：角落要来回跳视线）。

        ``round_key``：这张卡片所属的轮次身份（编排层给的）——过程态靠它决定抽不抽、
        终态靠它决定收的是不是自己那一轮（见 ``result_panel._LoadingFish``），**展示
        归属也靠它**（见 :meth:`_claims_display`）。

        **归属先于一切展示**：第一行就先判这张卡片/这条文案能不能动展示面，判不过
        直接返回 ``None``——岛/气泡的那条通知**不发**，面板的正文/译文/状态行/语言行
        **不写**，新一轮还在跑的加载动画**不碰**。短结果也走这一步（它虽然不上面板，
        但会覆写岛/气泡的文案；把判据放进面板就会漏掉这条路）。

        例外：过程态卡片（``result.loading``）**不往岛/气泡推**——它没有内容可推，
        推过去就是一条空消息把岛上正在说的话刷没（round 3 实机观感修正）。
        """
        if not self._claims_display(round_key):
            log.debug("快速识屏：丢弃已经被更新的那一轮顶下去的卡片 (round_key=%r)",
                      round_key)
            return None
        if not result.loading:
            self._notify(result.summary or result.status, "reply")
        long_text = len(result.primary.strip()) > SHORT_RESULT_LIMIT
        if not (result.force_panel or long_text or result.secondary.strip()):
            return None
        panel = self.panel()
        if panel is None:
            return None
        panel.show_result(result, self.island_widget(),
                          on_continue_ask=self._continue_ask, below=below,
                          on_language_change=self._language_change,
                          round_key=round_key)
        return panel

    def _claims_display(self, round_key: object) -> bool:
        """这张卡片（或它带来的那条通知）能不能动展示面：只有"最新到过的那一轮"能。

        状态只有一份（``_accepted_round``），判据分三类：

        - **整数身份**（生产里唯一的形态：``actions._op_seq`` 单调递增）：不小于水位
          的放行并推进水位；**小于**水位的＝已经被顶下去的那一轮，返回 ``False``。
          水位从"还没有任何编号到过"（``None``）起，所以**第一个到场的编号必然放行**
          （哪怕它是 0 或负数——落点不假设调用方从 1 开始发号）；
        - ``None``：**匿名**，不参与归属、照旧放行（不按编号发号的调用方/测试替身用的
          就是这条；落点不假设调用方一定带编号）；
        - 其它类型：没有可比大小的身份＝排不了序，同样放行且**不推进水位**。这类身份
          一律不记（"不接任意可哈希身份的历史"），所以既不会让状态长起来，也不会
          出现"谁把谁顶下去"的错觉——记录面绝不许成为吞掉一张卡片的理由。

        不记历史也**不做淘汰**：一旦淘汰，被忘掉的旧身份会重新"看起来最新"，旧回调
        复活来覆写屏上那张新卡片——那比内存增长坏得多。水位只涨不降（进程内），所以
        "迟到很久的旧终态"照样能被认出来。
        """
        if round_key is None or not isinstance(round_key, int):
            return True
        accepted = self._accepted_round
        if accepted is not None and round_key < accepted:
            return False
        self._accepted_round = round_key
        return True

    def end_loading(self, round_key: object = None) -> bool:
        """显式收口一轮加载（失败 / 取消这类没有终态内容的出口）；返回是否真的收了口。

        面板还没造出来就没有轮次可收——这里**不懒造面板**（不为一条空通知造窗）。

        收口**不走**上面的归属水位：它收的是加载动画那一轮，判据是**身份相等**（面板
        的 ``_LoadingFish`` 只认自己那一轮），旧轮次照样收不掉新一轮还在跑的表。收口
        也不改任何展示内容，所以不需要（也不能）借水位——让一个早被顶下去的轮次去
        推进水位，等于把它重新抬成"最新"。
        """
        panel = self._panel
        if panel is None:
            return False
        return panel.end_loading(round_key)

    def panel(self) -> ScreenResultPanel | None:
        """当前面板（懒造；造不出来返回 None，绝不阻断动作本身）。"""
        if self._panel is not None:
            return self._panel
        factory = self._panel_factory
        if factory is None:
            return None
        # 主题只在造面板那一刻定（面板是复用件，一次会话里不换色）；``None``
        # 表示"跟着宿主调色板现判"，由面板自己 resolve。
        style = {} if self._style is None else {"style": self._style}
        try:
            self._panel = factory(self._panel_parent, **style)
        except Exception:
            log.exception("快速识屏：结果浮层创建失败")
            return None
        return self._panel

    def island_widget(self):
        """可达且可见的岛（岛可能被关掉/重建，所以每次都现取）。"""
        island = self._island
        if callable(island):
            try:
                island = island()
            except Exception:
                return None
        if island is None:
            return None
        try:
            import shiboken6

            if not shiboken6.isValid(island):
                return None
        except TypeError:  # 非 Qt 对象（测试替身）
            pass
        except Exception:
            return None
        visible = getattr(island, "isVisible", None)
        if callable(visible) and not visible():
            return None
        return island

    # ------------------------------------------------------------ 内部
    def _notify(self, text: str, kind: str) -> None:
        island = self.island_widget()
        if island is not None:
            try:
                island.set_last_message(text)
                island.notify_event(kind)
                return
            except Exception:
                log.debug("快速识屏：灵动岛展示失败，回落气泡", exc_info=True)
        if self._bubble is not None:
            try:
                self._bubble(text, duration_ms=6000)
            except Exception:
                log.debug("快速识屏：气泡展示失败", exc_info=True)


# ---------------------------------------------------------------- 编排


class ScreenCaptureActions(QObject):
    """截图动作的编排宿主（一次一条，宿主长期持有）。

    菜单只负责"开始框选"（``run()``）；具体做什么由覆盖层动作条在松手后决定，
    经 ``controller.captured`` 的第三个参数带回来分发（``_on_captured``）。

    依赖全部可注入（``controller`` / ``ocr`` / ``translator`` / ``ask_dialog`` /
    ``presenter`` / ``watcher``），默认走真实实现；测试用假件即可在 offscreen 下
    把四条链路与所有出口钉死。

    ``watcher`` 传的是**取用回调的原料**：注入替身时它被包成"每次都返回这一个"的
    回调，不注入时用的是"每次从宿主现取"（见 :class:`_WatcherGate`）。
    """

    result_ready = Signal(object)     # ScreenResult
    failed = Signal(str)              # 采集或引擎失败理由

    _stage_done = Signal(int, object, object)   # 私有：后台线程 → GUI（代号, 值, 错误）
    _retranslate_done = Signal(int, object, object)   # 重翻独立槽（见 _submit 的 channel）

    def __init__(self, host=None, *, controller=None, ocr=None, translator=None,
                 ask_dialog=None, presenter=None, watcher=None, release=None,
                 run_inline: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._host = host
        self._config = _host_config(host)
        self._ocr = ocr
        self._translator = translator
        self._ask_dialog = ask_dialog
        self._release = release
        self._run_inline = bool(run_inline)
        self._watcher_gate = _WatcherGate(
            functools.partial(_host_watcher, host) if watcher is None else (lambda: watcher)
        )
        self._presenter = presenter if presenter is not None else self._default_presenter()
        if controller is not None:
            self._controller = controller
        else:
            # 能力闸门从这里注入覆盖层：翻译/问答要聊天配置（没配置就置灰写原因，
            # 别让用户点完才炸——盲审 m10）。OCR/复制是纯本地，永不置灰。
            chat_ok = self._chat_capable()
            caps = {"translate": chat_ok, "ask": chat_ok}
            self._controller = ScreenCaptureController(
                self,
                overlay_factory=lambda frame, screen: SelectionOverlay(
                    frame, screen, capabilities=caps),
            )
        self._busy = False
        self._pending: tuple[int, Callable, Callable] | None = None
        # 后台任务的代号：**回来只认当代**——重翻与新一轮采集共用一条信号通道，
        # 不带代号的话先回来的旧结果会拿走别人的回调（盲审 M1：译文被当成 OCR
        # 结果展示、OCR 子进程被误杀、真结果被静默丢）。
        self._stage_seq = 0
        # 加载轮次的身份：一次用户操作一个（见 ``result_panel._LoadingFish``）。
        # 采集链路与重翻链路**各有各的**：共用一块面板，但收口只许收自己那一轮
        # （重翻的收口不许把采集还在跑的轮连动画一起收掉，反之亦然）。
        self._op_seq = 0
        self._capture_round: object = None     # 采集会话（busy 那条链路）的轮次身份
        self._retranslate_round: object = None  # 重翻链路的轮次身份
        # 重翻的独立槽位：与采集链路的 ``_pending`` 互不覆盖（盲审 M1——重翻挤占
        # 采集槽会把在跑的 OCR 结果静默吞掉并把会话卡死在 busy）。
        self._pending_retranslate: tuple[int, Callable, Callable] | None = None
        self._last_ocr_source = ""    # 最近一次翻译链路的原文（语言行切换直接重翻）
        #: 与 ``_last_ocr_source`` **同源**的那次操作的上下文快照：重翻吃的是"这段原文
        #: 那次采集"的图与选区，不取"最近一次"（那时可能已经换成用户新截的那张了）。
        self._last_source_context: _OpContext | None = None
        self._stage_done.connect(self._on_stage_done)
        self._retranslate_done.connect(self._on_retranslate_done)
        # 两处连接的**判据**不同，别混着读（2026-10-13 交叉审查 nit 要的就是这条区分）：
        #
        # - 上面那条是**自连**（发出者与接收者是同一个对象）：不存在 ``宿主 ↔ 发出者``
        #   引用环，直接连绑定方法没有代价，也不需要弱引用；
        # - 下面三条是**跨对象**（controller → actions）：controller 被本对象强持有，
        #   直连绑定方法就形成 ``controller ↔ actions`` 环，把 Qt 对象的销毁推到解释器
        #   退出期（那条路径实测以原生崩溃收场）。所以跨对象一律走 ``_weak_slot``，
        #   不自作聪明地分"哪条安全"。
        self._controller.captured.connect(_weak_slot(self, "_on_captured"))
        self._controller.cancelled.connect(_weak_slot(self, "_on_cancelled"))
        self._controller.failed.connect(_weak_slot(self, "_on_failed"))

    # ------------------------------------------------------------ 只读视图

    @property
    def busy(self) -> bool:
        """是否正在一次截图动作里（采集中或引擎/网络在跑）。"""
        return self._busy

    # ---------------------------------------------------------------- 入口

    def run(self, *, screen=None, keep_pet: bool = False) -> bool:
        """开始一次框选；已在采集中或 host 不支持时返回 False。

        动作不在入口定死：松手后覆盖层动作条给出动作，随 ``captured`` 带回。
        ``screen`` = 本次显式选择的屏；``None`` = 光标所在屏。
        ``keep_pet`` = 不隐藏桌宠窗口，让桌宠进入冻结帧。
        """
        if self._busy:
            log.debug("快速识屏：上一轮还没收口，忽略本次触发")
            self.status("上一张还没看完呢…")
            return False

        self._busy = True
        # 一次用户操作＝一个轮次身份：窗口/面板都是复用件，"这次操作"只能由这里划出
        # 边界（见 ``_show_progress`` 与 ``_finish``）。
        self._op_seq += 1
        self._capture_round = self._op_seq
        self._watcher_gate.pause()
        try:
            started = bool(self._controller.start(screen=screen, keep_pet=keep_pet))
        except Exception as exc:  # 采集宿主千奇百怪，闭环不许因此塌掉
            log.exception("快速识屏：启动采集失败")
            self._watcher_gate.restore()   # 隔离必须对称：异常出口也要把 watcher 放回去
            self._finish()
            self.status(f"没能开始截图（{exc}）")
            self.failed.emit(str(exc))
            return False
        if not started:
            # 采集没起来：覆盖层/抓帧失败时 controller 会先发 failed（那条出口已经把
            # 理由给到用户了，busy 也收了），这里只兜"纯 false 没回调"的情况。
            if self._busy:
                self._watcher_gate.restore()
                self._finish()
                self.status("没能开始截图，稍后再试")
            return False
        return True

    # ------------------------------------------------------------ 采集回调

    def _on_captured(self, image, selection, action) -> None:
        if not self._busy:
            log.debug("快速识屏：忽略迟到的采集结果")
            return
        # 采集期到这一刻结束：窗口已还原、冻结帧已裁出，watcher 可以回岗。
        self._watcher_gate.restore()
        # 上下文在这一刻定下来（图 / 选区 / 本轮的轮次身份），随这次操作的回调走完全程：
        # 用户在等待期间又截一张，也不会串到这一张上来（见 _OpContext）。
        context = _OpContext(round_key=self._capture_round, image=image,
                             selection=selection)
        if action == ACTION_OCR:
            self._run_ocr(context)
        elif action == ACTION_TRANSLATE:
            self._run_translate(context)
        elif action == ACTION_COPY:
            self._run_copy(context)
        else:
            self._run_ask(context)

    def _on_cancelled(self, reason) -> None:
        if not self._busy:
            return
        self._watcher_gate.restore()
        self._finish()
        log.debug("快速识屏：用户取消 (%s)", reason)
        self.status("已取消")

    def _on_failed(self, reason) -> None:
        if not self._busy:
            return
        self._watcher_gate.restore()
        self._finish()
        text = SCREEN_RECORDING_PERMISSION_REASON if str(reason) == "screen-permission" else str(reason)
        self.status(f"这张没截成（{text}）")
        self.failed.emit(str(reason))

    # ---------------------------------------------------------------- 四条链路

    def _run_copy(self, context: _OpContext) -> None:
        """复制图像：纯本地，裁出的选区图进系统剪贴板。

        剪贴板同时塞 PNG 字节与 Qt 图像：``setImage`` 单写在 Windows 上只落
        ``CF_DIB`` 位图（实测 ``mimeData().formats() == ['application/x-qt-image']``），
        Electron 系应用（Kimi/微信/浏览器）对 DIB 的转换常出软化/偏色；显式带一份
        ``image/png`` 让它们优先取无损 PNG——PixPin 等工具粘出来清晰靠的就是这条。
        """
        image = context.image
        self._finish()
        try:
            from PySide6.QtCore import QBuffer, QIODevice
            from PySide6.QtWidgets import QApplication

            clipboard = QApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("剪贴板不可用")
            from PySide6.QtCore import QMimeData

            mime = QMimeData()
            mime.setImageData(image)
            buffer = QBuffer()
            if buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                if image.save(buffer, "PNG"):
                    mime.setData("image/png", bytes(buffer.data()))
                buffer.close()
            clipboard.setMimeData(mime)
        except Exception as exc:
            log.exception("快速识屏：写入剪贴板失败")
            self.status(f"没能复制（{exc}）")
            self.failed.emit(str(exc))
            return
        self.status("已复制到剪贴板")

    def _run_ocr(self, context: _OpContext) -> None:
        engine = self._ocr if self._ocr is not None else resolve_ocr_engine()
        if engine is None:
            self._finish()
            self._emit_result(ScreenResult(
                title=TITLE_OCR, status=OCR_PENDING_REASON, force_panel=True,
            ), context)
            return
        # 秒级等待不能白屏：先把卡片弹出来摆着"正在识别"（实机反馈：中间什么都没
        # 有像在卡死）。过程态只占位、不发 result_ready、不收 busy。
        self._show_progress(ScreenResult(
            title=TITLE_OCR, force_panel=True, loading=True,
            image=context.image,
        ), context)
        self._submit(lambda: engine(context.image),
                     lambda text: self._on_ocr_done(text, context),
                     functools.partial(self._on_stage_error, stage="本地识别"))

    def _on_ocr_done(self, text, context: _OpContext) -> None:
        self._finish()
        value = str(text or "").strip()
        if not value:
            self._emit_result(ScreenResult(title=TITLE_OCR, status="没有识别到文字"),
                              context)
            return
        self._emit_result(ScreenResult(
            title=TITLE_OCR, primary=value, force_panel=True,
            status=f"本地识别 · {len(value)} 字",
            image=context.image,
        ), context)

    def _run_translate(self, context: _OpContext) -> None:
        ocr = self._ocr if self._ocr is not None else resolve_ocr_engine()
        translator = self._translator if self._translator is not None else self._build_translator()
        self._show_progress(ScreenResult(
            title=TITLE_TRANSLATE, force_panel=True, loading=True,
            image=context.image,
        ), context)

        def work_ocr() -> str:
            return str(ocr(context.image) or "").strip() if ocr is not None else ""

        self._submit(
            work_ocr,
            lambda source: self._on_translate_source(source, translator, context),
            functools.partial(self._on_stage_error, stage="翻译"),
        )

    def _on_translate_source(self, source: str, translator, context: _OpContext) -> None:
        """原文到手：先摆上面板（等待不再白屏），再发翻译请求。"""
        source = str(source or "").strip()
        if not source:
            self._finish()
            self._emit_result(ScreenResult(
                title=TITLE_TRANSLATE, status="没有识别到文字，无可翻译",
                force_panel=True, image=context.image,
            ), context)
            return
        # 原文与它那次采集的上下文一起留档：语言行重翻吃的是**这段原文**的图与选区
        self._last_ocr_source = source   # 语言行切换时拿它直接重翻，不重跑 OCR
        self._last_source_context = context
        if translator is None:
            self._finish()
            self._emit_result(ScreenResult(
                title=TITLE_TRANSLATE, primary=source, force_panel=True,
                status=TRANSLATION_UNAVAILABLE_REASON, primary_label="原文",
                image=context.image,
            ), context)
            return
        self._show_progress(ScreenResult(
            title=TITLE_TRANSLATE, primary=source, primary_label="原文",
            force_panel=True, loading=True, image=context.image,
        ), context)
        self._submit(
            lambda: str(translator(context.image, source) or "").strip(),
            lambda translated: self._on_translate_done(
                (source, translated), translator=True, context=context),
            functools.partial(self._on_stage_error, stage="翻译"),
        )

    def _retranslate(self, target: str) -> None:
        """语言行切换：用缓存的原文直接重翻（OCR 不重跑），面板原地更新。

        注入优先：测试/宿主给了 ``translator`` 替身就走它（``translator(None,
        source)``——替身不接 target 语义），缺省走 ``translate_text`` 的 target 路。

        上下文取 ``_last_source_context``——**这段原文那次采集**的图与选区，不取
        "最近一次"状态：用户在重翻路上又截一张时，"最近一次"已经是新那张的了，
        用它就会把 A 的原文配到 B 的图上（跨截图返修 P1）。
        """
        source = self._last_ocr_source
        if not source or self._busy or self._config is None:
            return
        # 重翻也是一次独立的用户操作＝独立的一轮：它自己抽、自己收（收口只认这一轮的
        # 身份，绝不碰采集链路还在跑的那一轮）。
        self._op_seq += 1
        self._retranslate_round = self._op_seq
        origin = self._last_source_context
        context = _OpContext(
            round_key=self._retranslate_round,
            image=None if origin is None else origin.image,
            selection=None if origin is None else origin.selection,
        )
        self._show_progress(ScreenResult(
            title=TITLE_TRANSLATE, primary=source, primary_label="原文",
            force_panel=True, loading=True, image=context.image,
        ), context)
        config = self._config
        injected = self._translator
        if injected is not None:
            work = lambda: injected(None, source)   # noqa: E731 - 闭包取当前值
        else:
            from .translate import translate_text

            work = lambda: translate_text(source, config, target=target)  # noqa: E731
        self._submit(
            work,
            lambda translated: self._on_translate_done(
                (source, str(translated or "").strip()),
                translator=True, from_retranslate=True, context=context),
            # 重翻失败也别走 _on_stage_error 的 _finish（同上：不占别人的会话）——
            # 直接在面板上把原文摆回来 + 一句人话。上下文必须是**重翻这一轮**的：
            # 带采集链路的身份会把采集还在跑的那一轮连图一起收掉（终态也是收口），
            # 而现读"最近一次"的图则会把别人的图摆到这段原文上。
            lambda message: self._emit_result(ScreenResult(
                title=TITLE_TRANSLATE, primary=source, primary_label="原文",
                status=f"翻译失败：{message}", force_panel=True,
                image=context.image, language_selector=True,
            ), context),
            channel="retranslate",
        )

    def _on_translate_done(self, value, *, translator: bool, context: _OpContext,
                           from_retranslate: bool = False) -> None:
        # 重翻不占采集会话：不调 _finish（那会清掉别人正忙着的 busy 位、还会把
        # 正在跑的 OCR 子进程当"本链路的引擎"杀掉——盲审 M1 的时序二）。
        if not from_retranslate:
            self._finish()
        source, translated = value if isinstance(value, tuple) else (str(value or ""), "")
        source = str(source or "").strip()
        translated = str(translated or "").strip()
        if not source:
            status = "没有识别到文字，无可翻译"
        elif translator and not translated:
            status = "没有拿到译文"
        elif translator:
            status = f"已翻译 · {len(translated)} 字"
        else:
            status = TRANSLATION_UNAVAILABLE_REASON
        self._emit_result(ScreenResult(
            title=TITLE_TRANSLATE, primary=source, secondary=translated,
            force_panel=True, status=status, primary_label="原文" if translated else "",
            image=context.image,
            language_selector=bool(translated),
        ), context)

    def _run_ask(self, context: _OpContext) -> None:
        """截图问答：打开问答浮窗（图进输入栏），发送与否由用户在浮窗里决定。

        采集会话到这里就收口了——浮窗是独立生命周期的对话面，不占 busy 位；
        真正发请求的是浮窗自己（用户点「发送」那一刻），见 ``ask_dialog.py``。
        """
        self._finish()
        dialog = self._get_ask_dialog()
        if dialog is None:
            self._emit_result(ScreenResult(
                title=TITLE_ASK, status="AI 对话通道不可用（未配置或已关闭）",
                force_panel=True,
            ), context)
            return
        try:
            dialog.start_with_image(context.image, below=context.selection)
        except Exception as exc:
            log.exception("快速识屏：问答浮窗打开失败")
            self._emit_result(ScreenResult(
                title=TITLE_ASK, status=f"问答窗没能打开（{exc}）", force_panel=True,
            ), context)

    def _on_stage_error(self, message, *, stage: str) -> None:
        self._finish()
        self.status(f"{stage}失败：{message}")
        self.failed.emit(str(message))

    # ---------------------------------------------------------------- 线程纪律

    def _submit(self, work: Callable, on_ok: Callable, on_err: Callable,
                *, channel: str = "main") -> None:
        """把可能阻塞的一步放到后台线程，完成后经信号回到 GUI 线程。

        ``channel="retranslate"`` 走重翻的独立槽位（信号 ``_retranslate_done``、
        槽位 ``_pending_retranslate``）：语言行重翻与采集链路互不占槽。
        ``run_inline`` 是给测试用的直通开关（同步跑、异常同样走到 ``on_err``）：
        编排本身与线程无关，两条路径共用同一组回调。
        """
        if self._run_inline:
            try:
                value = work()
            except Exception as exc:
                log.exception("快速识屏：本步失败")
                on_err(str(exc) or type(exc).__name__)
            else:
                on_ok(value)
            return
        self._stage_seq += 1
        seq = self._stage_seq
        if channel == "retranslate":
            self._pending_retranslate = (seq, on_ok, on_err)
        else:
            self._pending = (seq, on_ok, on_err)
        threading.Thread(
            target=self._stage_worker, args=(seq, work, channel), daemon=True,
            name="pet-screen-capture",
        ).start()

    def _stage_worker(self, seq: int, work: Callable, channel: str) -> None:
        signal = self._retranslate_done if channel == "retranslate" else self._stage_done
        try:
            value = work()
        except Exception as exc:
            log.exception("快速识屏：本步失败")
            signal.emit(seq, None, str(exc) or type(exc).__name__)
            return
        signal.emit(seq, value, None)

    def _on_stage_done(self, seq, value, error) -> None:
        pending = self._pending
        if pending is None:
            return
        pending_seq, on_ok, on_err = pending
        if seq != pending_seq:
            # 迟到/串台的结果：槽里已经是别人（新一轮采集），丢弃
            log.debug("快速识屏：丢弃串台的后台结果 (seq=%s 槽位=%s)", seq, pending_seq)
            return
        self._pending = None
        if error is not None:
            on_err(str(error))
            return
        on_ok(value)

    def _on_retranslate_done(self, seq, value, error) -> None:
        """重翻槽的收口（与 ``_on_stage_done`` 同构，互不占槽）。"""
        pending = self._pending_retranslate
        if pending is None:
            return
        pending_seq, on_ok, on_err = pending
        if seq != pending_seq:
            log.debug("快速识屏：丢弃串台的重翻结果 (seq=%s 槽位=%s)", seq, pending_seq)
            return
        self._pending_retranslate = None
        if error is not None:
            on_err(str(error))
            return
        on_ok(value)

    # ---------------------------------------------------------------- 收口

    def _finish(self) -> None:
        self._busy = False
        self._pending = None
        # 会话收口＝本轮加载也收口：失败/取消这种出口只有一个"失败理由"、没有终态
        # 结果可摆到面板上，不显式收口的话加载卡会一直转，下一独立操作还会接着上一轮
        # 的脸（抽签失灵）。收口带**本会话的轮次身份**——重翻那一轮不是我的，收不得
        # （它自己也走不到这里：重翻链路刻意不调本方法，见 _on_translate_done）。
        self._presenter.end_loading(self._capture_round)
        # 「用完就丢」的调用点：本方法是**所有**出口的唯一收口（成功交付 / 取消 /
        # 失败 / 引擎缺席），所以这里放一次就覆盖了全部终态，不必在每条链路上
        # 各写一遍（漏一处就是"识别子进程在后台一直活着"）。
        self._release_engine()

    def _release_engine(self) -> None:
        """清扫本地 OCR（后台线程；详见 :meth:`_release_worker`）。"""
        release = self._release
        if release is None:
            release = resolve_ocr_release()
            if release is None:
                return  # 引擎模块没有释放入口（旧版）：不还而已，不影响已交付的结果
        if self._run_inline:
            self._release_worker(release)
            return
        threading.Thread(target=self._release_worker, args=(release,), daemon=True,
                         name="pet-screen-capture-unload").start()

    @staticmethod
    def _release_worker(release: Callable) -> None:
        """在后台线程里执行释放。

        不占 GUI 线程的理由是**实测**的：这一下要做的是"结束一个可能还在跑模型的
        进程"，实测 2~10 ms 量级（本机，见
        ``.scratch/screen-capture/probe_ocr_subprocess.txt``），而它正好落在
        "结果浮层即将弹出"这一刻——放 GUI 线程就是一次可见的掉帧。释放失败
        只记日志：图已经拿到了，清扫失败不该变成用户可见的错误。
        """
        try:
            release()
        except Exception:
            log.exception("快速识屏：释放本地 OCR 资源失败（不影响本次结果）")

    def _emit_result(self, result: ScreenResult, context: _OpContext) -> None:
        """终态结果落地。

        ``context`` 是这条结果所属操作的上下文快照（**发起那一刻**定下的轮次身份、
        图、选区）。终态是一次收口，带错身份会把另一条链路还在跑的那轮连图一起收掉；
        现读"最近一次"的图/选区则会把别人的图摆到这段文字上（跨截图 P1）。所以这里
        不给缺省值——每条终态都必须说清自己属于哪一次操作。
        """
        self._presenter.show(result, below=context.selection, round_key=context.round_key)
        self.result_ready.emit(result)

    def _show_progress(self, result: ScreenResult, context: _OpContext) -> None:
        """过程态也上面板（识别/翻译这种秒级等待不许白屏干等）。

        与 ``_emit_result`` 的区别：不占 busy、不发 ``result_ready``（不是结果，
        是"正在跑"的占位卡片，最终结果来了原地换内容——面板是复用件）。

        ``context`` 同样是**发起时**捕获的那一份：**同一次操作的两张卡片同身份**
        （``_run_translate`` 先"正在识别"再"正在翻译"），换身份才是新的一轮。
        """
        self._presenter.show(result, below=context.selection, round_key=context.round_key)

    def status(self, text: str) -> None:
        self._presenter.status(text)

    def _continue_ask(self, image, recognized_text: str, below=None) -> None:
        """结果面板的「继续提问…」：结果面板关掉，图（+ 已识别文字）带进问答浮窗。

        ``below`` 是**这张卡片自己**的选区（面板随卡片交下来的，见
        ``result_panel.ScreenResultPanel._continue_ask``）：它必须随卡片走，不能回头
        问"最近一次采集的选区"——那张卡的图与文字都可能来自更早的一次操作。
        """
        # 这一次点击也是一次用户操作：它的失败卡片要占一个**新**的轮次身份——借上一次
        # 采集的身份会被展示归属判成旧轮次（用户点了却什么都看不到）。
        self._op_seq += 1
        context = _OpContext(round_key=self._op_seq, image=image, selection=below)
        panel = self._presenter.panel()
        if panel is not None:
            panel.close_panel()   # 实机反馈：续聊开始后结果卡不该还挂着
        dialog = self._get_ask_dialog()
        if dialog is None:
            self._emit_result(ScreenResult(
                title=TITLE_ASK, status="AI 对话通道不可用（未配置或已关闭）",
                force_panel=True,
            ), context)
            return
        try:
            dialog.start_with_image(image, context_text=recognized_text, below=below)
        except Exception as exc:
            log.exception("快速识屏：问答浮窗打开失败")
            self._emit_result(ScreenResult(
                title=TITLE_ASK, status=f"问答窗没能打开（{exc}）", force_panel=True,
            ), context)

    # ---------------------------------------------------------------- 依赖

    def _default_presenter(self) -> ScreenCapturePresenter:
        host = self._host
        # 岛/气泡现取现用（岛会被重建、气泡面可能迟到）：构造期只挂取用回调。
        # 主题（结果浮层的浅色/深色）也从宿主现取：与菜单同一个配置键，读不到就是
        # "跟着宿主调色板判"（None），不再让浮层自己写死深色。
        return ScreenCapturePresenter(
            island=lambda: _host_island(host),
            bubble=_host_bubble(host),
            panel_factory=ScreenResultPanel,
            panel_parent=_host_dialog_parent(host),
            style=_host_theme(host),
            continue_ask=self._continue_ask,
            language_change=self._retranslate,
        )

    def _chat_capable(self) -> bool:
        """宿主有没有可用的聊天配置（翻译/问答的能力闸门；读配置面，不探网络）。"""
        config = self._config
        if config is None:
            return False
        try:
            settings = config.chat_settings()
            if not getattr(settings, "enabled", True):
                return False
            return getattr(settings, "active_config", None) is not None
        except Exception:
            log.debug("快速识屏：聊天能力判定失败，按不可用处理", exc_info=True)
            return False

    def _build_translator(self):
        """文本翻译器：OCR 原文 → 用户配置的聊天模型（纯文本调用，不碰视觉端点）。

        配置面不可用返回 None（结果浮层如实报"需要配置 AI 服务"）。
        """
        if self._config is None:
            return None
        try:
            from .translate import translate_text
        except Exception:
            log.exception("快速识屏：翻译模块装载失败")
            return None
        config = self._config
        return lambda _image, source: translate_text(source, config)

    def _get_ask_dialog(self):
        """问答浮窗（懒造 + 复用）：配置面不可用返回 None（如实上报，不静默）。"""
        dialog = self._ask_dialog
        if dialog is not None:
            return dialog
        if self._config is None:
            return None
        try:
            from .ask_dialog import ScreenAskDialog

            dialog = ScreenAskDialog(
                self._config,
                parent=_host_dialog_parent(self._host),
                style=_host_theme(self._host),
            )
        except Exception:
            log.exception("快速识屏：问答浮窗创建失败")
            return None
        self._ask_dialog = dialog
        return dialog


# ---------------------------------------------------------------- 宿主接线


def actions_for(host) -> ScreenCaptureActions:
    """按宿主取（并缓存）编排件：宿主长期持有它，采集会话才长得过菜单寿命。

    缓存落在宿主自己身上（``_screen_capture_actions``）：右键菜单是每次重建的
    对象，把编排件挂在菜单对象上会在菜单一关就丢——而一次截图会话横跨菜单关闭。
    """
    existing = getattr(host, "_screen_capture_actions", None)
    if isinstance(existing, ScreenCaptureActions):
        return existing
    actions = ScreenCaptureActions(host, parent=host if isinstance(host, QObject) else None)
    try:
        host._screen_capture_actions = actions
    except Exception:
        log.debug("快速识屏：编排件无法挂在宿主上，改为局部持有", exc_info=True)
    return actions
