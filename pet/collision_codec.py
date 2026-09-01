# -*- coding: utf-8 -*-
"""多开桌宠碰撞 IPC 协议帧编解码与水位去重（纯 Python 实现，无 Qt 依赖）。

从 collision.py 迁出：仅含与物理求解无关的协议层——
1. 协议帧解析与编码（4 字节大端长度前缀 + UTF-8 JSON，4096 字节上限超限丢弃）
2. 水位去重（按 epoch 记录每个 pair 最高已应用 tick）
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

FRAME_MAX_LENGTH: int = 4096            # 单帧最大字节数（含/不含前缀，此处限制载荷<=4096）
HEADER_SIZE: int = 4                    # 4字节无符号大端整数长度头


@dataclass
class DecodeError:
    """协议解码错误对象（避免抛异常）。"""
    reason: str
    raw_data: bytes = b""


def encode_frame(obj: Any) -> bytes:
    """将 Python 对象编码为 4 字节大端长度前缀 + UTF-8 JSON 字节帧。"""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = len(payload)
    header = length.to_bytes(HEADER_SIZE, byteorder="big", signed=False)
    return header + payload


class FrameStreamDecoder:
    """流式帧解析器，支持粘包与半包解析，超过 4096 字节安全丢弃。"""

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
