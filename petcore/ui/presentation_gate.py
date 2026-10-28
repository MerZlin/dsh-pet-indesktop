"""进程级「展示会话」闸门：同一时刻只允许一个 owner 占用桌宠气泡。

歌词这类连续展示（整首歌每秒一句）和自言自语这种定时冒泡共用同一个
``SpeechBubble``：谁后说谁就把前一句盖掉。闸门把「我现在占着气泡」变成一次显式、
可重入的会话：持有者自己的 ``say()`` 照常显示，其它 owner 的 ``say()/say_sequence()``
直接返回 ``False``（不弹、不报错、不排期）；用户动作带来的 ``force=True`` 仍然可以
显示（点桌宠的台词、菜单命令反馈不该被歌词吞掉）。

闸门**只认 owner 字符串**，不认识任何业务——Core 不知道"歌词"是什么。

两个兜底，防止一个坏掉的 owner 永久静音桌宠：

- 同一 owner 重入计数，必须成对 ``end_scene``；
- 持有期间要按拍 ``refresh_scene`` 续期，超过 ``STALE_SECONDS`` 没有续期就视为
  自动释放（歌词持有者每 1 秒一拍续期）。mod 崩在歌中间也不会把自言自语锁死。
"""

from __future__ import annotations

import threading
import time

#: 会话多久没有续期就自动释放（秒）。持有者按 1 Hz 续期，正常路径永远碰不到。
STALE_SECONDS = 45.0


class PresentationGate:
    """一个可重入的「谁在占用气泡」状态（线程安全；进程级实例见模块函数）。"""

    def __init__(self, *, clock=time.monotonic, stale_seconds: float = STALE_SECONDS):
        self._lock = threading.RLock()
        self._clock = clock
        self._stale_seconds = float(stale_seconds)
        self._owner = ""
        self._depth = 0
        self._touched = 0.0

    # -- 内部（持锁调用） ------------------------------------------------

    def _expire_locked(self) -> None:
        if not self._owner:
            return
        if self._clock() - self._touched <= self._stale_seconds:
            return
        self._owner = ""
        self._depth = 0

    # -- 公开面 ----------------------------------------------------------

    def begin_scene(self, owner: str) -> bool:
        """申请/续期一个展示会话。

        返回 ``True`` 表示现在由 ``owner`` 持有（同一 owner 重入时计数 +1）；
        ``False`` 表示另一个 owner 正在持有——调用方应当让路，不要重试抢占。
        ``owner`` 为空串会被拒绝：匿名占不了气泡。
        """
        name = str(owner or "").strip()
        if not name:
            return False
        with self._lock:
            self._expire_locked()
            if not self._owner:
                self._owner = name
                self._depth = 1
                self._touched = self._clock()
                return True
            if self._owner != name:
                return False
            self._depth += 1
            self._touched = self._clock()
            return True

    def end_scene(self, owner: str) -> None:
        """释放一次会话（与 ``begin_scene`` 成对）；别人的会话不受影响。"""
        name = str(owner or "").strip()
        with self._lock:
            self._expire_locked()
            if not name or name != self._owner:
                return
            self._depth -= 1
            if self._depth <= 0:
                self._owner = ""
                self._depth = 0

    def refresh_scene(self, owner: str) -> bool:
        """续期（不增加重入计数）：返回 ``True`` 表示会话仍归 ``owner``。

        长会话（整首歌）每拍调一次，防止 ``STALE_SECONDS`` 把它判成泄漏；同时也就
        地回答了"我还占着吗"——被别人抢走/已过期时返回 ``False``。
        """
        name = str(owner or "").strip()
        if not name:
            return False
        with self._lock:
            self._expire_locked()
            if name != self._owner:
                return False
            self._touched = self._clock()
            return True

    def owner(self) -> str:
        """当前持有会话的 owner；没人持有时返回空串。"""
        with self._lock:
            self._expire_locked()
            return self._owner

    def allows(self, speaker: str = "") -> bool:
        """``speaker`` 此刻能不能显示气泡（``speaker`` 缺省 = 匿名调用方）。"""
        with self._lock:
            self._expire_locked()
            if not self._owner:
                return True
            name = str(speaker or "").strip()
            return bool(name) and name == self._owner

    def reset(self) -> None:
        """清空状态（进程级实例只给测试/退出用）。"""
        with self._lock:
            self._owner = ""
            self._depth = 0
            self._touched = 0.0


#: 进程级实例：Core 的 presentation 端口与所有 mod 共用这一个。
_GATE = PresentationGate()


def begin_scene(owner: str) -> bool:
    return _GATE.begin_scene(owner)


def end_scene(owner: str) -> None:
    _GATE.end_scene(owner)


def refresh_scene(owner: str) -> bool:
    return _GATE.refresh_scene(owner)


def owner() -> str:
    return _GATE.owner()


def allows(speaker: str = "") -> bool:
    return _GATE.allows(speaker)


def reset() -> None:
    _GATE.reset()
