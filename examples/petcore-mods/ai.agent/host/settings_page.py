"""Agent 联动设置页：开关、来源、动画与气泡、音效、诊断。

改动实时写进全局配置（运行时每轮现读），保存只负责落盘；取消/关闭时
``discard_changes()`` 把配置与控件一起回滚到打开设置时的快照。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSlider,
    QWidget,
)

from petcore.ui.settings.base import SettingsPage, make_button, make_checkbox

#: 自定义 Agent 行的字段分隔符（``key | 显示名 | 事件文件路径``）。
CUSTOM_SEPARATOR = "|"
#: 诊断面板显示的事件文件上限。
DIAG_MAX_FILES = 12


def parse_custom_rows(text: str) -> list[dict]:
    """把多行编辑框解析成 ``[{key,name,path}]``；非法行忽略（与 core 校验同口径）。"""
    rows: list[dict] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(CUSTOM_SEPARATOR)]
        if len(parts) < 2:
            continue
        key = parts[0].lower()
        if not key or not key[0].isalnum():
            continue
        if len(parts) == 2:
            name, path = key, parts[1]
        else:
            name, path = parts[1] or key, CUSTOM_SEPARATOR.join(parts[2:]).strip()
        if not path:
            continue
        rows.append({"key": key, "name": name[:50], "path": path[:500]})
    return rows[:8]


def format_custom_rows(rows) -> str:
    lines = []
    for item in rows or ():
        if not isinstance(item, dict):
            continue
        lines.append(
            f"{item.get('key', '')} {CUSTOM_SEPARATOR} {item.get('name', '')} "
            f"{CUSTOM_SEPARATOR} {item.get('path', '')}"
        )
    return "\n".join(lines)


class AgentLinkPage(SettingsPage):
    title = "Agent 联动"

    def __init__(self, host, parent=None):
        super().__init__(host, parent)
        self.group_heading("Agent 联动")
        config = self.config()

        self.enabled = make_checkbox(self)
        self.enabled.setChecked(bool(config.get("ai_agent_enabled", False)))

        card = self.section("总开关", level=3)
        card.add_row(
            "启用 Agent 联动",
            self.enabled,
            "让桌宠跟着 DSH 等 Agent 的状态播放联动动作、报进度、报完成。默认关闭。",
        )

        # ------------------------------------------------------------ 来源
        self.dsh = make_checkbox(self)
        self.dsh.setChecked(bool(config.get("ai_agent_dsh_enabled", True)))
        self.events = make_checkbox(self)
        self.events.setChecked(bool(config.get("ai_agent_events_enabled", True)))
        self.custom = QPlainTextEdit(self)
        self.custom.setPlaceholderText("claude | Claude Code | C:/Users/me/.claude/agent-events/claude.jsonl")
        self.custom.setPlainText(
            format_custom_rows(config.get("ai_agent_custom_agents", []))
        )
        self.custom.setMinimumHeight(96)
        sources = self.section("事件来源", level=3)
        sources.add_row(
            "DSH 桥接目录",
            self.dsh,
            "监听上级目录的 dsh-pet-bridge/dsh*.jsonl（DSH 桥接插件写入）。",
        )
        sources.add_row(
            "统一事件目录",
            self.events,
            "监听数据目录下 agent-events/*.jsonl；任何 Agent 都能按协议往里追加。",
        )
        sources.add_row(
            "自定义 Agent",
            self.custom,
            "一行一个：key | 显示名 | 事件文件或目录。最多 8 个，可留空。",
            expand=True,
        )
        self.add_note(
            "只读：桌宠不写任何 Agent 配置、不注入 hooks、不回传任何内容；"
            "事件文件只记状态与工具名。"
        )

        # ------------------------------------------------------ 动画与气泡
        self.animation = make_checkbox(self)
        self.animation.setChecked(bool(config.get("ai_agent_animation_enabled", True)))
        self.commands = make_checkbox(self)
        self.commands.setChecked(bool(config.get("ai_agent_command_enabled", True)))
        self.activity = make_checkbox(self)
        self.activity.setChecked(bool(config.get("ai_agent_activity_bubbles", True)))
        self.done = make_checkbox(self)
        self.done.setChecked(bool(config.get("ai_agent_done_notice", True)))
        self.interval = QSlider(Qt.Orientation.Horizontal, self)
        self.interval.setRange(5, 100)
        self.interval.setValue(int(round(float(config.get("ai_agent_min_interval_seconds", 2.0)) * 10)))
        self._interval_label = QLabel("2.0", self)
        self._interval_label.setMinimumWidth(40)
        self._interval_label.setText(f"{self.interval.value() / 10.0:.1f}")
        interval_row = QWidget(self)
        interval_layout = QHBoxLayout(interval_row)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_layout.setSpacing(8)
        interval_layout.addWidget(self.interval, 1)
        interval_layout.addWidget(self._interval_label)
        behavior = self.section("动画与气泡", level=3)
        behavior.add_row(
            "联动动作",
            self.animation,
            "Agent 干活时轮换播「写代码 / 吃Token」，每第 3 次插播一个摸鱼动作。",
        )
        behavior.add_row(
            "过程气泡",
            self.activity,
            "按工具名报进度（正在跑命令 / 正在读文件…），同 Agent 10 秒最多一条。",
        )
        behavior.add_row(
            "接受 DSH 指令",
            self.commands,
            "DSH 通过 pet_control 工具或 /pet say|play 让桌宠播动作/冒泡；1 秒最多一条，动作名必须存在。",
        )
        behavior.add_row(
            "完成通知",
            self.done,
            "忙完 0.8 秒后稳定才报「干完活啦」；掉线抖动不会误报。",
        )
        behavior.add_row(
            "动作切换间隔",
            interval_row,
            "同一 Agent 两次联动动作之间的最小间隔（秒）。",
        )

        # ------------------------------------------------------------ 音效
        self.sound = make_checkbox(self)
        self.sound.setChecked(bool(config.get("ai_agent_sound_enabled", False)))
        self.volume = QSlider(Qt.Orientation.Horizontal, self)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(round(float(config.get("ai_agent_sound_volume", 0.65)) * 100)))
        self.cooldown = QSlider(Qt.Orientation.Horizontal, self)
        self.cooldown.setRange(0, 100)
        self.cooldown.setValue(
            int(round(float(config.get("ai_agent_sound_cooldown_seconds", 2.0)) * 10))
        )
        self._cooldown_label = QLabel("2.0", self)
        self._cooldown_label.setMinimumWidth(40)
        self._cooldown_label.setText(f"{self.cooldown.value() / 10.0:.1f}")
        cooldown_row = QWidget(self)
        cooldown_layout = QHBoxLayout(cooldown_row)
        cooldown_layout.setContentsMargins(0, 0, 0, 0)
        cooldown_layout.setSpacing(8)
        cooldown_layout.addWidget(self.cooldown, 1)
        cooldown_layout.addWidget(self._cooldown_label)
        self.sound_paths = {}
        for kind, label, fallback in (
            ("start", "开工音", "builtin:agent-start"),
            ("done", "完成音", "builtin:agent-done"),
            ("error", "报错音", "builtin:agent-error"),
        ):
            self.sound_paths[kind] = self._path_field(
                str(config.get(f"ai_agent_sound_{kind}_path", "") or fallback)
            )
        sound = self.section("音效", level=3)
        sound.add_row("启用音效", self.sound, "开工 / 完成 / 报错各自一个音，默认全关。")
        sound.add_row("音量", self.volume, "0–100%。")
        sound.add_row("冷却", cooldown_row, "同一 Agent 同类音效的最小间隔（秒）。")
        for kind, label, _fallback in (
            ("start", "开工音", ""),
            ("done", "完成音", ""),
            ("error", "报错音", ""),
        ):
            sound.add_row(
                label,
                self.sound_paths[kind],
                "留空或写 builtin:agent-* 用内置音；也可以填 wav 绝对路径。",
                expand=True,
            )

        # ------------------------------------------------------------ 诊断
        self.diagnostics = QPlainTextEdit(self)
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMinimumHeight(150)
        self.refresh_button = make_button("刷新", self)
        self.refresh_button.clicked.connect(self.refresh_diagnostics)
        self.test_button = make_button("发送测试事件", self)
        self.test_button.clicked.connect(self._send_test)
        buttons = QWidget(self)
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.test_button)
        button_layout.addStretch(1)
        diag = self.section("诊断", level=3)
        diag.add_row(
            "事件文件",
            buttons,
            "列出监听的来源与最近一行事件；「发送测试事件」会写一条 demo 事件走完整链路。",
            expand=True,
        )
        diag.add_widget(self.diagnostics)

        self._initial = self._snapshot()
        self._initial_config = self._config_values()
        self._connect_live()
        self.refresh_diagnostics()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh_diagnostics)
        self._timer.start()

    # ---------------------------------------------------------------- 配置

    def config(self):
        return self.host.config

    def _data_root(self) -> Path:
        root = getattr(self.host, "root", None)
        return Path(root) if root else Path.home()

    def _path_field(self, value: str):
        from petcore.ui.settings.base import value_field

        return value_field(value, self)

    def _connect_live(self) -> None:
        for widget, key in (
            (self.enabled, "ai_agent_enabled"),
            (self.dsh, "ai_agent_dsh_enabled"),
            (self.events, "ai_agent_events_enabled"),
            (self.animation, "ai_agent_animation_enabled"),
            (self.commands, "ai_agent_command_enabled"),
            (self.activity, "ai_agent_activity_bubbles"),
            (self.done, "ai_agent_done_notice"),
            (self.sound, "ai_agent_sound_enabled"),
        ):
            widget.toggled.connect(lambda value, key=key: self._write(key, bool(value)))
        self.custom.textChanged.connect(self._on_custom)
        self.interval.valueChanged.connect(self._on_interval)
        self.volume.valueChanged.connect(
            lambda value: self._write("ai_agent_sound_volume", round(value / 100.0, 2))
        )
        self.cooldown.valueChanged.connect(self._on_cooldown)
        for kind, field in self.sound_paths.items():
            field.editingFinished.connect(
                lambda kind=kind: self._write(
                    f"ai_agent_sound_{kind}_path",
                    field.text().strip() or f"builtin:agent-{kind}",
                )
            )

    def _write(self, key: str, value) -> None:
        self.config().set(key, value)
        self.changed.emit()

    def _on_interval(self, value: int) -> None:
        seconds = round(value / 10.0, 1)
        self._interval_label.setText(f"{seconds:.1f}")
        self._write("ai_agent_min_interval_seconds", seconds)

    def _on_cooldown(self, value: int) -> None:
        seconds = round(value / 10.0, 1)
        self._cooldown_label.setText(f"{seconds:.1f}")
        self._write("ai_agent_sound_cooldown_seconds", seconds)

    def _on_custom(self) -> None:
        self._write(
            "ai_agent_custom_agents", parse_custom_rows(self.custom.toPlainText())
        )

    # ---------------------------------------------------------------- 诊断

    def _sources(self) -> list[Path]:
        root = self._data_root()
        paths = [root / "agent-events", root.parent / "dsh-pet-bridge"]
        for item in parse_custom_rows(self.custom.toPlainText()):
            path = Path(str(item.get("path") or "")).expanduser()
            if path.name:
                paths.append(path)
        return paths

    def refresh_diagnostics(self) -> None:
        lines = [f"数据目录：{self._data_root()}"]
        files: list[Path] = []
        for source in self._sources():
            try:
                if source.is_dir():
                    found = sorted(
                        source.glob("*.jsonl"), key=lambda item: item.stat().st_mtime
                    )[-DIAG_MAX_FILES:]
                elif source.is_file():
                    found = [source]
                else:
                    found = []
            except OSError:
                found = []
            state = "目录缺失" if not source.exists() else f"{len(found)} 个文件"
            lines.append(f"{source} —— {state}")
            files.extend(found)
        if files:
            lines.append("")
            for path in files[-DIAG_MAX_FILES:]:
                lines.append(f"{path.name}: {self._last_line(path)}")
        else:
            lines.append("")
            lines.append("还没有事件文件：开启来源后让对应 Agent 跑一轮，或点「发送测试事件」。")
        self.diagnostics.setPlainText("\n".join(lines))

    @staticmethod
    def _last_line(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 512))
                chunk = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return "（读不到）"
        for line in reversed(chunk.splitlines()):
            line = line.strip()
            if line:
                return line[:200]
        return "（空）"

    def _send_test(self) -> None:
        """写一条 demo 事件，走完整链路（tailer → 状态机 → 气泡/动画）。"""
        directory = self._data_root() / "agent-events"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "demo.jsonl"
            now = round(time.time(), 3)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"ts": now, "agent": "demo", "event": "UserPromptSubmit"},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        {"ts": now + 0.1, "agent": "demo", "state": "idle"},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            self.diagnostics.setPlainText("写测试事件失败：数据目录不可写。")
            return
        QTimer.singleShot(2500, self.refresh_diagnostics)

    # ------------------------------------------------------------ 快照语义

    def _snapshot(self) -> tuple:
        return (
            self.enabled.isChecked(),
            self.dsh.isChecked(),
            self.events.isChecked(),
            self.animation.isChecked(),
            self.commands.isChecked(),
            self.activity.isChecked(),
            self.done.isChecked(),
            self.sound.isChecked(),
            self.custom.toPlainText(),
            self.interval.value(),
            self.volume.value(),
            self.cooldown.value(),
            tuple(field.text() for field in self.sound_paths.values()),
        )

    def dirty(self) -> bool:
        return self._snapshot() != self._initial

    def _config_values(self) -> dict:
        """当前控件表达的配置值（保存与回滚都从这份快照出发）。"""
        values = {
            "ai_agent_enabled": self.enabled.isChecked(),
            "ai_agent_dsh_enabled": self.dsh.isChecked(),
            "ai_agent_events_enabled": self.events.isChecked(),
            "ai_agent_animation_enabled": self.animation.isChecked(),
            "ai_agent_command_enabled": self.commands.isChecked(),
            "ai_agent_activity_bubbles": self.activity.isChecked(),
            "ai_agent_done_notice": self.done.isChecked(),
            "ai_agent_sound_enabled": self.sound.isChecked(),
            "ai_agent_custom_agents": parse_custom_rows(self.custom.toPlainText()),
            "ai_agent_min_interval_seconds": round(self.interval.value() / 10.0, 1),
            "ai_agent_sound_volume": round(self.volume.value() / 100.0, 2),
            "ai_agent_sound_cooldown_seconds": round(self.cooldown.value() / 10.0, 1),
        }
        for kind, field in self.sound_paths.items():
            values[f"ai_agent_sound_{kind}_path"] = (
                field.text().strip() or f"builtin:agent-{kind}"
            )
        return values

    def confirm_save(self) -> None:
        for key, value in self._config_values().items():
            self._write(key, value)
        self._initial = self._snapshot()
        self._initial_config = self._config_values()

    def discard_changes(self) -> None:
        values = dict(self._initial_config)
        widgets = [
            self.enabled,
            self.dsh,
            self.events,
            self.animation,
            self.commands,
            self.activity,
            self.done,
            self.sound,
            self.custom,
            self.interval,
            self.volume,
            self.cooldown,
            *self.sound_paths.values(),
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.enabled.setChecked(bool(values.get("ai_agent_enabled", False)))
            self.dsh.setChecked(bool(values.get("ai_agent_dsh_enabled", True)))
            self.events.setChecked(bool(values.get("ai_agent_events_enabled", True)))
            self.animation.setChecked(
                bool(values.get("ai_agent_animation_enabled", True))
            )
            self.commands.setChecked(
                bool(values.get("ai_agent_command_enabled", True))
            )
            self.activity.setChecked(
                bool(values.get("ai_agent_activity_bubbles", True))
            )
            self.done.setChecked(bool(values.get("ai_agent_done_notice", True)))
            self.sound.setChecked(bool(values.get("ai_agent_sound_enabled", False)))
            self.custom.setPlainText(
                format_custom_rows(values.get("ai_agent_custom_agents", []))
            )
            self.interval.setValue(
                int(round(float(values.get("ai_agent_min_interval_seconds", 2.0)) * 10))
            )
            self.volume.setValue(
                int(round(float(values.get("ai_agent_sound_volume", 0.65)) * 100))
            )
            self.cooldown.setValue(
                int(
                    round(
                        float(values.get("ai_agent_sound_cooldown_seconds", 2.0)) * 10
                    )
                )
            )
            self._interval_label.setText(f"{self.interval.value() / 10.0:.1f}")
            self._cooldown_label.setText(f"{self.cooldown.value() / 10.0:.1f}")
            for kind, field in self.sound_paths.items():
                field.setText(
                    str(values.get(f"ai_agent_sound_{kind}_path", "") or "")
                )
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        for key, value in values.items():
            self.config().set(key, value)
        self._initial = self._snapshot()

    def dispose(self) -> None:
        timer = getattr(self, "_timer", None)
        if timer is not None:
            timer.stop()
        super().dispose()
