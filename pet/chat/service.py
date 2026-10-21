from __future__ import annotations
import logging, threading, uuid
from typing import Any
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication
from .models import ProviderConfig
from .providers import OpenAICompatibleProvider, StreamEvent

log = logging.getLogger(__name__)
class _Worker(QThread):
    delta_received=Signal(str); completed=Signal(str); failed=Signal(str); stopped_by_user=Signal()
    # 非正文事件：(kind, text)，kind 为 'reasoning'（思考过程）或 'status'（联网进度）
    activity_received=Signal(str,str)
    def __init__(self,provider,messages,config,cancel):
        super().__init__()
        self.provider=provider; self.messages=messages; self.config=config; self.cancel=cancel; self.parts=[]
        self._responses = []
    def cancel_response(self):
        self.cancel.set()
        for resp in list(self._responses):
            try: resp.close()
            except Exception: pass
    def run(self):
        try:
            stream_kwargs = {}
            import inspect
            sig = inspect.signature(self.provider.stream)
            if 'response_holder' in sig.parameters:
                stream_kwargs['response_holder'] = self._responses
            for text in self.provider.stream(self.messages,self.config,self.cancel,**stream_kwargs):
                if self.cancel.is_set(): self.stopped_by_user.emit(); return
                if isinstance(text,StreamEvent):
                    if text.text: self.activity_received.emit(text.kind,text.text)
                    continue
                self.parts.append(text); self.delta_received.emit(text)
            if self.cancel.is_set(): self.stopped_by_user.emit()
            else:
                result = ''.join(self.parts)
                if result.strip():
                    self.completed.emit(result)
                else:
                    self.failed.emit('模型未返回任何内容，请稍后重试或检查模型配置。')
        except Exception as exc:
            self.stopped_by_user.emit() if self.cancel.is_set() else self.failed.emit(str(exc))
class ChatService(QObject):
    started=Signal(str); delta=Signal(str,str); finished=Signal(str,str); error=Signal(str,str); stopped=Signal(str)
    # (request_id, kind, text)：kind='reasoning' 思考过程增量 / 'status' 联网检索进度
    activity=Signal(str,str,str)
    # 进程级「回复完成」订阅（灵动岛事件动效等）：AppShell 注册一次，
    # 回调总在 GUI 线程执行（worker→本对象的链路全是 QueuedConnection）。
    _global_finished_listeners: list = []

    @classmethod
    def register_global_finished(cls, callback):
        if callback not in cls._global_finished_listeners:
            cls._global_finished_listeners.append(callback)

    @classmethod
    def unregister_global_finished(cls, callback):
        if callback in cls._global_finished_listeners:
            cls._global_finished_listeners.remove(callback)

    def __init__(self,provider=None,parent=None):
        super().__init__(parent); self.provider=provider or OpenAICompatibleProvider(); self._request_id=None; self._cancel=None; self._worker=None; self._workers=set()
        self._detached=False
        # 档 B 记忆提炼（pet/chat/memory_extract）：前端在构造后挂 hook
        # （(user_text, reply_text, provider, provider_config) -> None），
        # finished 路径在发完信号后同步调一次——hook 内部只做护栏检查并起后台线程。
        self.memory_extract_hook=None; self._extract_user_text=None; self._extract_config=None
        # 退出时先取消并短等在飞 worker，避免 QThread 运行中被销毁导致崩溃
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def shutdown(self) -> bool:
        # QThread 并非 daemon 线程，若在运行中被 Python GC 丢弃会触发 'QThread: Destroyed while running' 崩溃。
        # 因此取消并有界等待后，超时未退出的 worker 仍保留在 self._workers 集合中（不强行 clear/置空），
        # 等其 run() 结束后由 finished 信号自行 discard 回收引用。
        self.stop()
        for worker in list(self._workers):
            if hasattr(worker, "cancel_response") and callable(worker.cancel_response):
                try: worker.cancel_response()
                except Exception: pass
            elif hasattr(worker, "cancel") and worker.cancel is not None:
                worker.cancel.set()
            if worker.isRunning():
                worker.wait(1500)
        # 返回 True 表示全部退出，False 表示仍有 worker 在运行（供调用方参考）
        return not any(worker.isRunning() for worker in self._workers)
    @property
    def busy(self): return self._worker is not None and self._worker.isRunning()
    def has_running_workers(self) -> bool:
        """还有没有没退出的 worker（含已 stop、线程仍在校尾的）。

        关窗即销毁的路径靠它决定「现在删还是等 worker 退了再删」：QThread 运行中
        被销毁会直接崩进程。``busy`` 只看当前那一只，会被上一轮的收尾线程漏掉。
        """
        return any(worker.isRunning() for worker in list(self._workers))
    def detach(self) -> None:
        """销毁前摘线：worker 已发出但尚未投递的排队信号不许再进本对象。

        关窗即销毁（``pet/chat/lifecycle.py``）之后，worker 的排队回调可能落在
        本对象已删之后——那时 ``self.stopped.emit`` 会抛
        "Signal source has been deleted"。置位后 ``_current`` 恒 False，全部
        排队回调第一行就返回（含 ``_cleanup``，不碰任何 Qt 对象）。
        """
        self._detached = True
        self.stop()
    def send(self,messages:list[dict[str,Any]],config:ProviderConfig,request_id=None):
        self.stop(); rid=request_id or uuid.uuid4().hex; cancel=threading.Event(); worker=_Worker(self.provider,messages,config,cancel); self._request_id=rid; self._cancel=cancel; self._worker=worker
        # 档 B 提炼上下文：本轮最后一条纯文本用户消息（图片等多模态形态不提炼）
        self._extract_user_text=None; self._extract_config=config
        for m in reversed(messages):
            if isinstance(m,dict) and m.get('role')=='user' and isinstance(m.get('content'),str):
                self._extract_user_text=m['content']; break
        self._workers.add(worker)
        # 全部显式 QueuedConnection：worker 线程 emit 的信号若直连 lambda
        # 会在 worker 线程执行 _delta/_cleanup 等，与 GUI 线程的 busy()/
        # _request_id 读写构成数据竞态。队列投递保证回调只在 GUI 线程跑。
        worker.delta_received.connect(lambda text,rid=rid:self._delta(rid,text), Qt.QueuedConnection)
        worker.activity_received.connect(lambda kind,text,rid=rid:self._activity(rid,kind,text), Qt.QueuedConnection)
        worker.completed.connect(lambda text,rid=rid:self._finished(rid,text), Qt.QueuedConnection)
        worker.failed.connect(lambda text,rid=rid:self._error(rid,text), Qt.QueuedConnection)
        worker.stopped_by_user.connect(lambda rid=rid:self._stopped(rid), Qt.QueuedConnection)
        worker.finished.connect(lambda rid=rid:self._cleanup(rid), Qt.QueuedConnection)
        worker.finished.connect(lambda w=worker: self._workers.discard(w), Qt.QueuedConnection)
        self.started.emit(rid); worker.start(); return rid
    def stop(self):
        if self._worker is not None:
            if hasattr(self._worker, "cancel_response") and callable(self._worker.cancel_response):
                try: self._worker.cancel_response()
                except Exception: pass
            elif hasattr(self._worker, "cancel") and self._worker.cancel is not None:
                self._worker.cancel.set()
        elif self._cancel is not None:
            self._cancel.set()
    def _current(self,rid): return (not self._detached) and rid==self._request_id
    def _delta(self,rid,text):
        if self._current(rid): self.delta.emit(rid,text)
    def _activity(self,rid,kind,text):
        if self._current(rid): self.activity.emit(rid,kind,text)
    def _finished(self,rid,text):
        if self._current(rid):
            self.finished.emit(rid,text)
            for cb in list(type(self)._global_finished_listeners):
                try: cb(text)
                except Exception: pass
            self._maybe_extract_memory(text)
    def _maybe_extract_memory(self,reply_text):
        # 档 B：回复完成后安排静默记忆提炼；hook 异常绝不抛到对话路径
        hook=self.memory_extract_hook; user_text=self._extract_user_text; config=self._extract_config
        if hook is None or not user_text or config is None: return
        try: hook(user_text,reply_text,self.provider,config)
        except Exception: log.exception("记忆提炼 hook 异常（已静默）")
    def _error(self,rid,text):
        if self._current(rid): self.error.emit(rid,text)
    def _stopped(self,rid):
        if self._current(rid): self.stopped.emit(rid)
    def _cleanup(self,rid):
        if self._current(rid): self._worker=None; self._cancel=None
