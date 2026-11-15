"""设置侧栏「使用手册」：常见问题 / 使用说明 / 给 AI 的提示词。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QPlainTextEdit,
    QWidget,
)

from ... import paths
from .base import SettingsPage, make_button
from .manual_content import AI_PROMPT_TEMPLATE, FAQ_SECTIONS, WIKI_SECTIONS


class ManualPage(SettingsPage):
    title = "使用手册"
    content_max_width = 900

    def __init__(self, host, parent=None) -> None:
        super().__init__(host, parent)
        self._build_faq()
        self._build_wiki()
        self._build_ai()

    # ------------------------------------------------------------ 子页
    def _build_faq(self) -> None:
        with self.tab("faq", "常见问题"):
            self.add_note(
                "按现象查：API 报错、没有声音、动画不动、CPU 高、全屏不隐藏、"
                "角色包/WebP 导入失败。"
            )
            for title, body in FAQ_SECTIONS:
                card = self.section(title)
                label = QLabel(body, self)
                label.setObjectName("settingHint")
                label.setWordWrap(True)
                card.add_widget(label)

    def _build_wiki(self) -> None:
        with self.tab("wiki", "使用说明"):
            self.add_note("第一次用可以先看「第一次启动」「右键菜单」「AI 对话」三节。")
            for title, body in WIKI_SECTIONS:
                card = self.section(title)
                label = QLabel(body, self)
                label.setObjectName("settingHint")
                label.setWordWrap(True)
                card.add_widget(label)

    def _build_ai(self) -> None:
        with self.tab("ai", "给 AI 的提示词"):
            self.add_note(
                "把下面的提示词复制给任意 AI，它会先读设置 schema，再按你的要求改键。"
                "提示词里的路径由程序按当前安装位置自动填写。"
            )
            self.prompt_edit = QPlainTextEdit(self)
            self.prompt_edit.setReadOnly(True)
            self.prompt_edit.setMinimumHeight(360)
            self.prompt_edit.setObjectName("manualAiPrompt")
            self.prompt_edit.setPlainText(self._prompt_text())
            card = self.section("可复制的提示词")
            card.add_widget(self.prompt_edit)

            row = QWidget(self)
            self.copy_button = make_button("复制提示词", row)
            self.copy_button.clicked.connect(self._copy_prompt)
            self.export_button = make_button("导出设置说明", row)
            self.export_button.clicked.connect(self._export_guide)
            card.add_buttons([self.copy_button, self.export_button])
            self.status = QLabel("", self)
            self.status.setObjectName("settingHint")
            self.status.setWordWrap(True)
            card.add_widget(self.status)

    # ------------------------------------------------------------ 路径
    @property
    def data_root(self) -> Path:
        return Path(str(getattr(self.host, "root", Path.cwd())))

    def _program_dir(self) -> str:
        if getattr(sys, "frozen", False):
            return str(Path(sys.executable).resolve().parent)
        try:
            return str(paths.repo_root())
        except Exception:  # noqa: BLE001
            return str(Path.cwd())

    def _prompt_text(self) -> str:
        root = self.data_root
        return AI_PROMPT_TEMPLATE.format(
            program_dir=self._program_dir(),
            data_root=str(root),
            config_path=str(root / "config.json"),
            pet_config_path=str(root / "config-pet-N.json"),
            schema_path=str(root / "settings-schema.json"),
        )

    # ------------------------------------------------------------ 动作
    def _copy_prompt(self) -> None:
        text = self.prompt_edit.toPlainText()
        QApplication.clipboard().setText(text)
        self.status.setText(f"已复制 {len(text)} 个字符到剪贴板。")

    def _export_guide(self) -> None:
        root = self.data_root
        root.mkdir(parents=True, exist_ok=True)
        schema_path = root / "settings-schema.json"
        guide_path = root / "settings-guide.md"
        try:
            from ...settings_schema import as_dicts

            schema = {
                "schema": 1,
                "count": len(as_dicts()),
                "settings": as_dicts(),
            }
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            guide_path.write_text(
                "# DSH 桌宠设置说明\n\n"
                + self.prompt_edit.toPlainText()
                + "\n\n## 设置 schema\n\n见 `settings-schema.json`。\n",
                encoding="utf-8",
            )
        except OSError:
            self.status.setText("导出失败：数据目录不可写。")
            return
        self.status.setText(f"已导出：{schema_path} 与 {guide_path}")

    def keywords(self) -> list[str]:
        return [
            "使用手册",
            "使用说明",
            "常见问题",
            "FAQ",
            "API",
            "401",
            "429",
            "WebP",
            "精灵表",
            "AI",
            "提示词",
            "wiki",
            "manual",
            "教程",
        ]
