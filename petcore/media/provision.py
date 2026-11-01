"""WebM → WebP 首跑供给（复用主分支的世代/GC 管线）。

供给会派生 ffmpeg 做 VP9 解码 + WebP 编码。安装包实测一轮两个待转 clip
在 16 逻辑核机器上约 63 秒、平均 6.6% 总 CPU（峰值 13.3%）；用户开机后
正在用电脑时跑它，任务管理器里就是「启动 CPU 冒到 9%」。这些 clip 即使不转
也有 WebM 兜底，播放不缺失，只是画质/CPU 档位待升级，因此整轮供给**等到
系统空闲**（默认 60s 无键鼠输入）再起手；用户一回来也不会杀掉已在跑的
ffmpeg（`provision_once` 的设计就是不硬杀），只是不再起新的 clip。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger("petcore.media.provision")

_started = False

#: 系统连续无输入多久才允许起手供给（秒）。
PROVISION_IDLE_SECONDS = 60.0
#: 等待空闲时的轮询间隔（秒）。
PROVISION_IDLE_POLL_SECONDS = 20.0
#: 原行为：启动 5 秒后开跑。保留给非 Windows（没有 GetLastInputInfo）。
PROVISION_INITIAL_DELAY_SECONDS = 5.0


def provision_character(
    character,
    *,
    budget_seconds: float = 60.0,
    migration_clips: int = 1,
):
    """对单个角色跑一轮供给，返回 ``ProvisionReport``；不可用时返回 None。"""
    root = Path(getattr(character, "root", ""))
    videos = root / "videos"
    if not videos.is_dir():
        return None
    if not any(
        getattr(animation, "path", None) is not None
        and str(getattr(animation, "path")).lower().endswith(".webm")
        for animation in getattr(character, "animations", ())
    ):
        return None
    try:
        from pet import frameseq_provision
    except Exception:
        logger.debug("frameseq_provision unavailable", exc_info=True)
        return None
    try:
        return frameseq_provision.provision_once(
            videos,
            root / "frameseq",
            round_seconds=float(budget_seconds),
            migration_clips=max(1, int(migration_clips)),
        )
    except Exception:
        logger.warning("首跑供给失败", exc_info=True)
        return None


def _system_idle_seconds() -> float:
    """距离最后一次键鼠输入的秒数；非 Windows / 读失败返回「不空闲」。"""
    if sys.platform != "win32":
        return 0.0
    try:
        from ..platform.foreground import idle_seconds

        return max(0.0, float(idle_seconds()))
    except Exception:  # noqa: BLE001 - 探针坏了就当用户还在用电脑
        return 0.0


def start_background_provision(
    host,
    *,
    idle_wait: float = PROVISION_IDLE_SECONDS,
    poll_seconds: float = PROVISION_IDLE_POLL_SECONDS,
    initial_delay: float = PROVISION_INITIAL_DELAY_SECONDS,
    idle_provider=None,
    sleep=time.sleep,
) -> bool:
    """启动一次后台供给（每个进程最多一次）；返回是否真的启动。

    非 Windows 没有系统级空闲探针：`idle_wait` 强制为 0（保留 5 秒初延时）。
    测试可注入 `idle_provider` / `sleep`，不用真的等一分钟。
    """
    global _started
    if _started:
        return False
    window = getattr(host, "window", None)
    character = getattr(window, "character", None)
    if character is None:
        return False
    root = Path(getattr(character, "root", ""))
    if not root.is_dir() or not os.access(root, os.W_OK):
        return False
    _started = True
    provider = idle_provider or _system_idle_seconds
    if sys.platform != "win32" and idle_provider is None:
        idle_wait = 0.0
    idle_wait = max(0.0, float(idle_wait))
    poll_seconds = max(0.05, float(poll_seconds))

    def worker() -> None:
        if initial_delay > 0:
            sleep(float(initial_delay))
        waited = False
        while provider() < idle_wait:
            if not waited:
                logger.info(
                    "首跑供给等待系统空闲（需连续 %.0f 秒无键鼠输入，每 %.0f 秒检查一次）",
                    idle_wait,
                    poll_seconds,
                )
                waited = True
            sleep(poll_seconds)
        started = time.monotonic()
        logger.info("首跑供给开始（系统已空闲 %.0f 秒）", provider())
        report = provision_character(character)
        if report is None:
            return
        logger.info(
            "首跑供给完成 converted=%s retired=%s deferred=%s locked=%s idle=%s elapsed=%.1fs",
            getattr(report, "converted", 0),
            getattr(report, "retired", 0),
            getattr(report, "deferred_budget", 0)
            + getattr(report, "deferred_backoff", 0)
            + getattr(report, "deferred_disk", 0),
            getattr(report, "locked", False),
            getattr(report, "idle", False),
            time.monotonic() - started,
        )

    thread = threading.Thread(
        target=worker, name="petcore-provision", daemon=True
    )
    thread.start()
    return True
