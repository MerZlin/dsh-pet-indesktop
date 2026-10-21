"""聊天窗的收口生命周期：关闭即销毁。

Modern（``widgets.py``）与 Classic（``legacy_widgets.py``）两套聊天窗的布局互不
相干，但「关掉就把窗口树还给系统」这条收口语义必须逐位一致，所以集中在这里。

背景（用户实机反馈「AI 对话窗口关闭后占用的内存不返还」）：两套窗口此前都是
**进程级缓存单例**——``closeEvent`` 只 ``hide()`` 不销毁，``PetInstance`` 一直
持有引用，于是关窗后整棵控件树 + 会话气泡 + ChatService 常驻到进程结束。反复
开关不涨（探针证实不是泄漏），但一次开窗的驻留再也不还。

两条硬约束：

* **不许带着在飞 worker 一起死**：``ChatService`` 的 worker 是 ``QThread``，
  运行中被销毁会让进程直接崩（``service.shutdown`` 的 docstring 记着同款）。
  关闭时先 ``stop()``，仍有余下的 worker 就等它们的 ``finished`` 再删。
* **销毁只排一次**：X / Esc / ``reject`` / ``append_look_sync`` 之外的多条收口
  路径都会走到 ``QDialog.done()``，重复 ``deleteLater`` 虽无害但会让「谁负责
  收口」变模糊，所以用实例标记钉一次。
"""

from __future__ import annotations

from PySide6.QtCore import Qt


def _has_running_workers(service) -> bool:
    """服务里还有没有没退出的 worker（含已 stop 但线程仍在校尾的）。

    服务对象可能已随窗口销毁（``RuntimeError``），或测试替身没有这个方法——
    两种情形都按「没有在飞 worker」处理，绝不让清理路径抛异常。
    """
    check = getattr(service, "has_running_workers", None)
    if callable(check):
        try:
            return bool(check())
        except RuntimeError:
            return False
    busy = getattr(service, "busy", False)
    try:
        return bool(busy() if callable(busy) else busy)
    except RuntimeError:
        return False


def _detach_service(service) -> None:
    """销毁前先摘线：worker 已发但未投递的排队信号不许再进即将消失的对象。"""
    detach = getattr(service, "detach", None)
    if callable(detach):
        try:
            detach()
        except RuntimeError:
            pass


def _release_now(window, service) -> None:
    _detach_service(service)
    window.deleteLater()


def release_window_when_idle(window, service) -> None:
    """关窗即销毁：没有在飞 worker 立刻 deleteLater，否则等 worker 退出再删。"""
    if getattr(window, "_release_scheduled", False):
        return
    window._release_scheduled = True
    if not _has_running_workers(service):
        _release_now(window, service)
        return
    for worker in list(getattr(service, "_workers", None) or ()):
        worker.finished.connect(lambda w=window: _release_now(w, service),
                                Qt.ConnectionType.QueuedConnection)
    # 连上之后再复查一次：worker 可能恰好在「判定忙碌」和「建立连接」之间退出，
    # 那时 finished 已经发过了，没人再叫醒我们。
    if not _has_running_workers(service):
        _release_now(window, service)
