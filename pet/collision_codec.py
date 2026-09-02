# -*- coding: utf-8 -*-
"""多开桌宠碰撞 IPC 协议帧编解码与水位去重（纯 Python 实现，无 Qt 依赖）。

从 collision.py 迁出：仅含与物理求解无关的协议层——
1. 协议帧解析与编码（4 字节大端长度前缀 + UTF-8 JSON，256 KiB 有界载荷）
2. 水位去重（按 epoch 记录每个 pair 最高已应用 tick）
3. 协议消息 TypedDict（snapshot/impulse 等线格式类型，供编解码边界收敛，
   见 collision_ipc.py 的 _send/_handle_message；TypedDict 仅类型层，无运行期开销）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, TypedDict, Union

FRAME_MAX_LENGTH: int = 256 * 1024       # coordinator 下行；覆盖最多 128 个槽位的完整快照
STATE_FRAME_MAX_LENGTH: int = 4 * 1024   # client 上行状态；阻止单成员耗尽聚合快照预算
HEADER_SIZE: int = 4                    # 4字节无符号大端整数长度头


# ----------------------------------------------------------------------
# 协议消息线格式（TypedDict，total=False：线上字段可缺省）
# ----------------------------------------------------------------------

class ProbeMessage(TypedDict, total=False):
    """协调者决胜探测帧。"""
    type: Literal["probe"]
    runtime_id: str


class CoordinatorMessage(TypedDict, total=False):
    """探测应答：对方是协调者。"""
    type: Literal["coordinator"]
    runtime_id: str
    epoch: str


class HelloMessage(TypedDict, total=False):
    """客户端入会帧。"""
    type: Literal["hello"]
    runtime_id: str
    instance_id: str
    pid: int
    epoch: str


class WelcomeMessage(TypedDict, total=False):
    """协调者入会应答：携带 epoch 与当前成员表。"""
    type: Literal["welcome"]
    epoch: str
    coordinator_id: str
    tick: int
    policy: Dict[str, Any]
    members: List[Dict[str, Any]]


class LeaveMessage(TypedDict, total=False):
    """离开帧。"""
    type: Literal["leave"]
    seq: int


class StateMessage(TypedDict, total=False):
    """成员状态帧：_CollisionWorker.submit_state 的 state dict + type 标记。"""
    type: Literal["state"]
    runtime_id: str
    instance_id: str
    seq: int
    flags: int
    x: float
    y: float
    vx: float
    vy: float
    radius_x: float
    radius_y: float
    mass: float
    is_infinite_mass: bool
    character: str
    scale: float
    w: float
    h: float
    circles: Any
    last_seen: float
    ts: float


class SnapshotMessage(TypedDict, total=False):
    """快照帧：协调者 tick 时点全体成员表。"""
    type: Literal["snapshot"]
    epoch: str
    tick: int
    members: List[Dict[str, Any]]


class ImpulseMessage(TypedDict, total=False):
    """冲量帧：ImpulseResult.asdict() 平铺字段 + epoch/type 标记。"""
    type: Literal["impulse"]
    epoch: str
    tick: int
    pair: str
    a: str
    b: str
    nx: float
    ny: float
    j: float
    sep: float
    contact_x: float
    contact_y: float
    flags: int
    ax: float
    ay: float
    bx: float
    by: float
    dvx_a: float
    dvy_a: float
    dvx_b: float
    dvy_b: float
    dx_a: float
    dy_a: float
    dx_b: float
    dy_b: float


class DecodeSubscribeMessage(TypedDict, total=False):
    """P3 broker：client → coordinator 订阅共享解码请求。

    复用碰撞 QLocal 通道（collision_ipc.py），数据面（帧）不经过本消息——
    只协商「是否共享」与共享内存名/几何。老版本 coordinator 忽略未知类型
    → client 超时回退本地解码（设计 §3.5）。
    """
    type: Literal["decode_subscribe"]
    req_id: str
    path: str
    anim: str


class DecodeUnsubscribeMessage(TypedDict, total=False):
    """P3 broker：client → coordinator 取消订阅（本地 close 尽力发送）。"""
    type: Literal["decode_unsubscribe"]
    req_id: str


class DecodeGrantMessage(TypedDict, total=False):
    """P3 broker：coordinator → client 授权共享解码（携带 shm 信息与几何）。"""
    type: Literal["decode_grant"]
    req_id: str
    shm_name: str
    epoch: str
    frame_w: int
    frame_h: int
    bpp: int
    fps_x1000: int
    total_frames: int
    slot_count: int
    seq: int
    last_src: int


class DecodeDenyMessage(TypedDict, total=False):
    """P3 broker：coordinator → client 拒绝（素材未在播/剩余帧不足/预算满）。"""
    type: Literal["decode_deny"]
    req_id: str
    reason: str


WireMessage = Union[
    ProbeMessage, CoordinatorMessage, HelloMessage, WelcomeMessage,
    LeaveMessage, StateMessage, SnapshotMessage, ImpulseMessage,
    DecodeSubscribeMessage, DecodeUnsubscribeMessage,
    DecodeGrantMessage, DecodeDenyMessage,
]


@dataclass
class DecodeError:
    """协议解码错误对象（避免抛异常）。"""
    reason: str
    raw_data: bytes = b""


def encode_frame(obj: Any, max_frame_len: int = FRAME_MAX_LENGTH) -> bytes:
    """将 Python 对象编码为 4 字节大端长度前缀 + UTF-8 JSON 字节帧。"""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = len(payload)
    if length > max_frame_len:
        raise ValueError(f"Frame length {length} exceeds limit {max_frame_len}")
    header = length.to_bytes(HEADER_SIZE, byteorder="big", signed=False)
    return header + payload


class FrameStreamDecoder:
    """流式帧解析器，支持粘包与半包解析，超过配置上限时安全丢弃。"""

    def __init__(self, max_frame_len: int = FRAME_MAX_LENGTH) -> None:
        self._buffer = bytearray()
        self.max_frame_len = max_frame_len

    def feed(self, chunk: bytes) -> List[Any | DecodeError]:
        """喂入字节流，返回解析成功的消息对象列表或 DecodeError 列表。"""
        if not chunk:
            return []
        self._buffer.extend(chunk)
        results: List[Any | DecodeError] = []

        while True:
            if len(self._buffer) < HEADER_SIZE:
                break

            # 读取 4 字节大端长度
            length = int.from_bytes(self._buffer[:HEADER_SIZE], byteorder="big", signed=False)

            # 超限检查
            if length > self.max_frame_len or length < 0:
                dropped = bytes(self._buffer[:HEADER_SIZE])
                del self._buffer[:HEADER_SIZE]
                results.append(DecodeError(reason=f"Frame length {length} exceeds limit {self.max_frame_len}", raw_data=dropped))
                # The payload length is untrusted, so discard only this header
                # and search the remaining stream for the next plausible header.
                sync_at = None
                for offset in range(len(self._buffer) - HEADER_SIZE + 1):
                    candidate = int.from_bytes(self._buffer[offset:offset + HEADER_SIZE], "big")
                    if 0 < candidate <= self.max_frame_len:
                        sync_at = offset
                        break
                if sync_at is None:
                    self._buffer[:] = self._buffer[-(HEADER_SIZE - 1):]
                    break
                del self._buffer[:sync_at]
                continue

            # 空帧处理 (length == 0)
            if length == 0:
                # 移除这 4 字节
                del self._buffer[:HEADER_SIZE]
                results.append(DecodeError(reason="Empty frame (length 0)", raw_data=b""))
                continue

            # 检查是否接收完整帧载荷
            if len(self._buffer) < HEADER_SIZE + length:
                # 半包，等待更多数据
                break

            # 提取完整载荷
            payload_bytes = bytes(self._buffer[HEADER_SIZE:HEADER_SIZE + length])
            del self._buffer[:HEADER_SIZE + length]

            try:
                text = payload_bytes.decode("utf-8")
                obj = json.loads(text)
                results.append(obj)
            except UnicodeDecodeError as e:
                results.append(DecodeError(reason=f"UTF-8 decode error: {e}", raw_data=payload_bytes))
            except json.JSONDecodeError as e:
                results.append(DecodeError(reason=f"JSON decode error: {e}", raw_data=payload_bytes))

        return results


class WatermarkDeduplicator:
    """基于 epoch / pair / tick 的水位去重器 (plan4 §2.1 & §3.2)。

    客户端每个 epoch 内以 pair 为键记录最高已应用 tick 的水位，不重复应用低于或等于水位的事件。
    当 epoch 变更时，整体重置水位表。
    """

    def __init__(self) -> None:
        self.current_epoch: str = ""
        self.watermarks: Dict[str, int] = {}

    def should_apply(self, epoch: str, pair: str, tick: int) -> bool:
        """检查该 impulse 是否应当被应用。

        如果通过，更新水位并返回 True；若已重复或已过期则返回 False。
        """
        if not epoch or not pair:
            return False

        # epoch 切换：整体替换
        if epoch != self.current_epoch:
            self.current_epoch = epoch
            self.watermarks = {pair: tick}
            return True

        last_tick = self.watermarks.get(pair, -1)
        if tick > last_tick:
            self.watermarks[pair] = tick
            return True

        return False
