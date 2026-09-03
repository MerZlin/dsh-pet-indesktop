# -*- coding: utf-8 -*-
"""P3 broker 平台门禁单测（Qt-free，无真实共享内存依赖，全平台可跑）。

覆盖 P3A R2 P0-1（R3 收口）的门禁契约：
- ``broker_platform_supported()`` = OS（win32）**且**架构（AMD64/x86_64）
  双重判定：darwin/linux 任何架构、以及 **Windows ARM64**（ARM 内存模型在
  Windows 上同样弱序、无 x86 式 TSO，不能仅按 sys.platform 放行）一律 False；
- ``BrokerFacade.enabled`` 判定 = 配置键 ∧ 平台支持（平台不支持时
  shareable_start 返回 'local'，绝不跨进程共享解码）；
- P3A R3 低层门禁：``BrokerShmSession.create/attach`` 是公开 classmethod、
  可绕过 BrokerFacade 被直接调用——非支持平台（含模拟 Windows ARM64）必须在
  触碰任何共享内存之前直接抛 OSError（与 create 既有 OSError 错误风格一致，
  不静默返回失败）。

本文件全部为 monkeypatch 逻辑测试（拒绝路径不创建真实共享内存），故弱序
平台上同样运行（证明拒绝路径）；机制测试（真 create/attach/seqlock）在
test_decode_broker_shm.py，整文件 skipif 非支持平台，二者互补。
"""
from __future__ import annotations

import platform
import sys
from types import SimpleNamespace

import pytest

from pet.decode_broker import (
    BrokerFacade,
    BrokerShmSession,
    broker_platform_supported,
)

# 小几何：低层拒绝发生在触碰共享内存之前，几何仅作参数占位。
_W, _H = 40, 30


def _sim(monkeypatch, os_name: str, machine: str) -> None:
    """把 decode_broker 模块内的 sys.platform / platform.machine() 换成
    (os_name, machine)，不改全局解释器状态（R2 同款 SimpleNamespace 手法）。"""
    monkeypatch.setattr("pet.decode_broker.sys",
                        SimpleNamespace(platform=os_name))
    monkeypatch.setattr("pet.decode_broker.platform",
                        SimpleNamespace(machine=lambda: machine))


def test_broker_platform_supported_gate_matrix(monkeypatch):
    """平台判定 = win32 且 machine ∈ {AMD64, x86_64} 的矩阵：
    darwin/linux 任何架构一律 False；win32 只有 AMD64/x86_64 放行。"""
    # 弱序 OS（即使 x86_64 架构）一律 False
    for os_name in ("darwin", "linux"):
        for machine in ("x86_64", "amd64", "arm64", "aarch64"):
            _sim(monkeypatch, os_name, machine)
            assert broker_platform_supported() is False, (os_name, machine)
    # Windows 架构白名单：AMD64/x86_64 放行（大小写不敏感）
    for machine in ("AMD64", "amd64", "x86_64"):
        _sim(monkeypatch, "win32", machine)
        assert broker_platform_supported() is True, machine
    # Windows 非白名单架构一律拒绝
    for machine in ("ARM64", "aarch64", "x86", "", "unknown"):
        _sim(monkeypatch, "win32", machine)
        assert broker_platform_supported() is False, machine


def test_broker_platform_supported_rejects_windows_arm64(monkeypatch):
    """Windows ARM64 模拟拒绝：ARM 内存模型在 Windows 上同样弱序，无 x86 式
    TSO——sys.platform == 'win32' 不再足以放行，machine()=='ARM64' 必须 False。"""
    _sim(monkeypatch, "win32", "ARM64")
    assert broker_platform_supported() is False
    # 对照：同 OS 下 AMD64 放行（证明拒绝来自架构判定而非 OS 判定）
    _sim(monkeypatch, "win32", "AMD64")
    assert broker_platform_supported() is True


def test_gate_reflects_real_environment():
    """真机一致性：门禁结果与当前 sys.platform/machine() 的实证取值一致
    （防 monkeypatch 漏网 / 环境异常导致门禁与实证平台脱节）。"""
    real = (
        sys.platform == "win32"
        and platform.machine().lower() in {"amd64", "x86_64"}
    )
    assert broker_platform_supported() is real


def test_facade_enabled_is_config_and_platform_gate(monkeypatch):
    """enabled 判定 = 配置键 True 且平台支持；非支持平台（模拟 ARM macOS /
    Windows ARM64）即使配置 True 也强制不启用（shareable_start 一律 local，
    绝不跨进程共享解码）。"""
    # 平台不支持：enabled=True 被门禁夹成 False
    monkeypatch.setattr("pet.decode_broker.broker_platform_supported",
                        lambda: False)
    facade_off = BrokerFacade(enabled=True)
    assert facade_off.enabled is False
    # shareable_start 走 no-op → 'local'（无任何发布/订阅动作）
    movie = type("_FakeMovie", (), {"path": "nope.webm"})()
    assert facade_off.shareable_start("idle", movie) == "local"
    # 平台支持：配置键直通
    monkeypatch.setattr("pet.decode_broker.broker_platform_supported",
                        lambda: True)
    facade_on = BrokerFacade(enabled=True)
    assert facade_on.enabled is True
    facade_on2 = BrokerFacade(enabled=False)
    assert facade_on2.enabled is False


def test_shm_direct_call_blocked_when_platform_unsupported(monkeypatch):
    """P3A R3 低层门禁：broker_platform_supported()=False（非支持平台）时，
    BrokerShmSession.create/attach 直接调用（绕过 BrokerFacade）必须在触碰
    任何共享内存之前抛 OSError——不经 facade 也能被拒绝，不静默返回失败。"""
    monkeypatch.setattr("pet.decode_broker.broker_platform_supported",
                        lambda: False)
    with pytest.raises(OSError, match="平台不支持"):
        BrokerShmSession.create("dsh-broker-gate", _W, _H,
                                fps=24.0, total_frames=241)
    with pytest.raises(OSError, match="平台不支持"):
        BrokerShmSession.attach("dsh-broker-gate", _W, _H)


def test_shm_direct_call_blocked_on_simulated_windows_arm64(monkeypatch):
    """集成：模拟 Windows ARM64（sys.platform=win32 且 machine()=ARM64）——
    架构感知的 broker_platform_supported() 为 False，create/attach 直接调用
    被内建门禁拦截（OSError），且全程从未触碰 multiprocessing.shared_memory
    （若门禁失效，间谍 SharedMemory 会立刻 AssertionError 而非真建段）。"""
    import multiprocessing.shared_memory as mpsm

    calls: list = []
    orig_shm = mpsm.SharedMemory

    class _SpyShm(orig_shm):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError(
                "平台门禁未生效：unsupported 平台触碰了共享内存"
            )

    monkeypatch.setattr("multiprocessing.shared_memory.SharedMemory", _SpyShm)
    _sim(monkeypatch, "win32", "ARM64")
    assert broker_platform_supported() is False
    with pytest.raises(OSError, match="平台不支持"):
        BrokerShmSession.create("dsh-broker-gate", _W, _H,
                                fps=24.0, total_frames=241)
    with pytest.raises(OSError, match="平台不支持"):
        BrokerShmSession.attach("dsh-broker-gate", _W, _H)
    assert calls == [], "门禁拒绝路径不得触碰共享内存"
