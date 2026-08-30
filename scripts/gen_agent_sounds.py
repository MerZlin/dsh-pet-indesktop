# -*- coding: utf-8 -*-
"""生成 Agent 内置音效 WAV 文件（start.wav, done.wav, error.wav）。

使用标准库 wave + math 合成，采样率 22050Hz, 16bit, 单声道，峰值控制在 0.5 防止爆音。
- start: 上扬双音（约 0.25s）
- done: 悦耳上扬三音（约 0.4s）
- error: 下行低音（约 0.3s）
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 22050


def _generate_tone_samples(
    frequencies: list[float],
    durations: list[float],
    sample_rate: int = SAMPLE_RATE,
    peak_amplitude: float = 0.5,
) -> list[int]:
    """生成包含多段音调的 16-bit PCM 采样序列，带有平滑淡入淡出（Envelope）。"""
    all_samples: list[int] = []
    max_val = 32767 * peak_amplitude

    for freq, dur in zip(frequencies, durations):
        num_samples = int(sample_rate * dur)
        attack_len = int(num_samples * 0.1)
        decay_len = int(num_samples * 0.2)
        sustain_len = num_samples - attack_len - decay_len

        for i in range(num_samples):
            # 包络计算
            if i < attack_len:
                env = i / attack_len
            elif i < attack_len + sustain_len:
                env = 1.0
            else:
                release_idx = i - (attack_len + sustain_len)
                env = 1.0 - (release_idx / max(1, decay_len))

            # 正弦波
            t = i / sample_rate
            val = math.sin(2.0 * math.pi * freq * t) * env * max_val
            all_samples.append(int(val))

    return all_samples


def _write_wav(output_path: Path, samples: list[int], sample_rate: int = SAMPLE_RATE) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)  # 单声道
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        # 打包成 16-bit 有符号整数 (<h)
        raw_data = struct.pack(f"<{len(samples)}h", *samples)
        wf.writeframes(raw_data)


def generate_agent_sounds(target_dir: Path | None = None) -> dict[str, Path]:
    if target_dir is None:
        target_dir = Path(__file__).resolve().parents[1] / "assets" / "sounds" / "agent"

    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. start: 上扬双音（约 0.25s: 0.12s + 0.13s，440Hz -> 659.25Hz / A4 -> E5）
    start_samples = _generate_tone_samples([440.0, 659.25], [0.12, 0.13], peak_amplitude=0.5)
    start_path = target_dir / "start.wav"
    _write_wav(start_path, start_samples)

    # 2. done: 悦耳上扬三音（约 0.4s: 0.12s + 0.12s + 0.16s，523.25Hz -> 659.25Hz -> 783.99Hz / C5 -> E5 -> G5）
    done_samples = _generate_tone_samples([523.25, 659.25, 783.99], [0.12, 0.12, 0.16], peak_amplitude=0.5)
    done_path = target_dir / "done.wav"
    _write_wav(done_path, done_samples)

    # 3. error: 下行低音（约 0.3s: 0.15s + 0.15s，329.63Hz -> 220.0Hz / E4 -> A3）
    error_samples = _generate_tone_samples([329.63, 220.0], [0.15, 0.15], peak_amplitude=0.5)
    error_path = target_dir / "error.wav"
    _write_wav(error_path, error_samples)

    return {
        "start": start_path,
        "done": done_path,
        "error": error_path,
    }


if __name__ == "__main__":
    sounds = generate_agent_sounds()
    print("已生成 Agent 内置音效：")
    for name, path in sounds.items():
        print(f" - {name}: {path} ({path.stat().st_size} bytes)")
