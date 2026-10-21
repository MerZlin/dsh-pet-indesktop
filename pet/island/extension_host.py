# -*- coding: utf-8 -*-
"""第三方灵动岛组件的**稳定声明源** + **有界后台采样**（宿主侧，零 Qt）。

分工：

- ``pet/island_segments.py`` 是**契约层**（第三方交什么、宿主怎么兜底，纯逻辑零 Qt）；
- 本模块是**宿主侧**：① 从可信本地目录读**静态声明清单**（stdlib JSON）；
  ② 只在**运行期**按订阅导入 provider 并采样；③ 采样限定在**一条**有界节拍的
  后台线程里，GUI 只读快照——它自己既不 ``read`` 也不 ``format``。

## 可信边界（写清楚，否则迟早被当成沙箱用）

- **这不是沙箱**。组件是用户放进可信目录的**本地代码**，provider 在**本进程**里
  执行，权限与本应用相同。这里防的是"一个组件的异常读数 / 慢读拖垮界面"，
  **不防**恶意插件——所以没有"线程超时杀线程"那种假承诺（Python 杀不掉阻塞中的
  线程）：单次 ``read`` 超过阻塞预算就记一次慢读，连续到 :data:`BLOCK_LIMIT` 次
  **熔断**该段（此后不再调它），界面照常画、其余段照常采。
- **不做插件市场、不下载、不远程执行、不扫别的目录**。只有下面这几个根目录，
  清单是 JSON，provider 只在本进程内 import。
- **设置进程只读静态 metadata**：:func:`declarations` 只解析 JSON，**不 import 任何
  provider**——独立设置页（``python -m pet --settings``）因此不可能执行组件代码。

## 目录与清单

每个组件一个子目录，根目录下按目录名排序扫描：

- **用户根**：既有配置目录下的 ``island-extensions``（路径算法复用
  ``pet/config.py`` 的 ``APP_DIR_NAME`` / ``_default_base``，**不硬编码机器路径**）。
  Windows 上即 ``%APPDATA%/dsh-pet-standalone/island-extensions``；
- **项目根**：仓库（打包后是程序目录）下的 ``island-extensions``；
- 环境变量 ``DSH_PET_ISLAND_EXTENSION_DIRS``（``os.pathsep`` 分隔）：**一旦设置就
  只扫它**（显式指定的可信目录，开发与测试用）。

::

    <根目录>/<组件目录>/
        island-component.json    # 静态声明（本模块唯一的静态读入口）
        provider.py              # 运行期才导入的取值/格式化实现（文件名由清单指定）

清单（键缺一即拒收，不半信半疑地加载）::

    {
      "id": "cpu_temp",
      "label": "温度",
      "max_text": "88",
      "unit": "°C",
      "unit_full": "摄氏度",
      "source": "本机传感器",
      "detail": "CPU 封装温度",
      "thresholds": [70, 85],
      "provider": "provider.py",
      "read": "read_celsius",
      "format": "format_celsius"
    }

``id`` / ``label`` / ``max_text`` / ``unit`` / ``thresholds`` 的校验**复用契约层的同一份
实现**（清单就是那个声明去掉两个可调用对象），所以清单与 ``register_segment`` 的
口径不可能各自漂移。``provider`` / ``read`` / ``format`` 都是相对组件目录的定位，
路径不许爬出组件目录。

## 采样（一条线程，有界节拍）

- **谁调 read**：只有 :class:`ExtensionSampler` 的那条后台线程。宿主（GUI 线程）
  只读 :meth:`ExtensionSampler.snapshot` 给出的**字典副本**。
- **快照里有什么**：原始值（``raw_value``，告警阈值要它）、护栏之后的文案
  （``text``）、状态（``state``）、原因（``error_reason``）、采样时刻
  （``sampled_at``，monotonic）、**代次**（``epoch``）与**每段订阅代次**（``token``）。
- **两道代次隔离**：``start()`` 换代、``stop()`` 作废当刻代（在途那一拍回来后按
  ``epoch`` 判别，旧代结果一律丢弃）；``unsubscribe`` → ``subscribe`` 自增该段的
  **token**——那一段在飞的旧读数带着当刻 ``epoch`` 与 ok 状态，只有 token 拦得住它
  （"退订即丢它的读数：迟到不复活"）。
- **来源版本失效**：解析结果与 provider 文件的 ``(路径, mtime_ns, size)`` 绑定。
  清单不变但文件被**删掉 / 装回来 / 换内容**时，下一次采样就丢掉旧解析重新 import；
  声明扫描命中缓存时也按同一版本核对（见 :func:`_scan_cached`），所以"用户装回来了、
  岛却一直不订阅"与"文件删了、旧代码还在跑"这两条都不会发生。
- **线程有界**：只有这一条采样线程；``stop()`` 杀不掉卡在组件 ``read`` 里的线程，
  所以那条 worker 会被**保留引用**，``start()`` 在它还活着时**接续**它（清除停止位、
  换代、继续采新订阅），绝不开第二条 worker 同时跑同一个 provider。
- **0 订阅 = 零开销**：没人订阅时线程只等事件，一次 provider 调用都不发生。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Callable

from . import segments as island_segments

logger = logging.getLogger(__name__)

#: 静态清单文件名（每个组件目录一个）。
MANIFEST_NAME = "island-component.json"
#: 清单允许的键（多余键一律拒收：拼错的字段名不许静默丢弃）。
MANIFEST_KEYS = frozenset({
    "id", "label", "max_text", "unit", "unit_full", "source", "detail",
    "thresholds", "provider", "read", "format",
})
#: 根目录名：用户根在既有配置目录下，项目根在仓库/程序目录下（同一个名字）。
ROOT_DIR_NAME = "island-extensions"
#: 显式覆盖根目录的环境变量（``os.pathsep`` 分隔）：设置后**只扫它**。
ENV_DIRS = "DSH_PET_ISLAND_EXTENSION_DIRS"
#: provider 里取值函数 / 格式化函数的默认属性名。
DEFAULT_READ_ENTRY = "read"
DEFAULT_FORMAT_ENTRY = "format_value"

#: 采样节拍（秒）与它的可配区间：与内置段同一量级（1Hz），也允许组件放慢。
DEFAULT_INTERVAL_SECONDS = 1.0
MIN_INTERVAL_SECONDS = 0.5
MAX_INTERVAL_SECONDS = 60.0
#: 采样线程名（日志与线程枚举里能一眼认出是谁）。
THREAD_NAME = "pet-island-ext"

#: 单次 ``read`` 的**阻塞预算**（秒）：超过它记一次慢读。
BLOCK_BUDGET_SECONDS = 2.0
#: 连续多少次慢读之后熔断该段（此后不再调它的 ``read``）。
BLOCK_LIMIT = 2

#: "调用方没给这个参数"的哨兵（``None`` 是合法取值——"没有文件来源"，不能当缺省）。
_UNSET: Any = object()

#: provider 模块缓存上限（LRU）：每个路径只留**最新版本**，总量再有这个上限兜底。
#: 用户每改一次组件文件都是一个新键，不淘汰就是随"编辑次数"无界增长；上限远大于
#: 岛上能同时显示的段数，所以正常使用不会触发跨组件的反复重 import。
PROVIDER_CACHE_LIMIT = 32


class ProviderUnavailable(RuntimeError):
    """组件**暂缺**：清单在、provider 文件不在（未安装 / 被移除）。

    与"provider 坏了"分开：暂缺是"这份偏好先留着，不采样也不呈现"，
    坏了是"它在，但它报错"（进 error 态 + 卡片写原因）。
    """


class ProviderBroken(RuntimeError):
    """provider 在，但导入失败 / 缺入口函数 / 入口不可调用。"""


# ---------------------------------------------------------------- 静态声明
@dataclass(frozen=True)
class ComponentDeclaration:
    """一个组件的**静态声明**（清单归一化之后的真值，绝不含可调用对象）。

    字段面与 ``island_segments.SegmentDeclaration`` 的静态部分逐条对齐：这是在
    **设置进程**里唯一被读到的形状——它不可能执行组件代码（没有 provider 的
    入口对象，只有一个路径）。
    """

    segment_id: str
    label: str = ""
    max_text: str = ""
    unit: str = ""
    unit_full: str = ""
    source: str = ""
    detail: str = ""
    thresholds: tuple[float, float] | None = None
    provider: str = ""
    read_entry: str = DEFAULT_READ_ENTRY
    format_entry: str = DEFAULT_FORMAT_ENTRY
    directory: Path = field(default_factory=Path)
    #: 清单里声明的 provider 文件此刻是否存在（**扫描期**判定，重扫即刷新）。
    provider_present: bool = False

    @property
    def provider_path(self) -> Path:
        return self.directory / self.provider

    def missing_reason(self) -> str:
        """"组件暂缺"的**人话原因**（卡片原因行与设置页提示共用同一句）。"""
        return f"组件暂缺：找不到 provider 文件 {self.provider or '(未声明)'}"

    def contract_declaration(self, read: Callable, format_value: Callable):
        """静态声明 + 运行期入口 → 契约层的 ``SegmentDeclaration``（唯一装配点）。

        采样线程要用它的护栏（格式化异常 / 巨型串 / 多行 / None 一律就地兜住），
        所以这里不是"又造一个声明体系"，而是把清单接进契约层已有的那一个。
        """
        return island_segments.SegmentDeclaration(
            id=self.segment_id, format_value=format_value, max_text=self.max_text,
            label=self.label, unit=self.unit, unit_full=self.unit_full,
            source=self.source, detail=self.detail, thresholds=self.thresholds,
            read=read,
        )

    @property
    def id(self) -> str:
        """与契约层声明同形的别名（判决/缓存按 ``id`` 取键）。"""
        return self.segment_id

    def probe_declaration(self):
        """**只用来量宽/判超宽**的探针声明（不带 provider 入口，不会被执行）。

        超宽判决用的是契约层那份实现（``SegmentDeclaration.is_oversize`` /
        ``island_segments.judge_oversize``），所以"哪些段先被折"这件事的 px 口径
        只有一份：清单声明的盒宽与注册声明的盒宽按同一个公式实测。
        """
        return island_segments.SegmentDeclaration(
            id=self.segment_id, format_value=_noop_formatter, max_text=self.max_text,
            label=self.label, unit=self.unit, unit_full=self.unit_full,
            source=self.source, detail=self.detail, thresholds=self.thresholds,
        )


@dataclass(frozen=True)
class RejectedComponent:
    """被拒收的组件（清单坏了 / id 非法 / provider 路径非法）——如实留痕，不静默跳过。"""

    path: str
    reason: str


#: 扫描缓存：``签名 -> (声明, 被拒收的, 各声明的 provider 版本)``。签名 = 各根目录的
#: 直接子目录名 + 清单文件的 (mtime_ns, size)；provider 的增删改由第三项在命中缓存时
#: 核对（名字取自声明），两类变化都不需要重启进程（见 :func:`_scan_cached`）。
_CACHE: dict[tuple, tuple[
    tuple[ComponentDeclaration, ...], tuple[RejectedComponent, ...], tuple]] = {}
#: provider 模块缓存：``(绝对路径, mtime_ns, size) -> (read, format)``，**LRU 有界**
#: （见 :data:`PROVIDER_CACHE_LIMIT` 与 :func:`_provider_cache_put`）。同样的文件只
#: import 一次（import 有成本，且重复 import 会重复执行模块级代码）。
_PROVIDER_CACHE: "OrderedDict[tuple[str, int, int], tuple[Callable, Callable]]" = OrderedDict()


def user_root() -> Path:
    """用户可信组件根目录（沿用既有配置目录，不硬编码机器路径）。

    ``pet/config.py`` 的路径算法（``_default_base`` + ``APP_DIR_NAME``）是唯一真源；
    这里**延迟导入**它，免得 ``config`` 与本模块在 import 期互相等待。
    """
    from ..config import APP_DIR_NAME, _default_base

    return Path(_default_base()) / APP_DIR_NAME / ROOT_DIR_NAME


#: 项目（源码运行时是仓库、打包后是程序目录）的可信组件根目录。
#: 模块导入时算一次：``Path.resolve()`` 在 Windows 上要逐段解析（实测 ~250µs），
#: 而本函数在配置归一化路径上被反复调用（每次 reload / 保存各一次）——``__file__``
#: 在进程内不变，所以缓存它就是廉价且正确的。
_PROJECT_ROOT = Path(__file__).resolve().parents[2] / ROOT_DIR_NAME


def project_root() -> Path:
    """项目（源码运行时是仓库、打包后是程序目录）下的可信组件根目录。"""
    return _PROJECT_ROOT


def roots() -> tuple[Path, ...]:
    """本次生效的根目录序（用户根在前：显式安装的组件优先级更高）。

    环境变量 :data:`ENV_DIRS` 一旦设置就**只**扫它——"显式指定的可信目录"，
    不是"再多扫几个地方"。
    """
    override = os.environ.get(ENV_DIRS, "")
    explicit = [Path(part) for part in override.split(os.pathsep) if part.strip()]
    if explicit:
        return tuple(explicit)
    return (user_root(), project_root())


def _manifest_signature(root: Path) -> tuple:
    """一个根目录的**廉价签名**：子目录名 + 清单 (mtime_ns, size)。

    先用 ``is_dir()`` 短路不存在的根（生产默认两个根通常都不存在）：靠
    ``iterdir()`` 抛 ``OSError`` 表达"没有这个目录"要付异常构造的代价（实测
    ~200µs/次），而本函数在配置归一化路径上（每次 reload / 保存各一次）。
    ``os.scandir`` 的 ``DirEntry`` 在 Windows 上自带 stat 缓冲，比
    ``Path.iterdir() + is_dir()`` 少一轮系统调用。

    这里**刻意不解析清单**（读 + 解析一个小 JSON 在 Windows 上实测 ~160µs/组件，
    而这条路径被配置归一化反复调用）。provider 文件的版本放在 :func:`_scan_cached`
    里核对：那批声明自己带着 provider 名字（清单已经解析过了），所以"清单不变、
    只增删改 provider"也只需每组件一次 stat 就能看见（见 :data:`_CACHE`）。
    """
    if not root.is_dir():
        return ()
    entries: list[tuple] = []
    try:
        with os.scandir(root) as scan:
            children = sorted((entry for entry in scan
                               if _entry_is_dir(entry)),
                              key=lambda entry: entry.name.casefold())
    except OSError:
        return ()
    for child in children:
        manifest = Path(child.path) / MANIFEST_NAME
        try:
            stat = manifest.stat()
        except OSError:
            continue
        entries.append((child.name, int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(entries)


def _provider_version(directory: Path, provider: str) -> tuple[str, int, int]:
    """provider 文件的版本：``(路径, mtime_ns, size)``；stat 不到记 ``(-1, -1)``。

    ``-1`` 是"文件不在 / 读不动"的**如实**编码，不是伪造的版本号：装回来、删掉、
    改内容都会换签名 / 换键，运行期据此丢掉旧解析。

    边界（不假装能看见）：改写后的 mtime_ns 与 size 都与上一版**完全相同**时，这个
    版本键看不出来。现实里只有"同一时间戳 + 等价长度"才碰得上，组件按"写临时文件 +
    原子替换"发布即可避开；不靠比较假值来判断。
    """
    if not provider:
        return ("", -1, -1)
    path = directory / provider
    try:
        stat = path.stat()
    except OSError:
        return (str(path), -1, -1)
    return (str(path), int(stat.st_mtime_ns), int(stat.st_size))


def _declaration_sources(declarations) -> tuple:
    """这批声明各自的 provider 版本（名字来自声明，**不再读清单**）。"""
    return tuple((declaration.segment_id,
                  _provider_version(declaration.directory, declaration.provider))
                 for declaration in declarations)


def provider_source_keys(segment_ids) -> dict[str, tuple]:
    """一批段 id 的**来源版本键**（**一次静态扫描**）：``{段 id: 键或 None}``。

    采样线程每拍核对一次：键变了就丢掉那一段的解析缓存重新 import provider。
    纯运行期 ``register_segment`` 的段没有文件来源，键恒为 ``None``（不参与失效）。
    """
    wanted = {str(item) for item in segment_ids}
    found: dict[str, tuple] = {}
    for declaration in declarations():
        if declaration.segment_id in wanted:
            found[declaration.segment_id] = _provider_version(
                declaration.directory, declaration.provider)
    return {segment_id: found.get(segment_id) for segment_id in wanted}


def provider_source_key(segment_id: Any):
    """单段版 :func:`provider_source_keys`（``None`` = 没有文件来源）。"""
    return provider_source_keys((segment_id,))[str(segment_id)]


def _entry_is_dir(entry) -> bool:
    try:
        return bool(entry.is_dir())
    except OSError:  # pragma: no cover - 目录在扫描中途消失
        return False


def _signature() -> tuple:
    roots_now = roots()
    return tuple(str(root) for root in roots_now) + tuple(
        item for root in roots_now for item in _manifest_signature(root))


def _parse_manifest(manifest_path: Path, directory: Path):
    """清单 → 声明；任何不合规的地方**明确拒收**（返回 RejectedComponent，不抛穿）。"""
    def reject(reason: str) -> RejectedComponent:
        return RejectedComponent(path=str(manifest_path), reason=reason)

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return reject(f"清单读不出来：{type(exc).__name__}: {exc}")
    except (ValueError, UnicodeDecodeError) as exc:
        return reject(f"清单不是合法 JSON：{type(exc).__name__}: {exc}")
    if not isinstance(raw, dict):
        return reject(f"清单必须是 JSON 对象，收到 {type(raw).__name__}")
    unknown = sorted(set(raw) - MANIFEST_KEYS)
    if unknown:
        return reject(f"清单有未知字段（拼错字段名不许静默丢弃）：{unknown}")

    provider = raw.get("provider", "")
    if not isinstance(provider, str) or not provider.strip():
        return reject("缺少 provider（组件目录内的相对文件名）")
    relative = Path(provider.strip())
    # Windows 上 Path("/abs/x").is_absolute() 是 False（缺盘符）：盘符与根都要查，
    # 否则 POSIX 风格的绝对路径会绕过这条护栏。
    if relative.is_absolute() or relative.drive or relative.root or ".." in relative.parts:
        return reject(f"provider 必须是组件目录内的相对路径：{provider!r}")

    for key, default in (("read", DEFAULT_READ_ENTRY), ("format", DEFAULT_FORMAT_ENTRY)):
        entry = raw.get(key, default)
        if not isinstance(entry, str) or not entry.strip():
            return reject(f"{key} 必须是非空字符串（provider 里的属性名）")
        raw[key] = entry.strip()

    # 静态字段的校验**复用契约层**：清单就是 SegmentDeclaration 去掉两个可调用对象，
    # 所以 id/label/max_text/unit/thresholds 的规则只有一份实现（不会各自漂移）。
    try:
        probe = island_segments.SegmentDeclaration(
            id=raw.get("id"), format_value=_noop_formatter, max_text=raw.get("max_text"),
            label=raw.get("label", ""), unit=raw.get("unit", ""),
            unit_full=raw.get("unit_full", ""), source=raw.get("source", ""),
            detail=raw.get("detail", ""), thresholds=raw.get("thresholds"),
        )
    except (ValueError, TypeError) as exc:
        return reject(f"声明非法：{exc}")

    provider_path = directory / relative
    return ComponentDeclaration(
        segment_id=probe.id, label=probe.label, max_text=probe.max_text, unit=probe.unit,
        unit_full=probe.unit_full, source=probe.source, detail=probe.detail,
        thresholds=probe.thresholds, provider=str(relative),
        read_entry=raw["read"], format_entry=raw["format"],
        directory=directory, provider_present=provider_path.is_file(),
    )


def _noop_formatter(_value) -> str:  # pragma: no cover - 仅用于清单校验探针
    return ""


def _scan() -> tuple[tuple[ComponentDeclaration, ...], tuple[RejectedComponent, ...]]:
    """按根目录序扫描：**同名 id 先到先得**（用户根在前），重复的记一条 warning。"""
    declarations: list[ComponentDeclaration] = []
    rejected: list[RejectedComponent] = []
    seen: dict[str, Path] = {}
    for root in roots():
        for child in sorted((item for item in _iter_dirs(root)),
                            key=lambda path: path.name.casefold()):
            manifest = child / MANIFEST_NAME
            if not manifest.is_file():
                continue
            parsed = _parse_manifest(manifest, child)
            if isinstance(parsed, RejectedComponent):
                logger.warning("灵动岛组件被拒收（%s）：%s", parsed.path, parsed.reason)
                rejected.append(parsed)
                continue
            previous = seen.get(parsed.segment_id)
            if previous is not None:
                logger.warning(
                    "灵动岛组件 id %s 重复（%s 与 %s）：保留先扫到的那个",
                    parsed.segment_id, previous, manifest)
                rejected.append(RejectedComponent(
                    path=str(manifest),
                    reason=f"id {parsed.segment_id!r} 与 {previous} 重复"))
                continue
            seen[parsed.segment_id] = manifest
            declarations.append(parsed)
    return tuple(declarations), tuple(rejected)


def _iter_dirs(root: Path):
    try:
        return [child for child in root.iterdir() if child.is_dir()]
    except OSError:
        return []


def declarations() -> tuple[ComponentDeclaration, ...]:
    """当前全部组件声明（静态 metadata，**不 import provider**）。

    缓存按根目录签名失效：清单新增/修改/删除都会换签名，所以"装好组件不重启也能
    被下一次扫描发现"，而重复调用只花几次 ``stat``。
    """
    return _scan_cached()[0]


def rejected() -> tuple[RejectedComponent, ...]:
    """被拒收的组件（诊断用：清单坏了不静默跳过，日志里也有一条 warning）。"""
    return _scan_cached()[1]


def _scan_cached():
    """扫描（命中缓存时**同时核对 provider 版本**）。

    两道失效依据，缺一不可：

    1. **根目录签名**（清单的目录名 / mtime / size）——清单增删改换签名，重扫；
    2. **provider 文件版本**——用户装 / 卸 / 换 provider 时清单常常一字不改
       （清单只说声明，实现文件是另一次拷贝），目录自身的 mtime 也不足以当判据
       （内容替换未必改动目录 mtime）。命中缓存时对每个已声明组件 stat 一次它
       **自己声明的那份** provider：名字来自缓存里的声明，所以这一步不需要再解析
       清单（读 + 解析小 JSON 实测 ~160µs/组件，是这条路径真正贵的地方）。

    于是"清单不变、只增删改 provider"也会走重扫：``provider_present`` 重新判定、
    provider 模块按新版本重新 import（见 :func:`load_provider`）。
    """
    signature = _signature()
    cached = _CACHE.get(signature)
    if cached is not None:
        declarations, rejected_items, sources = cached
        if sources == _declaration_sources(declarations):
            return declarations, rejected_items
    declarations, rejected_items = _scan()
    _CACHE.clear()          # 只留最新一份：清单不会同时存在多个有效签名
    _CACHE[signature] = (declarations, rejected_items,
                         _declaration_sources(declarations))
    return declarations, rejected_items


def declaration_for(segment_id: Any) -> ComponentDeclaration | None:
    """按 id 取声明；不是已声明组件就返回 ``None``（不抛）。"""
    for declaration in declarations():
        if declaration.segment_id == segment_id:
            return declaration
    return None


def declared_metric_ids() -> frozenset[str]:
    """声明清单里的段 id 集（**设置进程与主进程同源的准入依据**）。"""
    return frozenset(declaration.segment_id for declaration in declarations())


def allowed_metric_ids() -> frozenset[str]:
    """``island_metrics`` 的**全部**合法成员：内置段 ∪ 已声明组件。

    这是配置准入的唯一真源（``pet/config.py::_clean_island_metrics`` 与设置页
    共用它）。它**刻意不看运行期注册表**——"谁注册了谁自动合法"会让配置的合法性
    取决于进程内的注册顺序，两个进程（主进程 / 设置进程）就可能各存一份结果。
    """
    return island_segments.BUILTIN_SEGMENT_IDS | declared_metric_ids()


def declared_provider_missing(segment_id: Any) -> str:
    """已声明但 provider 暂缺时返回原因（人话），否则 ``""``。"""
    declaration = declaration_for(segment_id)
    if declaration is None or declaration.provider_present:
        return ""
    return declaration.missing_reason()


def reset_cache() -> None:
    """丢弃扫描缓存与 provider 模块缓存（测试与"用户刚装好组件"时手动调用）。"""
    _CACHE.clear()
    _PROVIDER_CACHE.clear()


# ---------------------------------------------------------------- provider 导入（仅运行期）
def load_provider(declaration: ComponentDeclaration) -> tuple[Callable, Callable]:
    """导入组件 provider，返回 ``(read, format_value)``。

    **只在运行期调用**（采样线程里）：静态路径（设置进程 / 配置准入）一次都不会走到
    这里。失败面分成两类，因为产品对它们的处理不同：

    - :class:`ProviderUnavailable`：文件不在 → "组件暂缺"（保留偏好、不采样、不呈现）；
    - :class:`ProviderBroken`：文件在但导入失败 / 缺入口 / 入口不可调用 → error 态
      （界面照常，卡片写原因）。
    """
    path = declaration.provider_path
    try:
        stat = path.stat()
    except OSError as exc:
        raise ProviderUnavailable(
            f"{declaration.missing_reason()}（{type(exc).__name__}）") from exc
    key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _PROVIDER_CACHE.get(key)
    if cached is not None:
        _PROVIDER_CACHE.move_to_end(key)     # LRU：刚用到的排到最新
        return cached
    module_name = f"_pet_island_ext_{declaration.segment_id}_{key[1]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法为 {path.name} 建立模块规格")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # 组件代码：任何异常都不许掀翻宿主
        raise ProviderBroken(
            f"provider 导入失败 {type(exc).__name__}: {exc}") from exc
    entries = {}
    for role, name in (("read", declaration.read_entry),
                       ("format", declaration.format_entry)):
        entry = getattr(module, name, None)
        if not callable(entry):
            raise ProviderBroken(f"provider 缺少可调用的 {role} 入口 {name!r}")
        entries[role] = entry
    result = (entries["read"], entries["format"])
    _provider_cache_put(key, result)
    return result


def _provider_cache_put(key: tuple[str, int, int], entries: tuple[Callable, Callable]) -> None:
    """写入 provider 缓存：**同一路径只留最新版本**，总量按 LRU 上限淘汰。

    组件文件每被改写一次就多一个键；旧版本的模块对象没有任何消费者（那一段的解析
    缓存已经按来源版本作废），留着就是随"用户编辑组件文件的次数"无界增长。上限只兜
    底：正常使用（组件数远小于 :data:`PROVIDER_CACHE_LIMIT`）不会触发跨组件淘汰。
    """
    path = key[0]
    for stale in [item for item in _PROVIDER_CACHE if item[0] == path]:
        _PROVIDER_CACHE.pop(stale, None)
    _PROVIDER_CACHE[key] = entries
    while len(_PROVIDER_CACHE) > PROVIDER_CACHE_LIMIT:
        _PROVIDER_CACHE.popitem(last=False)      # 最久没用到的那个先走


# ---------------------------------------------------------------- 采样快照
@dataclass(frozen=True)
class ExtensionSample:
    """一段第三方组件在某一拍的状态（宿主读的全部内容都在这里）。

    - ``raw_value``：**原始值**（未经格式化）——告警阈值判定要它，所以不能只留文案；
    - ``text``：护栏之后的文案（契约层的格式化护栏在这里已经跑过，宿主只画不解释）；
    - ``state``：``island_segments`` 的四态词表；
    - ``error_reason``：这一拍为什么没有读数（error / unsupported 时非空）；
    - ``sampled_at``：采样时刻（monotonic），卡片的"几秒前"用它；
    - ``epoch``：采样代次（宿主按它拒收旧代的迟到结果）；
    - ``token``：**每段订阅代次**（出发时那一段的序列号）。退订→重订会自增，采样器
      按它丢掉"退订前那次在飞"的读数——它带着当刻 ``epoch``，只靠 ``epoch`` 拦不住。
      它**不进** :attr:`display_key`（序列号不是画什么的一部分）。
    """

    segment_id: str
    state: str = island_segments.STATE_SAMPLING
    text: str = ""
    raw_value: Any = None
    error_reason: str = ""
    sampled_at: float = 0.0
    epoch: int = 0
    token: int = 0

    @property
    def display_key(self) -> tuple:
        """"要不要重绘"的判据：状态/文案/原因——原始值**不进**（它只喂阈值判定）。

        刻意不用 dataclass 的 ``__eq__``：``raw_value`` 是组件给的对象，可能没有
        有意义的相等语义（每次读都算不等），那样每拍都会重绘一次。
        """
        return (self.state, self.text, self.error_reason)


def clamp_interval(interval: Any) -> float:
    """采样间隔夹到 [``MIN``, ``MAX``]；脏值回默认（不用 max/min 硬夹 NaN）。"""
    try:
        value = float(interval)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_INTERVAL_SECONDS
    if value != value:  # NaN：比较永远为假，夹取夹不出来
        return DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, value))


class ExtensionSampler:
    """第三方组件的**有界节拍采样器**：一条线程管全部组件。

    - **线程数有界**：无论装了多少组件，都只有这**一条**线程（不是每组件一条）；
      旧 worker 卡在组件 ``read`` 里时也不会有第二条（见 :meth:`start` 的接续）；
    - **单次 read 慢**：超过 :data:`BLOCK_BUDGET_SECONDS` 记一次慢读，连续到
      :data:`BLOCK_LIMIT` 次熔断该段（此后不再调它，快照里如实报"已熔断"）。
      **不声称能杀掉它**——Python 杀不掉阻塞中的线程；这条线程被卡住期间其余段的
      读数会一起延后，恢复时机由那次 read 自己返回决定（界面不受影响：GUI 从不 read）；
    - **订阅驱动**：0 订阅时线程只等事件，一次 provider 调用都不发生；
    - **两道代次隔离**：``start()`` 换代、``stop()`` 作废当刻代（旧代的迟到结果直接
      丢），另加**每段订阅 token**——退订 → 重订算新序列，退订前那次在飞的读数不许
      冒充新序列的第一拍（它带着当刻 epoch，只有 token 拦得住）；
    - **来源版本失效**：解析结果与 provider 文件的 ``(路径, mtime_ns, size)`` 绑定，
      文件被删 / 装回 / 换内容时重新解析（清单不变也算）。
    """

    def __init__(self, interval: float = DEFAULT_INTERVAL_SECONDS, *,
                 clock: Callable[[], float] = time.monotonic,
                 resolver: Callable[[str], Any] = None,
                 block_budget: float = BLOCK_BUDGET_SECONDS,
                 block_limit: int = BLOCK_LIMIT):
        self._interval = clamp_interval(interval)
        self._clock = clock
        #: 解析一个段 id 成可采样声明（默认 = 运行期注册表优先，其次清单 + provider）。
        self._resolver = resolver or resolve_declaration
        #: 慢读预算与熔断次数（可注入：让"阻塞"这条路径在测试里几毫秒跑完，
        #: 也允许宿主将来按产品口径调，而不是把 2.0 秒写死在测试的等待里）。
        self._block_budget = float(block_budget)
        self._block_limit = max(1, int(block_limit))
        self._lock = Lock()
        self._subscribed: set[str] = set()
        self._samples: dict[str, ExtensionSample] = {}
        self._declarations: dict[str, Any] = {}
        #: 每段的**来源版本键**（:func:`provider_source_key`），解析缓存按它作废。
        self._source_keys: dict[str, Any] = {}
        #: 每段的**订阅代次**（单调，只在"原本没订阅"时自增，见 :meth:`subscribe`）。
        self._tokens: dict[str, int] = {}
        self._slow_streak: dict[str, int] = {}
        self._blocked: dict[str, str] = {}
        self._epoch = 0
        self._thread: Thread | None = None
        self._stop_event: Event | None = None
        #: 当刻 worker 的"我已退出循环"标志（与 ``_thread`` 同生同灭）：``start()``
        #: 采纳退役线程时要判它——正在退出的线程不许被接回来。
        self._finished: Event | None = None
        #: 退役 worker（``(线程, 它的停止事件, 它的退出标志)``）：``stop()`` 杀不掉卡在
        #: 组件 ``read`` 里的线程，只能留着引用等它自己退出（见 :meth:`start`）。
        self._retiring: list[tuple[Thread, Event, Event]] = []
        self._wake = Event()

    # ------------------------------------------------------------ 状态
    @property
    def interval(self) -> float:
        return self._interval

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    @property
    def epoch(self) -> int:
        with self._lock:
            return self._epoch

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribed)

    def snapshot(self) -> dict[str, ExtensionSample]:
        """各段的最近一拍（**字典副本**：宿主拿到的不是内部状态）。"""
        with self._lock:
            return dict(self._samples)

    def blocked_reason(self, segment_id: str) -> str:
        with self._lock:
            return self._blocked.get(segment_id, "")

    def set_interval(self, interval: float) -> float:
        self._interval = clamp_interval(interval)
        self._wake.set()
        return self._interval

    # ------------------------------------------------------------ 订阅
    def subscribe(self, segment_id: str) -> int:
        """订阅一段；重复订阅不重复计数（宿主每次值刷新都会重放订阅）。

        **每段订阅代次（token）只在"这一段原本没订阅"时自增**：重复订阅是宿主
        每一轮刷新的常态（``_sync_extension_slot`` 对每个启用段重放一次），无条件
        自增会把每一秒的在飞样本都作废，那是行为回归。退订 → 重订因此拿到新 token：
        退订前那次在飞的 ``read`` 回来时对不上号，直接丢弃——它带着当刻 ``epoch``
        与 ok 状态，全局代次拦不住它。
        """
        key = str(segment_id)
        with self._lock:
            if key not in self._subscribed:
                self._subscribed.add(key)
                self._tokens[key] = self._tokens.get(key, 0) + 1
            # 重新订阅 = 新序列：上一次的熔断与慢读计数不跨序列（用户重开这一段
            # 就是想再要它一次，拿旧结论把它永久判死是错的）。
            if key in self._blocked:
                self._blocked.pop(key, None)
                self._slow_streak.pop(key, None)
            return len(self._subscribed)

    def unsubscribe(self, segment_id: str) -> int:
        """退订：声明、快照、熔断记录一起撤（退了就是退了，不留半份状态）。"""
        key = str(segment_id)
        with self._lock:
            self._subscribed.discard(key)
            self._samples.pop(key, None)
            self._declarations.pop(key, None)
            self._source_keys.pop(key, None)
            self._slow_streak.pop(key, None)
            self._blocked.pop(key, None)
            # ``_tokens`` **刻意不清**：它必须单调——清掉之后重订会重新领到 1，
            # 退订前那个 token=1 的在飞样本就又能对上号了。
            return len(self._subscribed)

    def clear(self) -> None:
        """退掉全部订阅（收口前的最后一步：订阅数不许留在采样器上）。"""
        with self._lock:
            self._subscribed.clear()
            self._samples.clear()
            self._declarations.clear()
            self._source_keys.clear()
            self._slow_streak.clear()
            self._blocked.clear()
            # 同上：``_tokens`` 保持单调，不清。

    # ------------------------------------------------------------ 线程
    def start(self) -> bool:
        """起线程；已经在跑就返回 False（不重复起第二条）。

        **退役 worker 还活着时不开第二条**：``stop()`` 摘掉线程引用、作废当刻代，但
        那次在飞的 ``read`` 只有它自己返回才结束（Python 杀不掉阻塞中的线程）。这时
        直接再起一条线程，同一个 provider 里就有两个 worker 同时在跑——所以这里
        **接续**那条线程：清掉它的停止位、把它重新挂回 ``_thread``、换代，之后它继续
        采新订阅（订阅集合每拍重读，所以"接续"就是"继续干当刻的活"）。既不会出现
        第二条 worker，也不会"``start()`` 之后永远停"。若它已经**决定退出**（循环条件
        已判否，见 :meth:`_loop_on`）或已经死掉，说明它不再占着 provider，那就正常
        起新线程——"清停止位"与"决定退出"在同一把锁里，所以不会接到一条正在退出的
        线程（那会让采样器看起来在跑、其实永远不采）。
        """
        if self.running:
            return False
        with self._lock:
            retired = self._adoptable()
            if retired is not None:
                thread, event, finished = retired
                event.clear()               # 它可能正卡在 read 里等返回：清完就接着跑
                self._epoch += 1
                self._samples.clear()       # 新代从"还没采到第一拍"开始
                self._stop_event = event
                self._finished = finished
                self._thread = thread
                self._wake.clear()
                return True
        event = Event()
        self._stop_event = event
        with self._lock:
            self._epoch += 1
            self._samples.clear()      # 新代从"还没采到第一拍"开始
        self._wake.clear()
        self._finished = Event()
        finished = self._finished
        self._thread = Thread(target=self._run, args=(event, finished), daemon=True,
                             name=THREAD_NAME)
        self._thread.start()
        return True

    def stop(self, *, wait: bool = False, timeout: float = 2.0) -> None:
        """停线程并作废当刻代；``wait=True`` 时**有界** join。

        退役的 worker **留着引用**（``_retiring``）：它卡在组件 ``read`` 里时没人杀得掉
        它，丢掉引用只是自欺（那次调用照样会跑完）；留着引用才谈得上"不许两条 worker
        同时跑同一个 provider"（见 :meth:`start`）。
        """
        event, self._stop_event = self._stop_event, None
        if event is not None:
            event.set()
        thread, self._thread = self._thread, None
        finished, self._finished = self._finished, None
        with self._lock:
            self._epoch += 1
            if thread is not None and event is not None and finished is not None:
                self._retiring.append((thread, event, finished))
        self._wake.set()
        if wait and thread is not None:
            thread.join(timeout)

    def close(self) -> None:
        """收口：停线程 + 有界 join（read 卡死时宁可放弃等待也不吊死调用方）。"""
        self.stop(wait=True, timeout=1.0)
        self._join_retiring(1.0)
        self.clear()

    def _adoptable(self) -> tuple[Thread, Event, Event] | None:
        """一个**还能接回来**的退役 worker：还活着，且**还没决定退出循环**。

        两个判据都在 ``self._lock`` 里读（调用方持锁），而 worker 决定退出时也是在同一
        把锁里置上第三个标志（见 :meth:`_loop_on`）——所以"接回来"与"正在退出"不可能
        同时成立。顺便清掉已经彻底结束的条目（不留死引用）。
        """
        live = [item for item in self._retiring if item[0].is_alive()]
        self._retiring = live
        for item in live:
            if not item[2].is_set():
                self._retiring.remove(item)
                return item
        return None

    def _join_retiring(self, timeout: float) -> None:
        """给退役 worker 一个**有界**的退出预算；卡死的那条留下来（如实，不假装）。"""
        for thread, _event, _finished in list(self._retiring):
            if thread.is_alive():
                thread.join(timeout)
        self._retiring = [item for item in self._retiring if item[0].is_alive()]

    # ------------------------------------------------------------ 采样
    def sample_once(self, segment_id: str, *, epoch: int | None = None) -> ExtensionSample:
        """同步采一拍（采样线程与测试共用同一实现；调用方负责线程归属）。"""
        key = str(segment_id)
        with self._lock:
            current = self._epoch if epoch is None else int(epoch)
            token = self._tokens.get(key, 0)
        return replace(self._sample(segment_id, current), token=token)

    def _resolve(self, segment_id: str, *, source=_UNSET):
        """解析一段（按段缓存）；给 ``source`` 时同时核对 provider 的来源版本。

        清单不变、只把 provider **删掉 / 装回来 / 换内容**也是来源变了：那时必须丢掉
        这一段的解析缓存重新走 ``self._resolver``——文件不在会抛
        :class:`ProviderUnavailable`（如实报"暂缺"），内容换过则按新的
        ``(路径, mtime_ns, size)`` 重新 import（旧 callable 不许继续跑）。

        来源键来自 :func:`provider_source_keys`（纯运行期 ``register_segment`` 的段是
        ``None``，两次都是 ``None``，不受影响）。
        """
        key = str(segment_id)
        with self._lock:
            cached = self._declarations.get(key)
            cached_source = self._source_keys.get(key, _UNSET)
        if cached is not None:
            current = provider_source_key(key) if source is _UNSET else source
            if cached_source == current:
                return cached
            with self._lock:
                self._declarations.pop(key, None)
                self._source_keys.pop(key, None)
        declaration = self._resolver(segment_id)
        with self._lock:
            self._declarations[key] = declaration
            self._source_keys[key] = (provider_source_key(key)
                                      if source is _UNSET else source)
        return declaration

    def _sample(self, segment_id: str, epoch: int,
                declaration=None) -> ExtensionSample:
        """一拍：取值（原始值）→ 契约层护栏格式化 → 快照；慢读按熔断规则记账。

        取值与格式化**都在本方法里**（即采样线程里）：GUI 只读快照。契约层的
        ``declaration.snapshot(raw)`` 已经把"格式化函数抛异常 / 返回巨型串 / 多行 /
        None"全部兜成 error 态 + 原因，这里不再另写一套护栏。

        ``epoch`` / ``token`` 描述"这一拍是**从哪个序列出发**的"，判断它们失效要看
        :meth:`_publish`；调用方（:meth:`_tick` / :meth:`sample_once`）在自己的那一把锁里
        取号并打在样本上——号码必须在**调用 read 之前**取定，否则退订/重订就抢在前面了。
        """
        moment = self._clock()
        if declaration is None:
            try:
                declaration = self._resolve(segment_id)
            except ProviderUnavailable as exc:
                return ExtensionSample(segment_id, state=island_segments.STATE_UNSUPPORTED,
                                       error_reason=str(exc), sampled_at=moment, epoch=epoch)
            except ProviderBroken as exc:
                return ExtensionSample(segment_id, state=island_segments.STATE_ERROR,
                                       error_reason=str(exc), sampled_at=moment, epoch=epoch)
            except Exception as exc:  # 解析器是注入面：异常不外溢成线程崩溃
                return ExtensionSample(
                    segment_id, state=island_segments.STATE_ERROR,
                    error_reason=f"声明解析失败 {type(exc).__name__}: {exc}",
                    sampled_at=moment, epoch=epoch)
        reader = getattr(declaration, "read", None)
        if not callable(reader):
            return ExtensionSample(segment_id, state=island_segments.STATE_ERROR,
                                   error_reason="段未声明数据源（read）",
                                   sampled_at=moment, epoch=epoch)
        started = self._clock()
        try:
            raw = reader()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("灵动岛组件 %s 取值失败：%s", segment_id, reason)
            with self._lock:
                self._slow_streak.pop(segment_id, None)
            return ExtensionSample(segment_id, state=island_segments.STATE_ERROR,
                                   error_reason=reason, sampled_at=moment, epoch=epoch)
        elapsed = self._clock() - started
        snapshot = declaration.snapshot(raw)
        if elapsed > self._block_budget:
            streak = self._bump_slow(segment_id)
            if streak >= self._block_limit:
                reason = (f"取值连续 {streak} 次超过 {self._block_budget:g} 秒"
                          f"（本次 {elapsed:.1f} 秒），已停止采样这一段")
                logger.warning("灵动岛组件 %s 读取阻塞，已熔断：%s", segment_id, reason)
                with self._lock:
                    self._blocked[segment_id] = reason
                return ExtensionSample(segment_id, state=island_segments.STATE_ERROR,
                                       error_reason=reason, sampled_at=moment, epoch=epoch)
        else:
            with self._lock:
                self._slow_streak.pop(segment_id, None)
        detail = snapshot.detail if isinstance(snapshot.detail, dict) else {}
        return ExtensionSample(
            segment_id, state=snapshot.state, text=snapshot.text, raw_value=raw,
            error_reason=str(detail.get("error_reason", "") or ""),
            sampled_at=moment, epoch=epoch)

    def _bump_slow(self, segment_id: str) -> int:
        with self._lock:
            streak = self._slow_streak.get(segment_id, 0) + 1
            self._slow_streak[segment_id] = streak
            return streak

    def _publish(self, sample: ExtensionSample) -> bool:
        """发布一拍；**三道判据缺一不可**（迟到结果一律不复活）。

        ① 代次：换线程 / ``stop()`` 之后旧代的样本作废；
        ② 订阅：退订之后到达的样本作废（退了就是退了）；
        ③ **每段 token**：退订 → 重订算新序列，退订前那次在飞的读数不许冒充第一拍
           ——它带着当刻 ``epoch``、状态还是 ok、这一段也确实又被订阅了，前两道
           判据都拦不住它（宿主会把它当成"新序列的第一拍"收下并推进告警）。
        """
        with self._lock:
            if sample.epoch != self._epoch:
                return False
            if sample.segment_id not in self._subscribed:
                return False
            if sample.token != self._tokens.get(sample.segment_id):
                return False
            self._samples[sample.segment_id] = sample
            return True

    def _tick(self) -> None:
        with self._lock:
            ids = tuple(self._subscribed)
            blocked = dict(self._blocked)
            epoch = self._epoch
            # 出发时的每段订阅代次：这一拍属于**哪个序列**在这一刻定死（read 可能
            # 在飞行中遇到退订/重订，回来时按这个号判收不收）。
            tokens = {segment_id: self._tokens.get(segment_id, 0) for segment_id in ids}
        # 来源版本一拍核对一次（清单一趟；纯注册面的段是 None，不参与）：清单不变、
        # 只删/装/换 provider 也要让已经解析过的 callable 作废。
        sources = provider_source_keys(ids)
        for segment_id in ids:
            if segment_id in blocked:
                continue          # 熔断：不再调它的 read（解除要靠重新订阅）
            try:
                declaration = self._resolve(segment_id, source=sources.get(segment_id))
            except (ProviderUnavailable, ProviderBroken):
                declaration = None     # 让 _sample 走去声明/坏声明那一支
            sample = self._sample(segment_id, epoch, declaration)
            self._publish(replace(sample, token=tokens.get(segment_id, 0)))

    def _loop_on(self, stop: Event, finished: Event) -> bool:
        """循环条件：**在同一把锁里**决定"继续采"还是"我退出了"。

        ``start()`` 采纳退役线程时也在同一把锁里清停止位并接回 ``_thread``——两个判据
        因此不可能同时成立：要么 worker 先看到"停止位已清"继续跑，要么 ``start()`` 先
        看到 ``finished`` 已置、不起第二条也不接这条正在退出的线程。
        """
        with self._lock:
            keep = not stop.is_set() and self._stop_event is stop
            if not keep:
                finished.set()          # 与 start() 的采纳判据互斥：先标"我退出了"
            return keep

    def _run(self, stop: Event, finished: Event) -> None:
        try:
            while self._loop_on(stop, finished):
                started = self._clock()
                try:
                    self._tick()
                except Exception:  # pragma: no cover - _sample 已兜住组件异常
                    logger.exception("灵动岛组件：采样异常（线程继续）")
                sleep = max(0.05, self._interval - (self._clock() - started))
                self._wake.wait(sleep)
                self._wake.clear()
        finally:
            # 兜底：万一有异常直接掀出循环（不是"决定退出"那条路），也要让
            # start() 知道这条线程不会再采了（不许被采纳）。
            finished.set()


def resolve_declaration(segment_id: str):
    """段 id → 可采样声明：**运行期注册表优先**，其次声明清单 + provider 导入。

    ``register_segment`` 注册过的段直接用它的声明（第三方在自己的进程里注册的那个
    就是权威）；否则按清单导入 provider，装配成契约层的声明。
    """
    registered = island_segments.get_segment(segment_id)
    if registered is not None:
        return registered
    declaration = declaration_for(segment_id)
    if declaration is None:
        raise ProviderUnavailable(f"段 {segment_id!r} 不是已声明的组件")
    read, format_value = load_provider(declaration)
    return declaration.contract_declaration(read, format_value)
