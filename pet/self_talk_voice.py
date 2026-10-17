# -*- coding: utf-8 -*-
"""点击台词 / 绑定台词的本地语音预缓存（后台补齐）。

**为什么要预缓存**：本机合成一句要几秒（自建 CosyVoice 更慢，20 秒上下，还要 ASR
回读校验、不满意就重抽），点击那一刻现合成必然卡顿。所以把点击会说出口的句子提前
落成音频文件，运行时直接播文件（见 ``AppShell.speak_self_talk``）：零延迟、断网可用、
不占常驻内存。

**用谁合成**：与「设置 → 语音报时 → 立即试听」同一条链路——``pet/tts`` 注册表里
**当前配置的后端与音色**（``voice_chime.synth_attempts``）。于是"试听有声音"就等价于
"台词语音能生成"。这里曾经自建了一条直连本机 CosyVoice（127.0.0.1:9880）的路，与注册表
完全脱钩：普通用户既没装那个服务、也得不到任何提示，表现就是"开关开了、没错误、一个
文件都没有"（issue #305）。自建服务只剩一个可选覆盖：``DSH_PET_TTS_URL`` 指向它时先走，
它不在线就**落回注册表**，不再静默收工。

**与外部的契约**（改动等于把已有缓存全部作废，测试已钉死）：

    <配置目录>/self_talk_voice/<md5(文本 utf-8)[:16]>.<扩展名>

扩展名由产出它的后端决定（edge/doubao → mp3，sapi/mimo 与外部产出脚本
``F:\\dsh\\tts\\build_self_talk_voice.py`` → wav）；查找侧按 ``<md5>.*`` 枚举，不认死
扩展名，外部脚本那份 wav 照样命中。

**名单**：每只宠**自己 slot 档案**里的自言自语（``self_talk_texts`` 是每宠键）＋该宠
各角色**点击动画绑定**台词（``character_profiles.<角色>.click_talk_bindings.<动画id>``），
跨宠按文本去重。读错配置层的后果是 slot≠0 的宠在**别人的名单**上生成、点击时命中不到
→ 回退在线合成，音色变成另一个人。

**开关**：``self_talk_voice_precache_enabled`` 默认关闭。开一次要在本机写几十个音频文件，
只有明确开启的用户才付这笔合成开销与磁盘占用。

**落盘是原子的**：先写同目录临时文件、再用 ``os.replace`` 换名，最终路径上只可能完整
文件或什么都不出现；查找侧还会核对 wav 头部声明的长度，半截产物既不上账也不被当成命中。

**导入是惰性的**：合成链（``pet.tts`` / ``voice_chime`` / ``config``）一律在函数里 import
——``import pet.tts`` 实测 0.35s，而本模块随 ``pet.app`` 在启动路径上被 import；预缓存
跑在后台线程，这笔成本放到那时付（与 ``voice_chime_service`` 的惰性口径一致）。
``cached_audio`` 是点击路径上的调用点，它只用标准库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import urllib.request
import uuid
from pathlib import Path

logger = logging.getLogger("dsh-pet-standalone")

CACHE_DIR_NAME = "self_talk_voice"
#: 临时文件前缀：以 ``.`` 开头，不进 ``<md5>.*`` 的查找模式（见 ``cached_audio``）。
TEMP_PREFIX = ".tmp-"
DEFAULT_SERVER = "http://127.0.0.1:9880"
SERVER_ENV = "DSH_PET_TTS_URL"
SETTING_KEY = "self_talk_voice_precache_enabled"

HEALTH_TIMEOUT = 1.5
SYNTH_TIMEOUT = 300.0

# 自建服务覆盖模式（``DSH_PET_TTS_URL``）下已知台词的固定声线，与外部脚本一致。
# 没列到的台词按口吻归类——注册表链路不用这些（音色由「语音报时」的后端配置给）。
VOICE_BY_TEXT = {
    "好女孩……": "happy-06",          # 温柔夸赞
    "好模型……": "neutral-01",        # 平淡陈述
    "欧鲸鲸……": "happy-08",          # 俏皮
    "今天也要认真工作呀。": "happy-01",  # 轻快鼓励
    "再陪你一会儿。": "sad-03",        # 温柔/陪伴
}

# 服务里的参照音是 <情绪>-01..08：归类结果必须落在这个命名空间里。
_FAMILY_SIZE = 8
_SAD_HINTS = ("晚安", "陪你", "想你", "累了", "难过", "对不起", "再见")
_ANGRY_HINTS = ("哼", "讨厌", "才不", "生气", "喂！", "别闹")
_HAPPY_HINTS = ("啦", "呀", "吧", "～", "~", "加油", "真棒", "好耶")


# --------------------------------------------------------------- 缓存键与命中判定


def cache_path(config_dir, text: str, ext: str = "wav") -> Path:
    """预缓存文件的查找路径（桌宠与外部产出脚本共用同一算法）。

    前后空白先剥掉：配置里手写的台词常带尾空格，不剥就会出现"同一句两个文件"，
    点击时命中不到缓存、悄悄回落到联网合成。``ext`` 默认 wav 是外部脚本的产出扩展名；
    注册表链路写的是对应后端的扩展名，查找侧见 :func:`cached_audio`。
    """
    stripped = str(text).strip()
    name = hashlib.md5(stripped.encode("utf-8")).hexdigest()[:16]
    return Path(config_dir) / CACHE_DIR_NAME / f"{name}.{ext}"


def _has_audio(path: Path) -> bool:
    """文件是否真的能播。

    **必须非空**：0 字节的文件是"有名字没内容"——合成的写入被打断（强杀/磁盘满）
    就会留下这种残file，用 ``is_file()`` 判断会把它当成已缓存，结果点击后播静音
    还查不出原因。

    **wav 还要头部自洽**：RIFF 头在偏移 4 处声明了整段长度，被截断的文件声明的长度
    必然大于实际字节数——那种文件播出来是半句/爆音，按"没有"处理让它重合。非 wav
    （edge/doubao 的 mp3）没有可校验的长度字段，只判非空；这类文件只由原子换名产生，
    不会出现半截（见 ``_publish``）。
    """
    try:
        size = path.stat().st_size
        if not path.is_file() or size <= 0:
            return False
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return True
    return size >= int.from_bytes(header[4:8], "little") + 8


def cached_audio(config_dir, text: str) -> Path | None:
    """该句已缓存的音频文件；None = 还没合出来（或只剩残片）。

    按 ``<md5>.*`` 枚举而不是拼死一个扩展名：扩展名由产出它的后端决定（mp3/wav），
    点击侧不该关心是谁合的。临时文件以 ``.`` 开头（见 ``_temp_path``），不进这个
    模式，所以在飞的合成不会被误当命中。
    """
    target = cache_path(config_dir, text)
    for path in sorted(target.parent.glob(f"{target.stem}.*")):
        if _has_audio(path):
            return path
    return None


def pick_voice(text: str) -> str:
    """按台词口吻挑一条参照声线；同一句话永远同一个结果（不做随机）。

    只服务自建服务覆盖模式：32 条参照音全部取自流萤语料，情绪靠声线名归类：
    温柔/晚安 → sad-*，轻快/鼓励 → happy-*，较真 → angry-*，平淡陈述 → neutral-*；
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


# --------------------------------------------------------------- 名单


def collect_lines(config) -> list[tuple[str, str]]:
    """单只宠要预缓存的台词：自言自语 + 该宠各角色点击动画绑定，按文本去重。

    ``config`` 必须是**这只宠**的配置视图（每宠键要走 slot 档案）：主配置那份只是
    slot0 的兜底，拿它统一读会让别的宠在 slot0 的名单上生成。
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


def collect_all_lines(configs) -> list[tuple[str, str]]:
    """多只宠的台词合集：每只宠读**它自己**的配置视图，跨宠按文本去重。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for config in configs:
        for text, source in collect_lines(config):
            if text not in seen:
                seen.add(text)
                out.append((text, source))
    return out


def _pending(config_dir, lines) -> list[tuple[str, str]]:
    """只留还没有可播本地音频的句子。"""
    return [(text, source) for text, source in lines if cached_audio(config_dir, text) is None]


# --------------------------------------------------------------- 网络边界（测试打桩点）


def _get_json(url: str, timeout: float):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _post_json(url: str, payload: dict, timeout: float) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# --------------------------------------------------------------- 落盘


def _temp_path(target: Path) -> Path:
    """同目录的唯一临时路径（``os.replace`` 只在同一文件系统内原子）。

    前缀用 ``.`` 而不是 ``<stem>.part``：查找侧按 ``<md5>.*`` 枚举，任何以文本哈希
    开头的残片都会被误当缓存（voice_chime 的缓存按固定路径命中，才没有这个坑）。
    """
    return target.with_name(f"{TEMP_PREFIX}{uuid.uuid4().hex[:12]}{target.suffix}")


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("清理台词语音临时文件失败：%s", path)


def _publish(target: Path, write) -> Path:
    """把 ``write(临时路径)`` 的产物原子换名到 ``target``；残片/空文件不上账。

    换名走 ``config.atomic_replace_with_retry``（与 config / slot_manager /
    overlay_spawn_state 同一份实现）：Windows 上目标 wav 正被播放器打开时裸
    ``os.replace`` 会直接 WinError 5——同一句的重新合成与点击播放会踩到。
    """
    from .config import atomic_replace_with_retry

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = _temp_path(target)
    try:
        write(temp)
        if not _has_audio(temp):
            raise ValueError("合成结果为空或残片")
        atomic_replace_with_retry(temp, target)
    except Exception:
        _discard(temp)
        raise
    return target


# --------------------------------------------------------------- 合成后端


def _attempts(config) -> tuple:
    """当前配置下可用的合成尝试链（与「试听」同一条：同一后端、同一音色）。

    不可用的后端先摘掉（缺依赖 / 缺凭据），跑一遍 ``preflight()`` 让"配置里已不成立
    的音色"换成默认值——与 ``VoiceChimeService._usable_attempts`` 同一套口径，只是
    这里在后台线程，提示只能进日志（不冒泡）。
    """
    from . import tts, tts_secrets
    from .voice_chime import normalize_chime_config, synth_attempts

    cfg = normalize_chime_config(config)
    secrets = {
        provider.id: {
            spec.name: tts_secrets.get(spec.secret_ref) for spec in tts.secret_fields(provider)
        }
        for provider in tts.providers()
        if tts.secret_fields(provider)
    }
    usable = []
    for attempt in synth_attempts(cfg, secrets=secrets):
        provider = tts.get(attempt.provider)
        values, note = provider.preflight(attempt.values)
        if note:
            attempt.values, attempt.plan = values, provider.plan(values)
            logger.info("台词语音预缓存：%s", note)
        code = provider.availability(attempt.values)
        if code:
            logger.info("台词语音预缓存：跳过后端 %s（%s）", provider.id, code)
            continue
        usable.append(attempt)
    return tuple(usable)


def _synth_with_backends(config_dir, text: str, attempts) -> Path:
    """按尝试链逐后端合成，第一个成功的原子落盘并返回其路径。"""
    from . import tts

    errors: list[str] = []
    for attempt in attempts:
        provider = tts.get(attempt.provider)
        target = cache_path(config_dir, text, attempt.ext)
        try:
            return _publish(
                target, lambda temp, p=provider, a=attempt: p.synth(text, a.plan, temp))
        except Exception as exc:
            errors.append(f"{attempt.provider}: {exc}")
            logger.warning("台词语音预缓存：%s 合成失败：%s", attempt.provider, exc)
    raise RuntimeError("；".join(errors) or "没有可用的合成后端")


def _server_ready(endpoint: str) -> bool:
    try:
        health = _get_json(f"{endpoint}/health", HEALTH_TIMEOUT)
    except Exception:  # noqa: BLE001 —— 服务缺失是常态，落回注册表即可
        return False
    return isinstance(health, dict) and bool(health.get("ok"))


def _synth_server(endpoint: str, text: str) -> bytes:
    blob = _post_json(f"{endpoint}/tts", {"text": text, "voice": pick_voice(text)},
                      SYNTH_TIMEOUT)
    if not blob:
        raise ValueError("合成结果为空")
    return blob


# --------------------------------------------------------------- 执行


def run_precache(config, *, configs=None, server: str | None = None) -> dict:
    """同步补齐缺失的台词音频；返回统计（后台线程与测试都走这里）。

    ``config`` 是主配置（合成后端 / 开关），``configs`` 是每只活跃宠的配置视图
    （台词名单是每宠键，不给就只按 ``config`` 自己那份读）。

    自建服务（``DSH_PET_TTS_URL`` 或显式 ``server``）先走；它不在线就**落回注册表**：
    不允许再出现"服务不在，于是一个文件都没有、用户也看不到任何提示"的静默结局
    （issue #305）。单句合成失败不影响其余句子，也不动已缓存的句子。
    """
    stats = {"checked": 0, "made": 0, "failed": 0, "skipped": 0, "reason": ""}
    config_dir = config.dir
    lines = collect_all_lines([config] if configs is None else configs)
    pending = _pending(config_dir, lines)
    stats["checked"] = len(lines)
    stats["skipped"] = len(lines) - len(pending)
    if not pending:
        stats["reason"] = "up-to-date"
        return stats

    endpoint = server or os.environ.get(SERVER_ENV, "")
    if endpoint and not _server_ready(endpoint):
        logger.warning("台词语音预缓存：本地 TTS 服务 %s 不可用，改用配置的合成后端", endpoint)
        endpoint = ""

    if endpoint:
        for text, source in pending:
            try:
                _publish(cache_path(config_dir, text),
                         lambda temp: temp.write_bytes(_synth_server(endpoint, text)))
            except Exception:
                stats["failed"] += 1
                logger.exception("台词语音预缓存失败：%s（%s）", text, source)
                continue
            stats["made"] += 1
            logger.info("台词语音预缓存完成：%s（%s，自建服务）", text, source)
        return stats

    attempts = _attempts(config)
    if not attempts:
        logger.warning(
            "台词语音预缓存无法进行：没有可用的合成后端"
            "（先在「语音报时」里点一次试听确认能出声，台词语音用的是同一个后端）")
        stats["reason"] = "no-backend"
        return stats

    for text, source in pending:
        try:
            target = _synth_with_backends(config_dir, text, attempts)
        except Exception:
            stats["failed"] += 1
            logger.exception("台词语音预缓存失败：%s（%s）", text, source)
            continue
        stats["made"] += 1
        logger.info("台词语音预缓存完成：%s（%s）-> %s", text, source, target.name)
    return stats


def enabled(config) -> bool:
    """预缓存开关（全局键）：关着时一个合成请求都不发。"""
    try:
        return bool(config.get(SETTING_KEY, False))
    except Exception:
        return False


_active: threading.Thread | None = None
_lock = threading.Lock()


def start_precache(config, *, configs=None, server: str | None = None) -> bool:
    """在后台补齐缺失台词（单飞：上一轮没跑完就不再起一轮）。

    ``configs`` 是每只活跃宠的配置视图（台词名单是每宠键）。返回是否真的起了新一轮；
    调用方（保存设置 / 启动）不需要等它。
    """
    global _active
    if not enabled(config):
        return False
    with _lock:
        if _active is not None and _active.is_alive():
            return False
        thread = threading.Thread(
            target=_run_quietly, args=(config, configs, server),
            name="self-talk-voice-precache", daemon=True,
        )
        _active = thread
        thread.start()
    return True


def _run_quietly(config, configs, server) -> None:
    try:
        run_precache(config, configs=configs, server=server)
    except Exception:
        logger.exception("台词语音预缓存线程异常（不影响点击）")
