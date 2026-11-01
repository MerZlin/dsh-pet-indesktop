"""设置侧栏「致谢」页：作者、贡献者、特别鸣谢、issue/PR 协作者、粉丝与签名。

面板本身是 :class:`ThanksPanel`（来自 ``page_about_thanks``）：签名与 B 站凭据
都是保存即生效，所以本页 ``dirty()`` 恒为假，不参与设置窗的保存/取消事务。
"""

from __future__ import annotations

from .base import SettingsPage
from .page_about_thanks import ThanksPanel


class ThanksPage(SettingsPage):
    title = "致谢"

    def __init__(self, host, parent=None) -> None:
        super().__init__(host, parent)
        self.panel = ThanksPanel(self.host, self)
        self._append_section_widget(self.panel)

    def dirty(self) -> bool:
        return False

    def dispose(self) -> None:
        self.panel.dispose()
        super().dispose()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.panel.refresh_if_stale()

    def keywords(self) -> list[str]:
        return [
            "致谢",
            "鸣谢",
            "感谢",
            "作者",
            "贡献者",
            "特别鸣谢",
            "粉丝",
            "签名",
            "开源",
            "contributors",
            "credits",
            "thanks",
            "fans",
        ]
