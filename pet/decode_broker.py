# -*- coding: utf-8 -*-
"""P3：多开共享解码 broker（最小切口，默认关）。

本模块提供「coordinator 兼任空闲素材共享解码源」所需的全部机制：

1. ``BrokerShmSession``：跨进程共享内存会话（纯 Python，无 Qt 依赖）。
   - 布局：固定 128 字节头部 + K 槽环形帧区；头字段定长小端序。
   - 头部写模型 = **奇偶 seqlock**（P3A 盲审 P0-1 修订）：每次头部变更
     （帧发布或终态标记）先**原子写奇数 seq**（in-progress），再写帧字节与
     受保护的动态区（flags/frame_count/last_src/last_slot/last_pub_ns），
     最后**原子写偶数 seq**（commit）。seq 是 8 字节对齐的独立提交词，只经
     单字读写（ctypes 单条对齐 8B 存取），绝不参与整块 memcpy——128 字节整块
     拷贝对另一进程不可原子，任何读到奇数 seq 或拷贝前后 seq 不一致的快照
     一律作废重试（读端不接受撕裂头）。
   - 发布端单写者（帧写与终态写共享一把线程锁）：``publish_frame(data, src)``
     每帧一槽（槽位 = src % K），``frame_count`` 只随帧提交推进；
   - 消费端无锁读：稳定偶数 seq 快照下拷贝动态区 → 复查 seq → 帧进度
     （frame_count）前进才读槽；槽拷贝同样夹在两次原子 seq 读之间校验。
   - 结束语义：``run_ended_natural`` / ``aborted`` 位由发布端（facade 驱动）
     写头（同样走奇偶提交）；消费端据此正常收尾或回退本地解码。
2. ``BrokerFacade``（GUI 线程）：角色镜像、发布会话注册、订阅请求/授权决策、
   消费端 feed 句柄与停滞检测、字节预算。挂在 GUI 线程，经
   ``CollisionIpcSession`` 的 queued 信号与 worker 通信，不直接触碰 socket。
3. 模块级字节预算记账（对齐 ``frame_cache.ByteBudgetLru`` 的硬上界语义）。

**平台限定（P3A R2 P0-1，R3 收口）**：提交词 ``seq`` 只经 ctypes 单条对齐
8B load/store（普通 C 单字存取），**不提供 acquire/release 或任何跨进程
memory barrier**——该写法仅在 x86/x64 的强内存序（TSO：无 store 乱序 +
对齐字存取原子）下构成可靠 seqlock，即本仓库的 Windows AMD64/x64 实证平台。
弱序平台（ARM macOS/Linux 等，以及 **Windows ARM64**——ARM 内存模型在
Windows 上同样为弱序、无 x86 式 store 顺序保证）上 ctypes 普通 8B 存取无
happens-before 语义，本协议**不做正确性声明、运行时一律不启用**：
``broker_platform_supported()`` 对 OS 与架构做双重判定（仅 Windows 且
AMD64/x86_64 返回 True），``BrokerFacade`` 的 ``enabled`` 判定会与平台
支持做与（非支持平台即使配置键为 True 也强制按 False 处理，绝不跨进程
共享解码）。R3 起 ``BrokerShmSession.create/attach`` 这两个公开低层入口
**内建同一平台门禁**：非支持平台上即使被直接调用，也在触碰任何共享内存
之前抛 OSError（不经 facade 也能被拒绝）。此限制在配置归一处
（``decode_broker_enabled`` 默认/归一，见 pet/config.py）显式声明；把弱序
平台从「后续补 barrier」改为「不支持」是诚实且安全的范围收窄——不在弱序
平台上假装 seqlock 成立。

铁律（见 _plan/current/P3_BROKER_DESIGN.md §1）：
broker 是尽力而为的加速路径，不是正确性依赖——任何一环失败（无 coordinator /
授权超时 / 打不开共享内存 / 几何不匹配 / 流停滞 / broker 被杀），消费端一律
**无感回退本地解码**（现有 WebMClip 本地 reader 从帧 0 起播，行为逐位不变）。

本批只做「双开同角色空闲素材共享解码」最小切口：只对 idle 类素材开 session，
不做全素材 broker；默认关（``decode_broker_enabled`` 灰度键）。
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import platform
import secrets
import struct
import sys
import threading
import time
from typing import Any

from . import catalog
from .config import APP_DIR_NAME

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 共享内存布局版本（本批 = 1）
BROKER_HEADER_VERSION = 1
# 固定魔数（校验名/版本错配）：'DSH-BROKER1' 的 64 位编码
BROKER_MAGIC = 0x4453482D42524F4B

# 头部字节数（固定布局，见 SessionHeader）
HEADER_SIZE = 128
# 环形槽数（本批固定 4；4 帧滑动窗口 = 播放窗口缓冲）
SLOT_COUNT_DEFAULT = 4
# RGBA
BPP = 4

# 可共享判定 / 授权门槛（设计 §3.3）：剩余帧 >= MIN_JOIN_FRAMES 才 grant，
# 保证迟到加入者本轮 idle 不会只剩几帧（截断最多 ~6s）。
MIN_JOIN_FRAMES = 96

# 订阅决策单次预算（毫秒，设计 P2-4 统一口径）：deny 毫秒级返回；
# 只有「无 coordinator / 老版本忽略」才耗到预算，耗尽即本地解码。
SUBSCRIBE_BUDGET_MS = 600
# 消费端停滞看门狗（毫秒，设计 §3.4）：seq 超过该时长不前进且未自然结束
# → 判定断流 → 本地回退。
STALL_BUDGET_MS = 600
# 发布端自然结束后 session 的存活宽限（秒）：等远端消费端读完尾帧再销毁。
SESSION_END_GRACE_S = 3.0
# 模块级共享内存字节预算（硬上界）：单 session ≈ 128 + K*帧字节 ≈ 3.5MiB，
# 16MiB ≈ 4 个并发 session（设计 §3.7）。
BROKER_SHM_MAX_BYTES = 16 * 1024 * 1024

# 头部 flags 位
FLAG_SESSION_ACTIVE = 1 << 0
FLAG_RUN_ENDED_NATURAL = 1 << 1
FLAG_ABORTED = 1 << 2


def frame_bytes(w: int, h: int, bpp: int = BPP) -> int:
    return max(1, int(w)) * max(1, int(h)) * max(1, int(bpp))


def session_size(w: int, h: int, bpp: int = BPP, slot_count: int = SLOT_COUNT_DEFAULT) -> int:
    return HEADER_SIZE + max(1, int(slot_count)) * frame_bytes(w, h, bpp)


def user_digest() -> str:
    """跨进程共享的用户身份摘要（与 collision_server_name 同源）。

    共享内存名含该摘要：不同系统用户的 broker session 互不可见（命名空间
    隔离）；同一用户的多开实例（碰撞通道内）可互相 attach。
    """
    if sys.platform == "win32":
        identity = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")
    else:
        identity = str(getattr(os, "getuid", lambda: 0)())
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def asset_key(path: str) -> str:
    """素材身份 = (绝对路径, mtime_ns, size)——与帧缓存/元数据缓存同口径。"""
    try:
        st = os.stat(path)
        return f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return os.path.abspath(path)


def make_shm_name(asset: str, epoch: str) -> str:
    """生成共享内存名：<app>-<user>-db-<asset 前 12 hex>-<epoch 前 6 hex>。

    长度 ≈ 20+16+3+12+6 ≈ 57，远低于 POSIX /dev/shm 255 上限。epoch 由
    发布会话创建时随机：崩溃残留的新名永不与旧名重名（设计 R3/R6）。
    """
    digest = hashlib.sha256(asset.encode("utf-8")).hexdigest()[:12]
    return f"{APP_DIR_NAME[:20]}-{user_digest()}-db-{digest}-{epoch[:6]}"


# 平台限定（P3A R2 P0-1 / R3 收口，见模块 docstring「平台限定」节）：seq 提交
# 词只经 ctypes 普通 8B load/store（无 acquire/release/跨进程 barrier），仅在
# x86/x64 TSO（Windows）下构成可靠 seqlock；弱序平台（ARM macOS/Linux 与
# **Windows ARM64**——ARM 内存模型在 Windows 上同样弱序）一律不启用——
# enabled 判定（BrokerFacade 构造）与低层入口（create/attach 内建检查）共用
# 本函数做 OS + 架构双重判定。
def broker_platform_supported() -> bool:
    """broker 共享内存 seqlock 是否可在当前平台启用（Windows AMD64/x64 only）。

    ctypes ``c_uint64`` 单条对齐 8B load/store 是普通 C 字存取，不提供
    acquire/release 或其他跨进程 memory barrier；只有 x86/x64 的 TSO 强序
    （本项目实证平台 = Windows）下它才是可靠的 seqlock 提交词。弱序平台
    （ARM macOS/Linux，以及 **Windows ARM64**——ARM 内存模型在 Windows 上
    同样是弱序、无 x86 式 store 顺序保证，只是 OS 是 Windows，不能仅按
    ``sys.platform == "win32"`` 放行）上一律返回 False：即使
    ``decode_broker_enabled`` 配置为 True，BrokerFacade 的 enabled 也为 False，
    broker 不跨进程启用。
    """
    # P3A R3：平台判定同时校验 OS 与架构——Windows ARM64（machine()=='ARM64'）
    # 与 ARM macOS/Linux 同属弱序，必须排除；仅 AMD64/x86_64 放行。machine()
    # 归一为小写比较（Windows 返回 'AMD64'、Linux 返回 'x86_64'）。
    return (
        sys.platform == "win32"
        and platform.machine().lower() in {"amd64", "x86_64"}
    )


def _shm_available() -> bool:
    try:
        from multiprocessing import shared_memory  # noqa: F401
        return True
    except Exception:
        return False


_SHM_AVAILABLE = _shm_available()


# ---------------------------------------------------------------------------
# 头部读写（固定 128 字节小端序布局；奇偶 seqlock，见模块 docstring）
# ---------------------------------------------------------------------------
# 偏移表（字节偏移 -> (struct 格式, 字段名)）。字段分三区：
#   - 静态区 0..95：create() 时一次性写入、此后永不变化（magic/version/几何/
#     fps/total_frames/slot_count/epoch/pub_pid/保留）——读端 attach 时校验一次，
#     无需任何同步（写只发生在任何读端出现之前）；
#   - 动态区 96..119（受保护）：flags/frame_count/last_src/last_slot/last_pub_ns，
#     只由发布端在奇偶提交括号内整块更新；读端在稳定偶数 seq 下拷贝校验；
#   - seq（120..127，u64）：跨进程唯一的原子提交词——只经单条对齐 8B 存取
#     （ctypes 单字读写），奇数 = 发布进行中，偶数 = 已提交；任何整块 memcpy
#     都不得触碰该字（128B pack 仅用于 create() 的初始写，此时无读端）。
_HEADER_FIELDS: list[tuple[int, str, str]] = [
    (0, "Q", "magic"),
    (8, "I", "version"),
    (12, "I", "frame_w"),
    (16, "I", "frame_h"),
    (20, "I", "bpp"),
    (24, "I", "fps_x1000"),
    (28, "I", "total_frames"),
    (32, "I", "slot_count"),
    (40, "Q", "epoch"),
    (48, "Q", "pub_pid"),
    (96, "I", "flags"),
    (100, "I", "frame_count"),
    (104, "I", "last_src"),
    (108, "I", "last_slot"),
    (112, "q", "last_pub_ns"),
    (120, "Q", "seq"),
]
assert _HEADER_FIELDS[-1][0] + 8 <= HEADER_SIZE, "header layout overflow"

# 动态区（受保护的整块）：flags(4) + frame_count(4) + last_src(4) +
# last_slot(4) + last_pub_ns(8) = 24 字节，96..119。
DYN_OFFSET = 96
DYN_SIZE = 24
_DYN_STRUCT = struct.Struct("<IIIiq")  # flags, frame_count, last_src, last_slot, last_pub_ns
assert _DYN_STRUCT.size == DYN_SIZE
# seq 提交词（u64）：120..127，8 字节对齐。
SEQ_OFFSET = 120


class SessionHeader:
    """共享内存头部定长读写的纯 Python 实现（无锁，单写者多读者）。"""

    __slots__ = tuple(name for _, _, name in _HEADER_FIELDS)

    def __init__(self, **kwargs: Any) -> None:
        defaults: dict[str, Any] = {
            "magic": BROKER_MAGIC,
            "version": BROKER_HEADER_VERSION,
            "flags": 0,
            "frame_w": catalog.CANVAS_W,
            "frame_h": catalog.CANVAS_H,
            "bpp": BPP,
            "fps_x1000": 24000,
            "total_frames": 0,
            "slot_count": SLOT_COUNT_DEFAULT,
            "epoch": 0,
            "pub_pid": 0,
            "frame_count": 0,
            "last_src": 0,
            "last_slot": 0,
            "last_pub_ns": 0,
            "seq": 0,
        }
        defaults.update(kwargs)
        for name in self.__slots__:
            setattr(self, name, defaults[name])

    # -- 序列化 ----------------------------------------------------------
    def pack(self) -> bytes:
        buf = bytearray(HEADER_SIZE)
        for offset, fmt, name in _HEADER_FIELDS:
            value = getattr(self, name)
            struct.pack_into(fmt, buf, offset, value)
        return bytes(buf)

    @classmethod
    def unpack(cls, data: bytes) -> "SessionHeader":
        if len(data) < HEADER_SIZE:
            raise ValueError(f"header too short: {len(data)}")
        obj = cls()
        for offset, fmt, name in _HEADER_FIELDS:
            setattr(obj, name, struct.unpack_from(fmt, data, offset)[0])
        return obj

    def validate(self, expected_w: int, expected_h: int, expected_bpp: int,
                 expected_slot_count: int) -> None:
        if self.magic != BROKER_MAGIC:
            raise ValueError(f"shm magic mismatch: {self.magic:#x}")
        if self.version != BROKER_HEADER_VERSION:
            raise ValueError(f"shm version mismatch: {self.version}")
        if self.frame_w != expected_w or self.frame_h != expected_h or self.bpp != expected_bpp:
            raise ValueError(
                f"shm geometry mismatch: {self.frame_w}x{self.frame_h}@{self.bpp}"
            )
        if self.slot_count != expected_slot_count:
            raise ValueError(f"shm slot_count mismatch: {self.slot_count}")

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"SessionHeader({self.to_dict()!r})"


def _read_header_buf(buf) -> SessionHeader:
    """从共享内存缓冲读头（128 字节整块拷贝后解析）。"""
    return SessionHeader.unpack(bytes(buf[0:HEADER_SIZE]))


# ---------------------------------------------------------------------------
# 共享内存会话（发布端）
# ---------------------------------------------------------------------------
class BrokerShmSession:
    """一个共享解码 session 的句柄（coordinator 侧创建发布 / 消费端 attach 读）。

    线程：``publish_frame`` 在 WebMClip reader 线程调用（设计 §4 每帧
    on_frame）；终态标记（``mark_natural_end``/``mark_aborted``）由 facade
    （GUI 线程）经本类方法写入——两个写者共享 ``_lock`` 串行化。

    提交模型 = **奇偶 seqlock**（P3A 盲审 P0-1 修订，见模块 docstring）：
    - 每次头部变更：原子写奇数 seq（in-progress）→ 写帧字节/整块写动态区
      → 原子写偶数 seq（commit）；
    - ``seq`` 是 8 字节对齐的独立提交词，只经单条对齐 8B 读写
      （``ctypes.c_uint64.from_buffer`` 的单 C 字存取）。struct 的
      pack_into/unpack_from 对整数是逐字节拷贝，**不原子**，绝不用于 seq；
    - **平台限定（P3A R2 P0-1 / R3）**：该「原子」只到单字 load/store 层面，
      无 acquire/release/跨进程 barrier——只在 x86/x64 TSO（Windows
      AMD64/x64）下构成可靠 seqlock；弱序平台（ARM macOS/Linux，以及
      **Windows ARM64**）由 ``broker_platform_supported()`` 运行时拒绝：
      ``BrokerFacade.enabled`` 恒 False，且 ``create()/attach()`` 这两个公开
      低层入口**内建同一门禁**（P3A R3）——即使绕过 facade 直接调用，也在
      触碰任何共享内存之前抛 OSError，协议对其不作正确性声明；
    - 读端先原子读 seq：奇数 → 发布进行中，快照作废；偶数且整块拷贝前后
      一致 → 动态区可信；槽字节拷贝同样夹在两次 seq 读之间校验；
    - ``read_frame(src)`` 另做 ring 窗口校验（P3A R2 P2-1）：src 不在稳定
      快照的 ``[last_src - slot_count + 1, last_src]`` 窗口内 → None；
    - 消费端 ``close()`` 只关本地句柄不 unlink；发布端 ``unlink()`` 销毁。
    """

    def __init__(self, name: str, header: SessionHeader) -> None:
        self.name = name
        self.header = header
        self._lock = threading.Lock()
        self._closed = False
        self._shm = None  # 延迟到 create()/attach() 赋值

    # -- 生命周期 --------------------------------------------------------
    @classmethod
    def create(cls, name: str, frame_w: int, frame_h: int, fps: float,
               total_frames: int, slot_count: int = SLOT_COUNT_DEFAULT,
               epoch: str | None = None) -> "BrokerShmSession":
        """创建并初始化共享内存（发布端）。失败抛 OSError（平台不支持/不可用）。

        P3A R3 低层门禁：本入口是公开 classmethod，可绕过 BrokerFacade 的
        enabled 判定被直接调用（本模块的机制单测即直接使用）——为防弱序平台
        （非 Windows x86/x64，含 Windows ARM64）上不经门禁就创建/使用
        seqlock 会话，这里内建与 ``broker_platform_supported()`` 一致的平台
        检查：非支持平台在触碰任何共享内存**之前**直接抛 ``OSError``
        （与既有「shared_memory 不可用 → OSError」同一环境类错误风格；
        facade 的 publish_start broad except 捕获后照常降级本地解码）。
        """
        if not broker_platform_supported():
            raise OSError(
                "broker 平台不支持: seqlock 仅 Windows x86/x64 (AMD64/x86_64) "
                "TSO 下可靠，弱序平台（含 Windows ARM64）拒绝直接 create"
            )
        from multiprocessing import shared_memory
        if not _SHM_AVAILABLE:
            raise OSError("multiprocessing.shared_memory 不可用")
        epoch = epoch or secrets.token_hex(8)
        size = session_size(frame_w, frame_h, BPP, slot_count)
        header = SessionHeader(
            frame_w=frame_w, frame_h=frame_h, bpp=BPP,
            fps_x1000=max(1, int(round(fps * 1000))),
            total_frames=max(1, int(total_frames)),
            slot_count=slot_count,
            seq=0, last_src=0, last_slot=0,
            epoch=int(epoch[:16], 16) if epoch[:16].isalnum() else 0,
            pub_pid=os.getpid(),
            flags=FLAG_SESSION_ACTIVE,
        )
        shm = shared_memory.SharedMemory(create=True, name=name, size=size)
        shm.buf[0:HEADER_SIZE] = header.pack()
        session = cls(name, header)
        session._shm = shm
        return session

    @classmethod
    def attach(cls, name: str, expected_w: int, expected_h: int,
               expected_bpp: int = BPP,
               expected_slot_count: int = SLOT_COUNT_DEFAULT,
               epoch: str = "") -> "BrokerShmSession":
        """附加到已存在的共享内存（消费端）。

        P3A R3 低层门禁：同 ``create()``，本公开入口内建平台检查——非支持
        平台（非 Windows x86/x64，含 Windows ARM64）在 attach 任何共享内存
        之前直接抛 ``OSError``（错误风格与 create 一致；facade 的
        ``_on_decode_reply`` broad except 捕获后 complete(None) → 本地解码）。

        P3A P1-4 修订：临时句柄统一 try/finally 管理——只有**全部**校验
        （mapping 实际 size ≥ header 声称的几何大小、magic、version、几何、
        slot_count、epoch）通过才把句柄所有权转移给 session；任何失败路径都
        ``close()`` 临时句柄，绝不把已 attach 的句柄泄漏给异常。
        """
        if not broker_platform_supported():
            raise OSError(
                "broker 平台不支持: seqlock 仅 Windows x86/x64 (AMD64/x86_64) "
                "TSO 下可靠，弱序平台（含 Windows ARM64）拒绝直接 attach"
            )
        from multiprocessing import shared_memory
        shm = shared_memory.SharedMemory(name=name)
        try:
            header = _read_header_buf(shm.buf)
            header.validate(expected_w, expected_h, expected_bpp,
                            expected_slot_count)
            # 尺寸校验：mapping 必须容得下 header 声明的几何（128 + K*帧字节）。
            # 注意 Windows 把 mapping 按页向上取整（size 可能大于声明值），
            # 因此用「下界」而非精确相等；header 声称的几何/槽数不足或越界
            # （畸形/恶意对象）在此被拒（P3A P1-4）。
            required = session_size(int(header.frame_w), int(header.frame_h),
                                    int(header.bpp), int(header.slot_count))
            if int(getattr(shm, "size", 0)) < required:
                raise ValueError(
                    f"shm size mismatch: {getattr(shm, 'size', 0)} < {required}"
                )
            if epoch and str(header.epoch) and int(header.epoch) != 0:
                want = int(epoch[:16], 16) if epoch[:16].isalnum() else 0
                if want and header.epoch != want:
                    raise ValueError("shm epoch mismatch")
        except Exception:
            try:
                shm.close()
            except Exception:
                pass
            raise
        session = cls(name, header)
        session._shm = shm
        return session

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def shm_size(self) -> int:
        if self._shm is None:
            return 0
        return self._shm.size

    # -- 原子提交词 seq（8B 对齐，仅单字存取，绝不进整块 memcpy）---------
    @staticmethod
    def _read_seq(buf) -> int:
        """原子读 seq（ctypes 单条对齐 8B 读 = 单 C 字 load）。

        struct.unpack_from 对整数走逐字节拷贝，跨进程读可能撕裂，禁止使用。
        注：该 load 只保证「单字不撕裂」，**无 acquire/跨进程 barrier**——
        仅在 Windows（x86/x64 TSO）下构成可靠 seqlock（平台门禁见类/模块
        docstring，P3A R2 P0-1）。
        """
        return int(ctypes.c_uint64.from_buffer(buf, SEQ_OFFSET).value)

    @staticmethod
    def _write_seq(buf, value: int) -> None:
        """原子写 seq（ctypes 单条对齐 8B 写 = 单 C 字 store）。

        同上：只保证单字不撕裂，**无 release/跨进程 barrier**——平台门禁
        （Windows AMD64/x64 only）由 ``broker_platform_supported()`` 收口：
        BrokerFacade.enabled 判定 + create/attach 低层内建检查（P3A R2
        P0-1 / R3）。
        """
        ctypes.c_uint64.from_buffer(buf, SEQ_OFFSET).value = int(value)

    def _commit_dyn(self, buf) -> None:
        """把本地 header 动态区整块写入共享内存（须在奇数 in-progress 之后、
        偶数 commit 之前调用；调用方持 _lock）。"""
        h = self.header
        struct.pack_into("<IIIiq", buf, DYN_OFFSET,
                         int(h.flags), int(h.frame_count), int(h.last_src),
                         int(h.last_slot), int(h.last_pub_ns))

    def _slot_offset(self, slot: int) -> int:
        return HEADER_SIZE + int(slot) * frame_bytes(self.header.frame_w, self.header.frame_h, self.header.bpp)

    # -- 发布（reader 线程 / facade 均可调用，_lock 互斥）-----------------
    def publish_frame(self, data: bytes, src: int) -> None:
        """发布一帧（奇偶 seqlock 提交）。

        顺序：奇数 seq（in-progress）→ 槽字节 → 动态区（frame_count +1、
        last_src/last_slot/last_pub_ns）→ 偶数 seq（commit）。任何读到奇数
        seq、或快照/槽拷贝前后 seq 不一致的读端都作废该次读取——跨进程
        128B/帧字节 memcpy 不原子，靠提交括号保证读端只见「旧完整态」或
        「新完整态」，绝不见中间态（P3A P0-1 修订）。
        """
        if self._closed or self._shm is None:
            return
        with self._lock:
            if self._closed:
                return
            h = self.header
            slot = int(src) % int(h.slot_count)
            off = self._slot_offset(slot)
            expect = frame_bytes(h.frame_w, h.frame_h, h.bpp)
            payload = data[:expect] if len(data) >= expect else data + b"\x00" * (expect - len(data))
            buf = self._shm.buf
            odd = int(h.seq) + 1
            self._write_seq(buf, odd)            # 1) in-progress（奇数）
            buf[off:off + expect] = payload      # 2) 帧字节
            h.frame_count += 1
            h.last_src = int(src)
            h.last_slot = slot
            h.last_pub_ns = time.monotonic_ns()
            self._commit_dyn(buf)                # 3) 动态区整块
            h.seq = odd + 1
            self._write_seq(buf, odd + 1)        # 4) commit（偶数）

    def on_frame(self, data: bytes, src: int) -> None:
        """WebMClip ``_publish_sink`` 钩子的回调名（= publish_frame 别名）。

        reader 线程每解码一帧调用一次；发布 cadence = 解码 cadence
        （本地 reader 的入队被有界队列背压到显示节奏，见设计 §3.6）。
        """
        self.publish_frame(data, src)

    def mark_natural_end(self) -> bool:
        """发布端素材自然播完：置位（保持 session 存活宽限期供消费端读尾帧）。

        幂等：已置自然结束或已中止 → 返回 False（本会话已定终态）。
        """
        return self._set_terminal(FLAG_RUN_ENDED_NATURAL, clear_active=False)

    def mark_aborted(self) -> bool:
        """发布端中途停止（窗口切走/交互/被杀前退出）：消费端回退本地解码。

        幂等：已自然结束（自然优先）或已中止 → 返回 False。
        """
        return self._set_terminal(FLAG_ABORTED, clear_active=True)

    def _set_terminal(self, add: int, clear_active: bool) -> bool:
        if self._closed or self._shm is None:
            return False
        with self._lock:
            if self._closed:
                return False
            h = self.header
            # 终态互斥：自然结束优先于中止（中止只是「没播完就被切走」，
            # 一旦自然结束标记已广播，后续中止不得把它改成 aborted）。
            if h.flags & FLAG_RUN_ENDED_NATURAL:
                return False
            if h.flags & FLAG_ABORTED:
                return False
            h.flags |= add
            if clear_active:
                h.flags &= ~FLAG_SESSION_ACTIVE
            buf = self._shm.buf
            odd = int(h.seq) + 1
            self._write_seq(buf, odd)             # in-progress（奇数）
            self._commit_dyn(buf)                 # flags 变更进动态区
            h.seq = odd + 1
            self._write_seq(buf, odd + 1)         # commit（偶数）
            return True

    # -- 读（消费端；只读头，不修改）--------------------------------------
    def try_read_header(self) -> SessionHeader | None:
        """稳定快照读取（奇偶 seqlock 校验）。成功返回一致快照，否则 None。

        整块拷贝前后各原子读一次 seq：seq 偶数且两次相等 → 动态区拷贝没有
        与任何发布提交重叠，快照可信。块内拷贝到的 seq 字节可能撕裂，一律
        以原子读值回填（P3A P0-1 修订：读端不再接受未经提交校验的头）。
        """
        if self._shm is None:
            raise ValueError("session not attached")
        buf = self._shm.buf
        for _ in range(3):
            s1 = self._read_seq(buf)
            if s1 & 1:
                continue  # 发布端 in-progress：快照作废
            raw = bytes(buf[0:HEADER_SIZE])
            s2 = self._read_seq(buf)
            if s2 == s1:
                header = SessionHeader.unpack(raw)
                header.seq = s1  # 以原子读为准（块拷贝里的 seq 字节可能撕裂）
                return header
        return None

    def read_header(self) -> SessionHeader:
        """稳定快照读取；未 attach 或发布端持续提交无法稳定时抛 ValueError。"""
        if self._shm is None:
            raise ValueError("session not attached")
        header = self.try_read_header()
        if header is None:
            raise ValueError("shm header unstable (publisher mid-commit)")
        return header

    def read_frame(self, src: int, expected_seq: int | None = None) -> bytes | None:
        """读取指定源帧号的槽位字节；校验失败返回 None（调用方重试/跳最新）。

        P3A R2 P2-1 修订（ring 窗口校验）：读前先取**稳定快照**，校验 ``src``
        落在该快照的 ring 窗口 ``[last_src - slot_count + 1, last_src]`` 内：
        - ``src > last_src``：尚未发布 → None；
        - ``src < last_src - slot_count + 1``：该帧的槽已被同槽更新的帧覆盖
          （ring 丢帧）→ None——绝不把「同槽新帧内容」当旧帧返回
          （``read_frame(0)`` 在发布 4 帧后再发第 4 帧时返回 None 而非帧 4）；
        - ``frame_count == 0``：任何帧都没发布过（last_src 初始 0 无意义）→ None。

        窗口判定必须与槽字节同属一次提交，因此 ``last_src`` 取自
        ``try_read_header()`` 的稳定快照，槽拷贝夹在两次原子 seq 读之间且
        与快照 seq 一致：发布端在快照与读槽之间又提交新帧（目标槽可能已被
        ring 覆盖）→ seq 前进 → 返回 None 让调用方重取最新快照，杜绝
        「旧标签配新槽内容」的错位帧。``expected_seq`` 由调用方快照提供：
        当前 seq 已不等于它（调用方快照过期）也返回 None。
        """
        if self._shm is None:
            return None
        buf = self._shm.buf
        # 稳定快照：窗口判定与槽读共用同一提交（含 3 次有界重试）。
        header = self.try_read_header()
        if header is None:
            return None  # 发布进行中/拷贝撕裂：调用方（poll）重取快照
        seq = int(header.seq)
        if expected_seq is not None and seq != expected_seq:
            return None  # 调用方快照已过期：让调用方重取最新
        if int(header.frame_count) <= 0:
            return None  # 从未发布过任何帧
        src = int(src)
        slots = int(header.slot_count)
        last_src = int(header.last_src)
        if src < 0 or src > last_src or src < last_src - slots + 1:
            return None  # ring 窗口外：未发布或已被覆盖（P3A R2 P2-1）
        slot = src % slots
        off = self._slot_offset(slot)
        expect = frame_bytes(int(header.frame_w), int(header.frame_h),
                             int(header.bpp))
        s1 = self._read_seq(buf)
        if s1 != seq:
            return None  # 快照后又有新提交：目标槽可能已被覆盖 → 重取
        data = bytes(buf[off:off + expect])
        s2 = self._read_seq(buf)
        if s2 != s1:
            return None
        return data

    def close(self) -> None:
        """关闭本地句柄（不 unlink；消费端调用）。幂等。"""
        with self._lock:
            shm = self._shm
            self._shm = None
            self._closed = True
        if shm is not None:
            try:
                shm.close()
            except Exception:
                pass

    def unlink(self) -> None:
        """发布端销毁：unlink 共享内存并关闭句柄。幂等。"""
        with self._lock:
            shm = self._shm
            self._shm = None
            self._closed = True
        if shm is not None:
            try:
                shm.close()
            except Exception:
                pass
            try:
                shm.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 模块级字节预算记账（硬上界；对齐 frame_cache 预算语义，设计 §3.7）
# ---------------------------------------------------------------------------
class BrokerBudget:
    """已创建 session 的字节记账（硬上界，超限拒绝创建新 session）。"""

    def __init__(self, max_bytes: int = BROKER_SHM_MAX_BYTES) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._bytes = 0
        self._lock = threading.Lock()

    def reserve(self, size: int) -> bool:
        with self._lock:
            if self._bytes + size > self._max_bytes:
                return False
            self._bytes += size
            return True

    def release(self, size: int) -> None:
        with self._lock:
            self._bytes = max(0, self._bytes - size)

    def total_bytes(self) -> int:
        with self._lock:
            return self._bytes

    def max_bytes(self) -> int:
        return self._max_bytes


BROKER_BUDGET = BrokerBudget()
_BUDGET_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 消费端 feed 句柄（reader 线程与 GUI 线程之间的桥）
# ---------------------------------------------------------------------------
class BrokerFeed:
    """一次订阅尝试的句柄：GUI 侧填 grant/deny 结果，reader 线程有界等待。

    - GUI（facade）在收到 decode_reply_ready 后调用 ``complete(result)``；
    - reader 线程在 movie.start() 后调用 ``wait_result(timeout)``；
    - result 为 None 表示 deny/超时/通道不可用 → 调用方本地解码。

    所有权（P3A P1-2 修订）：本句柄同时是「attach 出的 session 必须有主」的
    收口——reader 一旦放弃等待（超时/被停/deny 后回退本地）就调用
    ``expire()`` 闭锁本句柄：已落定的 result 立即 ``close()``，此后任何迟到
    的 ``complete(session)`` 也立即 ``close()`` 而非落定，杜绝跨进程
    SharedMemory 句柄无主泄漏。``complete``/``expire``/``result`` 全经
    ``_lock`` 串行化，跨线程安全。
    """

    def __init__(self, req_id: str, asset: str) -> None:
        self.req_id = req_id
        self.asset = asset
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._result: Any = None
        self._expired = False
        # facade 挂的回收回调（从 _pending 移除本 req）；reader 线程经
        # expire() 触发，facade 以 pending 锁保护（线程安全）。
        self._on_expired = None

    def complete(self, result: Any) -> None:
        """填 grant/deny 结果。grant = BrokerFeedSession；deny/超时 = None。

        若本句柄已 expired（reader 不再等待）或已有结果（重复 complete），
        新到的 result 立即 ``close()`` 收尾，绝不遗留无主 session。
        """
        close_me = None
        with self._lock:
            if self._expired or self._event.is_set():
                close_me = result
            else:
                self._result = result
                self._event.set()
        if close_me is not None:
            try:
                close_me.close()
            except Exception:
                pass

    def expire(self) -> None:
        """reader 放弃等待（超时/被停/deny 后回退本地）：闭锁本句柄。

        已落定的 result 立即 close（若 grant 恰在 reader 放弃前落定）；
        此后任何迟到 complete 都会被 close。唤醒仍在 wait_result 的调用方
        （返回 None → 本地解码）；经 ``_on_expired`` 通知 facade 清理 pending。
        """
        callback = None
        result = None
        with self._lock:
            if self._expired:
                return
            self._expired = True
            self._event.set()
            result, self._result = self._result, None
            callback = self._on_expired
        if result is not None:
            try:
                result.close()
            except Exception:
                pass
        if callback is not None:
            try:
                callback(self)
            except Exception:
                pass

    def wait_result(self, timeout_s: float) -> Any:
        if not self._event.wait(timeout_s):
            return None
        with self._lock:
            return self._result

    @property
    def ready(self) -> bool:
        """grant/deny/expire 是否已落定（reader 线程轮询 stop 信号时用）。"""
        return self._event.is_set()

    @property
    def expired(self) -> bool:
        return self._expired

    @property
    def result(self) -> Any:
        with self._lock:
            return self._result


class BrokerFeedSession:
    """消费端 feed 运行期对象：附加 shm + 逐帧读取（reader 线程使用）。

    创建于 grant 之后（facade 在 GUI 线程 attach 校验），读取在 WebMClip
    reader 线程。帧进度以动态区的 ``frame_count``（只随帧提交推进）为基准：
    终态标记提交（seq 前进但 frame_count 不动）不产生新帧，由 flags 收尾。
    """

    def __init__(self, session: BrokerShmSession, grant: dict[str, Any]) -> None:
        self._session = session
        self.grant = grant
        # frame_count 缺省（旧版 grant/测试直构）时按 last_src+1 推导
        # （src 从 0 单调递增；-1 = 尚未发布 → 0）。
        fc = grant.get("frame_count")
        if fc is None:
            fc = max(0, int(grant.get("last_src", -1)) + 1)
        self._last_frame_count = int(fc)
        self._stall_deadline = time.monotonic() + STALL_BUDGET_MS / 1000.0

    @property
    def shm_name(self) -> str:
        return self._session.name

    def poll(self) -> tuple[str, Any, Any]:
        """读取下一帧（非阻塞）。

        返回 (kind, data, src)：
        - ('frame', bytes, src)  新帧可用（frame_count 前进后读最新槽）；
        - ('end', None, None)    发布端自然结束（run_ended_natural 且无新帧）；
        - ('abort', None, None)  发布端中止 / 停滞超时（断流）→ 本地回退；
        - ('none', None, None)   暂无新帧（调用方稍后重试）。
        """
        try:
            return self._poll_once()
        except Exception as exc:
            logger.warning('broker feed 读失败，回退本地解码: %s', exc)
            return ("abort", None, None)

    def _poll_once(self) -> tuple[str, Any, Any]:
        header = None
        for _ in range(3):  # 有界重试：发布端比消费端快时收敛到「跳最新」
            h = self._session.try_read_header()
            if h is None:
                continue  # 发布进行中/拷贝撕裂：重取稳定快照
            header = h
            fc = int(h.frame_count)
            last_src = int(h.last_src)
            # 稳定快照内部一致性双保险：动态区整块受奇偶校验保护，理论上
            # 不可能不自洽；此处再验 last_slot == last_src % slot_count，
            # 防御性拒绝任何异常快照（P3A P0-1 要求）。
            if int(h.last_slot) != (last_src % int(h.slot_count)):
                continue
            if fc <= self._last_frame_count:
                break  # 无新帧：走下方终态/停滞判定
            data = self._session.read_frame(last_src, expected_seq=int(h.seq))
            if data is None:
                # 快照后被新提交赶上（目标槽可能已被覆盖）/拷贝撕裂：
                # 重取最新快照，绝不返回错位帧（P3A P2-1 修订）
                continue
            self._last_frame_count = fc
            self._stall_deadline = time.monotonic() + STALL_BUDGET_MS / 1000.0
            return ("frame", data, last_src)
        if header is None:
            return ("none", None, None)  # 连续不稳定：调用方稍后重试
        flags = int(header.flags)
        if flags & FLAG_ABORTED:
            return ("abort", None, None)
        if flags & FLAG_RUN_ENDED_NATURAL:
            return ("end", None, None)
        if time.monotonic() > self._stall_deadline:
            logger.warning('broker feed 停滞超时（%dms 无新帧），回退本地解码',
                           STALL_BUDGET_MS)
            return ("abort", None, None)
        return ("none", None, None)

    def close(self) -> None:
        try:
            self._session.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# BrokerFacade（GUI 线程编排；默认关 = 零行为差异）
# ---------------------------------------------------------------------------
class BrokerFacade:
    """broker 的 GUI 线程编排器（设计 §4 ③、§5 生命周期）。

    挂在 GUI 线程，经 ``CollisionIpcSession`` 的 queued 信号与 worker 通信：
    - coordinator：维护「本实例正在发布的素材 session」表（asset key → session），
      对远端 decode_subscribe 请求按当前 session 状态授权 grant/deny；
    - client：为一次共享播放发起 decode_subscribe，收到 grant 后 attach 共享
      内存并构造 ``BrokerFeedSession``，填入 ``BrokerFeed`` 供 WebMClip reader
      有界等待；deny/超时 → complete(None) → reader 本地解码。

    开关语义（铁律）：``decode_broker_enabled`` 默认 False（灰度）；本类所有
    方法在关闭时为 no-op，调用方（window/app）行为逐位不变。

    **平台门禁（P3A R2 P0-1 / R3）**：``enabled`` 判定 = 配置键 且
    ``broker_platform_supported()``（Windows 且 AMD64/x86_64）——非支持平台
    （ARM macOS/Linux 与 **Windows ARM64** 同属弱序）一律不启用（配置键即使
    为 True 也按 False 处理）。ctypes 普通 8B load/store 无 acquire/release
    语义，seqlock 只在 x86/x64 TSO（Windows）下可靠；不做平台门禁的 broker
    在弱序平台上是未证明正确的协议，这里选择运行时拒绝而不是假装支持
    （低层 create/attach 亦内建同一门禁，见 BrokerShmSession，P3A R3）。

    线程模型：本类自身在 GUI 线程创建/调用（方法全部 GUI 线程或经线程安全
    锁保护）；跨线程边界只经 ipc_session 的 queued 信号与
    ``threading.Event``（BrokerFeed 的 complete/wait）。publisher 表由 GUI
    线程独占读写；``_pending`` 表受 ``_pending_lock`` 保护（reader 线程经
    ``BrokerFeed.expire()`` → ``_on_feed_expired`` 回调跨线程移除条目，锁内
    操作只做 pop，绝不嵌套取 feed 锁）。唯一跨线程对象是 BrokerShmSession
    （自带锁）与 BrokerFeed（自带锁）。
    """

    def __init__(self, ipc_session=None, *, enabled: bool = False,
                 default_fps: float = 24.0, default_total_frames: int = 241,
                 canvas_w: int = 640, canvas_h: int = 360) -> None:
        self._ipc = ipc_session
        # P3A R2 P0-1 / R3：enabled 判定 = 配置开关 ∧ 平台支持（Windows 且
        # AMD64/x86_64——Windows ARM64 同属弱序，一并排除）。非支持平台即使
        # 配置键为 True 也强制关闭——弱序平台无跨进程 acquire/release，ctypes
        # 普通 8B 存取不足以构成可靠 seqlock。门禁结果只求值一次，保证单一、
        # 稳定的判定（不经多次调用产生不一致）。
        requested = bool(enabled)
        platform_ok = broker_platform_supported()
        self._enabled = requested and platform_ok
        if requested and not platform_ok:
            logger.warning('broker: 平台 %s/%s 非 Windows x86/x64（弱序无 TSO '
                           '保证），decode_broker 不启用',
                           sys.platform, platform.machine())
        self._canvas_w = int(canvas_w)
        self._canvas_h = int(canvas_h)
        # 素材 meta 缺失时的回退值（本仓库全部素材实测 241 帧/10.04s@24fps）。
        self._default_fps = float(default_fps)
        self._default_total_frames = max(1, int(default_total_frames))
        # asset key → _PublisherRecord（coordinator 侧；最多一素材一 session；
        # 只登记「尚在播/可授权」的 session——自然结束/中止即移出，宽限期内
        # 由定时器按 record 身份关闭，见 publish_natural_end）
        self._publishers: dict[str, "_PublisherRecord"] = {}
        # req_id → BrokerFeed（client 侧 pending 订阅）
        self._pending: dict[str, BrokerFeed] = {}
        self._pending_lock = threading.Lock()
        self._req_seq = 0
        self._closed = False

    # ---- 开关 / 角色 -------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    def role_known(self) -> bool:
        """角色是否已定（GUI 镜像；worker 选举/欢迎后经 queued 信号更新）。"""
        ipc = self._ipc
        return bool(getattr(ipc, "role_known", False)) if ipc is not None else False

    def is_coordinator(self) -> bool:
        ipc = self._ipc
        return bool(getattr(ipc, "is_coordinator", False)) if ipc is not None else False

    # ---- 绑定 / 解绑（app 在会话重建/切角色时调用）------------------------
    def bind(self, ipc_session) -> None:
        """绑定 CollisionIpcSession（或等价 duck-typed 会话）。

        连接 decode 转发信号到本类槽；会话重建（switch_character）时先
        unbind 旧会话再 bind 新会话，避免旧 worker 信号漏入。
        """
        self._ipc = ipc_session
        if ipc_session is None:
            return
        ipc_session.decode_subscribe_requested.connect(self._on_subscribe_requested)
        ipc_session.decode_reply_ready.connect(self._on_decode_reply)
        # 角色翻转：卸任 coordinator 时中止自己发布的 session（aborted 广播）
        ipc_session.role_changed.connect(self._on_role_changed)

    def unbind(self) -> None:
        """解绑当前 IPC 会话。

        P3A P1-5/P2-2 修订：解绑 = broker teardown 的一部分——除断开信号外，
        一律作废全部 pending 订阅（reader 立即回退本地，无需等 600ms 超时；
        旧会话迟到 grant 命中空表被丢弃，不 attach）。发布 session 不在此
        中止（rebind 同一会话时发布会话仍有效；卸任由 role_changed(False)/
        shutdown 负责）。
        """
        ipc = self._ipc
        if ipc is not None:
            try:
                ipc.decode_subscribe_requested.disconnect(self._on_subscribe_requested)
                ipc.decode_reply_ready.disconnect(self._on_decode_reply)
                ipc.role_changed.disconnect(self._on_role_changed)
            except Exception:
                pass  # 连接不存在（从未 bind / 已解绑）
        self._ipc = None
        self._invalidate_pending("unbind")

    # ---- 窗口层入口（window._switch 在 shareable movie start/end 时调用）-----
    def shareable_start(self, name: str, movie, path: str | None = None,
                        fps: float | None = None,
                        total_frames: int | None = None) -> str:
        """shareable（idle 类）movie 即将 start() 前调用。

        依当前角色挂 WebMClip 钩子：
        - coordinator → publish（movie._publish_sink = session）；
        - client → subscribe（movie._feed_source = BrokerFeed）；
        - 角色未定/功能关/预算满/平台不可用 → 'local'（movie 本地解码，
          不挂任何钩子 → WebMClip 默认路径逐位不变）。
        返回 'publish' / 'feed' / 'local'（日志与测试用）。
        """
        if not self._enabled or self._closed or self._ipc is None:
            return "local"
        if path is None:
            p = getattr(movie, "path", None)
            if p is None:
                return "local"
            path = os.fspath(p)
        if not self.role_known():
            return "local"  # 首帧延迟由窗口层（≤600ms）保证；兜底本地
        if self.is_coordinator():
            return self.publish_start(path, movie, fps=fps, total_frames=total_frames)
        return self.subscribe_start(path, path, name, movie)

    def shareable_end(self, name: str, movie, natural: bool = True) -> None:
        """shareable movie 播放结束（自然播完 / 窗口停播）后调用，幂等。

        coordinator：自然结束广播 run_ended_natural 并宽限销毁 session；
        中止广播 aborted 并销毁。client：清理 pending 订阅与 feed 钩子。
        同时清掉 movie 上的发布/订阅钩子，避免 WebMClip 被复用（_switch
        回退重播等路径）时误用上一轮的 sink/feed。
        """
        if not self._enabled or self._closed:
            return
        path = getattr(movie, "path", None)
        asset = os.fspath(path) if path is not None else ""
        if asset:
            if natural:
                self.publish_natural_end(asset)
            else:
                self.publish_abort(asset)
            self.subscribe_end(asset, movie=movie)
        # 幂等清理 movie 钩子（无论 asset 是否可解析）
        try:
            movie._publish_sink = None
        except Exception:
            pass
        try:
            if getattr(movie, "_feed_source", None) is not None:
                movie._feed_source = None
        except Exception:
            pass

    # ---- 发布侧（coordinator 的 movie 开始/结束）---------------------------
    def publish_start(self, asset: str, movie, fps: float | None = None,
                      total_frames: int | None = None) -> str:
        """coordinator 侧：素材 asset 即将起播 → 确保发布 session 存在。

        返回 'publish'（本实例发布）/ 'local'（不可发布，movie 本地解码）。
        movie 须已具备 ``_publish_sink`` 属性（WebMClip 的镜像钩子入口）。
        预算满/平台不可用/开关关 → 'local'（broker 尽力而为，绝不阻塞播放）。
        """
        if not self._enabled or self._closed:
            return "local"
        if not _SHM_AVAILABLE:
            logger.warning('broker: 平台无 shared_memory，发布降级本地: %s', asset)
            return "local"
        key = asset_key(asset)
        existing = self._publishers.pop(key, None)
        if existing is not None:
            # 同一素材重复起播（stop 后重入等异常路径；自然结束的记录在
            # publish_natural_end 已移出）：旧 session 尚未收尾——中止广播并
            # 幂等关闭（release 预算恰一次），再建新轮（一轮一 epoch）。
            existing.close()
            logger.info('broker: 替换旧发布 session asset=%s', key)
        fps = float(fps) if fps and fps > 0 else self._default_fps
        total = int(total_frames) if total_frames and total_frames > 0 else self._default_total_frames
        size = session_size(self._canvas_w, self._canvas_h, BPP)
        if not BROKER_BUDGET.reserve(size):
            logger.warning('broker: 共享内存预算满，发布降级本地: %s', asset)
            return "local"
        try:
            epoch = secrets.token_hex(8)
            session = BrokerShmSession.create(
                make_shm_name(key, epoch),
                self._canvas_w, self._canvas_h,
                fps=fps, total_frames=total,
                slot_count=SLOT_COUNT_DEFAULT, epoch=epoch,
            )
        except Exception as exc:
            BROKER_BUDGET.release(size)
            logger.warning('broker: 创建共享 session 失败，发布降级本地: %s (%s)',
                           asset, exc)
            return "local"
        record = _PublisherRecord(asset=asset, session=session, size=size,
                                  fps=fps, total_frames=total, movie=movie)
        self._publishers[key] = record
        try:
            movie._publish_sink = session
        except Exception:
            pass  # 无该属性（GifClip/替身）：session 白写无消费者
        logger.info('broker: 发布 session 就绪 asset=%s shm=%s', key, session.name)
        return "publish"

    def publish_natural_end(self, asset: str) -> None:
        """coordinator 侧：素材自然播完（窗口末帧 stop / movie finished）。

        广播 run_ended_natural（消费端据此正常收尾而非回退本地），随后
        宽限期后销毁 session（等远端读尾帧；引用计数语义简化为定时兜底，
        见设计 §3.7 的 SESSION_END_GRACE_S）。幂等：终态只置一次。

        P3A P1-3 修订：记录在自然结束的**当下**就从 ``_publishers`` 移出——
        宽限期内的新订阅直接 deny（session 已定终态），同一素材的下一轮
        起播干净地创建新 epoch session；定时器只按 record 身份 close（幂等、
        预算恰释放一次），绝不误删同 key 的新一轮记录。
        """
        key = asset_key(asset)
        record = self._publishers.pop(key, None)
        if record is None:
            return
        if not record.mark_natural_end():
            return  # 已置终态（自然/中止）→ 幂等返回（不重复调度）
        logger.info('broker: 发布 session 自然结束 asset=%s', key)
        self._schedule_close(record, grace=SESSION_END_GRACE_S)

    def publish_abort(self, asset: str) -> None:
        """coordinator 侧：素材中途停止（切走/暂停/关闭）→ aborted 广播。

        记录弹出后幂等关闭：``_PublisherRecord.close()`` 只 release 预算一次，
        自然结束宽限期记录已被 pop（此处 no-op），不会重复释放。
        """
        key = asset_key(asset)
        record = self._publishers.pop(key, None)
        if record is None:
            return
        if record.mark_aborted():
            logger.info('broker: 发布 session 中止 asset=%s', key)
        record.close()

    # ---- 订阅侧（client 的 movie 开始/结束）--------------------------------
    def subscribe_start(self, asset: str, path: str, anim: str,
                        movie) -> str:
        """client 侧：素材 asset 即将起播 → 发起 decode_subscribe。

        返回 'feed'（movie 已挂 feed 钩子）/ 'local'（未发起，movie 本地解码）。
        订阅决策预算 ≤600ms（reader 线程内等待 grant，设计 P2-4）——
        本方法只负责发请求与挂句柄，不阻塞 GUI。
        """
        if not self._enabled or self._closed or self._ipc is None:
            return "local"
        self._req_seq += 1
        req_id = f"d{os.getpid()}-{self._req_seq}"
        feed = BrokerFeed(req_id=req_id, asset=asset_key(asset))
        # reader 线程超时/放弃时经 feed.expire() → _on_feed_expired 跨线程
        # 移除 pending（锁内 pop），迟到 grant 命中空表即丢弃（P3A P1-2）。
        feed._on_expired = self._on_feed_expired
        with self._pending_lock:
            self._pending[req_id] = feed
        try:
            self._ipc.request_decode({
                "type": "decode_subscribe", "req_id": req_id,
                "path": str(path), "anim": str(anim),
            })
        except Exception as exc:
            with self._pending_lock:
                self._pending.pop(req_id, None)
            logger.warning('broker: 订阅发送失败，回退本地解码: %s (%s)', asset, exc)
            return "local"
        movie._feed_source = feed
        logger.info('broker: 订阅发起 asset=%s req=%s', asset, req_id)
        return "feed"

    def subscribe_end(self, asset: str, movie=None) -> None:
        """client 侧：本素材播放结束/中止 → 清理 pending 订阅（尽力发送
        decode_unsubscribe；worker 对未知消息静默忽略，无需等待答复）。
        传入 movie 时同步清掉其 feed 钩子（幂等）。

        弹出即 complete(None) 唤醒仍等待的 reader 回退本地；条目已不在表中
        的迟到 grant 由 _on_decode_reply 静默丢弃（不 attach → 无泄漏）。
        """
        key = asset_key(asset)
        with self._pending_lock:
            feeds = [feed for req_id, feed in list(self._pending.items())
                     if feed.asset == key]
            for feed in feeds:
                self._pending.pop(feed.req_id, None)
        for feed in feeds:
            if not feed.ready:
                feed.complete(None)  # 唤醒 reader 线程走本地回退
        if movie is not None:
            try:
                movie._feed_source = None
            except Exception:
                pass
        if self._ipc is not None:
            try:
                self._ipc.request_decode({"type": "decode_unsubscribe",
                                          "req_id": "end"})
            except Exception:
                pass  # 尽力而为

    # ---- worker → GUI 转发（coordinator 收订阅 / client 收答复）------------
    def _on_subscribe_requested(self, runtime_id: str, message: dict) -> None:
        """coordinator 收到 decode_subscribe：按当前发布 session 决策。"""
        if not self._enabled or self._closed or self._ipc is None:
            return
        req_id = str(message.get("req_id") or "")
        path = str(message.get("path") or "")
        if not req_id or not path:
            return
        key = asset_key(path)
        record = self._publishers.get(key)
        if record is None or record.session.closed:
            self._deny(runtime_id, req_id, "not_publishing")
            return
        try:
            header = record.session.read_header()
        except Exception:
            # 发布端正在高频提交/会话将销毁：拿不到稳定快照 → 拒绝，
            # 请求方回退本地（尽力而为，绝不发错 grant）。
            self._deny(runtime_id, req_id, "header_unstable")
            return
        total = int(header.total_frames) or record.total_frames
        last_src = int(header.last_src)
        remaining = total - last_src
        if remaining < MIN_JOIN_FRAMES:
            self._deny(runtime_id, req_id, f"too_late:{remaining}")
            return
        grant = {
            "type": "decode_grant", "req_id": req_id,
            "shm_name": record.session.name,
            "epoch": f"{int(header.epoch):016x}" if header.epoch else "",
            "frame_w": int(header.frame_w), "frame_h": int(header.frame_h),
            "bpp": int(header.bpp), "fps_x1000": int(header.fps_x1000),
            "total_frames": int(header.total_frames),
            "slot_count": int(header.slot_count),
            "seq": int(header.seq),
            "frame_count": int(header.frame_count),
            "last_src": int(header.last_src),
        }
        logger.info('broker: grant asset=%s req=%s remaining=%d',
                    key, req_id, remaining)
        self._ipc.send_decode_reply(runtime_id, grant)

    def _deny(self, runtime_id: str, req_id: str, reason: str) -> None:
        try:
            self._ipc.send_decode_reply(runtime_id, {
                "type": "decode_deny", "req_id": req_id, "reason": reason,
            })
        except Exception:
            pass

    def _on_decode_reply(self, message: dict) -> None:
        """client 收到 decode_grant/decode_deny：配对 req_id 后 complete。

        P3A P1-2 修订：pop 与 attach/complete 由 ``_pending_lock`` 与
        ``BrokerFeed`` 自身锁共同收口——
        - reader 已超时并 expire（条目已被移除）→ 此处 pop 为空，静默丢弃
          （根本不 attach，无句柄）；
        - reader 在 attach 进行中才 expire → ``feed.complete(session)`` 见
          expired 立即 close 该 session，绝不遗留无主句柄；
        - deny/attach 失败 → complete(None) → reader 本地解码。
        """
        if not self._enabled or self._closed:
            return
        req_id = str(message.get("req_id") or "")
        with self._pending_lock:
            feed = self._pending.pop(req_id, None)
        if feed is None or feed.ready:
            return  # 过期/已落定/重复 grant：丢弃（不 attach，幂等）
        if message.get("type") != "decode_grant":
            logger.info('broker: 订阅被拒（%s）→ 本地解码 req=%s',
                        message.get("reason"), req_id)
            feed.complete(None)
            return
        try:
            shm_name = str(message.get("shm_name") or "")
            epoch = str(message.get("epoch") or "")
            w = int(message.get("frame_w") or 0) or self._canvas_w
            h = int(message.get("frame_h") or 0) or self._canvas_h
            slots = int(message.get("slot_count") or SLOT_COUNT_DEFAULT)
            session = BrokerShmSession.attach(
                shm_name, w, h, expected_bpp=BPP,
                expected_slot_count=slots, epoch=epoch)
            feed_session = BrokerFeedSession(session, dict(message))
            feed.complete(feed_session)
            logger.info('broker: grant 已 attach req=%s shm=%s', req_id, shm_name)
        except Exception as exc:
            logger.warning('broker: grant attach 失败 → 本地解码 req=%s (%s)',
                           req_id, exc)
            feed.complete(None)

    # ---- pending 生命周期（跨线程收口）------------------------------------
    def _on_feed_expired(self, feed: BrokerFeed) -> None:
        """reader 线程经 feed.expire() 回调：从 _pending 移除该 req。

        与 GUI 线程的 pop 共用 ``_pending_lock``——grant 到达与 reader 超时
        二者在表上串行化，不存在「pop 后无人负责」的窗口（迟到 grant 命中
        空表被丢弃；若已 pop 给 grant 路径，则由 feed 锁保证 close）。
        """
        with self._pending_lock:
            if self._pending.get(feed.req_id, None) is feed:
                self._pending.pop(feed.req_id, None)

    def _invalidate_pending(self, reason: str) -> None:
        """作废全部 pending 订阅（连接断流/角色翻转/解绑/退出）。

        P3A P1-5 修订：回退由连接状态事件触发，600ms 预算只是无事件兜底——
        expire 闭锁每个 feed（醒 reader 返回 None → 本地解码）并清空表；
        之后才可能到达的旧 epoch 迟到 grant 命中空表被丢弃，不 attach。
        仅作废未落定条目；已 stream 的 feed 由 reader/movie stop 收尾
        （设计「角色翻转不追溯当前 movie」）。
        """
        with self._pending_lock:
            feeds = list(self._pending.values())
            self._pending.clear()
        for feed in feeds:
            feed.expire()
        if feeds:
            logger.info('broker: 作废 %d 个 pending 订阅（%s）', len(feeds), reason)

    def _on_role_changed(self, is_coordinator: bool, _epoch: str) -> None:
        """角色翻转（P3A P1-1/P1-5 修订）：

        - 卸任 coordinator（含 worker ``_resign_to`` 退选）→ 中止全部发布
          session（aborted 广播 → 消费端回退本地）+ 作废 pending；
        - 当选 coordinator（旧 coordinator 死亡/退选后的重选举）→ 旧 client
          epoch 的 pending 订阅已无授权来源，作废（reader 回退本地）；
        两者都不追溯当前 movie 的模式（设计「角色翻转不追溯」）。
        """
        if self._closed:
            return
        if is_coordinator:
            self._invalidate_pending("role_coordinator")
            return
        for key in list(self._publishers):
            record = self._publishers.pop(key, None)
            if record is not None:
                record.close()  # 幂等：中止广播 + unlink + 恰一次 release
                logger.info('broker: 角色翻转卸任，中止发布 session asset=%s', key)
        self._invalidate_pending("role_client")

    # ---- 收尾 ---------------------------------------------------------------
    def _schedule_close(self, record: "_PublisherRecord", grace: float) -> None:
        """自然结束宽限后销毁 record（幂等 close，预算恰一次）。

        record 已从 ``_publishers`` 移出，定时器只碰 record 自身（不触碰
        表），同 key 新一轮记录绝不受影响（P3A P1-3 修订）。"""
        def _close() -> None:
            record.close()
            logger.info('broker: session 已销毁 asset=%s', record.asset)
        timer = threading.Timer(max(0.0, grace), _close)
        timer.daemon = True
        record._close_timer = timer
        timer.start()

    def shutdown(self) -> None:
        """应用退出/会话重建：中止全部发布 session 并清理 pending。"""
        if self._closed:
            return
        self._closed = True
        for key in list(self._publishers):
            record = self._publishers.pop(key, None)
            if record is not None:
                record.close()  # 幂等：中止广播 + unlink + 恰一次 release
        self._invalidate_pending("shutdown")
        self.unbind()


class _PublisherRecord:
    """发布侧 session 记录（素材 → 存活 session + 预算记账 + movie 钩子）。

    终态状态机：``_terminal`` 只允许 None → 'natural' / 'aborted' 一次翻转，
    mark_natural_end / mark_aborted 返回是否首次置位（幂等，facade 据此
    只在第一次收尾时调度销毁/广播）。

    P3A P1-3 修订：``close()`` 幂等且只 release 预算一次（``_closed`` 门闩）；
    自然结束定时器经 ``_close_timer`` 可取消——同一 record 绝不重复 unlink/
    release，同一素材的新一轮记录互不影响。
    """

    __slots__ = ("asset", "session", "size", "fps", "total_frames",
                 "movie", "_terminal", "_closed", "_close_timer")

    def __init__(self, asset: str, session: "BrokerShmSession", size: int,
                 fps: float, total_frames: int, movie=None) -> None:
        self.asset = asset
        self.session = session
        self.size = size
        self.fps = fps
        self.total_frames = total_frames
        self.movie = movie
        self._terminal: str | None = None
        self._closed = False
        self._close_timer: threading.Timer | None = None

    def mark_natural_end(self) -> bool:
        if self._terminal is not None or self._closed or self.session.closed:
            return False
        self._terminal = "natural"
        return self.session.mark_natural_end()

    def mark_aborted(self) -> bool:
        if self._terminal is not None or self._closed or self.session.closed:
            return False
        self._terminal = "aborted"
        return self.session.mark_aborted()

    def close(self) -> None:
        """销毁 session 并释放预算（幂等：只执行一次）；同时摘掉 movie 钩子。

        - 未显式收尾的关闭（shutdown/角色卸任/替换）：先按中止语义广播；
        - 自然结束记录：终态已置 natural，只 unlink + release；
        - 重复调用/自然结束定时器与手动关闭重叠 → 第二次直接 no-op，
          预算绝不重复释放（P3A P1-3）。
        """
        if self._closed:
            return
        self._closed = True
        timer, self._close_timer = self._close_timer, None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        if self._terminal is None:
            try:
                self.session.mark_aborted()
            except Exception:
                pass
            self._terminal = "aborted"
        try:
            self.session.unlink()
        except Exception:
            pass
        BROKER_BUDGET.release(self.size)
        movie = self.movie
        if movie is not None:
            try:
                if getattr(movie, "_publish_sink", None) is self.session:
                    movie._publish_sink = None
            except Exception:
                pass
