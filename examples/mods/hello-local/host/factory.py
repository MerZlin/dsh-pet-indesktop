"""Offline sample using only the public v1 facade and Qt."""
from pet.mod_api.v1 import Contribution, FeatureDefinition

OWNER = "demo.hello-local"


def menu(menu, handle):
    action = menu.addAction("打个招呼（MOD 示例）")
    action.triggered.connect(lambda _checked=False: handle.invoke() if handle.active else None)


class Runtime:
    def __init__(self, context):
        self.context = context
        self.commands = {"hello": self.hello}
        self.dialogs = []
        self.active = False

    def start(self):
        self.active = True

    def hello(self):
        if not self.active or not self.context.execution_authorized():
            return
        values = self.context.configuration.read_namespace()
        state = self.context.documents["memory"].read()
        state["greetings"] = int(state.get("greetings", 0)) + 1
        self.context.documents["memory"].write(state)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setWindowTitle("离线 MOD 示例")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(values.get("message", "你好！这是独立安装的 MOD。"))
        box.finished.connect(lambda _result: self.dialogs.remove(box) if box in self.dialogs else None)
        self.dialogs.append(box)
        box.show()

    def stop(self):
        self.active = False
        for box in tuple(self.dialogs):
            box.close()
        self.dialogs.clear()

    def close(self):
        self.stop()


def create_host():
    return FeatureDefinition(OWNER, (Contribution("hello", "menu", command="hello", factory=menu),),
                             settings,
                             runtime_factory=lambda context, **_kwargs: Runtime(context))


def settings(context, parent=None):
    from .settings import Settings
    return Settings(context, parent)
