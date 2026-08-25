# -*- coding: utf-8 -*-
"""合成点击 Q 弹音效（无外部素材依赖）。

生成 assets/sounds/click.wav：短促"Q 弹"声——快速下扫 + 指数衰减 +
一次轻微回弹（模拟按压弹起的果冻感）。标准库 wave + math，无第三方依赖。

用法：python scripts/make_click_sound.py [输出路径]
"""
from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 22050


def _bounce_wave(duration: float = 0.16) -> list[float]:
    """下扫 + 衰减 + 回弹的合成波形（0.0~1.0 采样值）。"""
    n = int(SAMPLE_RATE * duration)
    samples: list[float] = []
    for i in range(n):
        t = i / SAMPLE_RATE
        # 主弹：440 -> 160 Hz 快速下扫，指数衰减
        f1 = 440.0 - 280.0 * min(1.0, t / 0.08)
        a1 = math.exp(-t * 22.0)
        # 回弹：第二声 200 -> 120 Hz，从 55ms 起，更轻更短
        t2 = t - 0.055
        a2 = math.exp(-max(t2, 0.0) * 38.0) if t2 > 0 else 0.0
        f2 = 200.0 - 80.0 * min(1.0, max(t2, 0.0) / 0.06)
        v = a1 * math.sin(2 * math.pi * f1 * t) + 0.55 * a2 * math.sin(2 * math.pi * f2 * max(t2, 0.0))
        samples.append(max(-1.0, min(1.0, v)))
    return samples


def write_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = _bounce_wave()
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for v in samples:
            frames += struct.pack('<h', int(v * 32767 * 0.7))
        w.writeframes(bytes(frames))
    print(f'written: {path} ({len(samples) / SAMPLE_RATE:.2f}s, {path.stat().st_size} bytes)')


if __name__ == '__main__':
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / 'assets' / 'sounds' / 'click.wav'
    write_wav(out)
