# -*- coding: utf-8 -*-
"""语音报时：调度服务 + edge-tts 合成 + QtMultimedia 播放（GUI 线程）。

架构（参考 pet/todo_reminder.py 与 pet/click_sound.py）：
- 模块顶层不 import Qt：QObject / QTimer / QtMultimedia 均在方法内惰性导入，
  保证纯逻辑层（voice_chime.py）可在无 Qt 环境测试；
- VoiceChimeService 不继承 QObject，持有无主 QTimer，由 AppShell 持有引用
  保证生命周期（同 TodoReminderService）；
- 报时调度 tick（20s）→ voice_chime.is_chime_minute 判定 + 槽位盖戳幂等；
- 文本双轨：语音用中文口播文本（build_chime_sentence，同时作为合成输入与
  缓存键），气泡用阿拉伯数字文本（build_bubble_sentence），两者解耦；
- 合成在后台线程跑 edge_tts（asyncio），完成后经 QObject 信号（queued）
  桥回 GUI 线程，用 QMediaPlayer + QAudioOutput 播放；
- edge_tts **懒加载**：模块顶层只做 find_spec 探测（不 import 本体），真正的
  import 推迟到 _TTSWorker 的合成线程里第一次合成时——edge_tts 首次 import
  实测 ~1.4s 且常驻内存，而语音报时默认关闭、服务可能整个不 start，顶层 import
  等于让每个用户白付这笔钱；
- 音频缓存于 config.dir/voice_chime_cache，按“文本+音色+语速+音调”哈希
  去重，同句不重复合成；
- 预合成降延迟：距下一报时点 ≤ 60s 时提前在后台线程合成该次报时音频并
  写缓存，到点直接播缓存实现近零延迟；预合成未及时完成则回退即时合成。
- 免打扰（含全屏等效）期间不出声：报时语音与节日语音在**合成入口**
  （``_fire``）、**回放出口**（``_handle_synthesized``）与 **``speak()`` 的
  排队入口**（免打扰期间不入队，不留到免打扰结束后补播）过同一道闸
  （``_quiet_muted``，判据仍是 ``quiet_mode``），预合成一并跳过（省一次
  TTS 网络请求），被丢弃的只有声音——气泡语义不变（照旧进暂存队列）；
  点击自言自语属交互应答（issue #208），在调用侧显式豁免（豁免随队携带）。
- **通道真空闲 = 无合成在飞（``_busy`` False）∧ 播放器未接管
  （``_player_engaged``）**——本模块唯一的占用判据（``_channel_idle``）。两个补播
  队列（``_queued_fire`` 到点报时、``_pending_speech`` 外部播报）共用它：不空闲就
  **原样留队**，等下一拍（合成完成 / EndOfMedia 终止 / 合成超时自愈）再来。凡是
  **合成完成却没起播**的结局（预合成、免打扰丢弃、合成失败、已停止）都没有
  EndOfMedia 媒体终止事件，正是靠这三拍里的另外两拍兜住：豁免的那条照常出声，
  尊重免打扰的那条被 ``_fire`` 的闸现读再丢一次。
- **代次作废旧合成**：回调协议（path/text/error）没有任务身份，故每次起合成与
  watchdog 释放时递增 ``_synthesis_generation``，worker 回调由闭包带回动身时的
  代次（信号首参）。代次不符 = 超时自愈已把这次合成作废，``_on_synthesized``
  整条丢弃、一个字段都不碰——否则旧任务的回调会取走**新任务**的 role / 免打扰
  口径 / busy 锁，让该静音的旧语音出声、应豁免的新语音被静音。
- **播放时刻重判接管**：合成期间别的音频可以合法起播（报时走缓存例外，不看占用），
  所以 ``_handle_synthesized`` 的两个播放出口在**动手播放那一刻**再判一次
  ``_player_engaged()``：被接管就不 ``setSource``，改为回队（报时回 ``_queued_fire``、
  外部播报回 ``_pending_speech``），等下一拍按上面的不变量出队。
- **回队不盖新**：两个回队点都是"槽非空即弃旧"——槽里已有更新的请求时，这次**较旧**
  的完成结果直接丢弃（debug 日志），槽内那条及其旗原样保留。排队契约的入口方向是
  "后来的覆盖前面的"（只留最新一条），回队方向不能倒灌成"旧的盖新的"，否则更新的
  请求会被一条过期语音永久顶掉。
- **播放接管自记标志**：``_player_engaged`` 除枚举判据外还认一个服务侧自记的
  ``_play_pending_end``（"已发出 play、未见终止态"）。实机实测 ``setSource`` + ``play()``
  之后约 30ms 起有一段 ``mediaStatus=LoadedMedia`` 而 ``playbackState`` 仍是
  ``StoppedState`` 的窗口（约 200ms），两个枚举子判据都不成立——只看枚举会把这段时间
  的通道误判成空闲，新语音当场 ``setSource`` 顶掉刚起播的那一段且永远等不到
  EndOfMedia。
- **自记标志的滞留窗口与三道兜底（如实登记）**：这个标志没有 Qt 事件保证一定有结局——
  设备被拔 / 音频输出异常时，``play()`` 之后可能**既不给终止态也不进 PlayingState**，
  标志卡 True 就是通道**永久判忙**（``speak`` 永远排队、``play_file`` 永远让路、合成
  完成永远回队出不来）。所以复位有四条路径：终止态（EndOfMedia / InvalidMedia）、
  ``errorOccurred``、``stop()`` / 免打扰切断、以及 ``_play_pending_since +
  _PLAY_PENDING_MAX_S``（120s，取值理由见该常量）的看门狗上限兜底（最后那条覆盖"连
  错误信号都不给"的静默滞留，并与合成超时共用同一条单次表 —— ``_reararm_watchdog``
  取最早到点的那个期限）。兜底本身有三道：① ``errorOccurred`` 接到
  ``_on_player_error``，按终止态同一套结算；② 上限到点清标志并按既有同序结算两个队列；
  ③ **同一拍还要连播放器一起治**（``_release_stale_play_pending``）——是
  ``stop()`` + **清空音源**（``setSource(QUrl())``，见 ``_unload_player_source``），
  不是单单 stop()：Qt 6.11 FFmpeg 后端 ``QFFmpegMediaPlayer::stop()`` 在
  ``mediaStatus == LoadingMedia`` 时只写 ``m_requestedStatus`` 就 return，**不改
  mediaStatus**（"加载永远不完成"的真死局下 stop() 等于没做，``LoadingMedia`` 仍让
  ``_player_engaged()`` 恒真、drain 判忙留队；之后加载自然完成才转 LoadedMedia，
  那不是 stop 的功劳）；空源才会让 mediaStatus 落到 ``NoMedia``（真机探针实测 ~5ms，9.4ms 内确认）。
  **真在 PlayingState 的段照旧在播、上限不碰它**：不停播放器、不清标志与两个队列、
  不动音源，只把期限起点顺延一个窗口再查（上限只管"卡住"，掐合法在播就是"报时被
  截断"）。
- **缓存原子落盘**：worker 先写同目录临时文件（``*.part``），完成后经
  ``config.atomic_replace_with_retry`` 原子换名到最终缓存路径——``edge_tts.save`` 是
  边收边写，直接写最终路径时进程被斩（daemon worker 随退出死掉）会留下永久截断的
  mp3，而 ``_cache_hit`` 只看"存在且非空"就会天天命中它、天天播静音。最终路径上只
  可能出现完整文件或什么都不出现；换名走重试版是因为 Windows 上目标 mp3 正被播放器
  打开时裸 ``os.replace`` 直接 WinError 5（同 key 的预合成 / 独立进程试听会撞上）；
  缓存目录里的孤儿 ``*.part`` 在服务启动时清扫（"在册"只保护本进程在飞的合成，
  跨进程靠写入占用，见 ``_sweep_orphan_temp_files``）。
- **免打扰切断在播段**：免打扰（含全屏等效）开启那一拍，当前正在播放且**尊重免打扰**
  的那一段立即停掉（用户的开关不能等到这段放完）；``respect_quiet=False`` 的交互应答
  （点击自言自语）照播——与入口闸的豁免口径同一条。免打扰退出**不补播**，与 ``_fire``
  的丢弃语义一致。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .. import tts, tts_secrets
from ..tts_diagnostics import missing_dependency_hint
from ..voice_chime import (
    build_bubble_sentence,
    build_chime_sentence,
    cache_key,
    chime_slot,
    next_chime_in_seconds,
    normalize_chime_config,
    synth_attempts,
)

logger = logging.getLogger(__name__)

# 缓存清理最小间隔（秒）：报时/试听/设置保存都会触发 apply_config，而缓存目录
# 全量 glob+stat 属纯 IO 开销；节流到 5 分钟一次即可满足 200 文件上限的收敛。
_PRUNE_MIN_INTERVAL_S = 300.0
# 合成回调超时（秒）：worker 线程若异常未回调，_busy 会永久置位导致报时静默，
# 超过该时限在调度 tick 中复位（正常合成 3-8s 完成，45s 余量充足）。
_SYNTH_TIMEOUT_S = 45.0
# 合成超时的**布表**间隔（秒/毫秒）：阈值 + 1s 余量（表到点那一拍再判一次是否真该解锁）。
_UNLOCK_INTERVAL_S = _SYNTH_TIMEOUT_S + 1.0
_UNLOCK_INTERVAL_MS = int(_UNLOCK_INTERVAL_S * 1000)
# 「已发出 play、未见终止态」（``_play_pending_end``）的最长合理时长（秒）：超过它
# 仍未见到终止态、也没进 PlayingState，就认定这一段**永远不会有结局**，复位标志并
# 按终止态同一套结算排队项。
#
# 取值理由（不是拍脑袋）：
# - 本机真实缓存目录 22 条报时音频实测 **1.42s ~ 7.73s**（均值 5.09s，ffmpeg 探针）；
# - 合法文本上限 = 报时句（十余字）+ 单条台词 120 字（``voice_chime._MAX_CUSTOM_QUOTE_LEN``
#   截断），约 130 字；语速可调到 -100%（edge-tts 下限，约半速），按 ~5 字/秒折算
#   **理论最坏 ≈ 52s**；
# - 取 120s = 理论最坏的两倍以上，正常长段不会被误伤；同时把"永久判忙"的持续
#   时间封在 2 分钟以内。
#
# 提前响也不危险（所以不必跟着文本长度动态算）：标志只是 ``_player_engaged`` 的
# **第三个**子判据，真在出声时 ``playbackState == PlayingState`` 照旧为真——上限
# 放掉标志不会把正在播的那一段误判成空闲。
_PLAY_PENDING_MAX_S = 120.0

# 预合成角色分两种，**占位状态互不相干**（2026-10-17）：
#
# - :data:`PRECACHE_ROLE`：报时预合成（``_maybe_precache``）。它自己占
#   ``_precache_slot`` / ``_precache_text`` / ``_precache_bubble`` / ``_precache_path``
#   这一组字段，到点由 ``_consume_precache`` 取用。
# - :data:`AUX_PRECACHE_ROLE`：**旁路预合成**（外部功能各要各的"点下去就出声"，如
#   每日一签的 ``precache``）。缓存文件按自己的文本落到自己的 cache key，
#   **一个报时占位字段都不碰**。
#
# 为什么必须分开：两者曾共用 ``role="precache"``，于是旁路预合成也能改报时那一组占位
# 字段——它的合成**失败**会把报时**已经合成好**的那一段占位一起清掉（到点白等一次冷
# 合成），它的合成**成功**又会把 ``_precache_path`` 覆盖成旁路的那个 mp3（报时日志
# 里报的是别人的文件名）。分开之后各自的成败只说自己那一句。
PRECACHE_ROLE = "precache"
AUX_PRECACHE_ROLE = "precache_aux"
#: 两个"提前合好、不出声"的角色：``_fire`` 的缓存命中分支与 ``_handle_synthesized``
#: 的播放出口都要把它们与真正要出声的角色分开。
PRECACHE_ROLES = (PRECACHE_ROLE, AUX_PRECACHE_ROLE)


def _remaining_unlock_ms(seconds: float) -> int:
    """把"剩余秒数"换算成布表的毫秒间隔。

    夹到 ``(0, 最大布表间隔]``：脏值（``inf`` / ``nan`` / 负数，或时钟被回拨导致
    "已经过去"的时间为负）不许变成 ``OverflowError`` 或 0 间隔的忙转表——这两种
    都会让手动通道**唯一**的自愈入口失效。
    """
    try:
        capped = min(max(0.001, float(seconds)), max(_UNLOCK_INTERVAL_S, _PLAY_PENDING_MAX_S))
        return max(1, int(capped * 1000))
    except (TypeError, ValueError, OverflowError):
        return _UNLOCK_INTERVAL_MS


# 「缺东西」错误码：由 provider 自己声明（与 TtsProvider.availability_code 同源），
# 服务层据此给可操作的降级提示，而不是笼统的「合成失败」。保留常量名是为了既有
# 调用点与测试不必跟着改。
EDGE_TTS_MISSING = tts.edge.AVAILABILITY_CODE
MIMO_KEY_MISSING = tts.mimo.AVAILABILITY_CODE

# 同一条 provider 自检提示每个进程只弹一次（如「音色已下架，已改用晓晓」）
_ANNOUNCED_NOTES: set[str] = set()

#: 合成中间产物的文件名后缀。最终缓存路径永远是 ``<key>.mp3``，半截产物只可能叫
#: ``<key>.<唯一标识>.part``——清扫只需按这个模式匹配，不必按修改时间猜。
TEMP_SUFFIX = ".part"

#: 在跑的合成所登记的临时文件（进程内台账）。启动清扫只删**孤儿**：同进程里
#: stop() → start() 之间可能还有上一次的合成在飞，删掉它的临时文件等于让那次合成
#: 白跑（POSIX 上 os.replace 直接 FileNotFoundError）。
_live_temp_paths: set[str] = set()
_live_temp_lock = threading.Lock()


def _nonempty_file(path: Path) -> bool:
    """文件存在**且非空**（0 字节 / 半截产物一律不算）。"""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _cache_hit(path: Path) -> bool:
    """缓存命中判定：**最终缓存路径**上的文件存在且非空。

    只判 ``exists()`` 不够——失败的合成会留下 0 字节文件（``edge_tts.save`` 先建文件
    再写，服务端"成功"却没给音频时就留下它），那会被当成「已有缓存」，于是播出一声
    静音且不再重新合成（实机在缓存目录里抓到过一串 0 字节 mp3）。

    "只认最终名"由 worker 保证：它写的是 ``*.part`` 临时文件（见 :func:`_temp_cache_path`），
    合成完成的产物经 ``os.replace`` 原子换名才出现在最终路径上，所以这里**不做**也
    不该做"多大才算完整"那类猜测。
    """
    return _nonempty_file(path)


def _temp_cache_path(out_path: Path) -> Path:
    """本次合成用的临时文件路径：同目录 + 唯一标识。

    必须同目录（``os.replace`` 只在同一文件系统内原子）且带唯一标识（同一段文本的
    两次合工会 hash 到同一个最终路径，先后两次合成必须各写各的临时文件）。
    """
    return out_path.with_name(f"{out_path.stem}.{uuid.uuid4().hex[:12]}{TEMP_SUFFIX}")


def _register_live_temp(path: Path) -> None:
    with _live_temp_lock:
        _live_temp_paths.add(str(path))


def _unregister_live_temp(path: Path) -> None:
    with _live_temp_lock:
        _live_temp_paths.discard(str(path))


def _sweep_orphan_temp_files(cache_dir: Path) -> None:
    """清扫缓存目录里的**孤儿**临时文件（服务启动时一次）。

    上一版 worker 直接把 ``edge_tts.save`` 写向最终路径，进程被斩（daemon worker
    随退出死掉）就在缓存目录里留下半截文件，而 ``_cache_hit`` 只看"存在且非空"
    → 天天命中、天天播静音。改成临时文件 + 原子换名后，半截产物只可能叫
    ``*.part``，所以这里**按名字模式直接删**——不按修改时间猜（"多久没动过算孤儿"
    在新进程刚起来时必然全都不算旧，等于没清）。

    "在册"的边界要说清：台账 :data:`_live_temp_paths` 是**进程内**的，跳过的只是
    **本进程**在飞的合成（同进程里 stop() → start() 之间可能还有上一次的合成在跑，
    删掉它的临时文件等于让那次合成白跑）。跨进程（独立设置进程的试听与主进程的
    预合成写同一个缓存目录）**没有**这份保护，靠的是：Windows 上正在被写入的文件
    ``unlink`` 会失败（PermissionError → 下面吞掉、留给对方继续写）；POSIX 上误删
    一个在写的 ``*.part`` 也只会让对方这次合成找不到临时文件而算本次失败、下一轮
    重试，**不会**产生坏缓存——最终路径只由原子换名产生，``_cache_hit`` 也只认
    最终路径。
    """
    with _live_temp_lock:
        live = set(_live_temp_paths)
    try:
        if not cache_dir.is_dir():
            return
        for stale in cache_dir.glob(f"*{TEMP_SUFFIX}"):
            if str(stale) in live:
                continue
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                # 删不掉就留着：最可能是**别的进程**正在写它（Windows 共享冲突），
                # 也可能只是本进程登记晚于这次清扫——两种都不该把清扫升级成启动失败。
                logger.debug("清理孤儿合成临时文件失败：%s", stale)
    except OSError:
        logger.debug("合成临时文件清扫失败：%s", cache_dir)


class _TTSWorker(threading.Thread):
    """后台线程：按尝试链逐个合成，第一个成功的写盘并回调返回。

    ``attempts`` 是本模块外的纯逻辑产物（``voice_chime.synth_attempts``），已排好
    主/备顺序；``paths`` 与之等长——每个后端各自的缓存路径（扩展名由 provider 的
    合成计划给出：edge mp3、MiMo wav）。

    真正的合成本体在 ``TtsProvider.synth()`` 里（provider 自己惰性导入依赖、自己
    组请求），所以本类**不需要**知道任何后端的细节：加后端不用动这里。
    全部失败时回调带回按顺序拼接的各后端错误（第一个是主后端，用户最该知道）。

    线程不直接碰 Qt 对象；完成后把结果交给 owner 提供的回调（owner 在
    GUI 线程，回调触发 queued 信号桥接）。

    **落盘是原子的**：合成写的是同目录临时文件（``*.part``），成功后 ``_publish()``
    换名到最终缓存路径。直接写最终路径时，进程在合成中途退出（本线程是 daemon，
    随进程被斩）会留下永久截断的文件，而 ``_cache_hit`` 只看"存在且非空"→ 天天
    命中它、天天播静音，还永远不再重合成。这一层由**本类**统一提供：provider 只需
    往 ``synth(text, plan, temp_path)`` 给的临时路径里写。

    临时文件台账（``_register_live_temp``）同样在这里登记：台账的意义是"本次进程
    正在写的路径不受启动清扫影响"，与哪个后端无关。
    """

    def __init__(self, text: str, attempts, paths, on_done) -> None:
        super().__init__(daemon=True)
        self._text = text
        self._attempts = tuple(attempts)
        self._paths = tuple(paths)
        self._on_done = on_done
        # 每个后端各自的临时路径：与 ``_paths`` 等长，合成前逐条登记进台账。
        self._temp_paths = [_temp_cache_path(Path(path)) for path in self._paths]

    def run(self) -> None:  # noqa: D102
        try:
            self._synthesize_attempts()
        finally:
            # 本线程是短命的、生命周期由本进程掌握：SAPI 若在这个线程上初始化过 COM，
            # 收尾时配对释放（常驻 GUI 线程不释放——那份计数跟着进程活）。
            try:
                tts.sapi.release_thread_com()
            except Exception:  # pragma: no cover - 非 Windows / 库缺失
                pass

    def _synthesize_attempts(self) -> None:
        """按尝试链逐个合成（``run`` 的本体；收尾的 COM 释放由 ``run`` 负责）。"""
        errors: list[str] = []
        for index, (attempt, path) in enumerate(zip(self._attempts, self._paths)):
            temp_path = self._temp_paths[index]
            provider = tts.get(getattr(attempt, "provider", ""))
            if provider is None:
                errors.append(f"未知的合成后端：{getattr(attempt, 'provider', '')}")
                continue
            # 临时文件台账：登记在册的路径不受启动清扫影响（见 _sweep_orphan_temp_files）。
            # 用 try/finally 而不是散在各条 return 前：任何异常路径都要注销，否则台账
            # 会随着合成次数无界增长。
            _register_live_temp(temp_path)
            try:
                provider.synth(self._text, attempt.plan, temp_path)
            except tts.TtsUnavailable as exc:
                # provider 在合成时才发现自己不可用（缺库 / 缺 Key）
                self._discard_partial(temp_path)
                errors.append(exc.code or provider.availability_code)
                logger.warning("%s 当前不可用（%s），尝试下一个合成后端", provider.id, exc)
                continue
            except Exception as exc:  # noqa: BLE001 —— 单个后端失败不该终止整条链
                # 失败产物必须清掉：provider 多半是先建文件再写（edge_tts.save 就是），
                # 半截/0 字节文件留在缓存目录会被后续 exists() 判定成"已有缓存"，
                # 播出来是一声静音且再也不重合成。
                self._discard_partial(temp_path)
                errors.append(f"{type(exc).__name__}: {exc}")
                logger.warning("%s 合成失败：%s", provider.id, exc)
                continue
            finally:
                _unregister_live_temp(temp_path)
            if not _nonempty_file(temp_path):
                # 服务端「成功」但没给音频（0 字节）：也算失败，别留下空缓存
                errors.append(f"{provider.id}: 服务端未返回音频（空文件）")
                self._discard_partial(temp_path)
                continue
            # 换名成功才回成功回调：换名失败等于这段音频没落进缓存，
            # 报成功会让到点的缓存命中路径播一个不存在的文件。
            if not self._publish(temp_path, Path(path)):
                errors.append(f"{provider.id}: 缓存换名失败")
                self._discard_partial(temp_path)
                continue
            self._on_done(str(path), self._text, "")
            return
        fallback_path = str(self._paths[0]) if self._paths else ""
        self._on_done(fallback_path, self._text, "|".join(errors) or "没有可用的合成后端")

    @staticmethod
    def _discard_partial(temp_path: Path) -> None:
        """删掉本次合成留下的临时文件。

        只删临时文件、**不碰最终路径**：最终路径上的东西只可能是别的合成原子换名
        出来的完整缓存，顺手删掉会毁掉一份本来能用的缓存。
        """
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("清理半截合成产物失败：%s", temp_path)

    @staticmethod
    def _publish(temp_path: Path, out_path: Path) -> bool:
        """把临时文件原子换名到最终缓存路径（同目录内换名 + 瞬时共享冲突重试）。

        这是本模块"缓存永远不半截"的全部机制：最终路径上只可能出现完整文件或
        什么都不出现，读者（``_cache_hit``）没有中间态可撞见。

        换名走 ``config.atomic_replace_with_retry``（口径与 config / slot_manager /
        overlay_spawn_state 同一份实现）：Windows 上目标 mp3 **正被播放器打开**时裸
        ``os.replace`` 会直接 WinError 5——同一段文本的两次合成（主进程预合成 vs
        独立设置进程试听）落同一个缓存键就会踩到，杀软/索引服务扫描刚落盘的文件也
        一样。这类共享冲突会自愈（同一份实现里仍是"只重试冲突、其它 OSError 立即
        上抛"），重试比"这次合成白跑"划算。

        重试预算耗尽仍失败（或非冲突错误，如 ENOENT/ENOSPC）→ 返回 False，走既有
        "换名失败 = 本次合成失败"路径（调用方记错误、清临时文件、下一轮再试）。
        惰性 import：保持本模块的导入图不变（这里已在后台线程、且刚跑完网络合成，
        一次 import 的成本可以忽略）。
        """
        from ..config import atomic_replace_with_retry

        try:
            atomic_replace_with_retry(temp_path, out_path)
            return True
        except OSError:
            logger.warning("语音缓存换名失败：%s", out_path, exc_info=True)
            return False


class _AudioBridge:
    """QObject 信号桥：后台线程合成完成 → queued 信号回 GUI 线程。

    _Signals 实例本身即 QObject，随服务存活到进程退出；历史遗留的无主
    QObject（_obj）与其 destroy() 从未被调用，删除以免误导生命周期判断。

    信号对象在 GUI 线程创建（_fire 由 GUI 线程调用），PySide6 依其线程亲和
    把跨线程 emit 排到 GUI 线程执行——已在实机测过：后台 emit、槽在 GUI
    线程运行（不要把这里改成普通 Python 回调绕开信号）。
    """

    def __init__(self) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Signals(QObject):
            # generation 在首位：合成的任务身份，迟到回调靠它作废（见 _on_synthesized）。
            synthesized = Signal(int, str, str, str)  # generation, path, text, error

        self.signals = _Signals()

    def callback_for(self, generation: int):
        """给**这一次**合成做回调载体：闭包捕获动身时的代次。

        返回的是 worker 认识的三参回调 ``(path, text, error)``——代次不进 worker
        协议，而是由闭包在 emit 时补进信号的首参，从而跨线程带到 GUI 线程的槽里
        （跨线程只有 queued 信号这一条路，代次必须随信号走，不能事后查表）。
        """
        def done(path: str, text: str, error: str) -> None:
            # 从后台线程调用：Qt 自动 queued 到 GUI 线程
            self.signals.synthesized.emit(generation, path, text, error)

        return done


class VoiceChimeService:
    """语音报时服务（AppShell 持有，GUI 线程）。

    配置键（config.py 平铺顶层键）：
      voice_chime_enabled / voice_chime_schedule / voice_chime_custom_times /
      voice_chime_voice / voice_chime_rate / voice_chime_pitch / voice_chime_volume /
      voice_chime_show_bubble / voice_chime_show_quote
    """

    TICK_INTERVAL_MS = 20_000
    BUBBLE_DURATION_MS = 8000
    MAX_CACHE_FILES = 200
    # 预合成窗口：距下一报时点 ≤ 60s 时提前在后台合成并写缓存，到点直接播。
    PRECACHE_WINDOW_S = 60

    def __init__(self, app) -> None:
        from PySide6.QtCore import Qt, QTimer

        self._app = app
        config = getattr(app, "config", None)
        self._cache_dir = Path(getattr(config, "dir", Path("."))) / "voice_chime_cache"
        # 契约形状（enabled/schedule/custom_times/voice/rate/pitch/volume）：
        # 与 _on_tick/_fire 读取的键一致，勿用带 voice_chime_ 前缀的镜像默认值。
        self._cfg: dict = normalize_chime_config(None)
        self._last_slot: str | None = None
        self._busy = False
        # monotonic 时间戳：_busy_since 用于合成卡死自愈，_last_prune_at 用于
        # 缓存清理节流（两者都只做"多久没做过了"的判断，不参与业务语义）。
        self._busy_since = 0.0
        self._last_prune_at = 0.0
        self._synthesis_role = "play"  # play | precache | precache_aux：区分合成完成回调的用途
        # 本次合成是否尊重免打扰（True = 免打扰期间丢弃结果）。与 _synthesis_role
        # 同生命周期：合成开始时置位、回调里消费后复位为默认值。
        self._synthesis_respect_quiet = True
        # 本次合成的时间戳：只在"播放时刻通道被接管、报时项回队"时原样带回
        # _queued_fire（气泡文本随队携带，该字段本身对播放无影响）。同生命周期。
        self._synthesis_now: datetime | None = None
        # 合成**代次**：每次起合成与 watchdog 释放时递增，worker 回调由闭包带回
        # 动身时的代次——回调协议只有 path/text/error，没有任务身份，代次就是身份。
        # 迟到的旧任务回调据此整条丢弃（见 _on_synthesized），不再串到新任务上。
        self._synthesis_generation = 0
        # 预合成状态：目标槽位 / 语音文本 / 气泡文本 / 缓存文件（到点命中即零延迟播放）
        self._precache_slot: str | None = None
        self._precache_text: str | None = None
        self._precache_bubble: str | None = None
        self._precache_path: str | None = None
        # 即时合成中的气泡文本：合成完成回调里与语音文本配对展示（二者解耦）
        self._pending_bubble: str | None = None
        # 停止作废标记：stop()（关闭开关/退出）后，飞行中的合成结果不再回放
        self._stopped = False
        # 外部播报（节日提醒）经 speak() 复用本服务的音频通道：通道忙时进深度 1
        # 待播队列，当前一段播完再播——不打断、不叠音。None 表示无待播。
        self._pending_speech: str | None = None
        # 排队的那一条是否尊重免打扰（False = 交互应答，出队时即便在免打扰也照常
        # 出声）。与 _pending_speech 同生命周期：入队时随文本一起置位，出队消费后
        # 复位为默认值 True。
        self._pending_respect_quiet = True
        # 媒体终止态枚举；_ensure_player 成功时填充，未创建播放器时为空元组。
        self._terminal_statuses: tuple = ()
        # 「已发出 play、未见终止态」标志：``_player_engaged`` 的第三个子判据。
        # 实机实测 setSource+play 之后约 30ms 起有一段 mediaStatus=LoadedMedia 而
        # playbackState 仍是 StoppedState 的窗口（约 200ms），两个枚举子判据都不成立
        # → 通道被误判成空闲、新语音当场顶掉刚起播的那一段（见 _player_engaged）。
        # 由两个播放出口（``_mark_play_pending``）置位；终止态、播放错误、stop() /
        # 免打扰切断、以及 ``_PLAY_PENDING_MAX_S`` 上限兜底复位（``_clear_play_pending``）。
        self._play_pending_end = False
        # 标志的置位时刻（monotonic）。这个标志的滞留窗口**没有** Qt 事件可挂——设备
        # 被拔这类结局既不给终止态也不进 PlayingState——所以除了事件复位，还要有
        # 一个时限兜底（见 _release_stale_play_pending 与 _PLAY_PENDING_MAX_S）。
        self._play_pending_since: float | None = None
        # 当前在播段是否尊重免打扰（None = 当前没有在播段）。播放出口随起播一起写，
        # 免打扰开关那一拍据此决定切不切（见 _on_quiet_state_changed）。
        self._current_respect_quiet: bool | None = None
        # 看门狗表（单次）：手动通道（立即报时 / 设置页试听）整条生命周期都不 start()，
        # 没有 20s tick 就没人跑 ``_release_stale_busy``——worker 万一漏回调，_busy 永久
        # 卡死、通道再也不出声（此后每次手动播报都只能进队列）。所以起合成（以及起播，
        # 见 _mark_play_pending）就地布一道超时，到点调同一个自愈入口；正常运行路径下
        # 它与 tick 互为冗余，语义不变。
        #
        # **两个未结期限共用这一条表**（`_reararm_watchdog` 取最早的一个）：① 合成卡死
        # （``_busy_since + _UNLOCK_INTERVAL_S``）；② 播放接管标志卡住
        # （``_play_pending_since + _PLAY_PENDING_MAX_S``）。二者都会让通道永久判忙，
        # 而手动通道没有别的周期性入口，共用一条表才能让两者都被兜住。
        #
        # **PreciseTimer 是这条表起码的要求**：Qt 的 CoarseTimer 允许比间隔提前最多
        # 5% 触发（46s 的表 ≈ 43.7s 就可能响），而这一响正是"过没过 45s 阈值"的判定
        # 时刻——提前响会把手动通道唯一的自愈入口白白烧掉。但 PreciseTimer 也**不是
        # 契约保证**（它只是精度承诺，调度压力下仍可能偏离），所以重布逻辑照旧要有：
        # 见 _release_stale_busy 的"经过不足阈值就按剩余时间重布"。
        self._unlock_timer = QTimer()
        self._unlock_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._unlock_timer.setSingleShot(True)
        self._unlock_timer.setInterval(_UNLOCK_INTERVAL_MS)
        self._unlock_timer.timeout.connect(self._release_stale_busy)
        # 让位钩子：由 AppShell 注入 Callable[[str], bool]，入参是本分钟的报时槽位，
        # 返回 True 表示该槽位让位给节日提醒（报时本分钟不说）。None = 无人让位。
        # 用注入而不是让报时反查节日服务，是为了保持本模块对外零依赖。
        self.yield_slot = None
        self._bridge: _AudioBridge | None = None
        self._player = None
        self._audio_out = None
        # 合成在飞时被 busy 门拦下的待补播报（_fire 排队，_on_synthesized 补播）
        self._queued_fire: tuple | None = None
        self._timer = QTimer()
        self._timer.setInterval(self.TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        # **构造即挂**免打扰监听：手动通道（「立即报时」/ 设置页试听，见 say_now）
        # 与点击自言的本地播报（play_file）都只懒创建服务、**从不 start()**，而
        # 免打扰开关那一拍要切掉正在出声的那一段——监听不能只在 start() 里注册
        # （理由与取舍见 _attach_quiet_listener）。放在构造末尾：此刻全部字段已就绪。
        #
        # 构造期防护：挂监听失败（依赖半死 / 订阅容器实现异常）时先把**可能已经挂上去**
        # 的回调摘掉再原样上抛——否则订阅表会留着一条指向**构造失败**对象的回调，而那个
        # 对象不会被任何人 stop()，回调永远悬着（半挂状态）。异常照旧上报给调用方，不吞。
        try:
            self._attach_quiet_listener()
        except Exception:
            try:
                self._detach_quiet_listener()
            except Exception:
                logger.debug("构造失败后摘除免打扰监听也失败", exc_info=True)
            raise

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        # 启动清扫：上一次进程被斩（daemon worker 随退出死掉）留下的孤儿 ``*.part``
        # 在这里清掉——它们永远不会被换名，留着只是垃圾（且会让缓存目录看起来有
        # 一堆"半截缓存"）。只删在册之外的，见 _sweep_orphan_temp_files。
        _sweep_orphan_temp_files(self._cache_dir)
        # 免打扰监听在构造时就挂好了（见 __init__ 与 _attach_quiet_listener），这里
        # 再挂一次是兜底：stop() 摘除之后再 start 的**同一实例**要靠它挂回来；重复挂
        # 由 add_listener 的身份去重保证幂等。
        self._attach_quiet_listener()
        self.apply_config()
        self._on_tick()
        self._timer.start()

    def stop(self) -> None:
        """停止调度；正在播放的音频一并停住，飞行中的合成结果作废（见 _on_synthesized）。"""
        self._stopped = True
        # 正在出声的这一段必须立刻停：关掉开关/退出后继续放完，用户听到的就是
        # "关不掉"；退出路径上播放器还可能正被析构。
        self._stop_current_segment()
        # 待播队列同样作废：关掉开关/退出后不该再补播一条排队的语音。
        self._pending_speech = None
        self._queued_fire = None
        # 合成锁一并松开：当前产品路径 stop() 之后这个对象即被丢（``_sync_chime_service``
        # 把引用置 None，下次懒创建的是新实例），所以这不是可达路径的修复，而是**防复用**：
        # 复用 stop 过的实例走手动通道（立即报时 / 设置页试听）时，残留的 ``_busy`` 会让
        # ``_fire`` 直接走"合成在飞，排队待补"，这个对象此后一次也发不出声音（ops-S1 重演）。
        self._busy = False
        self._busy_since = 0.0
        self._synthesis_role = "play"
        self._disarm_unlock_timer()
        self._timer.stop()
        self._detach_quiet_listener()

    def is_running(self) -> bool:
        """调度 tick 是否在跑（AppShell 的懒启停门控据此决定重启还是只刷配置）。"""
        return bool(self._timer.isActive())

    def apply_config(self) -> None:
        """重读配置（设置保存、右键开关后调用）。

        统一走纯逻辑层 normalize_chime_config：清洗/钳制规则只有一处实现，
        且 config.json 被手改成非法值时回落默认值而不是抛异常（本方法在
        start()（开机）与「立即报时」路径上执行，抛异常等于语音报时在启动期
        直接失败）。
        """
        config = getattr(self._app, "config", None)
        self._cfg = normalize_chime_config(config if config is not None else {})
        # 配置变更后预合成缓存可能失效（音色/语速/台词开关/时间点变化），丢弃。
        self._precache_slot = None
        self._precache_text = None
        self._precache_bubble = None
        self._precache_path = None
        self._prune_cache()

    # ------------------------------------------------------------ 对外入口
    def say_now(self, text: str = "") -> None:
        """手动报时（右键菜单「立即报时」/ 设置页试听共用）。

        text 留空时按当前时间组装“报时文本 + 台词/歌词”（台词每天整套换新、日内
        按序取用）；气泡用阿拉伯数字文本、语音保持中文数字口播，二者解耦。手动触发
        不占用调度槽位（与自动报时允许同分钟并存，由用户主动发起）。
        """
        self.apply_config()
        now = datetime.now()
        stripped = text.strip()
        if stripped:
            self._fire(stripped, now, stripped)
            return
        self._fire(build_chime_sentence(now, self._cfg), now, build_bubble_sentence(now, self._cfg))

    def speak(self, text: str, *, log_tag: str = "外部播报", respect_quiet: bool = True) -> bool:
        """向本服务（进程内唯一的音频通道）提交一段**纯语音**播报，不含气泡。

        供节日提醒等功能复用：合成、缓存、播放与报时**共用同一套**，因此结构上
        不可能与报时叠音。通道不空闲（合成中、正在出声，或刚起播而媒体仍在加载）
        时不打断，进深度 1 的待播队列，当前一段播完后自动播；队列已有待播时后来的
        覆盖前面的（只留最新一条，避免堆积出已过期的内容）。这条**新覆盖旧**只对
        入口方向成立：较早那条合成完成时若发现通道被接管，回队时若槽里已有更新
        请求则直接丢弃它自己（见 ``_handle_synthesized`` 的回队点），不会旧盖新。

        排队同样受免打扰契约约束，且契约**随队携带**：尊重免打扰的那条（默认）
        在入队时就过闸——免打扰期间**丢弃、不入队**（与 ``_fire`` 同语义，不留到
        免打扰结束后补播一条过期提醒）；显式豁免的那条（交互应答）把
        ``respect_quiet`` 一起存进队列，出队时用它去过 ``_fire`` 的闸——入队时没开
        免打扰、出队前才开的，出队那一拍仍会被闸丢弃（免打扰状态是现读的，不是
        入队快照）。

        气泡刻意不在这里做：节日提醒有自己的一套气泡（含 show_quote 开关与更长
        的展示时长），若再走报时的 ``_bubble`` 会被 ``voice_chime_show_bubble``
        二次影响，也会出现"两个气泡"。

        ``respect_quiet`` 默认 True = 免打扰（含全屏等效）期间丢弃这段语音（判据
        见 ``_quiet_muted``）。提醒类播报（节日提醒）用默认值；**交互应答**（点击
        自言自语）显式传 False——它回应用户刚做的动作，与 issue #208 里"点击台词
        照常冒"同口径。默认取 True 是刻意的：新调用方漏传时是"免打扰期间不出声"，
        而不是"免打扰期间突然响一声"。

        返回 False 仅表示文本为空；edge-tts 不可用时只记日志、不出声（调用方的
        气泡照常）。
        """
        stripped = (text or "").strip()
        if not stripped:
            return False
        # 唯一占用判据（通道真空闲 = 无合成在飞 ∧ 播放器未接管）：不空闲就排队。
        if not self._channel_idle():
            if respect_quiet and self._quiet_muted():
                # 入队即丢弃（与 _fire 的「丢弃不补播」同语义）：排队的节日语音在
                # 免打扰结束后出队补播，等于把一条过期提醒翻出来。
                logger.debug("免打扰期间静音，丢弃本次排队语音：%s", stripped[:20])
                return True
            self._pending_speech = stripped
            self._pending_respect_quiet = respect_quiet
            logger.info("%s：音频通道忙，已排队待播", log_tag)
            return True
        self._fire(stripped, datetime.now(), None, show_bubble=False, role="speak",
                   respect_quiet=respect_quiet)
        return True

    @property
    def channel_idle(self) -> bool:
        """通道此刻是否真空闲（无合成在飞、播放器未接管）。

        对外只读面：抽签要在「抽签中...」**念完之后**再静一段才念签文，判据必须是
        "声音停了"，不能从点击起算固定毫秒（音色/语速一变，间隔就没了）。
        """
        return self._channel_idle()

    def precache(self, text: str, *, log_tag: str = "预合成") -> bool:
        """把一段文本**提前合成进缓存、不出声**。

        给"点下去就要立刻出声"的功能用（每日一签）：先在后台把今天那段签文合出来，
        用户点的时候就是缓存命中，零网络延迟。语气与报时的预合成一致——免打扰期间
        不跑（合了也不该出声，白跑一趟网络）。

        **返回值语义（调用方靠它决定要不要重试）**：

        * ``True`` ＝ 返回时它**已可用或已在合成中**（缓存命中，或这次刚提交）；
        * ``False`` ＝ 这次没办成（免打扰 / 通道忙 / 合成后端不可用）→ **稍后重试**。
          真事故：这里曾经"放弃也算成功"，于是每日一签的两段只成了第一段
          （起手合成了、签文被丢），当天再也没补——返回值必须说实话。

        **不播放**靠两处保证：这里只给 :data:`AUX_PRECACHE_ROLE`（旁路预合成，**不碰**
        报时那一组 ``_precache_*`` 占位字段），且 ``_fire`` 的缓存命中分支与
        ``_handle_synthesized`` 的播放出口都对预合成角色直接返回。
        """
        stripped = (text or "").strip()
        if not stripped:
            return False
        if self._quiet_muted():
            return False
        # 可用后端＝provider 自己跑过 ``preflight()`` 的尝试序列（音色下架会在那里换成
        # 默认音色并把说明弹一次）；空元组就是「一个后端都用不了」，含没装 edge-tts。
        attempts = self._usable_attempts()
        if not attempts:
            logger.debug("%s：没有可用合成后端，跳过本次预合成", log_tag)
            return False
        # 缓存路径必须按 provider 的合成计划取（扩展名可能是 .mp3 也可能是 .wav），
        # 键里的 provider flavor 也由 ``_cache_path`` 统一带上：自己拼平铺 dict 会
        # 落到另一个键上，预览永远命中不了这里合出来的文件。
        paths = [self._cache_path(stripped, attempt) for attempt in attempts]
        if any(_cache_hit(path) for path in paths):
            return True
        if self._busy:
            # 通道忙（多半是上一段预合成在飞）：这次放弃，**由调用方稍后重试**。
            # 曾经这里是"放弃也算成功"，导致两段预合成只成了第一段（真事故）。
            logger.debug("%s：通道忙，跳过本次预合成", log_tag)
            return False
        self._fire(stripped, datetime.now(), None, show_bubble=False,
                   role=AUX_PRECACHE_ROLE, respect_quiet=True)
        return True

    def play_file(self, path: str, *, log_tag: str = "本地语音") -> bool:
        """直接播放一个**本地音频文件**（预缓存台词走这条路：零延迟、不联网）。

        与 ``speak()`` 共用同一个播放器（进程内唯一音频通道），所以同样不会与
        报时叠音。区别是刻意**不排队**：预缓存的是点击台词，点击那一刻已经过去
        了，晚几秒才冒出来反而突兀——通道忙就直接放弃这次出声（返回 False），
        由调用方决定是否回退到在线合成。

        **不走免打扰静音闸**：本入口只有点击自言自语在用（交互应答，同
        ``speak(respect_quiet=False)``），见 :meth:`speak` 的说明。
        """
        target = Path(path)
        if not target.is_file():
            return False
        # 唯一占用判据（同 speak 的入口门）：刚起播也算被占，否则会顶掉那一段。
        if not self._channel_idle():
            logger.info("%s：音频通道忙，本次本地播报跳过：%s", log_tag, target.name)
            return False
        # 这一段的免打扰口径是**豁免**：免打扰在它播放期间才生效时也不掐断（它就是
        # 交互应答，同 speak(respect_quiet=False)），见 _on_quiet_state_changed。
        self._current_respect_quiet = False
        if not self._play_only(str(target)):
            # 起播失败（播放器建不起来 / 设备异常）：这一段根本没在播，口径必须复位——
            # 留着 False 会让下一条判定读到上一段的残留状态（实际无害，没有在播段时
            # 免打扰切断是 no-op，但状态不干净）。_play_only 的契约不动，复位在这里做。
            self._current_respect_quiet = None
            return False
        return True

    # ------------------------------------------------------------ 调度
    def _on_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        # 兜底自愈：合成线程若异常未回调，_busy 会永久置位（后续语音只能不断入队、
        # 通道再也不出声）。tick 是 20s 周期的常驻入口，超时即复位。
        # **必须在 enabled 判定之前**：报时开关关掉、只剩节日提醒 / 点击自言时
        # 本服务照样在跑同一个通道（AppShell 的懒创建门控），那时 watchdog 若被
        # enabled 短路掉，唯一的自愈入口就永远执行不到，通道永不恢复。
        self._release_stale_busy()
        if not self._cfg.get("enabled"):
            return
        slot = chime_slot(now, self._cfg)
        if slot:
            if slot == self._last_slot:
                return
            # 让位检查必须在盖戳之前，且让位同样要消费该槽位——否则本分钟后续
            # tick（20s 一次）会反复询问，并在让位条件变化时补报，产生意外发声。
            # 用 getattr 取钩子：部分构造的服务对象（测试替身走 object.__new__）
            # 没有该属性，不该因此让 tick 抛异常。
            yield_slot = getattr(self, "yield_slot", None)
            if callable(yield_slot) and yield_slot(slot):
                self._last_slot = slot
                logger.info("报时让位给节日提醒：%s", slot)
                return
            self._last_slot = slot
            precache_bubble = self._precache_bubble  # 消费前取用（consume 会清空预合成状态）
            sentence = self._consume_precache(slot)
            bubble = precache_bubble if sentence is not None else None
            self._precache_bubble = None
            if sentence is None:
                sentence = build_chime_sentence(now, self._cfg)
                bubble = build_bubble_sentence(now, self._cfg)
            self._fire(sentence, now, bubble)
            return
        # 未命中：尝试为下一报时点预合成（距下一报时点 ≤ 窗口秒数）。
        self._maybe_precache(now)

    def _maybe_precache(self, now: datetime) -> None:
        """距下一报时点 ≤ 60s 时，提前在后台合成该次报时音频并写缓存。"""
        # 过期清理：预合成目标槽位已过（如配置变更/服务长时间暂停后恢复）。
        if self._precache_slot is not None:
            slot_time = self._precache_slot[:16]  # YYYY-MM-DDTHH:MM
            if now.strftime("%Y-%m-%dT%H:%M") > slot_time:
                self._precache_slot = None
                self._precache_text = None
                self._precache_bubble = None
                self._precache_path = None
        # 免打扰期间不预合成：这次报时到点会被静音闸丢掉，合成等于白跑一趟
        # 网络请求（退出免打扰后又回到即时合成的既有回退路径）。
        if self._quiet_muted():
            return
        next_secs = next_chime_in_seconds(now, self._cfg)
        if next_secs > self.PRECACHE_WINDOW_S:
            return
        next_at = now + timedelta(seconds=next_secs)
        next_slot = chime_slot(next_at, self._cfg)
        if not next_slot or next_slot == self._last_slot or next_slot == self._precache_slot:
            return
        if self._busy:
            return  # 合成中，下个 tick（20s 内）再试，窗口 60s 足够
        attempts = self._usable_attempts()
        if not attempts:
            return
        sentence = build_chime_sentence(next_at, self._cfg)
        bubble = build_bubble_sentence(next_at, self._cfg)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        paths = [self._cache_path(sentence, attempt) for attempt in attempts]
        # 先占位：即便本次合成未在报时点前完成，到点也会回退即时合成。
        self._precache_slot = next_slot
        self._precache_text = sentence
        self._precache_bubble = bubble
        self._precache_path = next((str(p) for p in paths if _cache_hit(p)), None)
        if self._precache_path:
            logger.info("预合成命中已有缓存：%s", Path(self._precache_path).name)
            return
        self._busy = True
        self._busy_since = time.monotonic()
        self._synthesis_role = PRECACHE_ROLE
        self._reararm_watchdog()
        generation = self._next_synthesis_generation()
        # 尝试序列里的音色/参数一律取 ``_usable_attempts()`` 的**结果**（provider 的
        # ``preflight`` 已把下架音色换成可用的）：缓存 key 与即时合成（_fire）用的
        # 都是替换后的值，这里传配置原值会让预合成的产物落到另一个 key 上——音色
        # 下架时预合成会白试两轮（配置原值在在线表里不存在），还正好踩进 45s 超时
        # 自愈的竞态，到点只好回退即时合成。
        worker = _TTSWorker(sentence, attempts, paths, self._worker_callback(generation))
        logger.info("预合成下一报时音频（%ds 后）：%s", next_secs, paths[0].name)
        worker.start()

    def _consume_precache(self, slot: str) -> str | None:
        """到点取用预合成文本；未完成/文件丢失时清空并回退即时合成。

        无论命中与否都清空整组预合成状态（含气泡文本）：气泡文本由调用方
        ``_on_tick`` 在消费前取用，避免残留串到下一次报时。
        """
        if self._precache_slot == slot and self._precache_path:
            path = Path(self._precache_path)
            if path.is_file():
                text = self._precache_text
                self._precache_slot = None
                self._precache_text = None
                self._precache_bubble = None
                self._precache_path = None
                logger.info("报时命中预合成缓存：%s", path.name)
                return text
        self._precache_slot = None
        self._precache_text = None
        self._precache_bubble = None
        self._precache_path = None
        return None

    # ------------------------------------------------------------ 合成后端
    def _secret_values(self) -> dict[str, dict[str, str]]:
        """按 provider 声明的 secret 字段读系统钥匙串（纯逻辑层不碰凭据）。

        返回 ``{provider_id: {字段名: 值}}``，注入 ``synth_attempts``；provider 若要
        新增凭据，只需在 ``fields`` 里加一个 ``kind="secret"`` 字段，这里自动跟上。
        """
        secrets_map: dict[str, dict[str, str]] = {}
        for provider in tts.providers():
            specs = tts.secret_fields(provider)
            if specs:
                secrets_map[provider.id] = {
                    spec.name: tts_secrets.get(spec.secret_ref) for spec in specs
                }
        return secrets_map

    def _usable_attempts(self) -> tuple[tts.TtsAttempt, ...]:
        """可用的合成尝试序列：provider 自报不可用的先摘掉，不浪费一次往返。

        摘掉而不是留给 worker 去失败，是为了让「没配 Key 就自动走后备后端」不额外
        付一次 HTTP 往返；全摘光时调用方走「只有气泡」的降级提示。

        这里还会跑一遍 ``provider.preflight()``：把「配置里已不成立的东西」先换掉
        （典型：在线音色被下架 → 换默认音色），并把说明转达给用户——否则用户只会
        看到「声音没了」，不知道是音色本身不存在了。
        """
        usable: list[tts.TtsAttempt] = []
        for attempt in synth_attempts(self._cfg, secrets=self._secret_values()):
            provider = tts.get(attempt.provider)
            if provider is None:
                continue
            values, note = provider.preflight(attempt.values)
            if note:
                attempt.values = values
                attempt.plan = provider.plan(values)
                self._announce_once(note)
            if provider.availability(attempt.values):
                continue
            usable.append(attempt)
        return tuple(usable)

    def _announce_once(self, note: str) -> None:
        """同一条提示每个进程只弹一次（避免每次报时都重复刷屏）。"""
        if note in _ANNOUNCED_NOTES:
            logger.debug("已提示过：%s", note)
            return
        _ANNOUNCED_NOTES.add(note)
        logger.warning(note)
        self._bubble(note)

    def _cache_path(self, text: str, attempt: tts.TtsAttempt) -> Path:
        """该后端该文本的缓存路径（扩展名由 provider 的合成计划给出）。

        键取**这个 attempt 自己**的 provider + values（``_usable_attempts`` 已跑过
        ``preflight``）：即时合成、预合成、缓存查找三处传的都是同一份 resolved
        attempt，音色被自动回退后产物仍按实际合成输入命名，不会挂在配置原音色名下。
        """
        key = cache_key(text, provider_id=attempt.provider, values=attempt.values)
        return self._cache_dir / f"{key}.{attempt.ext}"

    def _fire(self, sentence: str, now: datetime, bubble_text: str | None = None,
              *, show_bubble: bool = True, role: str = "play",
              respect_quiet: bool = True) -> None:
        """播报一次。

        语音用口播文本 ``sentence``（同时作为合成输入与缓存键）；气泡用
        ``bubble_text``（阿拉伯数字为主，缺省按 ``now`` 现算），二者解耦。

        ``show_bubble=False`` / ``role="speak"`` 是给外部播报（节日提醒）用的：
        只出声、不出气泡（气泡由调用方自己管），合成完成回调据此走 ``_play_only``。

        ``respect_quiet`` 见 :meth:`speak`：免打扰（含全屏等效）期间这里直接丢弃
        本次播报——**在合成之前返回**，连缓存都不查（用户听不到的声音没必要合成）。
        气泡照旧走 ``_bubble``（它自己会进暂存队列），语义与改动前逐字相同；出声的
        各条路径（缓存命中 / 即时合成 / 排队补播）都要经过本方法，所以这道闸只有
        一处。合成在飞时被静音的那一半在 ``_handle_synthesized``。``speak()`` 的
        排队入口另有一道**同语义**的闸（免打扰期间不入队，免得免打扰结束后补播一条
        过期提醒），出队时仍走本方法再判一次——免打扰状态是现读的，不是入队快照。
        """
        attempts = self._usable_attempts()
        if not attempts:
            self._notify_missing_tts()
            return
        bubble = bubble_text if bubble_text is not None else build_bubble_sentence(now, self._cfg)
        if respect_quiet and self._quiet_muted():
            # 丢弃而不是补播：报时/节日语音是时间敏感的（14:00 的报时 15:00 再响没有
            # 意义），与气泡「攒着退出后统一汇报」的语义刻意不同。
            logger.debug("免打扰期间静音，丢弃本次语音（气泡照旧）：%s", sentence[:20])
            if show_bubble:
                self._bubble(bubble)
            return
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        paths = [self._cache_path(sentence, attempt) for attempt in attempts]
        # 缓存命中（含预合成已完成）直接播放，零网络延迟；合成中不阻塞缓存播放。
        # 命中要求文件非空：失败的合成会留下 0 字节文件，只判 exists() 会把它当成
        # 「已有缓存」→ 播出一声静音且再也不重新合成。
        for out_path in paths:
            if not _cache_hit(out_path):
                continue
            if role in PRECACHE_ROLES:
                # 预合成（报时 / 旁路）撞上已有缓存：什么都不做。**不能往下走播放
                # 分支**——那会让"我只是想提前合好"变成"偷偷播了一次"。
                return
            # 起播前记下这一段的免打扰口径：免打扰在它播放**期间**才生效时按它决定
            # 切不切（见 _on_quiet_state_changed）。放在这里而不是播放方法里，是为了
            # 写入口径的地方与决定"播什么"的地方同一处。
            self._current_respect_quiet = respect_quiet
            if show_bubble:
                self._play_and_bubble(str(out_path), sentence, bubble)
            else:
                self._play_only(str(out_path))
            return
        if self._busy:
            # 合成在飞（多为本次报时的预合成）：排队待补，不得直接丢弃——
            # 到点回退即时合成正经此路径（_on_tick 在预合成未完成时调 _fire，
            # 被 busy 门挡死曾导致到点静默丢报时且 _last_slot 已盖戳不再重试）。
            # 这里**只判合成在飞、不判播放器**（判据刻意不是 _channel_idle）：本方法
            # 是"到点即报 / 立即报时"的入口，缓存命中在上面已直接起播——在下面加
            # 播放器判据会让同一方法里两条路径自相矛盾，还会把时间敏感的报时推迟到
            # 当前音频播完。通道占用的收敛点在两个出队口，不在这里。
            self._queued_fire = (sentence, now, bubble_text, show_bubble, role, respect_quiet)
            logger.info("语音报时合成在飞，排队待补：%s", sentence[:20])
            return
        self._busy = True
        self._busy_since = time.monotonic()
        self._synthesis_role = role
        self._synthesis_respect_quiet = respect_quiet
        self._synthesis_now = now
        self._pending_bubble = bubble if show_bubble else None
        # 本次合成有效：清掉上一次 stop() 留下的作废标记，
        # 否则新起的合成结果会被当成"迟到结果"丢弃。
        self._stopped = False
        self._reararm_watchdog()
        generation = self._next_synthesis_generation()
        worker = _TTSWorker(sentence, attempts, paths, self._worker_callback(generation))
        worker.start()

    def _reararm_watchdog(self) -> None:
        """按**最近的一个未结期限**布看门狗表；两个期限都没有就撤表。

        这条表是手动通道（从不 start()）唯一的自愈入口，而要兜的挂钟有两个：合成卡死
        （``_busy_since + _UNLOCK_INTERVAL_S``）与播放接管卡死
        （``_play_pending_since + _PLAY_PENDING_MAX_S``）。两者可能同时存在（预合成在飞
        时上一段还在播），共用一条表就必须取最早的那个——先到的那个到点后由
        ``_release_stale_busy`` 收掉，另一个会在那一拍之后重新布（见那里的重布分支）。
        """
        now = time.monotonic()
        deadlines: list[float] = []
        if getattr(self, "_busy", False):
            deadlines.append(getattr(self, "_busy_since", now) + _UNLOCK_INTERVAL_S)
        if getattr(self, "_play_pending_end", False):
            since = getattr(self, "_play_pending_since", None)
            if since is not None:
                deadlines.append(since + _PLAY_PENDING_MAX_S)
        if deadlines:
            self._arm_unlock_timer(min(deadlines) - now)
        else:
            # 两个挂钟都结了：**这才是真正的撤表**（单次表到点也会自然停表，这里显式
            # 撤一下，让"由谁布、由谁撤"成对，也不留一条只会空响的表）。
            timer = getattr(self, "_unlock_timer", None)
            if timer is not None:
                timer.stop()

    def _arm_unlock_timer(self, remaining_s: float) -> None:
        """布一道**只为自愈**的超时（见 ``_unlock_timer`` 与 ``_reararm_watchdog``）。

        单次表，重复调用即从此刻重新计时。``remaining_s`` 是距最近期限的剩余秒数；
        每次布表都显式设间隔，否则会沿用上一次重布留下的短间隔（表越响越早）。
        """
        timer = getattr(self, "_unlock_timer", None)
        if timer is None:
            return
        timer.setInterval(_remaining_unlock_ms(remaining_s))
        timer.start()

    def _disarm_unlock_timer(self) -> None:
        """合成锁有结局之后的挂钟**结算**：还有未结挂钟就按剩余期限重布，否则才真撤表。

        这里刻意**不是**裸 ``timer.stop()``：两个挂钟共用一条单次表，而"合成有结局"
        只说明**合成那一支**结了——播放接管那一支可能还挂着。可达序列：手动通道起
        合成 A → A 在飞期间另一请求命中缓存起播 B（``_fire`` 缓存旁路不看合成锁）→
        B 卡在没有终止信号的接管态 → A 完成 / 失败 / 超时。裸撤会让 B 的
        ``_PLAY_PENDING_MAX_S`` 兜底永远不到点（手动通道没有 20s tick，drain 在通道
        不空闲时又直接 return），通道永久判忙。

        实现只有一条（``_reararm_watchdog`` 按当前状态重算表），三个结局
        （完成 / 失败 / 合成超时）都调本方法，不在各点各写一份。
        """
        self._reararm_watchdog()

    def _worker_callback(self, generation: int):
        """取本次合成的 worker 回调（顺带保证信号桥已建好并接到本服务）。"""
        if self._bridge is None:
            self._bridge = _AudioBridge()
            self._bridge.signals.synthesized.connect(self._on_synthesized)
        return self._bridge.callback_for(generation)

    def _next_synthesis_generation(self) -> int:
        """递增合成代次并返回：起合成与 watchdog 释放各调用一次。

        代次变了 = 此前那次合成已作废（结果不再属于当前任务），它的迟到回调会被
        :meth:`_on_synthesized` 整条丢弃。
        """
        # getattr 守卫：object.__new__ 构造的测试替身没有 __init__ 属性
        generation = getattr(self, "_synthesis_generation", 0) + 1
        self._synthesis_generation = generation
        return generation

    # ------------------------------------------------------------ 播放
    def _on_synthesized(self, generation: int, path: str, text: str, error: str) -> None:
        """合成完成回调（GUI 线程）。首参是**动身时的代次**，用来认领本次任务。

        代次不一致 = 这次合成的结果已作废（超时自愈释放过、当前在跑的是别的一路）：
        整条丢弃——一个字段都不碰。否则旧任务会取走**新任务**的 role /
        respect_quiet 与 busy 锁：该被静音的旧提醒出声、应豁免的新语音被静音。
        """
        if generation != getattr(self, "_synthesis_generation", None):
            logger.debug("丢弃已作废旧合成的迟到结果（代次 %s，当前 %s）：%s",
                         generation, getattr(self, "_synthesis_generation", None),
                         Path(path).name)
            return
        role = self._synthesis_role
        self._synthesis_role = "play"
        respect_quiet = self._synthesis_respect_quiet
        self._synthesis_respect_quiet = True
        self._busy = False
        # 本次合成已有结局：**结算**看门狗表（不是裸撤）——播放接管那一支可能还挂着
        # （手动通道没有 tick，撤了就再也没人续命），由 _disarm_unlock_timer 按剩余
        # 期限重算。若 drain 又起了新合成，_fire 会重新布一道。
        self._disarm_unlock_timer()
        try:
            self._handle_synthesized(role, path, text, error, respect_quiet)
        finally:
            # 任何结局（完成/失败/已停止）都必须结算排队项：_stopped 时
            # _drain 内部丢弃，其余情况补播——不能让到点报时静默消失。
            # 两处判据都在 drain 内部（_channel_idle），调用点不再各自判：
            # 起了播的完成 → 通道被接管 → 两个队列都原样留队（EndOfMedia 再结算）；
            # 没起播的完成（预合成 / 免打扰丢弃 / 合成失败 / 已停止）→ 通道空出却
            # 没有 EndOfMedia → 就在此结清。
            # 顺序是「先 _drain_queued_fire」：待补播的报时若在此起了合成，待播语音
            # 会被留队（豁免随队携带），等下一拍——报时优先，且条目不丢。
            self._drain_queued_fire()
            self._drain_pending_speech()

    def _drain_queued_fire(self) -> None:
        """补播被 busy 门拦下的排队播报（若有）；通道不空闲则留队，stop() 后丢弃。

        出队口之一（另两处：:meth:`_on_media_status` 的终止态、:meth:`_on_synthesized`
        的 finally 收口），三处共用同一条判据 :meth:`_channel_idle`。
        """
        # 通道不空闲就**原样留队**（字段一律不动），等下一拍：合成完成 / EndOfMedia /
        # 合成超时自愈。——刚起播那一刻也算不空闲，否则会顶掉刚起播的那一段。
        if not self._channel_idle():
            return
        # getattr 守卫：object.__new__ 构造的测试替身没有 __init__ 属性
        queued = getattr(self, "_queued_fire", None)
        self._queued_fire = None
        if queued is None or self._stopped:
            return
        sentence, now, bubble_text, show_bubble, role, respect_quiet = queued
        self._fire(sentence, now, bubble_text, show_bubble=show_bubble, role=role,
                   respect_quiet=respect_quiet)

    def _handle_synthesized(self, role: str, path: str, text: str, error: str,
                            respect_quiet: bool = True) -> None:
        if self._stopped:
            # stop()（关闭开关 / 应用退出）之后才回来的结果：不再回放/气泡——
            # 否则「关掉语音报时之后又响一声」，退出路径上还可能触碰正在析构的窗口。
            logger.info("语音报时已停止，丢弃迟到的合成结果：%s", Path(path).name)
            return
        if error:
            if error == EDGE_TTS_MISSING:
                # 运行期才发现 edge-tts 缺失（顶层只做 find_spec 探测）：清掉可能
                # 残留的预合成占位，再走既有缺依赖降级文案。
                if role == PRECACHE_ROLE:
                    self._precache_slot = None
                    self._precache_text = None
                    self._precache_bubble = None
                    self._precache_path = None
                elif role == AUX_PRECACHE_ROLE:
                    # 旁路预合成没有占位要回滚——**尤其不许碰**报时那一组字段
                    # （那一段可能已经合好了，清掉就是替它白丢一次缓存）。
                    logger.debug("旁路预合成遇缺依赖：%s", Path(path).name)
                self._notify_missing_tts()
                return
            if role == PRECACHE_ROLE:
                # 预合成失败：丢弃占位，到点走即时合成回退。
                self._precache_slot = None
                self._precache_text = None
                self._precache_bubble = None
                self._precache_path = None
                logger.warning("预合成失败，到点将即时合成：%s", error)
                return
            if role == AUX_PRECACHE_ROLE:
                # 旁路预合成失败：它不留占位（调用方按返回值自己重试），
                # **更不许替报时清占位**——那会把报时已合好的那一段一起清掉。
                logger.warning("旁路预合成失败，调用方将重试：%s", error)
                return
            logger.warning("语音报时合成失败：%s", error)
            self._bubble(f"语音合成失败（{error[:40]}…）")
            return
        if role == PRECACHE_ROLE:
            # 预合成完成：仅记录缓存路径，不播放（到点由 _fire 直接播缓存）。
            self._precache_path = str(path)
            logger.info("预合成完成：%s", Path(path).name)
            return
        if role == AUX_PRECACHE_ROLE:
            # 旁路预合成完成：缓存文件已经在按自己文本算出的 key 上，**没有占位要记**
            # ——记进 ``_precache_path`` 会把它挂到报时那个槽位上（覆盖报时的完成结果）。
            # 这里的 return 同时挡住下面的播放出口：写日志时"提前合好"绝不出声。
            logger.info("旁路预合成完成：%s", Path(path).name)
            return
        if respect_quiet and self._quiet_muted():
            # 合成期间才进入免打扰（合成要 3-8s，全屏/手动免打扰都可能在中间发生）：
            # 结果既不出声也不回放，气泡仍按既有语义走（_bubble 自己会进暂存队列）。
            logger.debug("免打扰期间静音，丢弃合成结果（气泡照旧）：%s", Path(path).name)
            if role != "speak":
                self._bubble(self._pending_bubble if self._pending_bubble is not None else text)
            # 这次完成不起播 = 通道空出，待播 speech 由 _on_synthesized 的 finally
            # 统一出队（本次是其中之一，见那里）。此处不得再调一次：双出队路径。
            return
        # 播放那一刻重判通道占用：合成期间别的音频可以合法地起播——报时经缓存
        # 例外（_fire 的缓存命中路径不看占用），合成前判过的空闲早已不作数。此刻
        # setSource+play 会把在播的那一段顶掉（它的音源被替换、永远等不到
        # EndOfMedia）。改为回队，等下一拍按既有不变量（_channel_idle）出队。
        if role == "speak":
            # 外部播报（节日提醒）：只出声，气泡由调用方自己展示。
            if self._player_engaged():
                # 回队不盖新：槽里已有更新的请求（本次合成的结果比它旧）就丢弃本次，
                # 槽内那条及其旗原样不动——排队契约的入口方向是"新覆盖旧"，回队方向
                # 倒灌成"旧盖新"会让更新的请求永久丢失。
                if self._pending_speech is not None:
                    logger.debug("播放时刻通道已被占用，槽里已有更新的待播语音，"
                                 "丢弃这次旧结果：%s", text[:20])
                    return
                logger.debug("播放时刻通道已被占用，外部播报回队待播：%s", text[:20])
                self._pending_speech = text
                self._pending_respect_quiet = respect_quiet
                return
            self._current_respect_quiet = respect_quiet
            self._play_only(path)
            return
        if self._player_engaged():
            # 回队不盖新（同 speak 出口）：_queued_fire 里已有更新的报时排队项时，
            # 丢弃这次较旧的完成结果，原 tuple 原样保留。
            if self._queued_fire is not None:
                logger.debug("播放时刻通道已被占用，槽里已有更新的报时排队项，"
                             "丢弃这次旧结果：%s", text[:20])
                return
            logger.debug("播放时刻通道已被占用，报时回队待播：%s", text[:20])
            # 原 tuple 各字段原样带回（now 见 _synthesis_now 的说明）。
            self._queued_fire = (text, self._synthesis_now, self._pending_bubble,
                                self._pending_bubble is not None, role, respect_quiet)
            return
        self._current_respect_quiet = respect_quiet
        self._play_and_bubble(path, text, self._pending_bubble)

    def _play_and_bubble(self, path: str, text: str, bubble_text: str | None = None) -> None:
        """播放音频并展示气泡。

        ``text`` 为语音口播文本（中文数字）；气泡优先用 ``bubble_text``
        （阿拉伯数字文本），未传时回退 ``text`（兼容旧调用与测试）。

        **本方法不判通道占用**：它是"把这一段交给播放器"的执行末端，占用判定归
        调用方（``_handle_synthesized`` 的播放出口、``_fire`` 的缓存命中路径）。
        在这里加判据会变成静默不播，而回队的语义只能由调用方给。
        """
        if not self._ensure_player():
            self._bubble(f"播放器不可用，音频已缓存：{Path(path).name}")
            return
        try:
            from PySide6.QtCore import QUrl

            self._audio_out.setVolume(self._cfg["volume"] / 100.0)
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            # play 出口即置位"已发出 play、未见终止态"（见 _player_engaged）：
            # 从这一刻到终止态之间，枚举判据在 LoadedMedia 窗口里会漏判。
            self._mark_play_pending()
        except Exception:
            logger.exception("语音报时播放失败：%s", path)
            self._bubble("语音播放失败（设备/解码器异常）")
            return
        self._bubble(bubble_text if bubble_text is not None else text)

    def _play_only(self, path: str) -> bool:
        """只播放音频、不展示气泡（外部播报用，气泡由调用方自己管）。

        返回是否成功起播；失败只记日志——外部的气泡/提示由调用方决定，
        这里再插一条报时风格的气泡会与对方的展示重复。

        **本方法不判通道占用**（同 :meth:`_play_and_bubble`）：占用判定与"回队"
        的语义都在调用方（``_handle_synthesized`` 的播放出口）。
        """
        if not self._ensure_player():
            logger.warning("播放器不可用，外部播报跳过播放：%s", Path(path).name)
            return False
        try:
            from PySide6.QtCore import QUrl

            self._audio_out.setVolume(self._cfg["volume"] / 100.0)
            self._player.setSource(QUrl.fromLocalFile(path))
            self._player.play()
            self._mark_play_pending()
            return True
        except Exception:
            logger.exception("外部播报播放失败：%s", path)
            return False

    def _mark_play_pending(self) -> None:
        """置位"已发出 play、未见终止态"（+ 记时刻 + 按期限布看门狗表）。

        两个播放出口（``_play_and_bubble`` / ``_play_only``）共用的唯一置位点：时刻与
        标志必须一起写，否则时限兜底拿不到起点（见 ``_PLAY_PENDING_MAX_S``）。置位即
        布表：这一段若是"既不给终止态也不进 PlayingState"的死局，只有看门狗会来收。
        """
        self._play_pending_end = True
        self._play_pending_since = time.monotonic()
        self._reararm_watchdog()

    def _clear_play_pending(self) -> None:
        """放掉"已发出 play、未见终止态"标志（终止态 / 播放错误 / 超限 / 切段共用）。

        标志与时刻必须一起清：只清标志会让下一次置位沿用旧起点（上限提前到点）。
        """
        self._play_pending_end = False
        self._play_pending_since = None
        self._reararm_watchdog()

    def _player_engaged(self) -> bool:
        """播放器是否已**接管**音频通道（正在出声，或刚起播、媒体仍在加载）。

        三个子判据，缺任何一个都会漏判：

        1. ``playbackState == PlayingState``——正在出声。
        2. ``mediaStatus == LoadingMedia``——``playbackState`` 判不了"刚起播"。实机
           实测（QMediaPlayer + QAudioOutput，offscreen，``assets/sounds/click.wav``）：
           ``setSource`` 后立刻 ``play()`` 那一刻 ``playbackState`` 仍是 ``StoppedState``、
           ``mediaStatus`` 为 ``LoadingMedia``，约 240ms 后才转 ``PlayingState``。
        3. ``_play_pending_end``（"本服务已发出 play、还没见到终止态"）——实测同一探针
           在 ``play()`` 之后约 **31ms** 由 ``LoadingMedia`` 转 **``LoadedMedia``**，而
           ``playbackState`` 仍是 ``StoppedState``，直到约 **237ms** 才转 ``PlayingState``：
           这约 **200ms** 的窗口里前两个判据**都不成立**，通道会被判成空闲，新语音当场
           ``setSource`` 顶掉刚起播的那一段（它的音源被替换，永远等不到 EndOfMedia）。

        为什么不干脆把 ``LoadedMedia`` 也算接管：同一探针实测，一段播完后 ``mediaStatus``
        **停留在 ``LoadedMedia``**（EndOfMedia → LoadedMedia），``player.stop()`` 之后
        也停在 ``LoadedMedia``——把 ``LoadedMedia`` 算作接管会让通道在每段播完后永久
        判忙（"这个组合不可达"的前提不成立，故不用那条更简的改法）。自记标志只在
        ``play()`` 出口置位、由**四类**结局复位：终止态、播放错误（``errorOccurred``）、
        ``stop()`` / 免打扰切断、以及 ``_PLAY_PENDING_MAX_S`` 时限兜底。

        **这个标志有一个滞留窗口，如实登记**：它是服务侧的自记状态、没有 Qt 事件保证
        一定能结（设备被拔 / 音频输出异常时，``play()`` 之后可能既不给终止态也不进
        ``PlayingState``），卡住就是通道**永久判忙**——从"偶发误判空闲"退化成"再也
        不出声"（``speak`` 永远排队、``play_file`` 永远让路、合成完成永远回队出不来）。
        三道兜底：① ``errorOccurred`` 接到 :meth:`_on_player_error`，按终止态同一套
        结算；② 同一条看门狗表在 ``_play_pending_since + _PLAY_PENDING_MAX_S`` 到点
        （见 :meth:`_release_stale_play_pending`）清标志并结算队列，覆盖"连错误信号都
        没有"的那种静默滞留；③ 同一拍还把播放器治到 ``NoMedia``——``stop()`` +
        ``setSource(QUrl())``（只清标志治不彻底，本方法的第二个子判据仍为真；而单单
        ``stop()`` 也治不掉 LoadingMedia，理由见 :meth:`_unload_player_source`）。
        真在 PlayingState 的段则照旧在播、上限不碰它（只顺延期限起点）。

        拿不到枚举判据（QtMultimedia 缺失 / ``object.__new__`` 测试替身没有这些方法）
        时回落到自记标志：出队总比滞留安全（滞留就是本模块要修的那类静默丢语音）。
        """
        if self._player is None:
            return False
        if getattr(self, "_play_pending_end", False):
            return True
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            return (
                self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                or self._player.mediaStatus() == QMediaPlayer.MediaStatus.LoadingMedia
            )
        except Exception:
            return False

    def _player_playing(self) -> bool:
        """播放器是否**真的在出声**（只认 ``playbackState == PlayingState`` 一个判据）。

        与 :meth:`_player_engaged` 刻意不同：这里不认 ``LoadingMedia``、也不认自记标志
        ——上限兜底只允许掐"卡住"的那一段，真在 PlayingState 的音频是合法播放，绝不能
        停（停了用户听到的就是"报时被截断"）。

        拿不到枚举（QtMultimedia 缺失 / ``object.__new__`` 替身没有这些方法）时返回
        False：那种对象没有枚举可判，按"没在播"处理才能让治愈路径把播放器拽出可能的
        滞留态（真播放器一定实现了这个方法）。
        """
        if self._player is None:
            return False
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        except Exception:
            return False

    def _channel_idle(self) -> bool:
        """音频通道是否**真空闲**：无合成在飞 且 播放器未接管。

        全模块唯一的占用判据：两个补播队列的出队口（不空闲就原样留队）与 speak /
        play_file 的入口门（不空闲就排队 / 让路）都读它。``_fire`` 是唯一例外且是
        刻意的（到点即报的入口，见那里的说明）。
        """
        return not self._busy and not self._player_engaged()

    def _on_media_status(self, status) -> None:
        """媒体状态变化：终止态（播完/坏媒体）即"通道空出"，排空两个补播队列。

        终止态是"这一段音频真的结束了"的唯一信号：此刻两个队列里被占用判据留过队
        的条目都必须结算（报时优先，见 :meth:`_on_synthesized` 的同序说明）——否则
        留队的报时排队项要等下一次合成完成才有下一拍，最坏等到下个报时点。

        **刻意不在本方法里 import QtMultimedia**：终止态枚举在 ``_ensure_player``
        成功创建播放器时缓存到 ``self._terminal_statuses``。这样本方法没有 Qt
        导入（Linux 上 QtMultimedia 会拖 libpulse 等系统库，缺失即 ImportError），
        也让单元测试无需为了拿枚举值去导入 QtMultimedia——CI 的 ubuntu 作业正是
        因为测试里那次导入而红过。
        """
        if status not in getattr(self, "_terminal_statuses", ()):
            return
        # 这一段真的结束了：复位"已发出 play、未见终止态"与"当前在播段"两个字段。
        # 不复位标志就会被永久判忙（终止态之后 mediaStatus 停在 LoadedMedia，枚举
        # 判据不成立，只剩这个标志撑着）。
        self._clear_play_pending()
        self._current_respect_quiet = None
        self._drain_queued_fire()
        self._drain_pending_speech()

    def _on_player_error(self, error, error_string: str = "") -> None:
        """播放器报错即"通道空出"：按终止态**同一套**结算，不开第二套语义。

        音频输出被拔 / 设备异常时 ``play()`` 之后可能既没有 EndOfMedia 也不进
        ``PlayingState``——``_play_pending_end`` 会永久卡 True，通道永久判忙
        （``speak`` 永远排队、``play_file`` 永远让路、合成完成永远回队出不来）。
        ``errorOccurred`` 是这类结局唯一的信号，必须接上（见 ``_ensure_player``）。

        即使错误属于别的段（或来得比标志还早，例如 ``setSource`` 期间就报错），这里做的
        也只是"放掉标志 + 同序 drain"——比永久判忙安全；真正的兜底仍是
        ``_PLAY_PENDING_MAX_S`` 时限（那条覆盖"连错误都不报"的静默滞留）。
        """
        logger.warning("音频播放错误，按通道空出结算：%s（%s）", error, error_string)
        self._clear_play_pending()
        self._current_respect_quiet = None
        self._drain_queued_fire()
        self._drain_pending_speech()

    def _drain_pending_speech(self) -> None:
        """待播语音出队：通道真空闲时补播那一条（若有）并消费掉队列字段。

        出队口之一，与 :meth:`_drain_queued_fire` 共用同一条判据（:meth:`_channel_idle`）
        与同三个触发点：合成完成（:meth:`_on_synthesized` 的 finally）、媒体终止
        （:meth:`_on_media_status`）、合成超时自愈（:meth:`_release_stale_busy`）。
        凡是**合成完成却没起播**的结局（预合成、免打扰丢弃、合成失败、已停止）都
        不会有 EndOfMedia，待播语音不能指望那个事件来出队，否则会无声滞留到被后续
        偶然事件覆盖（豁免的那条被 ``_fire`` 照常播，尊重免打扰的那条被 ``_fire``
        的闸现读再丢一次，``_stopped`` 在这里直接丢弃）。

        **通道不空闲就原样留队**（字段一律不动）：合成在飞时出队会被 ``_fire`` 的
        busy 门迁进 ``_queued_fire``（离开了 END 的出口），播放器被接管时补播会顶掉
        刚起播的那一段——两者都是本模块修过的坑，判据内置在这里，调用点不再各自判。
        """
        if not self._channel_idle():
            return
        # getattr 守卫：object.__new__ 构造的测试替身没有 __init__ 属性
        pending = getattr(self, "_pending_speech", None)
        self._pending_speech = None
        # 随队携带的免打扰口径一并取出、并复位为默认值（与 _pending_speech 同生命周期）。
        respect_quiet = getattr(self, "_pending_respect_quiet", True)
        self._pending_respect_quiet = True
        if not pending or self._stopped:
            return
        self._fire(pending, datetime.now(), None, show_bubble=False, role="speak",
                   respect_quiet=respect_quiet)

    def _ensure_player(self) -> bool:
        if self._player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

            self._player = QMediaPlayer()
            self._audio_out = QAudioOutput()
            self._player.setAudioOutput(self._audio_out)
            # 缓存终止态枚举，供 _on_media_status 判空时使用（见该方法的说明）。
            self._terminal_statuses = (
                QMediaPlayer.MediaStatus.EndOfMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            )
            # 播完一条后排空待播队列（外部播报排队用）。
            self._player.mediaStatusChanged.connect(self._on_media_status)
            # 播放错误（设备被拔 / 输出异常）同样按"通道空出"结算：这类结局既不给
            # 终止态也进不了 PlayingState，只靠终止态复位会让通道**永久判忙**
            # （见 _on_player_error 与 _player_engaged 的滞留窗口说明）。
            self._player.errorOccurred.connect(self._on_player_error)
            return True
        except Exception:
            logger.exception("创建 QMediaPlayer 失败（检查 QtMultimedia ffmpegmediaplugin）")
            return False

    # ------------------------------------------------------------ 免打扰切断
    def _attach_quiet_listener(self) -> None:
        """订阅进程级免打扰状态边沿（幂等；**构造即挂**，start 时再挂一次兜底）。

        用既有 ``QuietState.add_listener``，不改状态机：本服务只是它的第二个订阅方
        （第一个是 ``QuietTicker``）。

        **为什么是构造即挂而不是只在 start() 里挂**：订阅的唯一职责是"免打扰开启那一
        拍切掉正在出声的那一段"，而这条音频通道有三条起播路径——自动报时（走 start）与
        **手动通道**（「立即报时」/ 设置页试听 → ``say_now``）、点击自言的本地播报
        （``play_file``）。后两条只懒创建服务、**从不 start()**（``_fire`` 的 busy 门与
        ``play_file`` 里都没有"必须先 start"的前置），只在 start() 注册等于让它们
        在免打扰开启时一直把这一段放完。构造点是"服务存在的唯一时刻"，挂在这里则未来
        新增起播路径也不会漏；挂在起播处（``_fire`` / ``play_file`` / 两个播放出口）
        则是三四处要各自记得挂，漏一处就复发。

        ``add_listener`` 按身份去重，所以构造 + start 重复挂不会叠。**必须与 stop()
        成对**——订阅表持有的是绑定方法，挂着不放就是一条指向半死服务的悬挂回调；
        AppShell 关闭这条通道时正是 ``stop()`` + 丢引用（见 ``_sync_chime_service``），
        两个动作合起来才是完整的生命周期收口。
        """
        from .. import quiet_mode

        quiet_mode.shared_quiet_state().add_listener(self._on_quiet_state_changed)

    def _detach_quiet_listener(self) -> None:
        """摘掉免打扰订阅（幂等；随服务 stop 走）。"""
        from .. import quiet_mode

        quiet_mode.shared_quiet_state().remove_listener(self._on_quiet_state_changed)

    def _on_quiet_state_changed(self) -> None:
        """免打扰状态边沿（GUI 线程，来自 QuietState 的订阅回调）。

        只处理**进入**抑制态这一半：正在出声的那一段若尊重免打扰（报时 / 节日语音），
        立即停掉。此前的三处静音闸只拦"未起播"（_fire / _handle_synthesized / 排队
        入口），所以用户手动开免打扰或切全屏（``quiet_also_on_fullscreen`` 开）时，
        已经起播的那一段会**一直放到完**——用户的开关不能等到这段放完。

        ``respect_quiet=False`` 的交互应答（点击自言自语）**不切**：它与入口闸的豁免
        是同一条口径（issue #208：点了就要有回应）。免打扰**退出**不补播，与 ``_fire``
        的丢弃语义一致——这里对退出边沿什么都不做。

        判据读``_quiet_muted()``（现读配置现值，全屏等效口径同 ``window_alerts``）。
        """
        if getattr(self, "_current_respect_quiet", None) is not True:
            return
        if not self._quiet_muted():
            return
        logger.info("免打扰生效，停止正在播放的语音")
        self._stop_current_segment()
        # 停掉这一段 = 通道空出，但**没有 EndOfMedia**（player.stop() 不产生媒体终止
        # 事件），与"合成完成却没起播"是同一类结局：两个补播队列必须在这一拍结清，
        # 否则留队的条目要等下一次合成完成（可能永远不来）。同序结算（报时优先），
        # 判据在 drain 内部——豁免的待播语音照常出声，尊重免打扰的照常被丢弃。
        self._drain_queued_fire()
        self._drain_pending_speech()

    def _stop_current_segment(self) -> None:
        """立刻停住当前在播段：停住播放器、**清空音源**、放掉它的两个记账字段。

        三个调用方共用（语义都是"停掉这一段"）：``stop()``（关闭开关 / 退出）、免打扰
        切段、以及播放接管的**超时治愈**。清载是切段语义的一部分，不是某条路径的补丁：
        Qt 的 ``stop()`` 在 ``LoadingMedia`` 期间不改 mediaStatus（见
        :meth:`_unload_player_source`），只停不清就会留下 `LoadingMedia` 判忙——三个
        调用方谁撞上这个死局都一样永久判忙（手动通道没有 tick）。清载没有副作用：
        下一段起播前 ``_play_and_bubble`` 本来就会 ``setSource`` 新源。
        """
        player = self._player
        if player is not None:
            try:
                player.stop()
            except Exception:
                logger.debug("停止语音播放失败（播放器可能已析构）", exc_info=True)
        # stop() 之后补一次清载：把 mediaStatus 拽出 LoadingMedia（详见
        # _unload_player_source —— 单单 stop() 治不掉"加载永不完成"那条死局）。
        self._unload_player_source()
        # ``player.stop()`` 不产生媒体终止态，且实测 stop 之后 ``mediaStatus`` 仍停在
        # ``LoadedMedia``——"已发出 play、未见终止"标志必须在这里显式复位，否则通道
        # 会被永久判忙（_player_engaged 的第三个子判据）。
        self._clear_play_pending()
        self._current_respect_quiet = None

    # ------------------------------------------------------------ 提示
    def _quiet_muted(self) -> bool:
        """当前是否因免打扰（含全屏等效）静音语音报时 / 节日语音。

        判据与气泡**同源**：``window_alerts.quiet_blocked`` 转发 ``quiet_mode`` 的
        唯一判据（手动或全屏等效，以及 ``quiet_also_on_fullscreen`` 配置），本方法
        只负责取宿主窗口，不另读标志——两处判据各自读标志就会出现"气泡压住了、
        声音还在响"这类不一致。

        宿主没有窗口（无 GUI 的测试替身 / 纯逻辑调用）时返回 False：没有窗口就
        没有全屏避让，免打扰也无从谈起。
        """
        from .. import window_alerts

        win = getattr(self._app, "win", None)
        return win is not None and window_alerts.quiet_blocked(win)

    def _bubble(self, text: str) -> None:
        """桌宠气泡展示报时文字（受 voice_chime_show_bubble 开关控制）。

        设置窗口打开等场景下 window.show_bubble 会被抑制（_bubble_suppressed），
        报时属用户主动关注事件，此时直接经桌宠气泡位（_speech_bubble）展示，
        避免“只听声不见字”。

        气泡**没上屏**是另一回事：``show_bubble`` 返回 False，最常见的原因是审批/
        问题提醒正占着气泡位（普通气泡让路）。那类弹窗是用户要回答的东西，绝不
        抢占——改道系统通知并留一条 warning。旧写法只看"调用没抛异常"就 return，
        于是气泡没出、通知没发、日志也没有，报时文字整条消失。

        飞行期是唯一的例外：这是**瞬时**状态（鱼被撞飞几秒），此时连直写也
        禁掉（鱼飞行过程中不弹气泡），也不排队、不改道系统通知——一次性播报
        丢弃即可，落地后正常。判据统一走 ``window_alerts`` 的门禁助手（惰性
        import：本模块顶层不 import Qt，见文件头）。
        """
        if not self._cfg.get("show_bubble", True):
            return
        from .. import window_alerts
        app = self._app
        win = getattr(app, "win", None)
        # 免打扰（含全屏等效）判定在**可见性判断之前**（同 festival_service）：
        # 宿主隐藏时若先看 isVisible，报时提醒会走系统通知，免打扰压不住也不进
        # 暂存队列。这里的暂存只针对**气泡**：语音由 _fire / _handle_synthesized
        # 的静音闸丢弃（不补播，理由见 _fire 的说明）。
        if win is not None and window_alerts.quiet_blocked(win):
            window_alerts.defer_quiet(text)
            return
        if win is not None and win.isVisible():
            if window_alerts.sprite_in_flight(win):
                return
            if window_alerts.bubble_blocked(win):
                if self._bubble_in_slot(win, text):
                    return
            else:
                try:
                    if win.show_bubble(text, duration_ms=self.BUBBLE_DURATION_MS):
                        return
                except Exception:
                    logger.exception("气泡展示失败")
                logger.warning("报时气泡未上屏（多半是提醒占着气泡位），改道桌面通知：%s", text)
        notify = getattr(app, "system_notify", None)
        if callable(notify):
            try:
                notify("语音报时", text)
            except Exception:
                logger.exception("桌面通知失败")

    def _bubble_in_slot(self, win, text: str) -> bool:
        """设置页抑制期直写桌宠气泡位（绕过 show_bubble 的抑制门禁）。

        抑制期是"设置页开着、普通气泡一律让路"的长时用户意图，而报时是用户主动
        关注的事件，故照旧直写；写不进去返回 False，由调用方改道桌面通知。
        """
        try:
            bubble = getattr(win, "_speech_bubble", None)
            rect = win.visible_content_rect()
            scale = getattr(win, "scale", 1.0)
            if bubble is not None and rect is not None:
                bubble.show_text(
                    text,
                    rect,
                    self.BUBBLE_DURATION_MS,
                    pet_scale=scale,
                    subtitle="",
                )
                return True
        except Exception:
            logger.exception("报时气泡降级展示失败")
        return False

    def _notify_missing_tts(self) -> None:
        msg = missing_dependency_hint()
        logger.warning(msg)
        self._bubble(msg)

    def _release_stale_busy(self) -> None:
        """看门狗入口：把到点仍未结的**两个**挂钟都收掉（合成卡死 / 播放接管卡死）。

        三个调用方：20s 的调度 tick（``_on_tick``）、看门狗表（``_unlock_timer`` 的
        timeout）、以及测试直接驱动。**"经过不足阈值"这一拍不是空转**：表是单次表，
        被提前叫醒（CoarseTimer 最多提前 5%，实测调度压力下 PreciseTimer 也不保证
        分毫不差）之后，若不按剩余时间重布，单次表就此耗尽——手动通道没有 tick，
        再漏一次回调就再也没有任何自愈入口，``_busy`` 永久卡死。

        **播放接管那一项必须排在 ``_busy`` 早退之前**：两者是独立的挂钟，合成空闲时
        标志照样可能卡住（设备被拔的静默滞留），被早退挡掉就等于没有兜底。
        """
        self._release_stale_play_pending()
        if not self._busy:
            return
        elapsed = time.monotonic() - self._busy_since
        if elapsed < _SYNTH_TIMEOUT_S:
            # 还不到阈值：按剩余时间重新布表（不是裸 return）。tick 路径上这是冗余的
            # 一次重布（下一次 tick 还会来），手动通道上是唯一的续命。走
            # ``_reararm_watchdog`` 而不是直接按 45s 折算：另一个挂钟（播放接管上限）
            # 若更早到点，它才是该唤醒的时刻。
            self._reararm_watchdog()
            return
        logger.warning("语音合成超时未回调，复位合成锁（%.0fs）", _SYNTH_TIMEOUT_S)
        self._busy = False
        self._synthesis_role = "play"
        # 合成那一支结了：**结算**看门狗表（同 _on_synthesized 的口径）——播放接管
        # 那一支若还挂着，必须按它的剩余期限重布，否则它的上限兜底永远不到点。
        self._disarm_unlock_timer()
        # 代次递增 = 这次挂死的合成从此作废：它若日后才回调（网络超时后返回），
        # 回调带的旧代次会被 _on_synthesized 认出来并整条丢弃，不会串到随后起的
        # 新任务上（取走新任务的 role / respect_quiet / busy 锁）。
        self._next_synthesis_generation()
        # 合成锁复位后结算排队项：挂死的合成不会再来回调，到点报时靠这里补。
        # 两个队列同序结算（报时优先），判据在 drain 内部——这里是超时自愈这一拍，
        # 不结算待播语音就会让它永远没人出队（挂死的合成也不会再产生 END）。
        self._drain_queued_fire()
        self._drain_pending_speech()

    def _release_stale_play_pending(self) -> None:
        """播放接管标志的上限兜底：超过 ``_PLAY_PENDING_MAX_S`` 仍未结就按"通道空出"收。

        覆盖 ``errorOccurred`` 也覆盖不到的静默滞留。**治到底**：只清自记标志治不彻底
        ——播放器死在 ``StoppedState + LoadingMedia`` 时枚举判据 ``LoadingMedia`` 仍让
        ``_player_engaged()`` 恒真，drain 判忙留队，而两个期限都空后表也真撤了（手动
        通道没有 20s tick）→ 此后再无挂钟可布、通道**永久判忙**。所以这一拍对**非
        PlayingState** 的播放器做两件事：``stop()``（停住语义）+ ``_unload_player_source()``
        清空音源。**单单 stop() 不够**：Qt 6.11 FFmpeg 后端的 ``stop()`` 在
        ``LoadingMedia`` 期间不改 mediaStatus（详见 :meth:`_unload_player_source`），
        只有空源才把 mediaStatus 落到 ``NoMedia``、真正解除接管判据；之后按既有同序
        结算两个队列。

        **真在 PlayingState 的那一段照旧在播，上限不碰它**：不停播放器、不清播放标志与
        两个队列、不动音源，**只**把期限起点（``_play_pending_since``）顺延一个窗口再查
        ——上限的语义只管"卡住"，合法在播的长音频被掐就是"报时被截断"；顺延不是原地
        重布（期限已过，原地重布会算出 ≤0 的剩余时间 → 1ms 忙转表），这样它若之后才
        卡住，下一个窗口照样收得住。

        未到上限时同样只是重布（单次表被提前叫醒后不重布就没了）。没有起点
        （``_play_pending_since`` 为 None，例如测试替身直接置位标志）时无从判定，
        保持原样——那种对象也没有真实播放器会被卡住。
        """
        if not getattr(self, "_play_pending_end", False):
            return
        since = getattr(self, "_play_pending_since", None)
        if since is None:
            return
        elapsed = time.monotonic() - since
        if elapsed < _PLAY_PENDING_MAX_S:
            self._reararm_watchdog()
            return
        if self._player_playing():
            self._play_pending_since = time.monotonic()
            self._reararm_watchdog()
            logger.debug("播放接管状态已超过 %.0fs 但仍在 PlayingState：顺延期限再查",
                         _PLAY_PENDING_MAX_S)
            return
        logger.warning("播放接管状态超时未结（%.0fs），按通道空出收（停住并清空卡住的播放器）",
                       _PLAY_PENDING_MAX_S)
        # 停住这一段 + 清空音源（_stop_current_segment 内统一做，见那里）
        self._stop_current_segment()
        self._drain_queued_fire()
        self._drain_pending_speech()

    def _unload_player_source(self) -> None:
        """把播放器的**音源清空**（``setSource(QUrl())``），让 mediaStatus 落到 ``NoMedia``。

        **为什么 stop() 不够**（Qt 6.11 FFmpeg 后端 ``QFFmpegMediaPlayer::stop()`` 的
        行为）：mediaStatus == ``LoadingMedia`` 时它只写 ``m_requestedStatus`` 就 return，
        **不改 mediaStatus**——「加载永远不完成」的真死局下，stop() 之后
        ``_player_engaged()`` 仍因第二个子判据（``LoadingMedia``）判接管，停段等于没做
        （之后若加载自然完成会转 ``LoadedMedia``，但那不是 stop 的功劳）。空源才是
        确定解除：实测（真机 QMediaPlayer + FFmpeg 后端）``stop()`` 后紧跟
        ``setSource(QUrl())``，mediaStatus 在毫秒量级内（~5ms，探针实测 9.4ms）转
        ``NoMedia``。

        调用方只有 :meth:`_stop_current_segment`——清载是"切段"语义的一部分（三个调用方
        谁撞上 LoadingMedia 死局都一样），所以统一在那里做，不在各条路径各补一次；
        清载无副作用：下一段起播前 ``_play_and_bubble`` 本来就会 ``setSource`` 新源。
        """
        player = self._player
        if player is None:
            return
        try:
            from PySide6.QtCore import QUrl

            player.setSource(QUrl())
        except Exception:
            logger.debug("清空播放器音源失败（播放器可能已析构）", exc_info=True)

    # ------------------------------------------------------------ 缓存维护
    def _prune_cache(self) -> None:
        """缓存超上限时按修改时间清理最旧文件（节流，避免每次配置变更全扫）。"""
        now = time.monotonic()
        if self._last_prune_at and now - self._last_prune_at < _PRUNE_MIN_INTERVAL_S:
            return
        self._last_prune_at = now
        try:
            if not self._cache_dir.is_dir():
                return
            files = sorted(
                # 各后端的产物都要算进上限：edge 出 mp3、MiMo 出 wav；
                # 只 glob mp3 会让 wav 缓存无上限地涨下去（新增产物格式时
                # 这里与 _sweep_orphan_temp_files 都要跟着认）。
                (p for p in self._cache_dir.iterdir()
                 if p.suffix.lower() in (".mp3", ".wav") and p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            for old in files[: max(0, len(files) - self.MAX_CACHE_FILES)]:
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    logger.debug("清理语音缓存失败：%s", old)
        except OSError:
            logger.debug("语音缓存目录不可访问：%s", self._cache_dir)
