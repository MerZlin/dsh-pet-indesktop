# -*- coding: utf-8 -*-
"""设置页「角色与素材」——角色包列表与分件下载。

**为什么单独成模块**：``modern_settings_dialog.py`` 有硬行数预算且明文规定
「再往上帝类里塞新页面时只许降不涨」。对话框里只留三行接线，页面本体在这里。

数据侧（:func:`installed_packs`）是纯函数、可单测；Qt 部分只负责把数据画出来 +
把下载丢到后台线程（绝不在 GUI 线程下载/解压）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from . import pack_download as pd
from .settings_widgets import CollapsibleGroup, SettingsSection
from .updater import REPO, _trusted_https_url

ENV_REGISTRY = "PET_PACK_REGISTRY"

#: 官方角色包清单地址（按序尝试）。清单随仓库 main 分支更新——**发新素材包不需要
#: 用户更新应用**（这是清单不内置的原因）。``PET_PACK_REGISTRY`` 环境变量仍可
#: 整体覆盖（测试/自建源）。
#:
#: 链路设计（实机踩出来的）：jsdelivr 三镜像优先（国内拉小文件快，但新清单要等
#: main 合并 + 缓存刷新，且偶发不可达）；**Release 资产地址兜底**（首次发布后就
#: 永远存在，jsdelivr 404/不可达时用户不至于看到空页）。主清单以 main 为准，
#: Release 副本由发布脚本同步（见 docs/PACK-PUBLISHING.md §3）。
DEFAULT_REGISTRY_URLS: tuple[str, ...] = (
    f"https://cdn.jsdelivr.net/gh/{REPO}@main/packs-index.json",
    f"https://fastly.jsdelivr.net/gh/{REPO}@main/packs-index.json",
    f"https://gcore.jsdelivr.net/gh/{REPO}@main/packs-index.json",
    f"https://github.com/{REPO}/releases/download/assets-v1/packs-index.json",
)


def registry_urls() -> list[str]:
    """清单地址列表（按序尝试）；环境变量覆盖时只有一条。"""
    override = os.environ.get(ENV_REGISTRY, "").strip()
    if override:
        return [override]
    return list(DEFAULT_REGISTRY_URLS)


def _version_tuple(text: str) -> tuple[int, ...]:
    nums: list[int] = []
    for piece in str(text).strip().lstrip("vV").split("."):
        try:
            nums.append(int(piece))
        except ValueError:
            nums.append(0)
    return tuple(nums)


def app_supports(app_min: str) -> bool:
    """当前应用版本是否满足包的 ``app_min``（不满足的包显示但置灰，不隐藏）。"""
    from .updater import APP_VERSION
    have, want = _version_tuple(APP_VERSION), _version_tuple(app_min or "0")
    n = max(len(have), len(want))
    return have + (0,) * (n - len(have)) >= want + (0,) * (n - len(want))


def _on_use_clicked(version_dir, button: QPushButton | None = None) -> None:
    """按钮回调：写活动包指针（**重启后生效**），结果**显示在按钮上**。

    终审：激活失败只记日志 = 用户以为成功了。成功 → 按钮变「已选择，重启后生效」
    并置灰；被拒（core 不闭环）→ 按钮恢复并显示原因。
    """
    try:
        ok = use_pack(version_dir)
    except Exception:      # noqa: BLE001 - 激活失败不该炸设置页
        logging.getLogger(__name__).warning('激活素材包失败', exc_info=True)
        ok = False
    if button is not None:
        if ok:
            button.setText("已选择，重启后生效")
            button.setEnabled(False)
        else:
            button.setText("使用这套（core 不完整，不可激活）")
            button.setEnabled(True)
    if not ok:
        logging.getLogger(__name__).warning('激活素材包被拒绝（core 不闭环？）: %s', version_dir)


def use_pack(version_dir) -> bool:
    """把已装的一套设为**活动包**（"使用这套"）。**重启后生效**。

    装完不自动切：这一步是用户显式选择。core 不闭环的版本会被 `set_active`
    拒绝（返回 False），页面据此提示而不是假装成功。播放进程在下次启动时
    校验指针并应用（PACK-MARKET-DESIGN-v2 §3：首版不做热切换）。
    """
    from . import character_pack as cp
    return cp.set_active(Path(version_dir))


def characters_root() -> Path:
    """角色包安装根目录（**固定用户数据目录**）。

    优先级：``PET_PACK_DIR`` 环境变量（测试/自定义）→ 用户数据目录下的
    ``characters/``（``catalog.user_characters_dir()``，产品发现链认这里）。

    早期版本直接写内置 ``assets/characters``（受保护、升级被覆盖）；
    后来取第一个外部根（exe 同目录/cwd）——可能受保护装不上、换工作目录
    就装丢（终审点名）。现在固定落用户数据目录。
    """
    override = os.environ.get("PET_PACK_DIR", "").strip()
    if override:
        return Path(override)
    from .catalog import user_characters_dir
    return user_characters_dir()


def installed_packs(root: Path | str | None = None) -> list[dict]:
    """扫描已安装角色包：``<root>/<id>/<version>-<variant>-<WxH>/character.json``。

    返回按 id/版本排序的字典列表；索引读不出的目录跳过（不让一条坏包掀翻列表）。
    """
    base = Path(root) if root is not None else characters_root()
    out: list[dict] = []
    if not base.is_dir():
        return out
    for index_path in sorted(base.glob("*/*/character.json")):
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(index, dict):
            continue
        folder = index_path.parent
        clips = index.get("clips") or []
        parts: dict[str, int] = {}
        total_bytes = 0
        for clip in clips:
            part = str(clip.get("part") or "core")
            parts[part] = parts.get(part, 0) + 1
            total_bytes += int(clip.get("bytes") or 0)
        out.append({
            "id": str(index.get("id") or folder.parent.name),
            "name": str(index.get("name") or index.get("id") or folder.parent.name),
            "folder": folder.name,
            "path": folder,
            "canvas": index.get("canvas") or [0, 0],
            "pipeline": index.get("pipeline") or "",
            "clips": len(clips),
            "parts": parts,
            "bytes": total_bytes,
        })
    # 「当前使用」= active.json 指针指向的那套（不是版本号最高的那套）
    for row in out:
        row["active"] = _is_active(row["path"])
    out.sort(key=lambda r: (r["id"], r["folder"]))
    return out


def _is_active(folder: Path) -> bool:
    """该版本是否是所在角色的活动包（读 `active.json`，坏指针一律 False）。"""
    pointer = folder.parent / "active.json"
    try:
        doc = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("use") == folder.name


def _fmt_mb(n: int) -> str:
    return f"{n / 1048576:.0f} MB" if n else "—"


#: 画布 → 用户能读的画质档名（720p 高清包落地时同一张表直接生效）
_QUALITY_LABELS = {(640, 360): "标清", (1280, 720): "高清"}


def _quality_label(canvas) -> str:
    return _QUALITY_LABELS.get((int(canvas[0]), int(canvas[1])),
                               f"{canvas[0]}×{canvas[1]}")


def _friendly_error(text) -> str:
    """下载失败的人话版（URL / 字节数 / 异常细节不甩到用户脸上）。"""
    t = str(text)
    if "磁盘空间不足" in t:
        return t
    if "另一个下载" in t or "另一个安装" in t:
        return "另一个下载任务正在进行，请稍候"
    if "未完成的安装事务" in t:
        return "上次安装未收尾，请重启应用后重试"
    if "sha256 不符" in t or "校验失败" in t:
        return "下载内容校验失败，请重试"
    if "字节数不符" in t or "没有可用的下载地址" in t or "urlopen" in t.lower():
        return "网络连接多次中断（已保留断点），请重试"
    # 兜底：URL 一律抹成「下载源」，只留短句
    return re.sub(r"https?://\S+", "下载源", t)[:80]


def _parse_part_filename(path: Path) -> tuple[str, str] | None:
    """从分件文件名解析 ``(角色 id, 版本目录名)``。

    发布门产物的命名是 ``<id>-<version>-<variant>-<WxH>.<part>.zip``
    （``shenshen-2.0.0-base-640x360.extra3.zip``）。用户手动下载的文件叫别的
    名字就拒掉（版本目录名没法猜——猜错了装成"另一个包"）。
    """
    m = re.match(r"^([A-Za-z0-9_-]+)-([0-9][A-Za-z0-9._-]*)-([A-Za-z0-9_-]+)-(\d+x\d+)\.[A-Za-z0-9_-]+\.zip$",
                 path.name)
    if not m:
        return None
    cid, version, variant, wh = m.group(1), m.group(2), m.group(3), m.group(4)
    return cid, f"{version}-{variant}-{wh}"


class _LocalInstaller(QWidget):
    """「从本地文件安装…」：选一个分件 zip，走与应用内下载**完全相同**的安装闸
    （解包预算 / 结构校验 / 发行身份 / 能播验证 / 事务安装）。"""

    done = Signal(str, bool)

    def __init__(self, parent=None, *, on_installed=None, on_changed=None):
        super().__init__(parent)
        self._on_installed = on_installed
        self._on_changed = on_changed
        self._btn = QPushButton("从本地文件安装…", self)
        self._btn.clicked.connect(self._pick)
        self._status = QLabel("", self)
        self._status.setObjectName("settingHint")
        self.done.connect(self._on_done)

    def button(self) -> QPushButton:
        return self._btn

    def status(self) -> QLabel:
        return self._status

    def _pick(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择角色包分件", str(Path.home()),
            "角色包分件 (*.zip)")
        if not selected:
            return
        path = Path(selected)
        parsed = _parse_part_filename(path)
        if parsed is None:
            self._status.setText(
                "文件名需为 <角色>-<版本>-<变体>-<宽x高>.<分件>.zip 格式"
                "（与发布页一致，例如 shenshen-2.0.0-base-640x360.extra3.zip）")
            return
        cid, folder = parsed
        self._btn.setEnabled(False)
        self._status.setText("安装中…")

        def work() -> None:
            try:
                target = characters_root() / cid / folder
                info = pd.install_part(path, target)
                self.done.emit(f"已安装 {info['clips']} 段，重启后生效", True)
            except Exception as exc:      # noqa: BLE001 - 失败要显示给用户
                self.done.emit(f"失败：{_friendly_error(exc)}", False)

        threading.Thread(target=work, daemon=True, name="pack-local-install").start()

    def _on_done(self, text: str, ok: bool) -> None:
        # 按钮/状态标签可能已随卡片重建被删（逻辑对象活着、可见对象死了）：
        # 与 _RegistryLoader._emit 同纪律，死了就丢，_rerender 会重建出新状态。
        try:
            self._status.setText(text)
            # 本地安装完成后按钮必须**恢复可点**（P2：离线/清单为空时页面
            # 没有 _entries 不会重建入口，不许一次成功后入口永久禁用）
            self._btn.setEnabled(True)
        except RuntimeError:
            pass
        if ok:
            for cb in (self._on_installed, self._on_changed):
                if cb is not None:
                    try:
                        cb()
                    except Exception:      # noqa: BLE001
                        logging.getLogger(__name__).debug('装完刷新失败', exc_info=True)


#: 状态徽标（rich text 彩色圆点；蓝=当前使用、绿=已安装、灰=未安装、黄=需升级）
_BADGE_ACTIVE = "<span style='color:#3a7bd5'>●</span> 当前使用"
_BADGE_NONE = "<span style='color:#999999'>○</span> 未安装"
_BADGE_INSTALLED = "<span style='color:#3d9a50'>●</span> 已安装"


def _badge_installed(done: int, total: int) -> str:
    return f"<span style='color:#3d9a50'>●</span> 已装 {done}/{total}"


def _preview_pixmap(target: Path) -> QPixmap | None:
    """包预览图：core 的 idle 第一段首帧（96×54；读不到就给 None，卡片显示占位）。"""
    try:
        idle_root = Path(target) / "anim" / "idle"
        if not idle_root.is_dir():
            return None
        for clip in sorted(idle_root.iterdir()):
            frame = clip / "f_0001.webp"
            if frame.is_file():
                pm = QPixmap(str(frame))
                if not pm.isNull():
                    return pm.scaled(96, 54, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
    except OSError:
        pass
    return None


class _PackCard(QFrame):
    """一个素材包一张卡（复用 ``settingsCard`` 样式令牌）。

    布局：左预览图 96×54，右侧 标题+徽标 / 信息行 / 操作按钮 / 进度条 / 折叠明细。
    下载进度（``progress_value`` 信号）驱动真进度条——不展开也看得见（用户拍板
    的包行进度要求的卡片化落地）。
    """

    def __init__(self, title: str, badge: str, info: str, *,
                 preview: QPixmap | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(12)
        preview_label = QLabel(self)
        preview_label.setFixedSize(96, 54)
        if preview is not None:
            preview_label.setPixmap(preview)
        else:
            preview_label.setText("无预览")
            preview_label.setObjectName("settingHint")
            preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(preview_label, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)
        head = QHBoxLayout()
        title_label = QLabel(title, self)
        title_label.setObjectName("settingLabel")
        head.addWidget(title_label, 1)
        badge_label = QLabel(badge, self)
        badge_label.setObjectName("settingHint")
        head.addWidget(badge_label, 0, Qt.AlignmentFlag.AlignRight)
        right.addLayout(head)
        self._info = QLabel(info, self)
        self._info.setObjectName("settingHint")
        self._info.setWordWrap(True)
        right.addWidget(self._info)
        self._actions = QHBoxLayout()
        self._actions.setSpacing(8)
        right.addLayout(self._actions)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setVisible(False)
        right.addWidget(self._bar)
        self._extra = QVBoxLayout()
        self._extra.setContentsMargins(0, 0, 0, 0)
        right.addLayout(self._extra)
        outer.addLayout(right, 1)

    def add_action(self, widget: QWidget) -> None:
        self._actions.addWidget(widget)

    def add_extra(self, widget: QWidget) -> None:
        self._extra.addWidget(widget)

    def set_progress(self, got: int, total: int) -> None:
        """数值进度（0..total）：下载中显示进度条，其它时刻藏起来。"""
        if total > 0 and 0 <= got < total:
            self._bar.setVisible(True)
            self._bar.setValue(min(99, got * 100 // max(1, total)))
        else:
            self._bar.setVisible(False)

    def hide_progress(self) -> None:
        self._bar.setVisible(False)

    def set_info(self, text: str) -> None:
        self._info.setText(text)


def _safe_card_done(card: _PackCard, static_hint: str, text: str, ok: bool) -> None:
    """下载收尾写卡片：卡片可能已随 _rerender 被 deleteLater（P1 复核），
    与 _RegistryLoader._emit 同纪律——死了就丢，重建的新卡自有正确状态。"""
    try:
        card.hide_progress()
        card.set_info(static_hint if ok else text)
    except RuntimeError:
        pass


class _BatchDownloader(QWidget):
    """「一键下载全部」：把缺的分件逐个下完（后台线程，进度经 signal 回主线程）。

    与 ``_Downloader`` 同一纪律：绝不在 GUI 线程下载/解压；装完由
    ``on_installed``（重建已安装区块）与 ``on_changed``（重铺清单行）收尾。
    """

    done = Signal(str, bool)
    progressed = Signal(str)
    #: 数值进度（got, total）：卡片进度条用，与文字进度同一次采样发出。
    progress_value = Signal(object, object)

    def __init__(self, pack, parts, target_dir: Path, parent=None, *,
                 on_installed=None, on_changed=None):
        super().__init__(parent)
        self._pack = pack
        self._parts = list(parts)
        self._target = target_dir
        self._on_installed = on_installed
        self._on_changed = on_changed
        total = sum(p.bytes for p in self._parts)
        self._btn = QPushButton(f"一键下载全部（{_fmt_mb(total)}）", self)
        self._btn.clicked.connect(self._start)
        self._status = QLabel("", self)
        self._status.setObjectName("settingHint")
        self.done.connect(self._on_done)
        self.progressed.connect(self._status.setText)

    def button(self) -> QPushButton:
        return self._btn

    def status(self) -> QLabel:
        return self._status

    def _start(self) -> None:
        self._btn.setEnabled(False)

        def work() -> None:
            try:
                missing = [p for p in self._parts
                           if p.name not in _installed_part_names(self._target)]
                if not missing:
                    self.done.emit("已是全部安装状态", True)
                    return
                probe = self._target
                while not probe.exists() and probe != probe.parent:
                    probe = probe.parent
                import shutil
                free = shutil.disk_usage(probe).free
                need = (sum(p.bytes for p in missing)
                        + int(sum((p.unpacked_bytes or p.bytes) for p in missing) * 1.2))
                if free < need:
                    raise pd.DownloadError(
                        f"磁盘空间不足：约需 {need // 1048576} MB，剩余 {free // 1048576} MB")
                for i, part in enumerate(missing, 1):
                    self.progressed.emit(
                        f"下载 {part.name}（{i}/{len(missing)}，{_fmt_mb(part.bytes)}）…")
                    archive = pd.download_part(
                        part, self._target,
                        progress=lambda got, total: self.progress_value.emit(got, total))
                    pd.install_part(archive, self._target)
                    try:
                        archive.unlink()
                    except OSError:
                        pass
                self.done.emit("全部已安装，重启后生效", True)
            except Exception as exc:      # noqa: BLE001 - 失败要显示给用户
                self.done.emit(f"失败：{_friendly_error(exc)}", False)

        threading.Thread(target=work, daemon=True,
                         name=f"pack-dl-all-{self._pack.id}").start()

    def _on_done(self, text: str, ok: bool) -> None:
        # 按钮/状态标签可能已随卡片重建被删（逻辑对象活着、可见对象死了）：
        # 与 _RegistryLoader._emit 同纪律，死了就丢，_rerender 会重建出新状态。
        try:
            self._status.setText(text)
            self._btn.setEnabled(not ok)
        except RuntimeError:
            pass
        if ok:
            for cb in (self._on_installed, self._on_changed):
                if cb is not None:
                    try:
                        cb()
                    except Exception:      # noqa: BLE001 - 刷新失败不改下载结果
                        logging.getLogger(__name__).debug('装完刷新失败', exc_info=True)


class _Downloader(QWidget):
    """一个分件一行：名字 + 体积 + 进度/结果。下载跑在后台线程。"""

    done = Signal(str, bool)
    #: 进度也必须经 signal 回主线程——在 worker 线程里直接 setText 是跨线程碰
    #: QWidget（Qt 不允许，轻则警告重则崩）。
    progressed = Signal(str)
    #: 数值进度（got, total）：卡片进度条用。
    progress_value = Signal(object, object)

    def __init__(self, part: pd.Part, target_dir: Path, parent=None, *,
                 on_installed=None, on_changed=None):
        super().__init__(parent)
        self._part = part
        self._target = target_dir
        self._on_installed = on_installed
        self._on_changed = on_changed
        self._btn = QPushButton(f"下载 {part.name}（{_fmt_mb(part.bytes)}）", self)
        self._btn.clicked.connect(self._start)
        self._status = QLabel("", self)
        self._status.setObjectName("settingHint")
        self.done.connect(self._on_done)
        self.progressed.connect(self._status.setText)

    def button(self) -> QPushButton:
        return self._btn

    def status(self) -> QLabel:
        return self._status

    def _start(self) -> None:
        self._btn.setEnabled(False)
        self._status.setText("下载中…")

        def work() -> None:
            try:
                # 磁盘不足预检（终审：几百 MB 的东西下到一半才发现放不下是最差的
                # 失败方式）。压缩包 + 解包余量（webp 已压缩，解包≈下载体积）按 2 倍算。
                probe = self._target
                while not probe.exists() and probe != probe.parent:
                    probe = probe.parent
                free = __import__("shutil").disk_usage(probe).free
                # 压缩包 + 解包余量（清单带 unpacked_bytes 时按真实解包体积 +20%）
                unpacked = self._part.unpacked_bytes or self._part.bytes
                need = self._part.bytes + int(unpacked * 1.2)
                if free < need:
                    raise pd.DownloadError(
                        f"磁盘空间不足：约需 {need // 1048576} MB，剩余 {free // 1048576} MB")
                archive = pd.download_part(
                    self._part, self._target,
                    progress=lambda got, total: (
                        self.progressed.emit(
                            f"下载中 {got // 1048576}/{total // 1048576} MB"
                            f"（{got * 100 // max(1, total)}%）"),
                        self.progress_value.emit(got, total)))
                pd.install_part(archive, self._target)
                try:
                    archive.unlink()
                except OSError:
                    pass
                # 重启生效：装完只改磁盘与指针，播放进程下次启动时校验并应用
                self.done.emit("已安装，重启后生效", True)
            except Exception as exc:      # noqa: BLE001 - 失败要显示给用户
                self.done.emit(f"失败：{_friendly_error(exc)}", False)

        threading.Thread(target=work, daemon=True, name=f"pack-dl-{self._part.name}").start()

    def _on_done(self, text: str, ok: bool) -> None:
        # 按钮/状态标签可能已随卡片重建被删（逻辑对象活着、可见对象死了）：
        # 与 _RegistryLoader._emit 同纪律，死了就丢，_rerender 会重建出新状态。
        try:
            self._status.setText(text)
            self._btn.setEnabled(not ok)
        except RuntimeError:
            pass
        if ok:
            for cb in (self._on_installed, self._on_changed):
                if cb is not None:
                    try:
                        cb()
                    except Exception:      # noqa: BLE001 - 刷新失败不改下载结果
                        logging.getLogger(__name__).debug('装完刷新失败', exc_info=True)


#: 注册表响应体积上限：无上限 `read()` 会被一个坏源拖住（也可能吃满内存）。
#: 注册表是几 KB 的清单，1 MiB 已经非常宽松（终审第四轮 B12）。
MAX_REGISTRY_BYTES = 1024 * 1024


def _fetch_registry_text(url: str) -> str:
    """取注册表正文（**限体积 + 来源白名单**）。超限直接报错，不做"截断后解析"。

    清单地址也要过可信 URL 校验（终审：清单是整个信任链的根，不能什么 URL
    都 urlopen）——官方默认地址与 ``PET_PACK_REGISTRY`` 覆盖都必须 https 且
    主机在白名单里。
    """
    if not _trusted_https_url(url):
        raise ValueError(f"清单地址不在可信白名单（必须 https + 官方主机）: {url}")
    from .pack_download import trusted_urlopen
    with trusted_urlopen(url, timeout=20) as resp:      # noqa: S310 - 已过白名单+逐跳校验
        raw = resp.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError(f"注册表超过体积上限 {MAX_REGISTRY_BYTES} 字节")
    return raw.decode("utf-8")


def _registry_parts(urls) -> list[tuple[pd.Pack, pd.Part]]:
    """按序拉注册表并摊平成 (pack, part) 列表；全部失败抛最后一个错误。

    拉取、JSON 解码、结构校验（``parse_registry``）都在**同一个逐源尝试边界**
    里（P2：第一个源返回合法 JSON 但清单结构不合法时，不许就此放弃镜像组——
    换下一个源，全灭才报错）。
    """
    last: Exception | None = None
    for url in urls:
        try:
            data = json.loads(_fetch_registry_text(url))
            out: list[tuple[pd.Pack, pd.Part]] = []
            for pack in pd.parse_registry(data):
                for part in pack.parts:
                    out.append((pack, part))
            return out
        except Exception as exc:      # noqa: BLE001 - 换下一个镜像
            last = exc
            continue
    raise ValueError(str(last) if last else "没有可用的清单地址")


class _RegistryLoader(QObject):
    """注册表**后台拉取**（终审 B12：设置页构造时同步联网会卡住整个窗口）。"""

    loaded = Signal(object)      # list[(pack, part)]
    failed = Signal(str)

    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self._urls = list(urls)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="pack-registry").start()

    def _run(self) -> None:
        try:
            entries = _registry_parts(self._urls)
        except Exception as exc:      # noqa: BLE001 - 失败要显示给用户
            self._emit(self.failed, str(exc))
            return
        self._emit(self.loaded, entries)

    @staticmethod
    def _emit(signal, payload) -> None:
        """设置页可能已经关了：对象已析构时 emit 会抛 RuntimeError（线程里无法被
        pytest 捕获，会报未处理线程异常）。这里按"没人收就丢掉"处理。"""
        try:
            signal.emit(payload)
        except RuntimeError:
            pass


class _DownloadSection(QObject):
    """「可下载」区块的数据驱动（后台拉清单 → 换行，远端 ∪ 本地合并视图）。

    **外壳（``SettingsSection("可下载")``）必须是域内容布局的直接块**——设置页
    指南与跳转条的覆盖测试只穿透普通容器层，把外壳藏进自定义 QWidget 里会让
    「可下载」在装配里"消失"（stale 检查实测抓到过）。换行只换块的内容，
    不换它在布局里的位置。
    """

    def __init__(self, urls, layout: QVBoxLayout, content: QWidget, *,
                 on_installed=None):
        super().__init__(content)
        self._layout = layout
        self._content = content
        self._on_installed = on_installed
        self._section = SettingsSection("可下载", [
            _InfoRow(content, "正在拉取角色包列表…", "网络慢或源不可达时不会卡住设置页"),
        ], content)
        layout.addWidget(self._section)
        self._loader = _RegistryLoader(urls, self)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def _replace(self, title: str, rows) -> None:
        idx = self._layout.indexOf(self._section)
        self._layout.removeWidget(self._section)
        self._section.deleteLater()
        self._section = SettingsSection(title, rows, self._content)
        self._layout.insertWidget(max(0, idx), self._section)

    def _local_install_widgets(self) -> list:
        """「从本地文件安装…」行（断网/镜像站手动下载的 zip 也能装）。

        联网清单加载失败时这行也必须在——手动安装本来就不依赖清单。
        """
        li = _LocalInstaller(self._content, on_installed=self._on_installed,
                             on_changed=self._rerender)
        return [_InfoRow(self._content, "手动下载的分件",
                         "从本地 zip 文件安装（文件名需与发布页一致）",
                         control=li.button()), li.status()]

    def _on_loaded(self, entries) -> None:
        if not entries:
            self._replace("可下载", [
                _InfoRow(self._content, "下载源里暂无分件", "确认来源地址指向的是角色包注册表"),
                *self._local_install_widgets()])
            return
        self._entries = entries
        by_pack: dict = {}
        for pack, part in entries:
            by_pack.setdefault(pack, []).append(part)
        rows = []
        for pack, parts in by_pack.items():
            target = characters_root() / pack.id / pack.folder_name()
            label = _quality_label(pack.canvas)
            if not app_supports(pack.app_min):
                # 不兼容的包**显示但置灰**（终审：不许假装它不存在）
                rows.append(_InfoRow(
                    self._content,
                    f"{label}角色包 · {pack.version}（{pack.canvas[0]}×{pack.canvas[1]}）",
                    f"需要应用 ≥ {pack.app_min} 才能使用，请先升级应用"))
                continue
            installed = _installed_part_names(target)
            active = _is_active(target)
            total = sum(p.bytes for p in parts)
            badge = (_BADGE_ACTIVE if active
                     else _badge_installed(len(installed), len(parts)) if installed
                     else _BADGE_NONE)
            static_hint = f"已装 {len(installed)}/{len(parts)} 个分件 · 共 {_fmt_mb(total)}"
            card = _PackCard(f"{label}角色包 · {pack.version}", badge, static_hint,
                             preview=_preview_pixmap(target), parent=self._content)
            batch = _BatchDownloader(pack, parts, target, self._content,
                                     on_installed=self._on_installed,
                                     on_changed=self._rerender)
            card.add_action(batch.button())
            batch.progressed.connect(card.set_info)
            batch.progress_value.connect(card.set_progress)
            batch.done.connect(
                lambda text, ok, c=card, s=static_hint: _safe_card_done(c, s, text, ok))
            group = CollapsibleGroup(
                f"分件明细（{len(parts)} 个）", self._content, expanded=False,
                description="每个分件单独下载，断点续传；装完重启后生效。")
            for part in parts:
                if part.name in installed:
                    group.body_layout.addWidget(
                        _InfoRow(group.body, f"{part.name}（{_fmt_mb(part.bytes)}）",
                                 "已安装，重启后生效"))
                    continue
                # 任务对象必须挂在长寿的 content 上（P1：_rerender 会 deleteLater
                # 整棵卡片树；挂在 group.body 下的下载器会被一起删掉，worker 线程
                # 随后 emit 直接 RuntimeError——并发下载时先完成的弄死后完成的）。
                # 按钮/状态标签交给布局随便重挂父（卡片没了会重建），逻辑对象不死。
                dl = _Downloader(part, target, self._content,
                                 on_installed=self._on_installed,
                                 on_changed=self._rerender)
                dl.progress_value.connect(card.set_progress)
                dl.done.connect(
                    lambda text, ok, c=card, s=static_hint: _safe_card_done(c, s, text, ok))
                group.body_layout.addWidget(
                    _InfoRow(group.body, part.name, "", control=dl.button()))
                group.body_layout.addWidget(dl.status())
            card.add_extra(group)
            rows.append(card)
        rows.extend(self._local_install_widgets())
        self._replace("可下载", rows)

    def _rerender(self) -> None:
        """装完一批后重铺清单行（分件状态 未下载 → 已安装）。"""
        entries = getattr(self, "_entries", None)
        if entries:
            self._on_loaded(entries)

    def _on_failed(self, message: str) -> None:
        # 说人话（终审：离线不许写成"已是最新"；原始错误进日志，不甩给用户）：
        # 清单加载失败 ≠ 素材坏了——已安装的照常可用，手动安装也不依赖清单。
        logging.getLogger(__name__).info('角色包清单拉取失败: %s', message)
        self._replace("可下载", [
            _InfoRow(self._content, "暂时拉不到素材清单",
                     "网络不通或清单还没发布；已安装的素材不受影响。"),
            *self._local_install_widgets()])


def _installed_part_names(target: Path) -> set[str]:
    """该版本目录里**真正装好**的分件名。

    不只看索引的 parts 列表（终审：删掉一段素材后索引还在，若仍显示"已安装"
    且不给下载按钮，用户就失去了重下修复的入口）——该分件声明的 clip 目录
    有一个不在盘上，就按未安装处理（页面会给下载按钮）。
    """
    try:
        doc = json.loads((target / "character.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(doc, dict):
        return set()
    parts = doc.get("parts")
    if not isinstance(parts, list):
        return set()
    clip_parts: dict[str, list[str]] = {}
    clips = doc.get("clips")
    if isinstance(clips, list):
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            part, rel = clip.get("part", "core"), clip.get("dir")
            if isinstance(rel, str):
                clip_parts.setdefault(str(part), []).append(rel)
    out: set[str] = set()
    for part in parts:
        part = str(part)
        dirs = clip_parts.get(part)
        if dirs is None:
            continue                      # 索引里没有这个分件的 clip，按未装
        if all((target / d).is_dir() for d in dirs):
            out.add(part)
    return out


class _InfoRow(QWidget):
    """一行普通信息（标题 + 说明 + 可选右侧控件）。

    **不用 SettingRow**：角色包列表/下载不是「持久化设置项」，套 SettingRow 会被
    「每个设置行必须归属某个域」的契约当成漏登记的设置项。
    """

    def __init__(self, parent, title: str, hint: str, *, control: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        head = QLabel(title, self)
        head.setObjectName("settingLabel")
        head.setWordWrap(True)
        layout.addWidget(head)
        self._hint_label: QLabel | None = None
        if hint:
            self._hint_label = QLabel(hint, self)
            self._hint_label.setObjectName("settingHint")
            self._hint_label.setWordWrap(True)
            layout.addWidget(self._hint_label)
        if control is not None:
            layout.addWidget(control)

    def set_hint(self, text: str) -> None:
        """改说明文字（包行下载进度用：不展开分件明细也看得到）。"""
        if self._hint_label is None:
            self._hint_label = QLabel(text, self)
            self._hint_label.setObjectName("settingHint")
            self._hint_label.setWordWrap(True)
            self.layout().insertWidget(1, self._hint_label)
        else:
            self._hint_label.setText(text)


def build_content(dialog=None) -> QWidget:
    """构造「角色与素材」页内容（由设置对话框调用）。"""
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(18)

    def _installed_section() -> SettingsSection:
        packs = installed_packs()
        if packs:
            rows = []
            for p in packs:
                canvas = p.get("canvas") or [0, 0]
                quality = _quality_label(canvas) if canvas and canvas[0] else p["name"]
                seg = p["folder"].split("-")
                version = seg[0]
                variant = f" · {seg[1]}" if len(seg) >= 3 and seg[1] != "base" else ""
                badge = _BADGE_ACTIVE if p.get("active") else _BADGE_INSTALLED
                card = _PackCard(f"{quality}角色包 · {version}{variant}", badge,
                                 f"{p['clips']} 段动画 · 约 {_fmt_mb(p['bytes'])}",
                                 preview=_preview_pixmap(p["path"]), parent=content)
                if not p.get("active"):
                    use = QPushButton("使用这套")
                    use.clicked.connect(
                        lambda _checked=False, path=p["path"], btn=use: _on_use_clicked(path, btn))
                    card.add_action(use)
                rows.append(card)
            return SettingsSection("已安装的角色包", rows, content)
        return SettingsSection("已安装的角色包", [
            _InfoRow(content, "暂无已安装的角色包",
                     "角色素材会装到用户数据目录；下面的「可下载」里可直接安装。"),
        ], content)

    holder: dict = {}

    def _refresh_installed() -> None:
        """装完分件后重建「已安装」区块（闭环：装完当页就能点「使用这套」，
        不用关掉设置页重开——终审第七轮）。"""
        old = holder.get("section")
        idx = layout.indexOf(old) if old is not None else 0
        if old is not None:
            layout.removeWidget(old)
            old.deleteLater()
        holder["section"] = _installed_section()
        layout.insertWidget(max(0, idx), holder["section"])

    holder["section"] = _installed_section()
    layout.addWidget(holder["section"])

    # 后台拉取官方清单（jsdelivr 镜像组）：绝不在 GUI 线程联网（终审 B12）。
    # 清单不内置——发新素材包不需要用户更新应用。
    _DownloadSection(registry_urls(), layout, content, on_installed=_refresh_installed)

    layout.addStretch(1)
    content.setProperty("contentMaxWidth", 720)
    return content


def build_section(dialog=None) -> QWidget:
    """给「桌宠」域用的一整块内容（与 build_content 同一份数据，供页面装配复用）。"""
    return build_content(dialog)
