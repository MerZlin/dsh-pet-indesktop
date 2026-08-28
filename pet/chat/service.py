from __future__ import annotations
import threading, uuid
from typing import Any
from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication
from .models import ProviderConfig
from .providers import OpenAICompatibleProvider
class _Worker(QThread):
    delta_received=Signal(str); completed=Signal(str); failed=Signal(str); stopped_by_user=Signal()
    def __init__(self,provider,messages,config,cancel): super().__init__(); self.provider=provider; self.messages=messages; self.config=config; self.cancel=cancel; self.parts=[]
    def run(self):
        try:
            for text in self.provider.stream(self.messages,self.config,self.cancel):
                if self.cancel.is_set(): self.stopped_by_user.emit(); return
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
        finally:
            if hasattr(self.provider, "cancel") and callable(self.provider.cancel):
                try: self.provider.cancel()
                except Exception: pass
class ChatService(QObject):
    started=Signal(str); delta=Signal(str,str); finished=Signal(str,str); error=Signal(str,str); stopped=Signal(str)
    def __init__(self,provider=None,parent=None):
        super().__init__(parent); self.provider=provider or OpenAICompatibleProvider(); self._request_id=None; self._cancel=None; self._worker=None; self._workers=set()
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
            if hasattr(worker, "cancel") and worker.cancel is not None:
                worker.cancel.set()
            if hasattr(worker, "provider") and hasattr(worker.provider, "cancel") and callable(worker.provider.cancel):
                try: worker.provider.cancel()
                except Exception: pass
            if worker.isRunning():
                worker.wait(1500)
        # 返回 True 表示全部退出，False 表示仍有 worker 在运行（供调用方参考）
        return not any(worker.isRunning() for worker in self._workers)
    @property
    def busy(self): return self._worker is not None and self._worker.isRunning()
    def send(self,messages:list[dict[str,Any]],config:ProviderConfig,request_id=None):
        self.stop(); rid=request_id or uuid.uuid4().hex; cancel=threading.Event(); worker=_Worker(self.provider,messages,config,cancel); self._request_id=rid; self._cancel=cancel; self._worker=worker
        self._workers.add(worker)
        # 全部显式 QueuedConnection：worker 线程 emit 的信号若直连 lambda
        # 会在 worker 线程执行 _delta/_cleanup 等，与 GUI 线程的 busy()/
        # _request_id 读写构成数据竞态。队列投递保证回调只在 GUI 线程跑。
        worker.delta_received.connect(lambda text,rid=rid:self._delta(rid,text), Qt.QueuedConnection)
        worker.completed.connect(lambda text,rid=rid:self._finished(rid,text), Qt.QueuedConnection)
        worker.failed.connect(lambda text,rid=rid:self._error(rid,text), Qt.QueuedConnection)
        worker.stopped_by_user.connect(lambda rid=rid:self._stopped(rid), Qt.QueuedConnection)
        worker.finished.connect(lambda rid=rid:self._cleanup(rid), Qt.QueuedConnection)
        worker.finished.connect(lambda w=worker: self._workers.discard(w), Qt.QueuedConnection)
        self.started.emit(rid); worker.start(); return rid
    def stop(self):
        if self._cancel is not None: self._cancel.set()
        if hasattr(self.provider, "cancel") and callable(self.provider.cancel):
            try: self.provider.cancel()
            except Exception: pass
    def _current(self,rid): return rid==self._request_id
    def _delta(self,rid,text):
        if self._current(rid): self.delta.emit(rid,text)
    def _finished(self,rid,text):
        if self._current(rid): self.finished.emit(rid,text)
    def _error(self,rid,text):
        if self._current(rid): self.error.emit(rid,text)
    def _stopped(self,rid):
        if self._current(rid): self.stopped.emit(rid)
    def _cleanup(self,rid):
        if self._current(rid): self._worker=None; self._cancel=None
