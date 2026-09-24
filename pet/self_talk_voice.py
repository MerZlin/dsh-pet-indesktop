# -*- coding: utf-8 -*-
"""点击台词 / 绑定台词的本地语音预缓存（后台补齐）。

**为什么要预缓存**：本机 CosyVoice（默认 127.0.0.1:9880）合成一句要 20 秒上下
（还要 ASR 回读校验、不满意就重抽），点击那一刻现合成必然卡顿。所以把点击会说
出口的句子提前落成音频文件，运行时直接播文件（见 ``AppShell.speak_self_talk``）：
零延迟、断网可用、不占常驻内存。

**与外部的契约**（改动等于把已有缓存全部作废，测试已钉死）：

    <配置目录>/self_talk_voice/<md5(文本 utf-8)[:16]>.wav

产出侧脚本是 ``F:\\dsh\\tts\\build_self_talk_voice.py``；两侧算法必须一致。

**名单**：全局自言自语（``self_talk_texts``）＋各角色**点击动画绑定**台词
（``character_profiles.<角色>.click_talk_bindings.<动画id>``），按文本去重。
绑定台词同样会朗读，漏收的后果是它只能回落在线合成 —— 音色会变成另一个人。

**开关**：``self_talk_voice_precache_enabled`` 默认关闭。本机没跑 TTS 服务是常态，
通用用户不该被服务探测打扰，所以只有明确开启的用户才会发请求。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger("dsh-pet-standalone")

CACHE_DIR_NAME = "self_talk_voice"
DEFAULT_SERVER = "http://127.0.0.1:9880"
SERVER_ENV = "DSH_PET_TTS_URL"
SETTING_KEY = "self_talk_voice_precache_enabled"

HEALTH_TIMEOUT = 1.5
SYNTH_TIMEOUT = 300.0

# 已知台词的固定声线（与外部脚本一致）。没列到的台词按口吻归类。
VOICE_BY_TEXT = {
    "好女孩……": "happy-06",  # 温柔夸赞
    "好模型……": "neutral-01",  # 平淡陈述
    "欧鲸鲸……": "happy-08",  # 俏皮
    "今天也要认真工作呀。": "happy-01",  # 轻快鼓励
    "再陪你一会儿。": "sad-03",  # 温柔/陪伴
}

# 服务里的参照音是 <情绪>-01..08：归类结果必须落在这个命名空间里。
_FAMILY_SIZE = 8
_SAD_HINTS = ("晚安", "陪你", "想你", "累了", "难过", "对不起", "再见")
_ANGRY_HINTS = ("哼", "讨厌", "才不", "生气", "喂！", "别闹")
_HAPPY_HINTS = ("啦", "呀", "吧", "～", "~", "加油", "真棒", "好耶")


def cache_path(config_dir, text: str) -> Path:
    """预缓存文件的查找路径（桌宠与产出脚本共用同一算法）。

    前后空白先剥掉：配置里手写的台词常带尾空格，不剥就会出现"同一句两个文件"，
    点击时命中不到缓存、悄悄回落到联网合成。
    """
    stripped = str(text).strip()
    name = hashlib.md5(stripped.encode("utf-8")).hexdigest()[:16] + ".wav"
    return Path(config_dir) / CACHE_DIR_NAME / name


def pick_voice(text: str) -> str:
    """按台词口吻挑一条参照声线；同一句话永远同一个结果（不做随机）。

    32 条参照音全部取自流萤语料，情绪靠声线名归类：温柔/晚安 → sad-*，
    轻快/鼓励 → happy-*，较真 → angry-*，平淡陈述 → neutral-*；
    同一情绪内的具体编号按文本哈希固定，保证"这句话的声音"稳定可复现。
    """
    stripped = str(text).strip()
    known = VOICE_BY_TEXT.get(stripped)
    if known:
        return known
    if any(hint in stripped for hint in _SAD_HINTS):
        family = "sad"
    elif any(hint in stripped for hint in _ANGRY_HINTS):
        family = "angry"
    elif stripped.endswith(("！", "!")) or any(hint in stripped for hint in _HAPPY_HINTS):
        family = "happy"
    else:
        family = "neutral"
    slot = int(hashlib.md5(stripped.encode("utf-8")).hexdigest(), 16) % _FAMILY_SIZE + 1
    return f"{family}-{slot:02d}"


def collect_lines(config) -> list[tuple[str, str]]:
    """收集要预缓存的台词：全局自言自语 + 各角色点击动画绑定，按文本去重。

    返回 ``[(文本, 来源说明), ...]``；来源只用于日志，方便核对"这句是哪来的"。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(text, source: str) -> None:
        stripped = str(text or "").strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            out.append((stripped, source))

    try:
        texts = config.get("self_talk_texts") or []
    except Exception:
        texts = []
    for text in texts:
        add(text, "全局自言自语")

    try:
        profiles = config.data.get("character_profiles")
    except Exception:
        profiles = None
    if isinstance(profiles, dict):
        for character_id in profiles:
            try:
                bindings = config.click_talk_bindings(character_id)
            except Exception:
                continue
            if not isinstance(bindings, dict):
                continue
            for action_id, lines in bindings.items():
                if not isinstance(lines, list):
                    continue
                for line in lines:
                    add(line, f"点击绑定[{character_id} / {action_id}]")
    return out


def _has_audio(path: Path) -> bool:
    """缓存文件是否真的可用。

    **必须非空**：0 字节的文件是"有名字没内容"——合成的写入被打断（强杀/磁盘满）
    就会留下这种残file，用 ``is_file()`` 判断会把它当成已缓存，结果点击后播静音
    还查不出原因。残file按"没有"处理，让它重新合成。
    """
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def missing_lines(config) -> list[tuple[str, str]]:
    """返回``[(文本, 声线), ...]``：只含还没有可用本地音频的那几句。"""
    config_dir = config.dir
    return [(text, pick_voice(text)) for text, _source in collect_lines(config) if not _has_audio(cache_path(config_dir, text))]


# --------------------------------------------------------------- 网络边界（测试打桩点）


def _get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post_json(url: str, payload: dict, timeout: float) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# --------------------------------------------------------------- 执行


def run_precache(config, *, server: str | None = None) -> dict:
    """同步补齐缺失的台词音频；返回统计（后台线程与测试都走这里）。

    服务不在线是**正常情况**（通用用户根本没装本地 TTS）：记一条 info 就收工，
    不抛异常、不刷错误日志。单句合成失败也不影响其余句子。
    """
    server = server or os.environ.get(SERVER_ENV, DEFAULT_SERVER)
    stats = {"checked": 0, "made": 0, "failed": 0, "skipped": 0, "reason": ""}
    config_dir = config.dir
    pending = missing_lines(config)
    stats["checked"] = len(collect_lines(config))
    stats["skipped"] = stats["checked"] - len(pending)
    if not pending:
        stats["reason"] = "up-to-date"
        return stats

    try:
        health = _get_json(f"{server}/health", HEALTH_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 —— 服务缺失是常态，不该冒泡
        logger.info("台词语音预缓存跳过：本地 TTS 服务不可用（%s）", exc)
        stats["reason"] = "service-unavailable"
        return stats
    if not isinstance(health, dict) or not health.get("ok"):
        logger.info("台词语音预缓存跳过：本地 TTS 服务未就绪")
        stats["reason"] = "service-unavailable"
        return stats

    for text, voice in pending:
        target = cache_path(config_dir, text)
        try:
            blob = _post_json(f"{server}/tts", {"text": text, "voice": voice}, SYNTH_TIMEOUT)
            if not blob:
                raise ValueError("合成结果为空")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
        except Exception:
            stats["failed"] += 1
            logger.exception("台词语音预缓存失败：[%s] %s", voice, text)
            continue
        stats["made"] += 1
        logger.info("台词语音预缓存完成：[%s] %s -> %s", voice, text, target.name)
    return stats


def _enabled(config) -> bool:
    try:
        return bool(config.get(SETTING_KEY, False))
    except Exception:
        return False


_active: threading.Thread | None = None
_lock = threading.Lock()


def start_precache(config, *, server: str | None = None) -> bool:
    """在后台补齐缺失台词（单飞：上一轮没跑完就不再起一轮）。

    返回是否真的起了新一轮；调用方（保存设置 / 启动）不需要等它。
    """
    global _active
    if not _enabled(config):
        return False
    with _lock:
        if _active is not None and _active.is_alive():
            return False
        thread = threading.Thread(
            target=_run_quietly,
            args=(config, server),
            name="self-talk-voice-precache",
            daemon=True,
        )
        _active = thread
        thread.start()
    return True


def _run_quietly(config, server: str | None) -> None:
    try:
        run_precache(config, server=server)
    except Exception:
        logger.exception("台词语音预缓存线程异常（不影响点击）")
