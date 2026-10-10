"""Offline worker sample: Core supplies the verified launch, never an EXE path."""
from pet.mod_api.v1 import Contribution, FeatureDefinition

OWNER = "demo.echo-worker"


def menu(menu, handle):
    action = menu.addAction("离线 Echo" if handle.descriptor.id == "echo" else "取消 Echo")
    action.triggered.connect(lambda _checked=False: handle.invoke() if handle.active else None)


class Runtime:
    def __init__(self, context):
        self.context = context
        from pet.mod_api.v1 import WorkerClient
        self.worker = WorkerClient(context)
        self.worker.ready.connect(self._send)
        self.worker.response.connect(self._result)
        self.worker.error.connect(self._error)
        self.commands = {"echo": self.echo, "cancel": self.cancel}
        self.pending_text = None
        self.request_id = None
        self.dialogs = []

    def start(self):
        pass  # No worker or request just because this MOD is enabled.

    def echo(self):
        self.cancel()
        self.pending_text = self.context.configuration.read_namespace().get("message", "你好，Worker！")
        if self.worker.is_ready:
            self._send()
        else:
            self.worker.start()

    def _send(self):
        if self.pending_text is not None:
            self.request_id = self.worker.request("echo", {"text": self.pending_text})
            self.pending_text = None

    def _result(self, rid, payload):
        if rid != self.request_id:
            return
        self.request_id = None
        self._show("独立 Worker 返回", str(payload.get("echo", payload.get("error", "无结果"))))

    def _error(self, code):
        self.pending_text = self.request_id = None
        self._show("Echo Worker", "Worker 无法启动或已停止，请重试；仍失败时更新该 MOD。\n结果码：" + code)

    def _show(self, title, text):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.finished.connect(lambda _result: self.dialogs.remove(box) if box in self.dialogs else None)
        self.dialogs.append(box)
        box.show()

    def cancel(self):
        self.worker.cancel(self.request_id)
        self.pending_text = self.request_id = None

    def stop(self):
        self.cancel()
        self.worker.stop()
        for box in tuple(self.dialogs):
            box.close()
        self.dialogs.clear()

    def close(self):
        self.stop()
        self.worker.close()


def create_host():
    return FeatureDefinition(OWNER, tuple(Contribution(op, "menu", command=op, factory=menu) for op in ("echo", "cancel")),
                             settings,
                             runtime_factory=lambda context, **_kwargs: Runtime(context), allow_in_process=False)


def settings(context, parent=None):
    from .settings import Settings
    return Settings(context, parent)
