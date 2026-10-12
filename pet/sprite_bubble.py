# -*- coding: utf-8 -*-
"""sprite 气泡跟随（4.1c）：真实 PetSpeechBubble 独立小窗跟随 sprite。

自 demo（.scratch/single-overlay-window/run_overlay_demo.py Phase 3b）
迁入 pet/ 的产品化版本：跟随源 = overlay.add_position_listener（sprite
不产生 moveEvent）；锚点 = sprite 身体框换算全局（V-2 口径——整画布
rect 会把气泡锚到透明边上沿）；30Hz 跟随节流 + 尾部补发（V-5：tick 级
fanout 游走期每 tick 一次 SetWindowPos ≈1.5ms 的 WM 移动税不能再交）。

构造/跟随/显隐全链路 try/except 静默降级——气泡是外围装饰，失败绝不
崩主链路。

4.3 后半（本刀）：气泡主体可点 → 快速对话入口。对齐旧路径
``window.py:3419-3447`` 的 ``_on_speech_bubble_clicked`` 语义——只有普通
无按钮气泡可点开对话栏，交互（buttons）/告警气泡的主体点击是 no-op。
点击能力复用既有 ``PetSpeechBubble`` 公开面（``clicked`` 信号 +
``set_interactive`` + ``show_text``），不新造控件、不复制气泡实现。

自言自语配图（本刀）：``show_image`` 补齐 ``PetSpeechBubble.show_image``
的锚点/缩放面——旧架构唯一宿主 PetWindow 在 overlay 拓扑下不构造，没有
这一步配图自言自语（``self_talk_image_chance``）在 sprite 世界无路可走。
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QPoint, QRect, QTimer

from .speech_bubble import PetSpeechBubble

logger = logging.getLogger(__name__)

_FOLLOW_INTERVAL = 1.0 / 30.0  # 30Hz 跟随上限


def sprite_anchor_rect_global(sprite, overlay_origin: QPoint) -> QRect:
    """气泡锚点：sprite 身体框（overlay 局部）换算到全局屏幕坐标。

    口径平移自 window_placement.bubble_anchor_rect（旧架构锚点是"host 的
    可见内容矩形"）；sprite 侧的同口径是 body_rect()（body_box×scale；
    未声明 body_box 回退全画布 rect()），直接加 overlay 原点即得全局
    锚点（overlay 铺满屏幕、自身不移动，原点即屏幕全局原点）。
    """
    rect = sprite.body_rect() if hasattr(sprite, "body_rect") else sprite.rect()
    return QRect(overlay_origin + rect.topLeft(), rect.size())


class SpriteBubbleFollower:
    """一只 sprite 的气泡跟随（真实 PetSpeechBubble 独立小窗）。

    ``on_clicked(sprite)``：气泡主体点击回调（快速对话入口）；不给 = 气泡
    保持全鼠标穿透（无聊天变体/未接线，静默降级）。

    对外面：本对象即位置监听者（``__call__``），并对 overlay 暴露可选的
    关键帧钩子 ``follow_now()``（拖拽起止立即同步，不看 30Hz 节流），对壳层/
    菜单面暴露 ``reflow()``/``refresh_anchor()``（缩放/体框变更后显式刷新）。
    """

    def __init__(self, overlay, sprite, *, style_id: str = "classic_top",
                 on_clicked=None) -> None:
        self._overlay = overlay
        self._sprite = sprite
        self._origin = overlay.geometry().topLeft()
        self.on_clicked = on_clicked
        self.bubble: PetSpeechBubble | None = None
        self._last_follow = 0.0
        self._follow_pending = False
        # 收口后**不可复用**（close 的契约，见 close 文档）：为 True 时
        # _on_sprite_moved/_flush_follow/set_origin/follow_now/refresh_anchor/
        # reflow 一律早退，bubble 恒为 None。
        self._closed = False
        # 父对象 = 所在 overlay（长寿命）：信号连接是 Qt 侧对 self._flush_follow
        # 的**强引用**，只有断开/删除这个 timer 才能让旧 follower 被回收
        # （overlay 存活期间换跟随器——如主宠提升——否则会逐次留下旧对象）。
        self._follow_timer = QTimer(overlay)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(int(_FOLLOW_INTERVAL * 1000))
        self._follow_timer.timeout.connect(self._flush_follow)
        try:
            self.bubble = PetSpeechBubble(style_id=style_id)
            self.bubble.clicked.connect(self._on_bubble_clicked)
            # 监听对象 = 本跟随器自身（可调用对象，见 __call__）：overlay 的
            # 拖拽关键帧钩子要按 getattr 从监听方取可选方法 ``follow_now``，
            # bound method 上挂不住属性，故注册对象而非 self._on_sprite_moved。
            overlay.add_position_listener(sprite, self)
        except Exception:
            self.bubble = None  # 构造失败 = 无气泡，静默降级

    def __call__(self, sprite) -> None:
        """位置监听入口（overlay 以 ``cb(sprite)`` 调用）＝ ``_on_sprite_moved``。"""
        self._on_sprite_moved(sprite)

    def anchor(self) -> QRect:
        # The WM can move a native window after construction; use its real origin.
        if hasattr(self._overlay, "mapToGlobal"):
            origin = self._overlay.mapToGlobal(QPoint(0, 0))
        else:
            origin = self._origin
        return sprite_anchor_rect_global(self._sprite, origin)

    def _on_sprite_moved(self, sprite) -> None:
        if self._closed or self.bubble is None:
            return
        now = time.monotonic()
        if now - self._last_follow >= _FOLLOW_INTERVAL:
            self._last_follow = now
            self._follow_pending = False
            try:
                self.bubble.reposition(self.anchor())
            except Exception:
                pass
        else:
            self._follow_pending = True
            if not self._follow_timer.isActive():
                self._follow_timer.start()

    def _flush_follow(self) -> None:
        """节流窗口结束后补发最后一帧位置（防气泡停在半途）。"""
        if self._closed or not self._follow_pending or self.bubble is None:
            return
        self._follow_pending = False
        self._last_follow = time.monotonic()
        try:
            self.bubble.reposition(self.anchor())
        except Exception:
            pass

    def _reposition_if_visible(self) -> bool:
        """按当前锚点重定位一次（仅可见气泡）；返回是否真的发起了重定位。

        与 ``PetSpeechBubble.reposition`` 同一口径（它对隐态本身 no-op），这里
        提前拦下是为了让调用方能区分"同步过了"与"根本没显示"。
        """
        bubble = self.bubble
        if bubble is None:
            return False
        try:
            if not bubble.isVisible():
                return False
            bubble.reposition(self.anchor())
            return True
        except Exception:
            logger.debug("overlay: 气泡强制重定位失败", exc_info=True)
            return False

    def follow_now(self) -> None:
        """跳过 30Hz 节流窗口，立即按当前锚点同步一次（拖拽起止关键帧）。

        旧机 ``window.py:3330``（拖拽第一帧）/``:3401``（松手终位）各强制
        ``_position_sync_now()`` 一次；新版位置通知只在 tick 扇出里发，抓起的
        首帧与松手那一拍若不做动作，气泡要等一次 tick + 可能一次节流补发。
        overlay 在 ``_commit_drag_if_threshold_crossed``/``_finish_grab`` 处经
        位置监听通道调用本方法（``getattr(cb, "follow_now")``）。

        纪律：**不写节流时钟**（``_last_follow`` 不动）——强制同步不得把 30Hz
        窗口重新盖上，否则起步拍反而把随后那一拍的真实拖拽位移推迟 33ms；
        同时取消防抖窗口里待补发的那一拍（本方法已给出最新位置）。气泡不可见
        /已关闭 = 无操作（与 ``set_origin`` 同款）。
        """
        if self._closed:
            return
        self._follow_pending = False
        timer = getattr(self, "_follow_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._reposition_if_visible()

    def refresh_anchor(self) -> None:
        """按当前锚点重定位一次（体框/角色变更后的显式刷新）。

        切角色换 ``body_box`` 后 sprite 的 rect 可能没变，而扇出只在 old != new
        时通知（``overlay_window.tick_advance``）——位置监听不会触发，调用方
        （``overlay_shell.switch_character``）须显式调本方法。与 ``follow_now``
        的差别只在语义：本方法不属于拖拽关键帧，不碰待补发状态。隐态无操作。
        """
        if self._closed:
            return
        self._reposition_if_visible()

    def reflow(self) -> bool:
        """缩放变更后按**新** ``pet_scale`` 重排可见气泡；返回是否真的重排。

        等价旧机 ``window.py:1000-1004``（可见气泡在缩放变更时
        ``bubble.reflow(新锚点, pet_scale=新scale)``）。调用点在壳层/菜单侧
        （``sprite_menu_facade`` 改大小、``overlay_shell.switch_character``
        换角色）：sprite 的 scale 变化只发脏区、不发位置通知，本层无从自行
        感知。气泡不可见/底层不支持 reflow/已关闭 = 无操作。
        """
        if self._closed:
            return False
        bubble = self.bubble
        if bubble is None:
            return False
        reflow = getattr(bubble, "reflow", None)
        if not callable(reflow):
            return False
        try:
            if not bubble.isVisible():
                return False
            reflow(self.anchor(), pet_scale=getattr(self._sprite, "scale", None))
            return True
        except Exception:
            logger.debug("overlay: 气泡按新缩放重排失败", exc_info=True)
            return False

    def say(self, text: str, duration_ms: int = 3200, *, subtitle: str = "") -> bool:
        """播一句气泡文本；气泡不可用/文案为空返回 False（静默降级）。"""
        return self.show(text, duration_ms, subtitle=subtitle)

    def show(self, text: str, duration_ms: int = 3200, *, subtitle: str = "",
             sticky: bool = False, buttons: list | None = None,
             title_first: bool = False, width_locked: bool = False,
             agent_title: str = "", agent_status: str = "", agent_final: bool = False) -> bool:
        """按 ``PetWindow.show_bubble`` 的形参面呈现一句气泡。

        ``sticky`` / ``buttons`` 原样透传给 ``PetSpeechBubble.show_text``——
        提醒/审批气泡的交互语义与旧路径同源（气泡控件自身负责按钮与穿透
        切换），本层只补锚点与降级。返回是否真的展示。
        """
        text = str(text or "").strip()
        if self.bubble is None or (not text and not agent_title):
            return False
        agent_options = (
            dict(agent_title=agent_title, agent_status=agent_status, agent_final=agent_final)
            if agent_title else {}
        )
        try:
            self.bubble.show_text(
                text, self.anchor(), duration_ms,
                subtitle=str(subtitle or ""),
                sticky=bool(sticky),
                buttons=list(buttons) if buttons else None,
                title_first=bool(title_first),
                width_locked=bool(width_locked),
                pet_scale=getattr(self._sprite, "scale", None), **agent_options)
            return True
        except Exception:
            logger.debug("overlay: 气泡播放失败", exc_info=True)
            return False

    def show_image(self, image_path, duration_ms: int = 3200,
                   image_scale: float = 1.0, pixmap=None) -> bool:
        """播一张配图气泡（配图自言自语；``PetSpeechBubble.show_image`` 形参面）。

        形参对齐 ``speech_bubble.py:960`` 的 ``show_image(path, anchor,
        duration_ms, *, pet_scale, image_scale)``：锚点由跟随器自算（气泡
        锚定 sprite 而非 window 的可见内容矩形），``pet_scale`` 取 sprite
        缩放，故调用方只关心图与显示时长/缩放。图片路径无效、气泡不可用
        或底层抛异常都返回 False——与 ``show`` 同款静默降级，配图失败绝
        不冒泡出异常到自言自语链路。
        """
        if self.bubble is None:
            return False
        try:
            return bool(self.bubble.show_image(
                image_path, self.anchor(), duration_ms,
                pet_scale=getattr(self._sprite, "scale", None),
                image_scale=image_scale, pixmap=pixmap))
        except Exception:
            logger.debug("overlay: 气泡配图播放失败", exc_info=True)
            return False

    def set_interactive(self, on: bool) -> None:
        """气泡是否可点（可点 = 可打开快速对话）。

        不可用时保持 QT 的 ``WA_TransparentForMouseEvents`` 全穿透——气泡
        绝不吞掉桌面上的点击（旧路径 ``_set_speech_bubble_interactive`` 同款）。
        """
        setter = getattr(self.bubble, "set_interactive", None)
        if not callable(setter):
            return
        try:
            setter(bool(on))
        except Exception:
            logger.debug("overlay: 气泡交互态切换失败", exc_info=True)

    def hide(self) -> None:
        """收起当前气泡（气泡不可用时静默）。"""
        hider = getattr(self.bubble, "hide", None)
        if callable(hider):
            try:
                hider()
            except Exception:
                logger.debug("overlay: 气泡收起失败", exc_info=True)

    def _on_bubble_clicked(self) -> None:
        """气泡主体点击 → 快速对话（``window.py:3419`` 语义）。

        交互（按钮）/告警气泡的主体点击必须是 no-op——按钮自身由气泡控件
        处理；以实际展示状态判定，不依赖文案关键词。
        """
        if getattr(self.bubble, "_interactive_active", False):
            return
        callback = self.on_clicked
        if callable(callback):
            callback(self._sprite)

    def set_origin(self, origin: QPoint) -> None:
        """全局原点变化后更新锚点原点（壳层在原点真变时调用；换屏重建同款）。

        原点变了就必须让**在显气泡**跟着走：气泡锚点 = ``body_rect`` + 全局原点，
        而跟随只在 sprite 位移时经位置监听触发——同尺寸平移屏（bounds 不变、
        sprite 局部位置不变）根本不会有位移通知，只存原点会让气泡停在旧全局
        位置直到下次 `show`（用户可见的"气泡不跟随"）。

        纪律：只在气泡**已可见**时重定位一次（``PetSpeechBubble.reposition`` 对
        隐藏态本身是 no-op，这里再判一次以明示"隐态不显示、不动作"）；``_place``
        走 ``animate=False`` 直移、不 show、不碰 ``_hide_timer``，故停留倒计时
        既不重启也不取消；顺手取消防抖窗口里待补发的那一拍，避免同帧双发。
        本方法是离散事件（原点跳变）入口，不进常态 tick。
        """
        if self._closed:
            return
        origin = QPoint(origin)
        if origin == self._origin:
            return
        self._origin = origin
        self._follow_pending = False
        timer = getattr(self, "_follow_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        bubble = self.bubble
        if bubble is None or not bubble.isVisible():
            return
        try:
            bubble.reposition(self.anchor())
        except Exception:
            logger.debug("overlay: 气泡随原点重定位失败", exc_info=True)

    def close(self) -> None:
        """收口本跟随器并**宣告不可复用**（幂等：重复 close 是 no-op）。

        收口三件事（缺一即残留）：
        1. 摘位置监听（sprite 位移不再回调本方）；
        2. 停 + 断 + **删除** ``_follow_timer``——它的父对象是长寿命 overlay，
           且 ``timeout`` 信号连接持有 ``self._flush_follow`` 的强引用：
           ``stop()`` 只让这一拍不触发，引用仍被 Qt 侧握着；不 ``deleteLater``
           的话旧 follower（连带 ``_sprite``）会活到 overlay 销毁，而 overlay
           在「主宠提升」等路径上被复用、会逐次累积；
        3. 关掉并置空气泡（``bubble=None`` 是本层"无气泡"的唯一表示）。

        overlay 已销毁（wrapper 失效）时全部步骤静默降级：本方法绝不抛。
        close 之后本对象不再可用（``_on_sprite_moved``/``_flush_follow``/
        ``set_origin``/``follow_now``/``refresh_anchor``/``reflow`` 一律早退），
        需要继续跟随时由壳层新建跟随器。
        """
        if self._closed:
            return
        self._closed = True
        self._follow_pending = False
        try:
            self._overlay.remove_position_listener(self._sprite, self)
        except Exception:
            pass
        timer = getattr(self, "_follow_timer", None)
        if timer is not None:
            try:
                if timer.isActive():
                    timer.stop()
            except RuntimeError:
                pass  # 父对象（overlay）已销毁：timer wrapper 已失效
            try:
                timer.timeout.disconnect(self._flush_follow)
            except (RuntimeError, TypeError):
                pass  # 未连接/already 断开/对象失效
            try:
                timer.deleteLater()  # 断开 Qt 侧对 bound method 的引用，回收旧 follower
            except RuntimeError:
                pass
        if self.bubble is not None:
            try:
                self.bubble.close()
            except Exception:
                pass
            self.bubble = None
