from __future__ import annotations
import threading, uuid
from typing import Any
from PySide6.QtCore import QObject, QThread, Signal
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
class ChatService(QObject):
    started=Signal(str); delta=Signal(str,str); finished=Signal(str,str); error=Signal(str,str); stopped=Signal(str)
    def __init__(self,provider=None,parent=None):
        super().__init__(parent); self.provider=provider or OpenAICompatibleProvider(); self._request_id=None; self._cancel=None; self._worker=None
    @property
    def busy(self): return self._worker is not None and self._worker.isRunning()
    def send(self,messages:list[dict[str,Any]],config:ProviderConfig,request_id=None):
        self.stop(); rid=request_id or uuid.uuid4().hex; cancel=threading.Event(); worker=_Worker(self.provider,messages,config,cancel); self._request_id=rid; self._cancel=cancel; self._worker=worker
        worker.delta_received.connect(lambda text,rid=rid:self._delta(rid,text)); worker.completed.connect(lambda text,rid=rid:self._finished(rid,text)); worker.failed.connect(lambda text,rid=rid:self._error(rid,text)); worker.stopped_by_user.connect(lambda rid=rid:self._stopped(rid)); worker.finished.connect(lambda rid=rid:self._cleanup(rid)); self.started.emit(rid); worker.start(); return rid
    def stop(self):
        if self._cancel is not None: self._cancel.set()
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
