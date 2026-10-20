# -*- coding: utf-8 -*-
"""帧序列「首跑自动供给」（帧序列化 B 档）。

素材包不带 frameseq 时（安装包只带 webm），运行时后台低优先级把 webm 转成
WebP 帧序列。**全部 clip 统一一档**：**无损** + 白底反解 v6（``ENCODER_DESC``）
——热集（idle/move/turn/click/drag ≈95% 播放时长）与冷集（random/events）不再分档，
旧的四代产物（热集无损 / 冷集 Q80 / 反解 v2 / 反解 v3）因此都不再被采纳
（``current_generation``）。

转换分**两段**（``convert_clip``）：

1. **无损中间帧**：ffmpeg 一次调用把 webm 每一帧写成无损 WebP（``-lossless 1``，
   参数见 ``_ffmpeg_argv``）；
2. **逐帧白底反解 v5 + 有损重编码**：``unblend_frames_in_place`` 用 Pillow 逐帧跑
   ``unblend_v3``（反解 ``unblend_rgba`` → 低 alpha 亮色收色 → 镂空还原
   ``restore_hair_holes`` → alpha 微缩 1px）最后接 **alpha 底板清理**
   （``floor_faint_alpha``：``a == 1`` 的像素清成全透明）后原地编码**无损 WebP**。镂空还原
   本来是 v3 档位内的缺陷收口（当时不构成新档），针对
   v3 结构性够不到的那一类——**抠图整块失败的背景残留**（发丝间该透明的缝隙留成了
   ``a = 255`` 的实心灰白块）；但它在实机上**误切了白色鲸鳍**（残留与白色艺术品在该
   判据的形状特征上同构），**已默认挂起**（``HOLE_RESTORE_ENABLED = False``，直通、
   零开销），所以 v5 也不含它（**没有"v4 产物"**，等用户给出精确残留位置后重标定
   再开启）。底板清理是 v5 相对 v3 的**唯一**差别：抠图把整块背景留成了 ``a = 1``
   （实测占 640×360 的 ≈82%），叠在任何背景上都是 ``dst × 254/255``（白底 255 →
   254，整块矩形比背景暗一档），微缩够不到它。

为什么不让 ffmpeg 一趟做完有损：素材在白底上抠图，半透明像素的存值里混着白
（``stored = 角色色 × a + 255 × (1-a)``），必须在**有损编码之前**按 alpha 反解
——先有损再反解等于把白晕连量化噪声一起固化下来。ffmpeg 侧理论上能用 ``geq``
把反解和编码合成一趟（实测 41ms/帧，比本模块的 Python 逐帧快 ~1.6x），本模块仍
走 Python 逐帧，理由有三：反解在这里是**纯函数**（``unblend_rgba``，逐像素口径
由单测钉住；``geq`` 只能靠真跑 ffmpeg 验）；v3 的收色要的是**邻域**信息（最近的
不透明邻居颜色），``geq`` 表达不了；而且逐帧粒度让关机窗口里的"停手单位"是一次
unlink 而不是一整趟编码。成本对比见 PR 报告的性能分析。

转换结束（含部分成功）由 MovieLibrary 在 GUI 线程 rescan，此后新请求的 clip
自动走 FrameSeqClip（已创建的 clip 不动）。依据与实测数字见
.scratch/frame-seq-feasibility/FEASIBILITY.md。

硬约束（实现即契约）：

- **源身份** = 源 webm 的 sha256；世代目录名 ``<stem>.g<戳12>/``（戳 = sha256
  前 12 位）——**目录名即身份**，meta.json 同时写全量 ``source_sha256``。
  采纳（帧序列化 B 档生效）要求五件事同时成立：目录名看得出戳、meta 有合法
  戳、名字戳 = meta 戳前缀、meta 帧数 = 磁盘帧数、**meta encoder = 当前档位串
  ``ENCODER_DESC``**（无损 + 白底反解 v6：档位不符说明这份产物不是按当前档产出的
  ——改档前的热集无损 / 冷集 Q80 / 反解 v2 / 反解 v3 世代全在这一条上被拦住
  ——宁可重转，不拿旧档产物顶替）；
  给强身份时还要求 meta 戳 =
  当前源 sha256。**任何一条不成立都不采纳，回退 WebM 播放**——没有身份凭证的
  旧版/手工产物绝不当当前源用（``<stem>/``、``<stem>.g<戳>.tmp/`` 亦然）。
- 采纳前按当前源重算 sha256 比对：源换版 ⇒ 旧世代立即失效，绝不继续播旧素材。
  **身份每次由内容重算，不做 ``(mtime_ns, size)`` 记忆**：同尺寸 + mtime 复原的
  替换（解压/复制保留时间戳落地新素材就是这形态）会命中 stat 记忆，把旧世代的戳
  继续当当前源身份 —— 静默播上一版帧；stat 值证明不了内容身份，这是唯一可证口径。
  代价实测：11 段热集共 5.45MB，一次全量 sha256 ≈ 12ms（页缓存热）/ 18ms（冷），
  且只在需要身份的两处发生：给待转 clip 命名输出目录、比对「已有世代目录」是否仍
  属当前源（供给范围外的目录一个字节都不读）。**帧内容永不哈希/解码**
  （155MB 帧集一个字节都不读，采纳只看带戳 meta 与帧数）——这是本模块的能耗红线。
- **发布前再验一次身份**（``convert_clip``）：转换期间源被换掉 ⇒ 产物丢弃。meta
  里的戳是身份凭证，帧来自 v2 而戳写 v1 就是一份假凭证（源将来回到 v1 时它会被
  采纳，播的却是 v2 的帧），比"没有缓存"更糟；宁可这一轮不产出，下一轮重转。
- 供给只向「源身份命名」的新目录发布：先转 ``<世代名>.tmp/``，成功后
  ``os.rename`` 原子发布为 ``<世代名>/``（tmp 全程就绪，不存在"半个目标"窗口）。
  **绝不原地覆盖在用世代目录**（活 FrameSeqClip 可能正在读）；同名目录存在但
  不可采纳（半拷贝/损坏残留）时才重建它，``force`` 是构建期/维修用的显式
  逃生门。重建 = 旧目标先改名挪到 ``<世代名>.replacing/``、**发布成功后才删**
  ——绝不"先删旧目标再 rename"：删了再失败就是净丢一份在用素材。发布/回滚/
  半成品清理任何一步失败都按 ``(False, 错误文本)`` 返回（不抛异常：一个 clip
  的发布失败不许掀掉整轮供给，后续 clip 与清扫照做）。
- **世代退役**：发布新世代时只给同 clip 的其它世代目录打 sidecar 退役标记
  （``<世代名>.retired``，mtime = 退役时刻，幂等不刷新）；清扫只删「已标记 +
  满宽限期 ``RETIRED_GRACE_S`` + 不在用」的目录。**回朝**（源内容退回某一版、
  那一版的世代重新成为当前世代）时旧标记必须作废——它的龄属于上一次离场，对
  这一次离场无效：清扫判龄前先删掉「又是当前世代」的标记
  （``clear_current_markers``），供给闸门也把「当前世代还带着退役标记」当待办
  （``stale_current_markers``，回朝窗口里起一轮）。不作废的后果是那个世代在
  再次离场的那一轮被按旧龄当超龄退役删掉，宽限期（跨进程唯一缓冲）形同虚设；
  作废 = 下一次离场重新起算（标记只是删除闸门，删它只会推迟删除）。
  「在用」= 本进程 MovieLibrary 已采纳的世代目录（``protect_generations``，
  进程内单调只增）+ 当前源对应的世代目录。旧版无戳 ``<stem>/`` 目录不在世代
  命名空间内：不采纳、不补戳、**也不删**（它可能正被旧版本实例读取）。代价与
  残留限制：升级后旧的无戳 153MB 帧集不会被自动回收（要人工删除或重新打包），
  新世代要重转一份，但**按启动预算分批**（每个启动周期最多 1 个 clip，三宠三库
  共用同一份配额；与全新安装同路径：延后 5s、低于正常优先级、可取消、随会话结束
  中止），不再是一次 11 个 clip 的全量后台重编码；迁移期内磁盘是双份（旧产物按
  契约不删）。上限：每个 clip 常驻 = 当前世代 + 宽限窗口内的历史世代；宽限到期由
  下一次供给轮的清扫回收。跨进程只剩宽限期一道闸（另一个实例若持续读取同一世代
  超过 ``RETIRED_GRACE_S``，理论上有被删风险——单实例产品口径下不成立）。
- ffmpeg 参数固化不可改（见 ``_ffmpeg_argv``）：``-c:v libvpx-vp9`` 强制
  libvpx 解码（原生 vp9 解码器静默丢弃 VP9 alpha 位流）、``-pix_fmt bgra``
  直通（libwebp 走 yuva420p 会 chroma 下采样）、``-nostdin``（循环里跑
  ffmpeg 会吞父进程 stdin）；
- **两段式管线与白底反解 v5**（硬约束，见模块文档）：① 阶段一**永远无损**
  （``-lossless 1`` 是唯一的阶段一编码档）；② 阶段二逐帧跑 ``unblend_v3``：
  ``rec = clip((stored - 255×(1-a)) / a, 0, 255)`` 之后接**低 alpha 亮色收色**
  （a 落在收色带内、且比最近的不透明邻居亮出 ``UNBLEND_EXCESS_LUMA`` 的像素，
  颜色收成邻居色）、**镂空还原**（实心灰白的背景残留整块切透，只动 alpha；
  **默认挂起**，见模块常量 ``HOLE_RESTORE_ENABLED``；挂起即不在 v5 里，没有 v4 档）、
  **alpha 微缩 1px**（3x3 最小值滤波）与 **alpha 底板清理**
  （``floor_faint_alpha``：``a == 1`` → 0，RGB 同时置 0；v5 的**唯一**新增步骤）。
  **颜色会被改、alpha 也会被改**（v2 的
  "alpha 一个位都不动"到此为止）：档位串与反解标记一起换，旧世代按既有迁移机制
  自动不采纳；收色只动半透明像素，且判据方向决定**只会变暗**——不存在修完反而
  更亮（>30）的像素。反解本身两端不反解：``a < UNBLEND_MIN_ALPHA``
  （除数太小、数值不稳，且这些像素实测 RGB≈0）与 ``a >= UNBLEND_MAX_ALPHA``
  （白底项 ≤2%，整数除法反而引入 ±1 抖动）。单帧处理不了就**跳过该帧并记日志**
  （原文件那份无损帧原样留着：能播、帧数对），绝不中断整段供给；只有整段进不了
  阶段二（Pillow / ``ImageMath.lambda_eval`` 不可用）才按失败收口——宁可不产出，
  也不写一份「无损 + 反解 v6」却不成立的假 meta。帧数契约因此只有一处定义：
  ``meta.frames`` = 磁盘 ``f_*.webp`` 数（阶段二原地覆盖，一张都不增删）；
- fps 探测用 ``imageio_ffmpeg.read_frames`` 的 meta（运行时不带 ffprobe）；
- 幂等：当前源已有可用世代目录 → 跳过（不重编码、不重建半成品）；
- **每轮（每次启动）供给预算**——两条口径，都不是"每库一份"：

  ① **迁移配额**：有等价旧产物的 clip（升级形态：旧版无戳 ``<stem>/`` 在场，播放
  不降级）每个**启动周期**最多**尝试** ``MIGRATION_CLIPS_PER_ROUND`` 个（按尝试记
  账：失败也吃配额，免得一轮里连着挂多个）。配额记在**进程内共享**的台账里
  （``claim_migration_slot``，键 = frameseq 根的规范路径）：一次启动 = 一个进程，
  三宠三库共用同一个素材根 ⇒ 合计只迁 1 个，而不是每库各迁 1 个。它是**进程生命
  周期**状态、**不是跨进程计数**（多进程多宠层已在 Phase 4.4b 删除；另一实例是另
  一个进程、另一份启动预算，单实例产品口径下不叠加；进程退出即归零 = 下次启动重
  新起算）。升级首跑因此从"11 个 clip 全量重转（≈3 分钟后台 ffmpeg，且立刻多占一
  份 153MB）"变成"每个启动周期 1 个 clip"，余下留到下次启动。

  ② **起手上限** ``ROUND_BUDGET_S``：是**准入线**，不是墙钟硬上限——只在起一个 clip
  之前检查，已起手的 clip 一定跑完（**绝不硬杀 ffmpeg**），所以本轮实际墙钟可能超出
  该上限，最多超出"一个 clip 的转换时长"。到点后余下 clip 一个都不再起手，留到下次
  启动（``budget_stopped``）。日志必须同时说清这两件事，否则"按预算收手"会被读成
  "本轮墙钟 ≤ 上限"。

  供给序固定 **热集（idle → move → 其余热集）→ events → random**（档内按相对
  路径，见 ``plan_clips``）：迁移配额因此先花在播放时长占比最高的 idle/move，
  而不是字典序里的 click；冷集（random/events）排在全部热集之后 —— 热集缺一段的
  代价（该段回退 WebM 播放）远大于冷集缺一段。

  没有旧产物的 clip（全新安装 / 新素材）不受迁移配额约束——帧序列是那里唯一来源，
  不存在"白烧一份"——但**不预设一轮转完**：仍受起手上限约束，未起手的留到下次启动。
  **旧产物只是"等价缓存"的旁证，绝不据此被采纳/补戳/改写/删除**（身份仍只看带戳
  meta），判定只 stat + 列名（不读旧 meta、不读帧内容）；升级期磁盘确实是双份
  （旧产物按契约不删，收口留给用户/重新打包）。
- **磁盘准入**（``provision_once`` 起手每个 clip 之前）：``frameseq`` 根所在卷的剩余
  空间 < ``max(1GiB, 源 webm 大小 × 20)`` 就不起手（``report.deferred_disk``），
  已转产物一个字节不删、失败退避语义不变——帧序列是缓存，盘满了**宁可晚点转，
  绝不把盘写满**（写满会让整机/整个素材目录的写入一起失败）。探针拿不到（路径
  异常）时按"有空间"处理：不能因为一次 stat 失败就永久停掉供给。
- **失败退避**（``.backoff.json``，跨启动生效）：``convert_clip`` 报错的 clip 记一笔
  连续失败，退避 ``BACKOFF_BASE_S * 2^(n-1)``（封顶 ``BACKOFF_MAX_S``）内不再重试
  ——连 ffmpeg 都不派生。没有它，一个每次必挂（或挂很久）的 clip 会在每次启动重复
  烧掉整轮预算；有它，代价收敛成"一个退避窗口一次尝试"。成功即销账，表空即删
  sidecar（不在素材目录留常驻文件），损坏的表当空表（供给绝不被一个坏文件卡死）。
  所有错误一视同仁（含"转换期间源漂移"这类瞬时错误）：代价是那个 clip 晚一个退避
  窗口重试（窗口只有分钟级，且它本来就在走 WebM），换的是绝不反复烧预算。
  **退避写盘失败只 warning 说明"本次退避未落盘、下次启动会立刻重试"**，绝不谎称
  "Ns 内不再重试"——日志与行为必须一致（``write_backoff`` 的返回值不许丢）。
- **扫描与供给范围**：``scan_generations`` 递归扫任意深度（世代目录内部不再下钻）
  —— ``events/balance/*.webm`` 的世代目录在三层，旧的两层扫描永远看不到它（既不
  会被采纳，也永远不清扫）；范围键 ``世代目录.parent / stem`` 与 ``clip_base_dir``
  对齐。供给范围 = 热集 + random + events。
- 多实例互斥：frameseq 根目录 QLockFile（``.converting.lock``），拿不到锁
  直接放弃本轮（另一实例在转），不等待；**库侧据此有界重试**
  （``PROVISION_LOCK_RETRY_*`` + ``library._retry_frameseq_provision_after_lock``），
  否则同进程里抢不到锁的其它角色（三宠三库）在这次启动里再也不会供给。
- ISSUE-111 纪律：每个 clip 转换前查 ``pet.webm_clip.session_ending()``，
  置位即停止后续；**探针之后、``_popen`` 之前再查一次**（半成品清理 + mkdir +
  fps 探测自己就派生一次 ffmpeg，这段窗口里置位必须仍拦得住）；library 收尾
  置 cancel 谓词并终止在飞 ffmpeg 子进程，线程有界等待（≤2s）后退出。这个"有界"
  要成立，**清扫也必须可中断**：
  ``sweep_retired`` 收 ``cancelled`` 谓词，每个目录开始前与目录内**每条目**之间
  都复查它 + ``session_ending()``。``shutil.rmtree`` 进目录后不可中断，一个上千帧
  的世代目录就足够让 2s 失效（线程带着没退完的 I/O 与已收尾的库一起走）；逐条目
  粒度把"不可中断的窗口"压到一次 unlink。取消后未删净的目录保留退役标记，仍是
  下一轮的候选（续删）；``_popen`` 返回到 ``on_proc`` 注册之间的取消窗口由
  ``_track_proc`` 在同一把锁内复查取消位闭合（否则那个进程没人 terminate，
  communicate 会陪它跑到转换结束）；兜底超时（取消未被及时响应）时线程由 library
  摘出库（``setParent(None)`` + 模块级强引用）持有到跑完——QThread 带着活线程被
  销毁是 Qt 的 abort，绝不允许；
- 无 ffmpeg exe：编码静默 no-op，但世代清扫照做（纯本地 I/O，不依赖 ffmpeg）；
- Windows 下 ffmpeg 子进程 ``creationflags=BELOW_NORMAL_PRIORITY_CLASS``
  （POSIX 忽略），一次性重编码的 CPU 让给交互；
- ``PET_FRAMESEQ=0`` 整体禁用供给（dev 逃生门，不进 Config/设置页/schema）。

线程模型：``FrameseqProvisionWorker`` 是 library 拥有的 QThread（QObject
亲和 GUI 线程）；``run()`` 内只碰纯 Python 数据与线程内自建的 QLockFile，
收尾用 queued ``finished_work`` 信号回 GUI 线程触发 rescan——Qt 对象不出
owning 线程。退役清扫（删除目录）只在 worker 线程做；GUI 线程只读目录名与
标记 mtime（库创建时的 rescan 有界、不删任何东西）。

**「有没有活干」的判定也在 worker 线程**（``plan_clips`` / ``pending_retirements``
/ ``stale_current_markers``）：这三项各自要给供给范围内的 clip 算源哈希（热集
5.45MB + random 45MB + events 3MB），放在 GUI 线程就是一次可感的启动卡顿。无事
可做时 worker 立即结束并在 ``report.idle`` 留痕，库侧据此**不**做收尾 rescan
（rescan 同样要重算源哈希）；只有本轮确有新转换或清扫退役才重扫。
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from PySide6.QtCore import QLockFile, QThread, Signal

from .webm_clip import session_ending

try:  # 与 webm_clip 同一依赖口：不可用时供给静默 no-op（播放同样会降级）
    import imageio_ffmpeg
except Exception:  # pragma: no cover - 缺依赖属环境问题
    imageio_ffmpeg = None

logger = logging.getLogger(__name__)

# 热集目录（≈95% 播放时长）：供给序排在最前
HOT_FOLDERS: tuple[str, ...] = ("idle", "move", "turn", "click", "drag")
# 冷集目录（random/events）：数量多、单段短、磁盘占比大（45MB+3MB 对热集 5.45MB），
# 屏幕时间占比极低。改档后**没有"有损档"**（全部 clip 统一无损）：这组名字只
# 决定供给序（排在全部热集之后）与迁移配额的先后。
LOSSY_FOLDERS: tuple[str, ...] = ("random", "events")
# 供给范围 = 热集 + 冷集
SUPPLY_FOLDERS: tuple[str, ...] = HOT_FOLDERS + LOSSY_FOLDERS
# dev 逃生门（不进 Config/设置页/schema）
ENV_DISABLE = "PET_FRAMESEQ"
# frameseq 根目录锁（多实例互斥）
LOCK_NAME = ".converting.lock"
# 库创建后延迟触发：让启动峰值（高优先级预热）先过去
PROVISION_DELAY_MS = 5000
# 半成品临时目录后缀（在世代目录名前追加；带这个名字的目录永不被采纳）
TMP_SUFFIX = ".tmp"
# 被替换的旧目标暂存后缀（发布成功后才删；发布失败要能还回原位）
REPLACE_SUFFIX = ".replacing"
# 世代目录：<stem>.g<戳12>（目录名即源身份；发布后不再改动）
GEN_INFIX = ".g"
GEN_STAMP_HEX = 12
# 退役标记（sidecar 文件，mtime = 最近一次被取代的时刻）
RETIRED_MARK_SUFFIX = ".retired"
# 退役宽限期：标记满它才允许删除（此刻仍可能有活 clip / 其它实例在读）
RETIRED_GRACE_S = 24 * 3600.0
# 每轮（每次启动）供给预算（见 provision_once）：
# - 迁移配额按**进程内共享**的台账结算（一次启动 = 一个进程；三宠三库共用素材根 ⇒
#   合计最多迁 MIGRATION_CLIPS_PER_ROUND 个），不是每库一份；
# - ROUND_BUDGET_S 是**起手上限**（准入线），不是墙钟硬上限：已起手的 clip 跑完
#   （不硬杀 ffmpeg），实际可能超出至多一个 clip 的转换时长。
# 供给序：先 idle、再 move、然后其余热集，再 events，最后 random（档内按相对路径，
# 见 plan_clips / SUPPLY_PRIORITY）。
SUPPLY_PRIORITY: tuple[str, ...] = ("idle", "move")
LOSSY_PRIORITY: tuple[str, ...] = ("events", "random")
MIGRATION_CLIPS_PER_ROUND = 1
ROUND_BUDGET_S = 120.0
# 锁被占（另一实例 / 同进程里先拿到锁的库）时库侧的有界重试：首个退让 4s、逐次翻倍、
# 封顶 60s，最多 PROVISION_LOCK_RETRY_LIMIT 次——拿不到就等下次启动，不无限轮询。
PROVISION_LOCK_RETRY_LIMIT = 8
PROVISION_LOCK_RETRY_DELAY_MS = 4000
PROVISION_LOCK_RETRY_MAX_DELAY_MS = 60000
# 失败退避（sidecar 落盘，跨启动生效）：第 n 次连续失败等 BASE * 2^(n-1)，封顶 MAX
BACKOFF_NAME = ".backoff.json"
BACKOFF_BASE_S = 300.0
BACKOFF_MAX_S = 6 * 3600.0
DEFAULT_FPS = 24.0
# 统一编码档：**全部 clip** 一档（无损 + 白底反解 v6 = v3 三步 + alpha 底板清理）。
# encoder 串是采纳条件的一部分（``current_generation``），改它 = 全部旧世代失效
# ——这是设计好的迁移路径：旧世代不采纳 ⇒ 回退 WebM 播放 + 后台按新档重新供给。
# **v3 与 v5 之间没有"v4 档"**：``HOLE_RESTORE_ENABLED``（镂空还原）从未启用、
# 现在仍是挂起状态（实机误切白色鲸鳍），所以它不占档位——没有一份产物是"v4 产物"。
# v5 相对 v3 只多一步：把 ``a == 1`` 的底板清成全透明（``floor_faint_alpha``）。
ENCODER_DESC = "libwebp lossless (bgra straight, unblend-white-v6)"
# meta 里的反解标记：这份帧集是**白底反解 v6 过**的（供将来识别/迁移用）。
# 与 ``ENCODER_DESC`` 同写同弃，采纳闸门只看 encoder 串——标记不足以单独构成身份。
UNBLEND_MARK = "white-v6"
# 改档之前的 encoder 串（热集无损 / 冷集 Q80 / 反解 v2 / 反解 v3）：**只作历史识别**，
# 绝不写进 meta。它们天然不等于 ``ENCODER_DESC`` ⇒ 那一代产物不被采纳
# （迁移路径，不是巧合）；列在这里是为了让"上一代的串长什么样"在代码里可查，
# 而不是只能靠字符串比对去猜。v2（``unblend-white``）与 v3 的差别是**像素级**的
# （v2 的产物在深色角色边缘留着白边），所以它必须同旧两档一样重转，不能混用；
# v3 与 v5 同理（v3 的产物铺着一整块 ``a == 1`` 的底板，叠在任何背景上都会显出一块
# 浅灰——白底 255 → 254，这正是"白底整块色差"）。
LEGACY_ENCODER_DESCS: tuple[str, ...] = (
    "libwebp lossless (bgra straight)",
    "libwebp lossy q80 (bgra straight)",
    "libwebp lossy q70 (bgra straight, unblend-white)",
    "libwebp lossy q70 (bgra straight, unblend-white-v3)",
    "libwebp lossy q70 (bgra straight, unblend-white-v5)",
)
# 阶段二编码参数：libwebp 编码器（Pillow 的 webp 后端就是 libwebp，与 ``_ffmpeg_argv``
# 用的同一个），quality=70 与用户实测口径一致；method=4 = libwebp 默认压缩档，与
# ffmpeg 的 ``compression_level`` 默认值 4 同档（实测体积/PSNR 持平，见报告）。
# v6 起**不再有损**：运行编码层必须位精确（``lossless=True`` + ``exact=True``）。
# ``exact=True`` 是硬要求——libwebp 默认会改写 ``alpha == 0`` 像素的 RGB，
# 那样「解码重建 == 参考 RGBA」就不成立。``method=2`` 是无损档的甜点：method=6
# 只再省 ~2% 体积，编码却慢 7 倍（实测 610ms vs 83ms/帧）。
WEBP_LOSSLESS = True
WEBP_EXACT = True
WEBP_METHOD = 2
# 白底反解 v3 的两端（详见模块文档「两段式管线与白底反解 v5」）：
# ``UNBLEND_MIN_ALPHA`` = 0.08 × 255 ≈ 20.4，即 a/255 < 0.08 不反解；a ≥ 250 也不反解。
UNBLEND_MIN_ALPHA = 21
UNBLEND_MAX_ALPHA = 250
# ---------------------------------------------------- 白底反解 v3：亮色收色 + 微缩
# v3 在 v2（``unblend_rgba`` 的按 alpha 除法）之后追加两步，专治 v2 的结构性盲区：
#
# **盲区**：``rec = (stored - 255×(1-a))/a`` 在 ``stored ≈ 255`` 时**恒等于 255**
# ——「白色艺术品边缘」（鲸鳍、白蕾丝的 AA 边）与「纯白底污染」在公式眼里一模一样；
# 而 ``UNBLEND_MIN_ALPHA = 21`` 的下限又让 a=13..20 的白像素原样残留。用户实机目验
# 的残留白边正是这两处。
#
# **v3 换判据**：不问"这个像素白不白"，问"它比**最近的不透明邻居**亮多少"——
# 邻居本身是白的（白鳍/白蕾丝）⇒ 亮度差 ≈ 0 ⇒ 一位不动；邻居是深色的（头发、
# 鲸鳍深色边缘）⇒ 差超阈值 ⇒ 收成邻居色。白底污染因此在深色边缘被拉回去，
# 而白色艺术品毫发无伤：这是 v2 公式分不开的那两类，必须靠邻域信息才分得开。
# ``UNBLEND_EXCESS_LUMA`` 的依据：白晕与真色之间的亮度差实测在 60 色阶以上
# （白 ≈ 250 对深色角色 ≈ 40-90），取 30 是"稳稳抓住污染、又不会把正常的明暗过渡
# （AA 边相邻两档差 10-20）误判成污染"的中间档。
UNBLEND_EXCESS_LUMA = 30
# 收色带的 alpha 区间（**下含上不含**）：``UNBLEND_PULL_MIN_ALPHA <= a < UNBLEND_PULL_MAX_ALPHA``。
# 上界 = 0.5 × 255 = 127.5 ⇒ ``a < 128``：a ≥ 0.5 的像素自己的角色色已占存值一半
# 以上，把它当"污染"收掉等于用邻居色盖掉真颜色（半透明头发丝、睫毛会变糊）；
# 下界 = 0.01 × 255 ≈ 2.55 ⇒ ``a >= 3``：a=1..2 的像素在屏幕上几乎不可见，而这一圈
# 窄到邻居估计本来就不稳。区间**两端都写进常量**，免得"0.01/0.5"这种分数口径散在
# 代码里各写一遍。
UNBLEND_PULL_MIN_ALPHA = 3
UNBLEND_PULL_MAX_ALPHA = 128
# 扩散轮数：每轮 3x3 盒滤波把已知区向外渗 1 圈，5 轮 = 11×11 窗口（±5px）。
# 依据：素材白晕实测宽 2-4px，5 轮留一倍余量；半径再大对结果无影响（更远的邻居
# 权重早已趋 0）只增加耗时——每轮 ≈4ms/帧，是单帧预算里最大的一笔。
UNBLEND_DIFFUSE_ROUNDS = 5
# ------------------------------------------------- 白底反解 v4：镂空还原（工序四）
# v3 之后仍有一类盲区：**抠图整块失败的背景残留**。发丝之间的镂空缝隙本该是
# ``a ≈ 0``，实机目验里却是一块 ``a = 255`` 的实心灰白（墙色、低饱和）——v3 的
# 反解/收色全部建立在"``a`` 落在半透明带里"之上，对 ``a >= 250`` 的实心块一位
# 都碰不到（``unblend_rgba`` 上端不反解、``pull_band`` 上限 128）。
#
# 判据三层，缺一不切（``restore_hair_holes``）：
#
# 1. **候选**（``hole_candidate_mask``）：``a >= 250`` 且亮度 > ``HOLE_LUMA_MIN``
#    且通道极差 < ``HOLE_RANGE_MAX`` 且 ``r - b <= HOLE_WARM_MAX``。依据：残留是
#    **墙色**——实测块内颜色 ``(198,204,221)``/``(216,210,215)`` 一档，亮度
#    190-230、通道极差 5-23、``r - b`` 落在 ``[-27, +6]``；而发丝自己的蓝调高光是
#    **高饱和**的（极差 60+），皮肤是**暖调**（实测 ``(243,228,218)`` 极差 25、
#    ``r - b = 25``）——两者都留在候选之外或者被后面的结构口径拦下。
#    ``HOLE_LUMA_MIN = 150`` 是"明显比发色亮"的下限（发色 ``luma`` 30-110），
#    ``HOLE_RANGE_MAX = 30`` 与用户给的起点一致；``HOLE_WARM_MAX = 12`` 是
#    "墙色 vs 肤色"的分界（残留实测上界 +6 对皮肤实测下界 +25，取中），
#    单靠极差分不开这两类——浅肤色 ``(243,228,218)`` 的极差只有 25，也在 30 之内。
# 2. **包围**（``surround_mask``）：``HOLE_WINDOW × HOLE_WINDOW``（15×15）邻域里
#    **蓝调发色**（``b > r + HOLE_HAIR_BLUE_DELTA`` 且 ``b > HOLE_HAIR_BLUE_MIN``
#    且 ``a >= 250``）占比 >= ``HOLE_SURROUND_MIN``。依据：残留块长在发丛里，四周
#    是发色；孤立的白块（贴透明背景的白围裙下摆、暴露在轮廓外的白配饰）四周是
#    透明背景，占比不足。``HOLE_HAIR_BLUE_DELTA/MIN`` 沿用用户给出的
#    "B > R+20 且 B > 90" 口径（实测发色 ``(50,56,103)`` 一档通过，墙色
#    ``(216,210,215)`` 的 ``b - r = -1`` 不通过）。
# 3. **结构**（``_hole_blocks`` + ``_hole_cut_mask``）：三条同时成立——
#    ① 连通块尺寸落在 ``[HOLE_SIZE_MIN, HOLE_SIZE_MAX]``；② 块里有像素落在
#    ``a < 250`` 像素的 ``HOLE_EDGE_MAX`` 邻域内（**贴缝证据**）；③ 块的包围带
#    （外扩 ``HOLE_RING_MAX`` 像素、让开贴块的 2 圈）里**不透明**像素占比 >=
#    ``HOLE_RING_OPAQUE_MIN``（**实心包围证据**）。
#    为什么必须有结构条件：光靠 1+2 会切掉两类不该切的东西——**眼睛高光**
#    （白点 + 深蓝虹膜，虹膜正好通过蓝调判定）和**白蕾丝发箍**（细线织成的巨
#    大连通网，每个线像素的 15×15 窗口里 80% 以上是发色，占比同样过线）。
#    ①②③ 正是为它们设的：高光点整块埋在实心皮肤/虹膜内部，到最近的
#    ``a < 250`` 像素实测 44-84px（远大于 ``HOLE_EDGE_MAX = 12``）；蕾丝网是
#    一个连通块，远超 ``HOLE_SIZE_MAX``。而**残留块恰恰相反**：它是"发缝里
#    matting 只做了一半"的产物——缝边已经是半透明，缝内却整块留在 255，
#    实测到半透明像素 3-8px。两张表的差（8px 对 44px）就是 ``HOLE_EDGE_MAX``
#    的取值依据，取 12 落在空档正中。
#    ③ 是第四批实测补上的一条（放烟花段）：这一段的特效是**大片半透明蓝色辉光**，
#    辉光里的亮斑既落在候选里、又被辉光自身（蓝调、且常为 a=255 的核心）包围，
#    1+2 会把它当残留切掉——实测 2947px/帧。区分点在**包围带的实心程度**：真残留
#    嵌在实心发丛里，辉光里的亮斑嵌在半透明场里。
#
# **⚠ 挂起（``HOLE_RESTORE_ENABLED``）**：上面这整套判据只做到"发色包围的灰白
# 实心块"这一步——父代理目视裁定发现它**无法区分抠图残留与白色艺术品**（白色
# 鲸鳍、白蕾丝都长成同一个形状：亮、低饱和、被发色包围、贴着半透明 AA 边）。
# 第四批的真素材全量扫描里那道"实心包围"闸就是这两类被同一把尺子量到的证据。
# 因此工序**默认挂起**（``HOLE_RESTORE_ENABLED = False``，零开销直通），
# 等用户给出精确的残留位置后再重标定（届时很可能要靠位置/连通块归属这类本模块
# 拿不到的信息）。**下列参数现值只是历史记录**，不代表已达成可开启的状态。
# ``HOLE_SIZE_MIN = 8``：1-4px 是单点色度溢出的量级（实测真素材上的零散绿调
# 像素就是 4px），残留核心实测 82-168px；``HOLE_SIZE_MAX = 600``：蕾丝带那种
# 连通结构实测 2000px 以上，600 取在两者之间（宁可漏切小块，绝不切错大块——
# 错杀即失败）。两个阈值作用在不同的量上：``MIN`` 卡的是**命中核心**（判据真正
# 标出来的像素），``MAX`` 卡的是**最终切出去的那一块**（候选掩膜的连通块，
# 见 ``_hole_cut_mask``）——真素材实测核心 168px 落在 224px 的候选连通块里。
HOLE_LUMA_MIN = 150
HOLE_RANGE_MAX = 30
HOLE_WARM_MAX = 12
HOLE_WINDOW = 15
HOLE_SURROUND_MIN = 0.6
HOLE_EDGE_MAX = 12
# 包围带（外扩 HOLE_RING_MAX、再让开贴块的 2 圈）里"不透明"像素的占比下限。
# 依据（真素材全量实测）：**真残留块** 440 个样本的占比落在 0.481-1.00；
# **放烟花段特效辉光里的亮斑**（x > 440，远离角色）179 个样本落在 0.072-0.431。
# 0.50 取在两道分布之间、并有意偏向"不切"那一侧（下侧余量 0.069、上侧 0.019）：
# 代价是 482 帧里有 5 帧（≈1%）的残留块因为占比 0.481-0.499 落在阈值下而漏切，
# 换的是特效辉光基本不被切。这是 v4 里余量最小的一道闸，报告里单独标注为需要
# 目视复核的一条。
HOLE_RING_MAX = 9
HOLE_RING_OPAQUE_MIN = 0.5
# 包围带统计时贴块让出的圈数：``MaxFilter(2×2+1)``（半径 2）以内的像素不计入
_HOLE_RING_SKIRT = 2
HOLE_SIZE_MIN = 8
HOLE_SIZE_MAX = 600
HOLE_HAIR_BLUE_DELTA = 20
HOLE_HAIR_BLUE_MIN = 90
# **工序总开关（默认挂起）**：v4 镂空还原在实机上**误切了白色艺术品**——父代理
# 目视裁定确认白色鲸鳍被切透。根因是判据走到"发色包围的灰白实心块"这一步就再也
# 分不下去了：抠图残留与白色艺术品（鲸鳍、蕾丝）**在这个形状特征上完全同构**
# （亮、低饱和、被蓝调发色包围、贴着半透明 AA 边），上列三层判据只能拦住眼睛
# 高光与巨大连通网这两种，拦不住"被发色包围的中等白色实体"。
#
# 因此现在**默认关闭**：``restore_hair_holes`` 直通返回输入（零开销早退，调用方
# 不做任何特判——``unblend_v3`` 的工序位置与顺序不变，开关一开就是原来的行为）。
# 等用户给出**精确的残留位置**后重标定再开启；重标定大概率要引入本模块拿不到的
# 信息（位置先验 / 连通块归属 / 手工标注），不是调阈值能解决的。
#
# **上面那组 HOLE_* 参数现值只是历史记录**：它们对应"第四批真素材全量扫描通过、
# 但实机目视不通过"的那一版，不代表已达成可开启的状态。开启前必须重新标定。
HOLE_RESTORE_ENABLED = False

# alpha 微缩（腐蚀）像素数，``UNBLEND_ERODE_PX = 1`` ⇒ 3x3 最小值滤波一次。
# 依据：反解 + 收色之后边缘还剩一圈"半透明白雾"（软渐变），再把 alpha 收 1px
# 才能把轮廓环压下去（用户实测：轮廓环亮度 7.1 → 3.4）；2px 会啃到真轮廓，
# 出现剪纸感。
UNBLEND_ERODE_PX = 1
# 阶段二逐帧落盘后缀：写完再 ``os.replace`` 到 ``f_*.webp``——失败留下的半个文件
# 绝不冒充帧（``frames_on_disk`` 只数 ``f_*.webp``，``.part`` 不进帧数账）。
FRAME_PART_SUFFIX = ".part"
# 磁盘准入：剩余空间 < max(FLOOR, 源 webm 大小 × FACTOR) 就不起手（见 provision_once）
DISK_FREE_FLOOR_BYTES = 1 << 30
DISK_SOURCE_FACTOR = 20
# 与 webm_clip._FFMPEG_INPUT_PARAMS 同口径（fps 探测用；转换参数见 _ffmpeg_argv）
_PROBE_INPUT_PARAMS = ["-c:v", "libvpx-vp9", "-threads", "1"]
# 源身份是内容、不是 stat：不做任何按 (mtime_ns, size) 的摘要记忆（见 source_sha256）
# 本进程已采纳的世代目录（供给线程清扫时据此跳过"可能在读"的目录）。
# 不可变快照 + 重绑（不原地改）：跨线程读取无需加锁；单调只增（活 clip 的
# 生命周期不追踪，进程内一注册即保护到底——宁可晚清扫，绝不删在读目录）。
_LIVE_GENERATIONS: frozenset[Path] = frozenset()
_LIVE_LOCK = threading.Lock()
# 本进程已用掉的迁移配额（键 = 素材根的规范路径 → 已用尝试数）。
# **进程生命周期**状态：一次启动 = 一个进程，进程退出即归零（下次启动重新起算）；
# 三宠三库共用同一素材根 ⇒ 共用同一个键的同一份配额。它不是"跨进程持续计数"
# （多进程多宠层已在 Phase 4.4b 删除）：另一实例是另一个进程、另一份预算。
_MIGRATION_USED: dict[str, int] = {}
_MIGRATION_LOCK = threading.Lock()


def provision_disabled() -> bool:
    """PET_FRAMESEQ=0 时整体禁用首跑供给（dev 逃生门，仅读环境变量）。"""
    return os.environ.get(ENV_DISABLE, "").strip() == "0"


def lock_retry_delay_ms(attempt: int, *,
                        base_ms: int = PROVISION_LOCK_RETRY_DELAY_MS,
                        max_ms: int = PROVISION_LOCK_RETRY_MAX_DELAY_MS) -> int:
    """第 ``attempt`` 次（从 1 起）锁重试的退让毫秒数：``base × 2^(n-1)``，封顶。

    纯算术（不碰盘/不排期）：库侧拿它喂 ``QTimer.singleShot`` —— 抢不到 QLockFile
    时要有界重试，但退让必须逐次变大，别把"另一个实例正在转"变成高频轮询。
    """
    step = max(1, int(attempt))
    grown = float(base_ms) * 2.0 ** (step - 1)
    return int(min(grown, float(max_ms)))


# ---------------------------------------------------------------- 源身份 / 世代命名
def _is_hex(text: object, length: int) -> bool:
    text = str(text)
    return len(text) == length and all(c in "0123456789abcdef" for c in text.lower())


def source_sha256(webm: Path | str) -> str | None:
    """源文件 sha256（小写 hex），**每次由内容重算**；不可读/不存在返回 None。

    不做 ``(mtime_ns, size)`` 记忆：stat 值证明不了内容身份——同尺寸 + mtime 复原
    的替换（解压、复制保留时间戳、原地改写）会让 stat 记忆判"没变"，旧世代的戳
    继续被当成当前源身份，于是静默播上一版帧（等于伪造身份凭证）。本模块只认内容：
    调用方要么拿到刚刚重算的结果，要么拿到 None（按「无法确定身份」降级）。

    代价（实测，assets/characters/shenshen/videos 的 11 段热集共 5.45MB ≈12ms
    页缓存热；全量 106 段 53MB ≈百毫秒级，其中 random 独占 45MB）——开销只出现在：
    给待转 clip 命名输出目录、比对「已有世代目录」是否仍属当前源（``plan_clips`` /
    ``rescan_frameseq``，且 ``rescan_frameseq`` 先按目录名筛掉没有世代目录的 clip）。
    供给范围（热集 + random + events）内的 clip 都会算；范围外的目录一个字节不读。
    这两条路径都在 worker 线程或"确有变化"时才跑（见模块线程模型）。
    **只读源 webm，绝不读帧文件内容**（155MB 帧集一个字节都不读）。
    """
    digest = hashlib.sha256()
    try:
        with open(Path(webm), "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def stamp_of(sha: str) -> str:
    """目录名里用的戳：sha256 前 ``GEN_STAMP_HEX`` 位（48 bit 定位精度）。"""
    return str(sha)[:GEN_STAMP_HEX].lower()


def stamp_from_name(name: str) -> str | None:
    """从目录名解析世代戳；不是世代目录名返回 None（含 ``.tmp`` 半成品）。"""
    text = str(name)
    if GEN_INFIX not in text:
        return None
    stem, _, tail = text.rpartition(GEN_INFIX)
    if not stem or not _is_hex(tail, GEN_STAMP_HEX):
        return None
    return tail.lower()


def clip_base_dir(webm: Path | str, videos_dir: Path | str,
                  frameseq_root: Path | str) -> Path:
    """webm 对应的帧序列基础目录：frameseq/<folder>/<stem>（旧版命名，不再发布）。"""
    rel = Path(webm).relative_to(Path(videos_dir)).with_suffix("")
    return Path(frameseq_root) / rel


def clip_out_dir(webm: Path | str, videos_dir: Path | str, frameseq_root: Path | str,
                 *, sha: str | None = None) -> Path:
    """webm 对应的**世代**输出目录：frameseq/<folder>/<stem>.g<戳>/。

    源不可读（sha 拿不到）时回退基础目录名：该路径永不作为世代目录被采纳，
    ``convert_clip`` 也会拒绝向它发布（宁可不转，不造无戳缓存）。
    """
    base = clip_base_dir(webm, videos_dir, frameseq_root)
    digest = sha if sha is not None else source_sha256(webm)
    if not digest:
        return base
    return base.with_name(f"{base.name}{GEN_INFIX}{stamp_of(digest)}")


def hot_webms(videos_dir: Path | str,
              folders: Iterable[str] = HOT_FOLDERS) -> list[Path]:
    """指定目录（默认热集；供给范围用 ``SUPPLY_FOLDERS``）下的全部 webm，按相对路径排序。

    深度不限（``events/balance/*.webm`` 也算），第二级以下只要求第一级目录命中；
    排序与构建期工具同口径，顺序确定（不赌目录枚举顺序）。
    """
    root = Path(videos_dir)
    wanted = {str(f).lower() for f in folders}
    if not root.is_dir():
        return []
    found: list[Path] = []
    for path in root.rglob("*.webm"):
        rel = path.relative_to(root)
        if len(rel.parts) > 1 and rel.parts[0].lower() in wanted:
            found.append(path)
    return sorted(found)


def clip_folder(webm: Path | str, videos_dir: Path | str) -> str:
    """webm 相对 ``videos_dir`` 的第一级目录名（小写）；不在其下/无子目录返回 ``""``。

    供给序与冷集分组只认这一级（``webm`` 不在 ``videos_dir`` 下时不抛，退化成空串 =
    既不是热集也不是冷集）。
    """
    rel = Path(webm)
    try:
        rel = rel.relative_to(Path(videos_dir))
    except ValueError:
        return ""
    return rel.parts[0].lower() if len(rel.parts) > 1 else ""


def is_lossy_clip(webm: Path | str, videos_dir: Path | str,
                  *, folders: Iterable[str] = LOSSY_FOLDERS) -> bool:
    """该 clip 是否属于**冷集目录**（``LOSSY_FOLDERS`` = random/events）：看第一级目录。

    改档后它**不再选择编码档**（全部 clip 都是无损 + 反解 v6）；剩下的含义只有"冷集"
    ——供给序把冷集排在全部热集之后（``supply_priority``）。保留是因为构建期 CLI
    （``tools/convert_frameseq.py``）按它给 ``convert_clip(lossy=...)`` 传参，而那个
    文件不在本批改动范围内：同名同步更新后，本函数与 ``convert_clip`` 的 ``lossy``
    参数可一并删除。
    """
    folder = clip_folder(webm, videos_dir)
    return bool(folder) and folder in {str(f).lower() for f in folders}


def scan_generations(frameseq_root: Path | str) -> dict[Path, list[Path]]:
    """**递归**扫出 ``{基础目录: [该 clip 的全部世代目录]}``（按 mtime 新→旧）。

    只按目录名判定世代（``<stem>.g<戳12>``）：旧版 ``<stem>/``、``*.tmp``、
    任意非世代目录都不入表；键 = ``世代目录.parent / stem``（与 ``clip_base_dir``
    对齐，任意深度都成立——``events/balance/x.g<戳>/`` 的键是
    ``frameseq/events/balance/x``）。**世代目录内部不再下钻**（它只装帧与 meta，
    下钻只会把里面的残留名字当新世代）。**不读 meta、不碰帧内容**——廉价钱筛。
    """
    root = Path(frameseq_root)
    found: dict[Path, list[Path]] = {}
    if not root.is_dir():
        return found
    tail = len(GEN_INFIX) + GEN_STAMP_HEX
    for dirpath, dirnames, _files in os.walk(root):
        for name in list(dirnames):
            if stamp_from_name(name) is None:
                continue
            dirnames.remove(name)                 # 世代目录内部不再下钻
            gen = Path(dirpath) / name
            found.setdefault(gen.parent / name[:-tail], []).append(gen)
    for dirs in found.values():
        dirs.sort(key=_mtime, reverse=True)
    return found


def read_meta(gen_dir: Path | str) -> dict | None:
    """读世代目录的 meta.json；缺失/损坏/不是对象返回 None。"""
    try:
        data = json.loads((Path(gen_dir) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def frames_on_disk(gen_dir: Path | str) -> int:
    """磁盘上的 ``f_*.webp`` 帧数（只数列目录名，不读内容、不构造 Path）。

    一次 ``os.scandir`` 按名字计数（帧目录是扁平结构，上千帧也不解析路径对象）：
    本函数在采纳闸门（GUI 线程 rescan）与转换收尾里都会跑。
    """
    count = 0
    try:
        with os.scandir(gen_dir) as entries:
            for entry in entries:
                name = entry.name
                if name.startswith("f_") and name.endswith(".webp"):
                    count += 1
    except OSError:
        return 0
    return count


def _complete_meta(out_dir: Path | str, *, sha: str | None = None) -> dict | None:
    """通过「身份 + 帧数」全部自洽检查时返回那份 meta，否则 None（档位不在此判）。

    只看带戳 meta 与磁盘帧数：**不读帧内容**（155MB 帧集永不哈希/解码）。档位
    （encoder）是 ``current_generation`` 的额外条件——``is_complete`` 签名语义不变。
    """
    path = Path(out_dir)
    name_stamp = stamp_from_name(path.name)
    if name_stamp is None:
        return None
    meta = read_meta(path)
    if meta is None:
        return None
    stamp = meta.get("source_sha256")
    if not _is_hex(stamp, 64):
        return None
    if not str(stamp).lower().startswith(name_stamp):
        return None
    if sha is not None and str(stamp).lower() != str(sha).lower():
        return None
    frames = meta.get("frames")
    if type(frames) is not int or frames < 1:
        return None
    return meta if frames_on_disk(path) == frames else None


def is_complete(out_dir: Path | str, *, sha: str | None = None) -> bool:
    """世代目录是否**可采纳**：带戳 meta + 名字戳自洽 + 帧数与磁盘一致。

    - 旧版无戳产物（meta 缺 ``source_sha256``）= 无身份凭证 → False（回退 WebM）；
    - ``<stem>.g<戳>.tmp/`` 半成品 → False（名字解析即拒）；
    - 给了 ``sha``（当前源身份）则要求 meta 戳 == 它：源换版 ⇒ False；
    - 帧数只看 meta 与磁盘计数，**不读帧内容**（155MB 帧集永不哈希/解码）。

    不含档位判定（见 ``current_generation``）：本谓词只回答"这份产物身份与帧数
    自洽吗"，调用方按自己的口径再叠加条件。
    """
    return _complete_meta(out_dir, sha=sha) is not None


def current_generation(webm: Path | str, videos_dir: Path | str,
                       frameseq_root: Path | str) -> Path | None:
    """当前源对应的**可用世代目录**；无戳/过期/帧数不符/**档位不符**/源不可读 → None。

    生产侧（``plan_clips``）与消费侧（``MovieLibrary.rescan_frameseq``）共用
    本谓词——"什么算当前源的帧序列缓存"只有一处定义。档位也是采纳条件：``meta``
    的 encoder 必须等于当前档位串（``ENCODER_DESC`` = 无损 + 白底反解 v6）——不符说明
    这份产物不是按当前档位产出的（改档前的热集无损 / 冷集 Q80 / 反解 v2 / 反解 v3、
    手工搬运、旧工具），
    不采纳、列进待转。**改档即让全部旧世代走这条路径**：回退 WebM 播放 + 后台按
    新档重新供给，而不是拿旧档产物顶替。
    """
    digest = source_sha256(webm)
    if digest is None:
        return None
    out_dir = clip_out_dir(webm, videos_dir, frameseq_root, sha=digest)
    meta = _complete_meta(out_dir, sha=digest)
    if meta is None or meta.get("encoder") != ENCODER_DESC:
        return None
    return out_dir


def legacy_frames_dir(webm: Path | str, videos_dir: Path | str,
                      frameseq_root: Path | str) -> Path | None:
    """同 clip 的**旧版无戳产物**目录 ``frameseq/<folder>/<stem>/``（升级形态旁证）。

    它回答的只有一个问题："这个 clip 是否已有一份等价缓存（播放不降级）" → 有则
    迁移不急，按启动预算慢慢来（``provision_once``）；没有（全新安装/新素材）则
    帧序列是唯一来源，照常转——**不预设一轮转完**（仍受起手上限约束，未起手的留到
    下次启动）。**它既不是身份凭证，也不在世代命名空间内**：
    不采纳、不补戳、不改写、不删——身份只看带戳 meta（``is_complete``）。

    判定只 stat + 列一两个目录项：**不读旧 meta、不读帧内容**（旧 153MB 帧集一个
    字节都不碰）。
    """
    base = clip_base_dir(webm, videos_dir, frameseq_root)
    try:
        if not base.is_dir():
            return None
        if next(base.glob("f_*.webp"), None) is None:
            return None
    except OSError:
        return None
    return base


def supply_priority(webm: Path | str, videos_dir: Path | str) -> tuple[int, str]:
    """供给序键 ``(组, 相对路径)``：idle=0、move=1、其余热集=2、events=3、random=4。

    ``hot_webms`` 保持"按相对路径排序"（构建期工具与库扫描共用同一顺序、行为不变），
    供给序在 ``plan_clips`` 里落：迁移配额一个启动周期只有 1 个，字典序会让 click
    长期霸占这个名额（审计结论），而 idle/move 是播放时长占比最高的两类。冷集
    （random/events）排在**全部**热集之后、events 在 random 之前：热集缺一段的代价
    （该段回退 WebM 播放）远大于冷集缺一段，而 events 的屏幕时间又高于 random。
    确定性来自键的后半截（相对路径）——不赌目录枚举顺序。

    ``webm`` 不在 ``videos_dir`` 下（异常输入）时不抛：退化成整条路径排序。
    """
    rel = Path(webm)
    try:
        rel = rel.relative_to(Path(videos_dir))
    except ValueError:
        pass
    folder = rel.parts[0].lower() if len(rel.parts) > 1 else ""
    if folder in LOSSY_PRIORITY and folder not in SUPPLY_PRIORITY:
        # 冷集排在全部热集之后：events 又排在 random 之前
        rank = len(SUPPLY_PRIORITY) + 1 + LOSSY_PRIORITY.index(folder)
    else:
        try:
            rank = SUPPLY_PRIORITY.index(folder)
        except ValueError:
            rank = len(SUPPLY_PRIORITY)          # 其余热集 / 无目录 / 范围外目录
    return rank, rel.as_posix()


def plan_clips(videos_dir: Path | str, frameseq_root: Path | str,
               *, folders: Iterable[str] = SUPPLY_FOLDERS) -> list[tuple[Path, Path]]:
    """待供给清单（webm → 世代输出目录）：只含「当前源没有可用世代」的 clip。

    默认范围 = 热集 + random + events（``SUPPLY_FOLDERS``）。无世代目录的 clip 直接
    入列，但输出目录名要源身份（``clip_out_dir`` 会算一次源哈希：106 段素材
    53MB ≈百毫秒级，**每次由内容重算，无 stat 记忆**）；该 clip 有世代目录时才做
    强身份比对（同一份源哈希）+ 档位比对（``current_generation``，档位不符 = 待转）。
    空 = 供给范围内全部 clip 都有当前源的可用世代。

    出列顺序 = **供给序**（``supply_priority``：热集 idle → move → 其余 → events →
    random，档内按相对路径）：``provision_once`` 按这个顺序起 clip，迁移配额因此先给
    idle/move（播放时长占比最高的两类），档内的相对路径保证同一素材上每次结果一致。
    """
    root = Path(videos_dir)
    out_root = Path(frameseq_root)
    generations = scan_generations(out_root)
    plan: list[tuple[Path, Path]] = []
    for webm in hot_webms(root, folders):
        base = clip_base_dir(webm, root, out_root)
        if base in generations and current_generation(webm, root, out_root) is not None:
            continue
        plan.append((webm, clip_out_dir(webm, root, out_root)))
    plan.sort(key=lambda pair: supply_priority(pair[0], root))
    return plan


# ------------------------------------------------- 进程内迁移配额（每启动周期）
def migration_slot_key(frameseq_root: Path | str) -> str:
    """配额台账的键：frameseq 根的规范绝对路径（同一素材根 → 同一份配额）。

    ``normcase`` 让 Windows 上的大小写/斜杠差异归一到同一个键：同一个素材目录不许
    因此开出两份配额。``resolve`` 失败（路径形状怪）退化成原字符串，绝不抛。
    """
    try:
        return os.path.normcase(str(Path(frameseq_root).resolve()))
    except OSError:
        return os.path.normcase(str(frameseq_root))


def claim_migration_slot(frameseq_root: Path | str, *,
                         limit: int = MIGRATION_CLIPS_PER_ROUND) -> bool:
    """扣一个迁移配额：还有余量则记一笔返回 True，用完返回 False（不改账）。

    **进程内共享**（见模块文档"每轮供给预算"）：三宠三库在同一个素材根上各跑一轮
    时合计只迁 ``limit`` 个，而不是每库各迁 ``limit`` 个。台账是进程生命周期状态、
    不是跨进程计数——另一实例是另一个进程、另一份启动预算。
    """
    key = migration_slot_key(frameseq_root)
    with _MIGRATION_LOCK:
        used = _MIGRATION_USED.get(key, 0)
        if used >= max(0, int(limit)):
            return False
        _MIGRATION_USED[key] = used + 1
        return True


def reset_migration_slots() -> None:
    """清空进程内迁移台账（测试隔离/维护口；产品路径无需调用——进程退出即归零）。"""
    with _MIGRATION_LOCK:
        _MIGRATION_USED.clear()


# ---------------------------------------------------------------- 在用世代保护
def protect_generations(dirs: Iterable[Path | str]) -> None:
    """登记本进程正在使用的世代目录（清扫据此跳过）。幂等、只增不减。"""
    global _LIVE_GENERATIONS
    new = frozenset(Path(d) for d in dirs)
    if not new:
        return
    with _LIVE_LOCK:
        _LIVE_GENERATIONS = _LIVE_GENERATIONS | new


def live_generations() -> frozenset[Path]:
    """本进程已采纳的世代目录快照（不可变，跨线程安全读）。"""
    return _LIVE_GENERATIONS


def clear_live_generations() -> None:
    """清空在用世代集合（测试隔离/维护口；产品路径无需调用）。"""
    global _LIVE_GENERATIONS
    with _LIVE_LOCK:
        _LIVE_GENERATIONS = frozenset()


# ---------------------------------------------------------------- 世代退役 / 清扫
def retired_marker(gen_dir: Path | str) -> Path:
    """世代目录的退役标记路径（sidecar：目录内容不动，活 reader 不受影响）。"""
    return Path(str(gen_dir) + RETIRED_MARK_SUFFIX)


def mark_retired(gen_dir: Path | str) -> bool:
    """标记世代退役（幂等：已有标记不刷新，保留首次退役时刻）。"""
    marker = retired_marker(gen_dir)
    if marker.exists():
        return False
    payload = {"generation": Path(gen_dir).name, "retired": time.time()}
    try:
        marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return False
    return True


def clear_retired(gen_dir: Path | str) -> bool:
    """退役标记作废：世代**又成了当前世代**（回朝）——它不再是"被取代"状态。

    标记记的是"距上一次离场的时长"，而 sidecar 不会因为源内容回退而消失：留着
    它，这个世代再次离场时，清扫会拿旧龄当"超龄退役"，在离场的同一轮就删掉它
    （宽限期是跨进程唯一缓冲，另一实例里还在读它的 clip 直接踩空）。作废标记 =
    下一次离场重新起算。未标记返回 False。标记只是删除闸门，作废只会推迟删除
    （保守方向）。
    """
    marker = retired_marker(gen_dir)
    if not marker.exists():
        return False
    try:
        marker.unlink()
    except OSError:
        return False
    return True


def retired_age_s(gen_dir: Path | str, *, now: float | None = None) -> float | None:
    """退役时长（秒）：未标记/标记不可读返回 None；未来时刻按 0（保守不清扫）。"""
    try:
        retired_at = retired_marker(gen_dir).stat().st_mtime
    except OSError:
        return None
    return max(0.0, (time.time() if now is None else float(now)) - retired_at)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _current_dirs(videos_dir: Path | str, frameseq_root: Path | str) -> set[Path]:
    """当前源对应的世代目录集合（永不删除：哪怕被误标记 + 超宽限）。

    范围 = ``hot_webms`` 默认范围（热集 + random + events）：冷集的"当前世代"
    同样不能被清扫删掉（源回退到某一版时，那一版正是当前世代）。成本有闸：调用方
    先只 stat 标记，真有待清扫/待作废候选时才走到这里。
    """
    root = Path(frameseq_root)
    dirs: set[Path] = set()
    for webm in hot_webms(videos_dir, SUPPLY_FOLDERS):
        digest = source_sha256(webm)
        if digest is None:
            continue
        dirs.add(clip_out_dir(webm, videos_dir, root, sha=digest))
    return dirs


def stale_current_markers(videos_dir: Path | str,
                          frameseq_root: Path | str) -> list[Path]:
    """**又是当前世代**却还带着退役标记的目录（只读；回朝留下的旧账）。

    源内容退回某一版时，那一版的世代目录重新成为当前世代，但 sidecar 标记不会
    自己消失——它的龄属于"上一次离场"，对现在这次离场无效。只读谓词：GUI 线程
    的供给闸门据此判定"有账要清"（起一轮把标记作废），真的作废在 worker 线程。
    先只 stat 标记（廉价），确实有标记时才去算当前世代（源哈希每次重算内容）。
    """
    marked: list[Path] = []
    for dirs in scan_generations(frameseq_root).values():
        for gen in dirs:
            if retired_marker(gen).exists():
                marked.append(gen)
    if not marked:
        return []
    current = _current_dirs(videos_dir, frameseq_root)
    return [gen for gen in marked if gen in current]


def clear_current_markers(videos_dir: Path | str,
                          frameseq_root: Path | str) -> list[Path]:
    """作废「又是当前世代」的退役标记（worker 线程；返回已作废的目录）。"""
    cleared: list[Path] = []
    for gen in stale_current_markers(videos_dir, frameseq_root):
        if clear_retired(gen):
            cleared.append(gen)
    return cleared


def _retire_candidates(videos_dir: Path | str, frameseq_root: Path | str, *,
                       grace_s: float = RETIRED_GRACE_S,
                       now: float | None = None) -> list[Path]:
    """待清扫世代目录：已标记 + 满宽限；再用「当前世代 + 在用世代」两道闸过滤。

    先只 stat 标记（廉价），真有待清扫候选时才算当前世代（源哈希每次重算内容）
    ——稳态下这个顺序保证没有退役目录时零哈希开销。
    """
    aged: list[Path] = []
    for dirs in scan_generations(frameseq_root).values():
        for gen in dirs:
            age = retired_age_s(gen, now=now)
            if age is not None and age >= grace_s:
                aged.append(gen)
    if not aged:
        return []
    keep = _current_dirs(videos_dir, frameseq_root)
    live = live_generations()
    return [gen for gen in aged if gen not in keep and gen not in live]


def pending_retirements(videos_dir: Path | str, frameseq_root: Path | str, *,
                        grace_s: float = RETIRED_GRACE_S,
                        now: float | None = None) -> list[Path]:
    """待清扫的退役世代目录（只读；库的供给闸门据此决定要不要起 worker）。"""
    return _retire_candidates(videos_dir, frameseq_root, grace_s=grace_s, now=now)


def _sweep_stopped(cancelled: Callable[[], bool] | None) -> bool:
    """清扫停机谓词：调用方取消谓词已置位，或会话已结束（issue #111 关机窗口）。"""
    return session_ending() or (cancelled is not None and cancelled())


def _remove_generation_bounded(gen_dir: Path, *,
                               cancelled: Callable[[], bool] | None = None) -> bool:
    """逐条目删除一个退役世代目录；返回是否已删净。

    ``shutil.rmtree`` 一旦进目录就不可中断：关机窗口里"每个目录一个不可中断单位"
    就是库收尾 2s 有界等待的漏洞——线程带着没退完的 I/O 与已收尾的库一起走
    （实测：240 帧 / 15MB 的世代目录一次 rmtree 94ms，12 个候选连续删 1.07s）。
    这里逐条目删（正常形状 = 扁平帧文件 + meta.json，单位操作 = 一次 unlink），
    条目之间复查停机谓词——取消即返回 False，实测 1.8ms 内停手；同目录内的嵌套
    子目录按一个条目原子删（世代目录不该有嵌套，出现即手工残留）。

    未删净 = 调用方不 unlink 退役标记 → 仍是待清扫候选，下一轮从断点续删。删到
    一半的目录既不可采纳（帧数/meta 对不上）又没人在读（满宽限 + 非当前世代 +
    非在用），残留无害。单条目失败（Windows 上活 reader 还占着句柄就是
    ``PermissionError``）同样返回 False：宁可晚一轮清扫，绝不越权删在读目录。
    """
    try:
        with os.scandir(gen_dir) as entries:
            items = list(entries)
    except OSError:
        return False
    for entry in items:
        if _sweep_stopped(cancelled):
            return False
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        except OSError:
            logger.debug('退役世代目录删除失败: %s', entry.path, exc_info=True)
            return False
    try:
        os.rmdir(gen_dir)
    except OSError:
        return False
    return True


def sweep_retired(videos_dir: Path | str, frameseq_root: Path | str, *,
                  grace_s: float = RETIRED_GRACE_S,
                  now: float | None = None,
                  cancelled: Callable[[], bool] | None = None) -> list[Path]:
    """删除待清扫的退役世代目录（本模块唯一的破坏性动作；返回已删列表）。

    删除条件（全部满足）：已打退役标记、标记满 ``grace_s``、不是当前源对应的
    世代、不在本进程已采纳集合里。宽限期就是"活 reader 的缓冲"：目录退役时
    可能正被已创建的 FrameSeqClip 读，谁也不能在这一刻删它。

    判龄前先作废「**又成了当前世代**」的退役标记（``clear_current_markers``）：
    它已不是"被取代"状态，回朝前那段旧龄对这一次离场无效——不作废，这个世代就会
    在再次离场的那一轮拿着旧龄被当超龄退役删掉，宽限期（跨进程唯一缓冲）形同虚设。

    ``cancelled`` 是库收尾用的可中断口径：每个目录**开始前**与目录内部**每条目
    之间**都复查它（另加 ``session_ending()``）——关机窗口里毫秒级停手，不必等
    一个上千帧目录删完；未删净的目录保留退役标记，仍是下一轮的候选（续删）。
    """
    clear_current_markers(videos_dir, frameseq_root)
    removed: list[Path] = []
    for gen in _retire_candidates(videos_dir, frameseq_root, grace_s=grace_s, now=now):
        if _sweep_stopped(cancelled):
            break
        if not _remove_generation_bounded(gen, cancelled=cancelled):
            continue
        try:
            retired_marker(gen).unlink()
        except OSError:
            pass
        removed.append(gen)
    return removed


def _retire_superseded(webm: Path | str, videos_dir: Path | str,
                       frameseq_root: Path | str, *, keep_dir: Path | str,
                       now: float | None = None,
                       cancelled: Callable[[], bool] | None = None) -> list[Path]:
    """新世代发布后收口旧世代：先打退役标记，再清扫已满宽限期的（返回已删列表）。

    ``cancelled`` 原样透传给清扫：这一轮的中途清扫与轮头那次清扫同为关机窗口里
    的阻塞点，必须一样能停手。
    """
    base = clip_base_dir(webm, videos_dir, frameseq_root)
    for gen in scan_generations(frameseq_root).get(base, []):
        if gen != Path(keep_dir):
            mark_retired(gen)
    return sweep_retired(videos_dir, frameseq_root, now=now, cancelled=cancelled)


# ---------------------------------------------------------------- 失败退避（跨启动）
def backoff_key(webm: Path | str, videos_dir: Path | str,
                frameseq_root: Path | str) -> str:
    """退避表的键：clip 在 frameseq 根下的相对路径（``idle/待机呼吸休闲``）。

    按位置而不是世代命名——退避记的是"这个 clip 在这台机器上转不出来"，源换版
    不等于换了个 clip（最坏多等一个退避窗口，不会永久拉黑）。
    """
    base = clip_base_dir(webm, videos_dir, frameseq_root)
    return base.relative_to(Path(frameseq_root)).as_posix()


def read_backoff(frameseq_root: Path | str) -> dict[str, dict]:
    """读退避表 ``{clip 键: {"failures": n, "retry_after": 墙钟秒}}``。

    缺失/损坏/形状不对一律当空表——供给绝不能被一个坏 sidecar 卡死（代价是那个
    clip 多做一次无谓尝试，远小于"帧序列永远不转"）。
    """
    try:
        data = json.loads(
            (Path(frameseq_root) / BACKOFF_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    clips = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(clips, dict):
        return {}
    return {str(key): dict(entry)
            for key, entry in clips.items() if isinstance(entry, dict)}


def write_backoff(frameseq_root: Path | str, table: dict[str, dict]) -> bool:
    """原子写退避表：成功/空表返回 True；空表 = 删 sidecar（不留常驻文件）。"""
    path = Path(frameseq_root) / BACKOFF_NAME
    if not table:
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        tmp.write_text(json.dumps({"version": 1, "clips": table},
                                  ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def backoff_remaining(table: dict[str, dict], key: str, *,
                      now: float | None = None) -> float:
    """键上还剩多少秒退避（0 = 现在可转）。``now`` 用**墙钟**（跨进程/跨启动语义）。"""
    entry = table.get(key)
    if not isinstance(entry, dict):
        return 0.0
    try:
        retry_after = float(entry.get("retry_after") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, retry_after - (time.time() if now is None else float(now)))


def note_failure(table: dict[str, dict], key: str, error: object = "", *,
                 now: float | None = None) -> float:
    """记一次失败：``failures += 1``，退避 ``BASE * 2^(n-1)`` 封顶 ``MAX``；返回等待秒数。

    指数步数上限 32 步再封顶：长期反复失败的键不会把乘法做成溢出。
    """
    stamp = time.time() if now is None else float(now)
    entry = table.get(key)
    try:
        failures = int((entry or {}).get("failures") or 0) + 1
    except (AttributeError, TypeError, ValueError):
        failures = 1
    delay = min(BACKOFF_BASE_S * float(2 ** min(max(failures - 1, 0), 32)),
                BACKOFF_MAX_S)
    table[str(key)] = {
        "failures": failures,
        "retry_after": stamp + delay,
        "error": _err_text(error)[:120],
    }
    return delay


def note_success(table: dict[str, dict], key: str) -> bool:
    """记一次成功：销账（返回是否真的删了条目；退避不许变成永久拉黑）。"""
    return table.pop(str(key), None) is not None


def clear_backoff(frameseq_root: Path | str) -> None:
    """删掉退避 sidecar（测试隔离/维护口；产品路径无需调用）。"""
    write_backoff(frameseq_root, {})


# ---------------------------------------------------------------- 探测 / 进程
def probe_fps(webm: Path | str) -> float:
    """用 imageio_ffmpeg.read_frames 的 meta 探测 fps（运行时禁用 ffprobe）。

    只取首个 meta 即 close（不消费帧），失败/异常回退 DEFAULT_FPS。
    """
    if imageio_ffmpeg is None or session_ending():
        return DEFAULT_FPS
    gen = None
    try:
        gen = imageio_ffmpeg.read_frames(
            str(webm), pix_fmt="bgra", bits_per_pixel=32,
            input_params=list(_PROBE_INPUT_PARAMS),
        )
        meta = next(gen)
        fps = float(meta.get("fps") or 0.0)
        return fps if fps > 0 else DEFAULT_FPS
    except Exception:
        return DEFAULT_FPS
    finally:
        if gen is not None:
            try:
                gen.close()
            except Exception:
                pass


def ffmpeg_exe() -> str | None:
    """imageio_ffmpeg 自带 exe；不可用/会话结束（issue #111）时返回 None。"""
    if imageio_ffmpeg is None or session_ending():
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _creation_flags() -> int:
    """Windows：ffmpeg 低于正常优先级 + 不建控制台窗口（POSIX 忽略，返回 0）。

    打包产物是 --windowed（无控制台），派生 ffmpeg.exe 这类控制台程序不带
    CREATE_NO_WINDOW 时系统会新建可见的命令行窗口，首跑供给期间反复闪窗。
    """
    if os.name != "nt":
        return 0
    return (int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
            | int(getattr(subprocess, "CREATE_NO_WINDOW", 0)))


def _ffmpeg_argv(exe: str, webm: Path, tmp_dir: Path) -> list[str]:
    """阶段一固化参数（任何一项都来自 FEASIBILITY.md 的实测坑，禁止改动）。

    **永远无损**（``-lossless 1``）：这一趟只负责把源像素原样抓下来，有损编码在
    阶段二（``unblend_frames_in_place``）——先有损再反解会把白晕连量化噪声一起
    固化，反解就白做了。解码器、像素格式一律不动（``-pix_fmt bgra`` 是 alpha 存活
    的前提，``-lossless 1`` 保住阶段二的输入是原样像素）。
    """
    return [
        exe,
        "-nostdin", "-v", "error", "-y",
        # 解码器强制 libvpx：原生 vp9 解码器静默丢弃 VP9 alpha 位流
        "-c:v", "libvpx-vp9",
        "-i", str(webm),
        # bgra 直通：默认 yuva420p 即使 -lossless 1 也 chroma 下采样
        "-pix_fmt", "bgra", "-c:v", "libwebp", "-lossless", "1",
        str(tmp_dir / "f_%04d.webp"),
    ]


# -------------------------------------------- 阶段二：白底反解 v6 + 无损重编码
def _pil():
    """懒载 Pillow：``(Image, ImageMath, ImageFilter, ImageChops)``。

    PIL 不随本模块导入常驻（模块在启动路径上被 library 导入，见
    docs/OPTIMIZATION_CHECKLIST.md 的"不加载 Pillow"口径）；缺失时抛 ``ImportError``，
    由 ``unblend_frames_in_place`` 收口成「本段进不了阶段二」。
    """
    from PIL import Image, ImageChops, ImageFilter, ImageMath
    return Image, ImageMath, ImageFilter, ImageChops


def unblend_rgba(im):
    """白底反解一帧 RGBA（纯函数，不改入参）：``rec = clip((stored - 255×(1-a)) / a)``。

    素材在白底上抠图：半透明像素的存值 = ``角色色 × a + 255 × (1-a)``，所以反解
    就是把混进去的那份白按 alpha 除回去。性质：拿**白底**重新合成这份反解结果与
    原图逐像素等价（屏幕上观感不变），换深色底时白晕消失。

    两端不反解（都是硬口径，见 ``UNBLEND_MIN_ALPHA`` / ``UNBLEND_MAX_ALPHA`` 的
    注释）：``a < 21`` 与 ``a >= 250`` 保持原样。**alpha 一个位都不动**——A band
    原样并回，有损编码那边实测 alpha 也是逐像素无损的（libwebp 的 alpha 通道走
    无损位流）。

    精度：反解误差上界 = ``255 × 0.5 / a`` 色阶（存值本来就是整数，除以 a 把它
    放大）——a=21 时 ≈6 阶、a≥64 时 ≤2 阶。越透明越不准，但越透明越看不清：反解
    的价值全在边带（a 几十到两百）那一圈，白晕在那里最明显。

    实现用 ``ImageMath``（C 逐像素，32 位整数模式）：这是 Pillow 里唯一能表达
    "逐像素除以另一张图"的原语——``ImageChops`` 不支持 ``I`` 模式（实测
    ``ValueError: image has wrong mode``），8 位 L 模式放不下反解需要的中间精度
    （``stored × 255`` 超过 255）。``255-a`` 与除数各算一次、3 个通道共用，
    640×360 单帧实测 12.2ms（Pillow 12.2 / Python 3.13；拆解见 PR 报告）。

    本函数是 v3 的**第一步**（``unblend_v3`` 在其后接亮色收色与 alpha 微缩）——
    口径仍是"按 alpha 除白"，别把后面两步的逻辑往这里塞：这一步的纯函数单测
    （白底合成等价、两端不动、alpha 逐位保持）钉的是除法的正确性。
    """
    Image, ImageMath, _ImageFilter, _ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"unblend_rgba 只接受 RGBA，收到 {im.mode}")
    red, green, blue, alpha = im.split()
    size = im.size
    alpha_i = alpha.convert("I")
    level = Image.new("I", size, 255)
    # 混进去的白人份（255 - a）与除数各算一次，3 个通道共用
    white = ImageMath.lambda_eval(
        lambda d: d["level"] - d["alpha"], level=level, alpha=alpha_i)
    # 除数下限 1：a < UNBLEND_MIN_ALPHA 的像素会被掩膜挡在结果之外，这里只为避开除零
    denom = ImageMath.lambda_eval(
        lambda d: d["max"](d["alpha"], d["one"]),
        alpha=alpha_i, one=Image.new("I", size, 1))
    fixed = [
        ImageMath.lambda_eval(
            lambda d: (d["c"] - d["w"]) * d["level"] / d["d"],
            c=band.convert("I"), w=white, level=level, d=denom,
        ).convert("L")          # I→L 的 convert 顺带把 <0 / >255 裁到 0 / 255
        for band in (red, green, blue)
    ]
    mask = alpha.point(
        lambda v: 255 if UNBLEND_MIN_ALPHA <= v < UNBLEND_MAX_ALPHA else 0)
    return Image.merge("RGBA", (*Image.composite(
        Image.merge("RGB", fixed), im.convert("RGB"), mask).split(), alpha))


def luma_band(rgb):
    """Rec.601 整数亮度（L 图）：``(299R + 587G + 114B) / 1000``（整除）。

    逐像素口径**只有一处定义**——收色的判据（``own > neighbor + 30``）与单测里
    "修后不许异常变亮"的度量必须是同一个量，否则两边各写一份公式就是各说各话。
    ``ImageMath`` 里两整数相除是整除（实测 ``22×255/28 = 200``，不会升成浮点），
    所以整条链都在整数域：同样的输入任何机器上给同样的结果。
    """
    Image, ImageMath, _ImageFilter, _ImageChops = _pil()
    red, green, blue = rgb.split()
    size = rgb.size
    return ImageMath.lambda_eval(
        lambda d: (d["r"] * 299 + d["g"] * 587 + d["b"] * 114) / d["k"],
        r=red.convert("I"), g=green.convert("I"), b=blue.convert("I"),
        k=Image.new("I", size, 1000),
    ).convert("L")              # I→L 顺带把 >255 裁到 255（luma 上界本来就是 255）


def opaque_seed(alpha):
    """不透明种子掩膜（L，0/255）：``a >= UNBLEND_MAX_ALPHA`` 的像素。

    与 ``unblend_rgba`` 的上端同一个阈值（同一个常量的两种用法）：那里是"不反解"，
    这里是"可以当邻居的信源"。同口径很重要——a ≥ 250 的存值里白人份 ≤2%，它们的
    颜色本来就是角色色，直接拿来当邻居颜色是安全的；而且收色要修的那些像素
    （a < 128）与种子（a ≥ 250）在 alpha 上互不相交，改色不会污染信源。
    """
    _Image, _ImageMath, _ImageFilter, _ImageChops = _pil()
    return alpha.point(lambda v: 255 if v >= UNBLEND_MAX_ALPHA else 0)


def pull_band(alpha):
    """收色带掩膜（L，0/255）：``UNBLEND_PULL_MIN_ALPHA <= a < UNBLEND_PULL_MAX_ALPHA``。

    单独成函数是因为它有两个用途：收色判据的第二项，以及 ``unblend_v3`` 的**裁剪
    依据**——带外的像素一个都不会被收色（判据第一项就不成立），所以只需要在这一带
    的包围盒（外扩扩散半径）里跑扩散。
    """
    _Image, _ImageMath, _ImageFilter, _ImageChops = _pil()
    return alpha.point(
        lambda v: 255 if UNBLEND_PULL_MIN_ALPHA <= v < UNBLEND_PULL_MAX_ALPHA else 0)


def _reach_mask(den):
    """可达掩膜（L，0/255）：归一化分母 > 0 的像素（窗口里有已知像素）。"""
    return den.point(lambda v: 255 if v > 0 else 0)


def neighbor_color_map(im, *, rounds: int = UNBLEND_DIFFUSE_ROUNDS):
    """``(邻居颜色, 可达掩膜)``：把不透明像素的颜色逐轮渗进半透明区（纯函数）。

    逐轮语义（``rounds`` = ``UNBLEND_DIFFUSE_ROUNDS``）：种子 = 不透明像素
    （``opaque_seed``）；每轮 ``num = BoxBlur(已知区颜色)``、
    ``den = BoxBlur(覆盖掩膜)``、估计 = ``num × 255 / den``（归一化盒滤波 = 窗口内
    已知像素颜色的均值），把估计值写进**可达**（``den > 0``）像素并把它并入覆盖
    掩膜，下一轮由它们继续往外渗。``rounds`` 轮后覆盖掩膜 = "距不透明区 ≤ rounds
    圈（切比雪夫距离）"的集合。

    **每轮都归一化**是关键：渗出去的值始终是颜色本身（0-255 量级）。若改成"每轮只
    盒滤波、最后一次性除以权重"（不做归一化），几轮之后累加和会衰减到个位数，8 位
    精度直接把最外圈的估计毁掉（深色邻域估成 0 = 凭空变黑）。

    返回值：颜色在**可达掩膜**为 255 的像素上有效（其余是 0）。可达掩膜是必需的第二
    个返回值——0 既可表示"黑"也可表示"窗口里根本没有不透明像素"，收色判据必须能把
    这两者分开，否则会把没有邻居的像素"收成黑色"。
    """
    Image, ImageMath, ImageFilter, ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"neighbor_color_map 只接受 RGBA，收到 {im.mode}")
    size = im.size
    covered = opaque_seed(im.split()[3])
    # 已知区：只有不透明像素带颜色，其余为 0（0 在这里是"未知"，不是"黑"）
    known = ImageChops.multiply(
        im.convert("RGB"), Image.merge("RGB", (covered, covered, covered)))
    level = Image.new("I", size, 255)
    one = Image.new("I", size, 1)
    for _ in range(max(0, int(rounds))):
        num = known.filter(ImageFilter.BoxBlur(1))      # 已知像素颜色之和 / N
        den = covered.filter(ImageFilter.BoxBlur(1))    # 已知像素数 / N × 255
        est = Image.merge("RGB", [
            ImageMath.lambda_eval(
                # 除数下限 1：den == 0 的像素（窗口里没有已知像素）会被可达掩膜挡住，
                # 这里只为避开除零
                lambda d: d["n"] * d["level"] / d["max"](d["den"], d["one"]),
                n=band.convert("I"), level=level, den=den, one=one,
            ).convert("L")
            for band in num.split()
        ])
        reach = _reach_mask(den)
        known = Image.composite(est, known, reach)      # 可达像素取估计值
        covered = ImageChops.lighter(covered, reach)    # 它们成为下一轮的已知信源
    return known, covered


def pull_mask(im, neighbor, reach):
    """收色掩膜（L，0/255）：三件事同时成立才 255，缺一不动。

    1. ``UNBLEND_PULL_MIN_ALPHA <= a < UNBLEND_PULL_MAX_ALPHA``（``pull_band``）；
    2. ``luma(本像素) > luma(邻居) + UNBLEND_EXCESS_LUMA``；
    3. 邻居可知（``reach``）：窗口里真有不透明像素，**不是** 0 兜底。

    判据用的是 ``luma_band``（Rec.601 整数亮度），与单测里"修后不许异常变亮"的
    度量同一个量——判据与验收各写一份公式就是各说各话。
    """
    Image, ImageMath, _ImageFilter, ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"pull_mask 只接受 RGBA，收到 {im.mode}")
    size = im.size
    hot = ImageMath.lambda_eval(
        lambda d: (d["own"] > d["neigh"] + d["excess"]) * d["level"],
        own=luma_band(im.convert("RGB")), neigh=luma_band(neighbor),
        excess=Image.new("I", size, UNBLEND_EXCESS_LUMA),
        level=Image.new("I", size, 255),
    ).convert("L")
    return ImageChops.multiply(
        ImageChops.multiply(pull_band(im.split()[3]), hot), reach)


def pull_white_contamination(im, neighbor, mask):
    """把 ``mask`` 为 255 的像素颜色替换成 ``neighbor``（alpha 一位不动，纯函数）。

    判据与替换色**同源**（都来自 ``neighbor_color_map`` 那一张图），所以效果方向是
    定死的：只有 ``own 亮度 > neighbor 亮度 + 30`` 的像素才会被替换，新亮度 = 邻居
    亮度 < 旧亮度 − 30 ⇒ **收色只会变暗**。"修后不许异常变亮（>30）"因此不是靠事后
    检查兜住的，是这条替换规则本身的性质（单测按逐像素不变量钉住）。
    """
    Image, _ImageMath, _ImageFilter, _ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"pull_white_contamination 只接受 RGBA，收到 {im.mode}")
    if mask.getbbox() is None:              # 整帧无事（白艺术品 / 干净素材）
        return im
    return Image.merge("RGBA", (*Image.composite(
        neighbor, im.convert("RGB"), mask).split(), im.split()[3]))


def erode_alpha(im, *, px: int = UNBLEND_ERODE_PX):
    """alpha 微缩 ``px`` 像素（3x3 最小值滤波一次；RGB 一位不动，纯函数）。

    收色之后边缘还剩一圈"软白渐变"（半透明像素叠在白底上仍然发白），把 alpha 收
    1px 能把它压掉（用户实测：轮廓环亮度 7.1 → 3.4）。只动 A band：像素变透明之后
    它的 RGB 已不可见，改 RGB 只会引入无谓的色差（而有损编码里那部分字节本来也不保）。

    边缘语义 = **edge pad**：Pillow 的秩滤波在边界处把窗口裁到图像内，等价于先按
    边缘复制再取 min（被复制进来的像素就是边界像素自己，取 min 不改变结果）。
    """
    Image, _ImageMath, ImageFilter, _ImageChops = _pil()
    if int(px) <= 0:
        return im
    red, green, blue, alpha = im.split()
    kernel = 2 * int(px) + 1
    return Image.merge("RGBA", (red, green, blue, alpha.filter(ImageFilter.MinFilter(kernel))))


# ------------------------------------------------- 白底反解 v5：alpha 底板清理
# 微缩之后还剩一类像素：**整块底板的 ``a = 1``**。它不是轮廓上的渐隐，而是抠图时
# 背景整块留下的"几乎全透明"值——实测源 webm 与 v3 产物上占 640×360 的 ≈82%。
# 它够不到的两个理由：① ``erode_alpha`` 取的是 3×3 最小值，只能啃轮廓外圈 1px，
# 底板内部的 1 是稳定的（邻域全是 1）；② 反解/收色的口径都建立在"半透明带"或
# "``a >= 250`` 的实心块"上，``a = 1`` 两边都不在。
#
# 屏幕上它就是 ``dst × 254/255``：白底 255 → **254**（整块矩形比背景暗一档，
# 用户报的"白底整块色差"），深色底各自按比例压暗一档。清成 ``a = 0`` 即可
# 完全复原背景。判据是**恰好等于** ``FAINT_ALPHA``（不是"<= 某阈值"）：
# ``a = 0`` 本来就透明、``a >= 2`` 承载着真实的半透明过渡（发丝/AA 边），
# 一个都不许动。
FAINT_ALPHA = 1


def faint_alpha_mask(alpha):
    """``a`` **恰好等于** ``FAINT_ALPHA`` 的像素掩膜（L，0/255，纯函数）。

    单独成函数是因为它是"清哪些像素"的唯一判据：清理与单测的两侧断言共用它
    （``a = 1`` 命中、0 与 2..255 全不命中），口径只有一处。
    """
    _Image, _ImageMath, _ImageFilter, _ImageChops = _pil()
    return alpha.point(lambda v: 255 if v == FAINT_ALPHA else 0)


def floor_faint_alpha(im):
    """alpha 底板清理：``a == FAINT_ALPHA`` 的像素清成全透明（纯函数）。

    依据、判据与屏幕观感见上面那组常量注释：``a = 1`` 的底板叠在任何背景上都是
    ``dst × 254/255``（白底 255 → 254），清成 0 之后背景逐位复原。只动 ``a == 1``：
    ``a = 0`` 原样、``a >= 2`` 一个位不动（**颜色也不动**——见下）。

    归零像素的 RGB 同时置 0：它们已经全透明（屏幕上看不见任何颜色），而存值本身
    必须自洽——"alpha=0 的像素 RGB 必须是 0"是预乘透明的读法，置 0 不会在任何
    实现里漏色，置 255 则会在"把 RGB 直接当颜色用"的实现里漏出一圈白（**不用 255**）。
    实测这一步对绝大多数像素是空操作（底板本来就被编码器/源存成 ``(0,0,0,1)``）：
    真素材上 ``a = 1`` 的像素里 99.0-99.5% 的 RGB 已经是 0，只有微缩环上那 ≈0.5%
    带着非 0 颜色（最大 ≈170），置 0 正是为它们准备的。

    位置 = ``unblend_v3`` 的**最后一步**（微缩之后、编码之前）。与放在微缩之前
    **逐位等价**：清理把 0/1 都映射成 0（``F(0) = F(1) = 0``，其余不变）是单调不增的，
    而微缩取的是 3×3 最小值（``min F(x) = F(min x)``）；放在最后是因为它产出的
    就是发布出去的像素，WebP 的 alpha 走无损位流（实测编码→解码后 ``a == 1`` 仍是 0）。

    没有 ``a == 1`` 的帧**原对象返回**（稳态零成本）；非 RGBA 输入当场报错
    （工序契约：调用点喂进来的坏输入必须在这里炸，不许静默直通）。
    """
    Image, _ImageMath, _ImageFilter, ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"floor_faint_alpha 只接受 RGBA，收到 {im.mode}")
    _red, _green, _blue, alpha = im.split()
    faint = faint_alpha_mask(alpha)
    if faint.getbbox() is None:
        return im
    colors = im.convert("RGB")          # convert 是拷贝：不要就地改入参
    colors.paste((0, 0, 0), (0, 0), faint)
    return Image.merge("RGBA", (*colors.split(), ImageChops.subtract(alpha, faint)))


def hole_candidate_mask(im):
    """近白灰**实心**像素掩膜（L，0/255）：``a >= 250`` 且亮度 > ``HOLE_LUMA_MIN``
    且通道极差 < ``HOLE_RANGE_MAX`` 且 ``r - b <= HOLE_WARM_MAX``（纯函数，判据见
    模块常量注释）。

    逐像素口径**只有一处定义**：判定、单测夹具的两侧断言、真素材扫描共用它。
    实现全在 Pillow 的 C 原语上（``lighter``/``darker``/``subtract``/``point``）：
    640×360 单帧 7 次带状运算，没有一次逐像素 Python 循环。
    """
    Image, _ImageMath, _ImageFilter, ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"hole_candidate_mask 只接受 RGBA，收到 {im.mode}")
    rgb = im.convert("RGB")
    red, green, blue = rgb.split()
    # 通道极差 = max(R,G,B) - min(R,G,B)（两个 lighter/darker 各一次，3 通道共用）
    top = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    bottom = ImageChops.darker(ImageChops.darker(red, green), blue)
    light = luma_band(rgb).point(lambda v: 255 if v > HOLE_LUMA_MIN else 0)
    low_sat = ImageChops.subtract(top, bottom).point(
        lambda v: 255 if v < HOLE_RANGE_MAX else 0)
    # 墙色不是肤色：残留实测 r - b <= +6，皮肤实测 >= +25。减法饱和到 0，
    # 偏冷的像素（b > r）天然全部通过。
    not_warm = ImageChops.subtract(red, blue).point(
        lambda v: 255 if v <= HOLE_WARM_MAX else 0)
    # 与反解上端同一个常量：``a >= UNBLEND_MAX_ALPHA`` 才是"实心"
    # （``a < 250`` 是收色/微缩的地盘，v4 一个像素都不碰）
    opaque = im.split()[3].point(
        lambda v: 255 if v >= UNBLEND_MAX_ALPHA else 0)
    return ImageChops.multiply(
        ImageChops.multiply(ImageChops.multiply(light, low_sat), not_warm),
        opaque)


def hair_blue_mask(im):
    """蓝调发色掩膜（L，0/255）：``a >= 250`` 且 ``b > r + HOLE_HAIR_BLUE_DELTA``
    且 ``b > HOLE_HAIR_BLUE_MIN``（纯函数）。

    它只回答"这个像素是不是**发色**"——包围性判据的信源。用蓝调而不是"深色"
    是因为深色在角色身上到处都是（睫毛、衣褶、眼睛），会把包围性稀释成"四周
    不是背景"；发色的蓝调在墙色灰白上是明确的负判（实测墙色 ``b - r = -1``）。
    """
    _Image, _ImageMath, _ImageFilter, ImageChops = _pil()
    if im.mode != "RGBA":
        raise ValueError(f"hair_blue_mask 只接受 RGBA，收到 {im.mode}")
    red, _green, blue, alpha = im.split()
    bluer = ImageChops.subtract(blue, red).point(
        lambda v: 255 if v > HOLE_HAIR_BLUE_DELTA else 0)
    deep = blue.point(lambda v: 255 if v > HOLE_HAIR_BLUE_MIN else 0)
    opaque = alpha.point(lambda v: 255 if v >= UNBLEND_MAX_ALPHA else 0)
    return ImageChops.multiply(ImageChops.multiply(bluer, deep), opaque)


def surround_mask(hair):
    """邻域发色占比 >= ``HOLE_SURROUND_MIN`` 的掩膜（L，0/255，纯函数）。

    占比用 ``BoxBlur((HOLE_WINDOW - 1) // 2)`` 一次算出来：Pillow 的盒滤波在
    L 图上就是窗口均值（除数固定为核面积、越界按**边缘复制**补齐——与
    ``erode_alpha`` 的边界口径同一套），半径 7 正好是 15×15。判据取
    ``>`` 阈值 ``round(HOLE_SURROUND_MIN × 255)``：8 位量化下 0.6 就是 153。
    """
    _Image, _ImageMath, ImageFilter, _ImageChops = _pil()
    if hair.mode != "L":
        raise ValueError(f"surround_mask 只接受 L，收到 {hair.mode}")
    radius = (HOLE_WINDOW - 1) // 2
    level = round(HOLE_SURROUND_MIN * 255)
    return hair.filter(ImageFilter.BoxBlur(radius)).point(
        lambda v: 255 if v >= level else 0)


def _hole_blocks(mask):
    """把掩膜切成 4 连通的块：``[((x0, y0), [(x, y), ...]), ...]``（纯函数）。

    **只在掩膜的包围盒里取像素**：真素材上候选核心只有一百多像素、包围盒几十
    像素见方，整帧取数据是纯浪费；``getbbox()`` 为空时直接返回（无候选帧的
    稳态路径因此零成本）。
    """
    box = mask.getbbox()
    if box is None:
        return []
    tile = mask.crop(box)
    width, height = tile.size
    live = {(index % width, index // width)
            for index, value in enumerate(tile.get_flattened_data()) if value}
    blocks: list[tuple[tuple[int, int], list[tuple[int, int]]]] = []
    while live:
        seed = next(iter(live))
        live.discard(seed)
        stack = [seed]
        block = [seed]
        while stack:
            x, y = stack.pop()
            for neighbour in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if neighbour in live:
                    live.discard(neighbour)
                    stack.append(neighbour)
                    block.append(neighbour)
        blocks.append(((box[0], box[1]), block))
    return blocks


def _hole_cut_mask(candidate, core, alpha):
    """要切透的掩膜（L，0/255，纯函数）：对 ``core`` 里每个连通块跑结构口径。

    三条**块级**条件（顺序 = 从便宜到贵）：

    1. 核心尺寸下限 ``HOLE_SIZE_MIN``：判据至少要标出这么多像素才算"抓到一整块
       残留"（1-4px 是单点色度溢出的量级，实测真素材上就有一撮）；
    2. **贴缝证据**：核心里有像素落在某个 ``a < 250`` 像素的 ``HOLE_EDGE_MAX``
       邻域内（挡眼睛高光/皮肤那种深埋在实心区里的浅色块）；
    3. **实心包围证据**：块的包围带（外扩 ``HOLE_RING_MAX`` 再减掉块自己）里
       不透明像素占比 >= ``HOLE_RING_OPAQUE_MIN``（挡特效辉光里的亮斑——那里
       四周自己在半透明）；
    4. **块粒度**：真正切出去的是**候选掩膜的连通块**（不是逐像素命中的核心——
       逐像素口径对块内部天然不成立：15×15 窗口被块自己占满。实测真素材：核心
       168px，落在 224px 的候选块里），该块超过 ``HOLE_SIZE_MAX`` 就整块放弃
       （挡蕾丝带那种细线织成的巨大连通结构）。

    距离用 ``MinFilter(2 × HOLE_EDGE_MAX + 1)`` 一次算（局部最小 alpha < 250
    ⇔ 25×25 窗口里有非实心像素），**在核心包围盒外扩后的小块上算**——
    真素材 640×360 上一次整帧 ``MinFilter(25)`` 实测 272ms，裁到几十像素见方
    就是 2ms 量级。包围带同理：``MaxFilter(2 × HOLE_RING_MAX + 1)`` 只在该块
    自己外扩后的小块上做。
    """
    Image, _ImageMath, ImageFilter, ImageChops = _pil()
    from PIL import ImageDraw
    size = alpha.size
    window = 2 * HOLE_EDGE_MAX + 1
    ring_window = 2 * HOLE_RING_MAX + 1
    opaque = alpha.point(lambda v: 255 if v >= UNBLEND_MAX_ALPHA else 0)
    cut = Image.new("L", size, 0)
    for origin, block in _hole_blocks(core):
        if len(block) < HOLE_SIZE_MIN:
            continue
        left = min(x for x, _ in block) + origin[0]
        top = min(y for _, y in block) + origin[1]
        right = max(x for x, _ in block) + 1 + origin[0]
        bottom = max(y for _, y in block) + 1 + origin[1]
        area = _pad_box((left, top, right, bottom), size, HOLE_EDGE_MAX)
        local_min = alpha.crop(area).filter(ImageFilter.MinFilter(window))
        if not any(local_min.getpixel((x + origin[0] - area[0],
                                       y + origin[1] - area[1])) < UNBLEND_MAX_ALPHA
                   for x, y in block):
            continue
        # 块粒度 = 候选掩膜里与核心相连的那一整块（floodfill 只在连通块上走，
        # 代价就是这一块的像素数）
        whole = candidate.copy()
        ImageDraw.floodfill(
            whole, (block[0][0] + origin[0], block[0][1] + origin[1]), 128,
            thresh=0)
        whole = whole.point(lambda v: 255 if v == 128 else 0)
        if whole.histogram()[255] > HOLE_SIZE_MAX:
            continue
        if not _surrounded_by_opaque(whole, opaque, ring_window):
            continue
        cut.paste(255, (0, 0), whole)
    return cut


def _surrounded_by_opaque(block, opaque, ring_window):
    """块的包围带里不透明像素占比是否达标（纯函数，见 ``HOLE_RING_OPAQUE_MIN``）。

    包围带 = 块外扩 ``HOLE_RING_MAX`` 像素**再减掉块外扩 2 像素**（贴着块的一圈
    留给 AA 过渡，不参与统计——真残留块的紧邻一圈本来就是半透明的发缝边）。
    只在块自己外扩后的小块上做两次 ``MaxFilter``（真残留块 30x20 量级 ⇒ 1-3ms）。

    实测（真素材全量）：带内不透明占比 —— 真残留块 0.481-1.00（440 样本），
    放烟花段辉光里的亮斑 0.072-0.431（179 样本）。取 0.50 落在中间那道窄缝里。
    """
    Image, _ImageMath, ImageFilter, ImageChops = _pil()
    box = block.getbbox()
    if box is None:
        return False
    area = _pad_box(box, block.size, HOLE_RING_MAX)
    tile = block.crop(area)
    ring = ImageChops.subtract(
        tile.filter(ImageFilter.MaxFilter(ring_window)),
        tile.filter(ImageFilter.MaxFilter(2 * _HOLE_RING_SKIRT + 1)))   # 贴着块的一圈不计
    total = ring.histogram()[255]
    if not total:
        return False
    hit = ImageChops.multiply(ring, opaque.crop(area)).histogram()[255]
    return hit / total >= HOLE_RING_OPAQUE_MIN


def restore_hair_holes(im):
    """镂空还原（纯函数）：把"抠图整块漏掉的背景残留"的 alpha 切到 0，颜色不动。

    **默认挂起**（``HOLE_RESTORE_ENABLED = False``）：实机目视确认它把白色鲸鳍
    一起切了（残留与白色艺术品在"发色包围的灰白实心块"这个形状特征上同构，见
    开关常量的注释）。挂起时**直通返回输入**——零开销，不做掩膜、不载 Pillow，
    调用方（``unblend_v3``）对它没有任何特判，开关翻 True 即恢复原行为。

    模式契约（只接受 RGBA）**在挂起时也一样强制**：那一刻函数仍然是"镂空还原"
    这个工序的入口，收到形状不对的输入就该当场报错，不能因为功能关了就默默把它
    当成直通函数（否则将来打开开关时，调用点喂进来的坏输入会在别处才炸）。

    工序位置 = ``unblend_v3`` 里**收色之后、alpha 微缩之前**（见 ``unblend_v3``）。
    只改 alpha 是硬契约：残留块的颜色是墙色，但"看不见"就够了——改颜色反而在
    有损编码里留下可见的色块（半透明像素的 RGB 在屏幕上会被背景稀释，透明像素
    的 RGB 则完全不显示）。

    裁剪（纯性能手段，不改结果）只有一处：候选掩膜的包围盒外扩
    ``max(窗口半径, 贴缝半径)`` 之后算**逐像素包围**——盒内像素的 15×15 邻域
    完整落在裁剪区内，因此核心掩膜与整帧口径逐像素相同（贴缝距离本来就在更小的
    核心包围盒上算）。块粒度那一步用**整帧**候选掩膜做 floodfill，不裁剪：
    "与核心相连"是拓扑性质，裁一刀就可能把块切断。

    无候选（``hole_candidate_mask`` 包围盒为空）时**原对象返回**：稳态帧零成本。
    """
    if im.mode != "RGBA":
        raise ValueError(f"restore_hair_holes 只接受 RGBA，收到 {im.mode}")
    if not HOLE_RESTORE_ENABLED:            # 挂起：零开销直通（在 _pil() 之前）
        return im
    Image, _ImageMath, _ImageFilter, ImageChops = _pil()
    candidate = hole_candidate_mask(im)
    box = candidate.getbbox()
    if box is None:
        return im
    margin = max((HOLE_WINDOW - 1) // 2, HOLE_EDGE_MAX)
    area = _pad_box(box, im.size, margin)
    tile = im.crop(area)
    core = ImageChops.multiply(
        candidate.crop(area), surround_mask(hair_blue_mask(tile)))
    if core.getbbox() is None:
        return im
    whole = Image.new("L", im.size, 0)
    whole.paste(core, area[:2])
    cut = _hole_cut_mask(candidate, whole, im.split()[3])
    if cut.getbbox() is None:
        return im
    alpha = im.split()[3]
    alpha.paste(0, (0, 0), cut)
    return Image.merge("RGBA", (*im.convert("RGB").split(), alpha))


def _pad_box(box: tuple[int, int, int, int], size: tuple[int, int],
             margin: int) -> tuple[int, int, int, int]:
    """把包围盒外扩 ``margin`` 像素并裁到图像内（收色裁剪用）。"""
    left = max(0, int(box[0]) - int(margin))
    top = max(0, int(box[1]) - int(margin))
    right = min(int(size[0]), int(box[2]) + int(margin))
    bottom = min(int(size[1]), int(box[3]) + int(margin))
    return left, top, right, bottom


def _pull_region(im, box: tuple[int, int, int, int]):
    """在 ``box`` 内跑扩散 + 收色，只把**被收色的像素**贴回整帧（纯函数）。

    收色带外的像素一个都不会动（``pull_mask`` 的第一项），所以扩散没必要在全帧上跑：
    裁剪区 = 收色带包围盒外扩 ``UNBLEND_DIFFUSE_ROUNDS`` 像素——这个外扩量保证"影响
    区内像素的全部不透明种子都在区内"（扩散每轮只往外走 1px），因此区内算出来的邻居
    颜色与全帧算的逐像素相同（单测用"整帧参考实现"钉住这条等价性）。实测裁剪区占
    整帧 27.9%（真素材 640×360：227×283 对 640×360），5 轮扩散从 67.5ms 降到
    19.2ms——阶段二最贵的一笔因此省下七成。
    """
    Image, _ImageMath, _ImageFilter, _ImageChops = _pil()
    tile = im.crop(box)
    neighbor, reach = neighbor_color_map(tile)
    mask = pull_mask(tile, neighbor, reach)
    if mask.getbbox() is None:
        return im
    # 贴回的是 **RGB**、alpha 原样并回：``Image.paste`` 遇到模式不同的源会先把源转成
    # 目标模式（RGB→RGBA 填 alpha=255），带着掩膜贴进 RGBA 会把被收色像素的 alpha
    # 抬成 255——收色的契约是"只动颜色"，这一条必须由模式选择保证，不能靠后面的
    # 微缩去兜（微缩是取 min，抬上去的 255 只有邻居更小才压得回来）。
    colors = im.convert("RGB")
    colors.paste(neighbor, box[:2], mask)   # mask = 255 的像素整格换成邻居色
    return Image.merge("RGBA", (*colors.split(), im.split()[3]))


def unblend_v3(im):
    """阶段二单帧（纯函数）：白底反解 → 低 alpha 亮色收色 → **镂空还原** →
    alpha 微缩 1px → **alpha 底板清理**。

    函数名里的 ``v3`` 指这条链的主体（v2 之后的那三步），**产物档位现在是 v6**
    （``ENCODER_DESC`` = lossless ``unblend-white-v6``）。v6 与 v5 的**反解链逐位
    相同**（同一个 ``unblend_v3`` + 链尾 ``floor_faint_alpha``），差别只在编码参数：
    v6 起 ``lossless=True`` + ``exact=True``，运行编码层位精确（解码重建 == 参考
    RGBA）。档号里 v4（镂空还原）从未启用、现仍挂起，所以没有 "v4 产物"。

    收色的两步**必须**在反解之后：反解的除法是 v2 那部分的全部价值（a ≥ 21 的像素
    颜色被除回去），而收色的判据是"亮度差"——先收色等于拿还没除白的存值去比，深色
    角色自己的半透明边（存值被白抬亮）会被误判成污染。

    微缩**必须**在收色之后：先缩 1px 会把最外圈（污染最重、也是收色要修的那一圈）
    直接删掉，看着"白边没了"其实是把轮廓啃掉了；反过来，收色先动颜色、微缩只动
    alpha，两者互不干扰，各自的效果都能单独验。

    **底板清理在最后**：``a == 1`` 铺满整个背景（≈82%），微缩够不到它（它不在轮廓
    环上、邻域全是 1），而它是"白底整块色差"的成因（白底 255 → 254）。放在微缩
    之后与放在微缩之前**逐位等价**（``floor_faint_alpha`` 的注释里有证明），放在
    最后是因为它产出的就是发布出去的像素。

    **镂空还原**（``restore_hair_holes``，工序四 = 本仓一直叫的 "v4"）放在收色之后、
    微缩之前。**它现在默认挂起**（``HOLE_RESTORE_ENABLED = False``：实机目视确认
    误切白色鲸鳍，见该常量注释）——挂起时这一步是 O(1) 直通。
    工序位置留着，是为了开关一开就回到原位、不必再改这里。
    放在这个位置的顺序理由（开关开启后成立）：

    - 收色之后是因为它对颜色的要求正好相反——收色要判"比邻居亮多少"，镂空判的是
      "整块是不是墙色"；顺序对结果没有耦合，但对**阅读**有：读到这里时颜色已经被
      收过一轮，残留块的颜色才是它最终发布出去的样子；
    - 微缩之前是因为微缩是 3x3 取最小值：镂空切出来的 0 会把洞再啃大一圈（洞本身
      的边界因此也带 1px 的软过渡，与轮廓外圈同一套抗锯齿口径）。若放在微缩之后，
      洞就是硬边。

    单帧 640×360 实测（真素材帧，Pillow 12.2 / Python 3.13）：反解 10.2ms +
    收色 25.6ms（裁剪区内的 5 轮扩散 18ms + 判据 + 替换）+ 微缩 7.0ms = **44ms**；
    镂空还原开启时实测 **+24.5ms**（第四批口径；挂起时 **+0ms**）；
    v5 的底板清理实测 **+4.6ms**（640×360 真素材帧、铺满 a==1；无 a==1 的帧
    **1.3ms**——掩膜 + 包围盒 + 原对象返回，见 PR 报告 §四）。
    再加无损重编码 83ms（method=2；method=6 要 610ms），含逐帧读写**≈145ms/帧**
    （预算 210ms；2.5 万帧 ≈41 分钟）。对照：v2 同口径 69.2ms/帧；同一份 v3 代码
    把扩散放回整帧是 102.8ms（单帧计算，不含编码）——**裁剪是这条管线守得住预算的
    那一步**（收色带包围盒外扩扩散半径，实测占整帧 27.9%）。
    """
    rectified = unblend_rgba(im)
    box = pull_band(rectified.split()[3]).getbbox()
    if box is not None:                     # 没有收色带（例如全不透明帧）就只微缩
        rectified = _pull_region(
            rectified, _pad_box(box, rectified.size, UNBLEND_DIFFUSE_ROUNDS))
    return floor_faint_alpha(erode_alpha(restore_hair_holes(rectified)))


def unblend_frames_in_place(frames_dir: Path | str, *,
                            cancelled: Callable[[], bool] | None = None
                            ) -> tuple[int, int, bool, str]:
    """阶段二：把目录里的 ``f_*.webp`` 逐帧反解 v6 + **无损**重编码（原地覆盖）。

    ``v5`` = ``unblend_v3``（白底反解 → 低 alpha 亮色收色 → alpha 微缩 1px →
    alpha 底板清理，见模块文档）。相对 v3 的差别是**背景底板**：``a == 1`` 的像素
    清成 0（``floor_faint_alpha``）——它就铺在整个画面的背景上（实测 ≈82%），
    叠在任意背景上都是 ``dst × 254/255``（白底 255 → 254）。所以档位串与反解标记
    一起换了（``ENCODER_DESC`` / ``UNBLEND_MARK``），v3 及更早的世代按既有迁移机制
    自动不采纳；相对 v2 的两处差别（**半透明像素的颜色可能被改成邻居颜色**、
    **轮廓外圈 alpha 会降 1px**）仍在产物里。

    返回 ``(已重编码帧数, 跳过帧数, 是否被打断, 错误文本)``：

    - **单帧失败（读不出/解不开/写不进）⇒ 清掉 ``*.part`` 并计数**：本函数仍会
      继续把余下帧转完（让调用方拿到准确的 ``skipped`` 计数），但**调用方必须
      整段作废**——返回值里的 ``skipped`` 是"这一轮不可发布"的信号，不是"跳过就好"；
    - **整段不可用**（Pillow / ``ImageMath.lambda_eval`` 缺失、目录读不开）⇒
      ``(0, 0, False, 错误文本)``：调用方丢弃半成品并按失败收口。绝不发布「meta 写着
      新档却写着无损、里面却是别的档位」的世代——那是假凭证（与「戳与帧不符」同一类错）；
    - ``cancelled`` / ``session_ending()`` 置位 ⇒ **停手并置 ``被打断`` 位**：此时
      目录里是「一部分新档 + 一部分未处理」的混合体，绝不能发布，由调用方丢弃半成品
      （issue #111 的关机窗口：逐帧粒度把不可中断的窗口压到一次 unlink）；
    - 每帧先写 ``f_*.webp.part`` 再 ``os.replace``：失败留下的半个文件不冒充帧，
      也不会被 ``frames_on_disk`` 数进帧数账。

    ``Pillow >= 10.3`` 才有 ``ImageMath.lambda_eval``（``eval`` 在 12.0 移除）；
    更旧的 Pillow 按"整段不可用"收口——不给老版本留一条静默产出错档产物的路。
    """
    try:
        Image, ImageMath, _ImageFilter, _ImageChops = _pil()
        if not hasattr(ImageMath, "lambda_eval"):     # Pillow < 10.3 没有这一项
            raise AttributeError("ImageMath.lambda_eval 不可用")
    except (ImportError, AttributeError) as exc:
        return 0, 0, False, f"阶段二不可用（Pillow/ImageMath.lambda_eval）: {_err_text(exc)}"
    directory = Path(frames_dir)
    try:
        names = sorted(
            entry.name for entry in os.scandir(directory)
            if entry.name.startswith("f_") and entry.name.endswith(".webp"))
    except OSError as exc:
        return 0, 0, False, f"帧目录读不开: {_err_text(exc)}"
    encoded = skipped = 0
    for name in names:
        if session_ending() or (cancelled is not None and cancelled()):
            return encoded, skipped, True, ""
        path = directory / name
        part = path.with_name(path.name + FRAME_PART_SUFFIX)
        try:
            # 先读进内存再解码/写盘：读写同一个路径时的句柄纠缠（Windows 上尤其）
            # 在这里一次解决掉，失败路径也就不可能留下半个 f_*.webp。
            with Image.open(io.BytesIO(path.read_bytes())) as opened:
                frame = opened if opened.mode == "RGBA" else opened.convert("RGBA")
                rectified = unblend_v3(frame)
            rectified.save(part, format="WEBP", lossless=WEBP_LOSSLESS,
                           exact=WEBP_EXACT, method=WEBP_METHOD)
            os.replace(part, path)
        except (OSError, ValueError) as exc:
            skipped += 1
            _discard(part)
            logger.warning('白底反解失败，跳过该帧（保留无损帧）: %s (%s)',
                           name, _err_text(exc))
            continue
        encoded += 1
    return encoded, skipped, False, ""


def _popen(argv: Sequence[str], **kwargs) -> subprocess.Popen:
    """Popen 适配层（测试注入点：假 exe / 桩 subprocess）。"""
    return subprocess.Popen(list(argv), **kwargs)


def _terminate_quietly(proc: object) -> None:
    """尽力终止子进程：已退出 / 无 terminate / 句柄异常一律静默跳过。

    取消路径上"终止得掉就终止、终止不掉也不能抛"：抛出去会掀掉整轮供给，而
    真正该保住的是库收尾的有界等待（见 ``FrameseqProvisionWorker.cancel``）。
    """
    poll = getattr(proc, "poll", None)
    try:
        if callable(poll) and poll() is not None:
            return
        proc.terminate()
    except Exception:
        pass


def _write_meta(out_dir: Path, webm: Path, *, sha: str, fps: float, frames: int) -> None:
    """写带源身份戳的 meta（戳 = 源 sha256 全量；目录名只带前 12 位）。

    ``encoder`` 一档到底（``ENCODER_DESC``，采纳闸门按它比对），``unblend`` 是反解
    标记（``UNBLEND_MARK``）：只做"这份帧集是白底反解 v5 过的"的指纹，采纳不看它——
    标记与 encoder 串同写同弃，任何一半都不足以单独构成身份凭证。
    """
    meta = {
        "fps": float(fps),
        "source": webm.name,
        "frames": int(frames),
        "encoder": ENCODER_DESC,
        "unblend": UNBLEND_MARK,
        "source_sha256": str(sha),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def _err_text(raw) -> str:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    return str(raw or "").strip()[:300]


def _remove_path(path: Path) -> str | None:
    """删除文件或目录：成功/本就不存在返回 None，失败返回错误文本（不抛）。

    ``shutil.rmtree`` 对普通文件是无声失败（``ignore_errors=True`` 下更彻底），
    留下的残留会让随后的 ``mkdir`` 抛 ``FileExistsError``——一次残留带走一整轮。
    """
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return _err_text(exc)
    return None


def _discard(path: Path) -> None:
    """尽力删除（半成品 / 被替换的旧内容）：失败只记日志，不改本轮结果。"""
    problem = _remove_path(path)
    if problem is not None:
        logger.debug('路径清理失败 %s: %s', path, problem)


def _publish_generation(tmp_dir: Path, target: Path) -> str | None:
    """把就绪的 ``tmp_dir`` 发布成 ``target``：成功返回 None，失败返回错误文本。

    同路径已有旧目标时**先改名挪开、发布成功后才删**：``os.rename`` 不接受覆盖
    已有目录（Windows 尤甚），而"先删旧目标再 rename"一旦 rename 抛出就是净丢
    一份在用素材。发布失败把旧目标改名回原位（回滚）——调用方负责清半成品。
    """
    aside: Path | None = None
    if target.exists():
        aside = target.with_name(target.name + REPLACE_SUFFIX)
        problem = _remove_path(aside)
        if problem is not None:
            return f"上次被替换的旧目标无法清理: {problem}"
        try:
            os.rename(target, aside)
        except OSError as exc:
            return f"旧目标无法挪开: {_err_text(exc)}"
    try:
        os.rename(tmp_dir, target)  # 原子发布：library 只认完成态世代目录
    except OSError as exc:
        reason = f"世代目录发布失败: {_err_text(exc)}"
        if aside is not None:
            try:
                os.rename(aside, target)  # 回滚：旧目标回原位，绝不净删
            except OSError:
                logger.warning('旧目标回滚失败，内容留在 %s', aside, exc_info=True)
        return reason
    if aside is not None:
        _discard(aside)
    return None


def convert_clip(webm: Path | str, out_dir: Path | str, *, force: bool = False,
                 exe: str | None = None, fps_probe: Callable[[Path], float] | None = None,
                 on_proc: Callable[[object], None] | None = None,
                 cancelled: Callable[[], bool] | None = None,
                 sha: str | None = None, lossy: bool = False) -> tuple[bool, str]:
    """单 clip 转换：``(是否本次新转, 失败原因或空串)``。

    两段式（见模块文档）：

    - **阶段一**：ffmpeg 一次调用把源 webm 写成**无损**帧序列（``_ffmpeg_argv``，
      永远 ``-lossless 1``）；
    - **阶段二**：``unblend_frames_in_place`` 逐帧白底反解 v6 + **无损**原地重编码。整段
      进不了阶段二（Pillow/ImageMath 缺失）⇒ 丢弃半成品、按失败收口；单帧失败只
      跳过那一帧（日志留痕，帧数不变）。

    - ``lossy`` 是**兼容参数**（改档后一档到底，传什么都不改变产物）：构建期 CLI
      （``tools/convert_frameseq.py``）仍在按目录传它，那个文件不在本批改动范围；
      同步更新后可连同 ``is_lossy_clip`` 一并删除；
    - 目标必须是「源身份命名的世代目录」（``clip_out_dir`` 的产物）：目录名解析
      不出戳、或戳与当前源 sha256 不符 → 拒绝发布（返回错误，不拉 ffmpeg）；
    - 当前源已有可用世代目录（``is_complete``，含强身份）**且档位相符** →
      (False, "")，不重编码；档位不符（改档前的产物/旧工具产出）当未完成重转，
      否则 ``plan_clips`` 会永远列着它而这里每次都跳过；
    - 先转 ``<世代名>.tmp/``，成功后 ``os.rename`` 原子发布（同路径已有旧目标则
      先把旧目标挪到 ``<世代名>.replacing/``、发布成功后才删，失败回滚）；同名
      目录存在但不可采纳（半拷贝/损坏残留/档位不符）或显式 ``force``（构建期/
      维修）时才重建它——正常自动供给只写**新**目录，绝不覆盖在用世代目录：目标一旦在本
      进程已采纳集合里（``live_generations``）就直接拒绝，宁可这轮不转；
    - 发布失败（旧目标挪不开 / rename 抛错）按 ``(False, 错误文本)`` 收口：旧目标
      留在原地或复位，只有半成品被清——**本函数不向外抛 I/O 异常**，一个 clip 的
      发布失败不许掀掉整轮供给；
    - **发布前再验一次源身份**（在阶段二**之前**：身份对账是毫秒级、阶段二是秒级，
      先花便宜的；阶段二只重编码已经抓下来的 tmp 帧、不再碰源，所以"帧来自哪一版"
      由这道对账定性就够了）：转换期间源被换掉、或调用方传入的 ``sha`` 已陈旧 →
      丢弃产物并返回错误：绝不发布「戳与帧不符」的世代（那是伪造身份凭证）；
    - ``cancelled`` 置位：终止在飞 ffmpeg（阶段一）/ 逐帧停手（阶段二）、清掉半成品，
      返回 (False, "")。
    - 会话结束 / 取消有**两道**闸门：进函数时一次，半成品清理 + mkdir + fps 探测
      之后、派生之前再一次（探测自己就派生一次 ffmpeg，中途置位必须仍拦得住）；
      阶段二每帧再复查一次（它自己的耗时也可能到秒级）。
    """
    source = Path(webm)
    target = Path(out_dir)
    digest = sha if sha is not None else source_sha256(source)
    if digest is None:
        return False, "源 webm 不可读，无法确定身份"
    name_stamp = stamp_from_name(target.name)
    if name_stamp is None or not digest.startswith(name_stamp):
        return False, f"输出目录名不是源身份的世代目录: {target.name}（拒绝发布无戳缓存）"
    if not force:
        # 幂等闸门 = 身份/帧数自洽（is_complete 口径）**且**档位相符：只看 is_complete
        # 会让"档位不符"的目录永远列在待转清单里、每次又在这里被跳过（供给永不自洽）。
        # 改档后"档位不符"就是全部旧世代（无损 / Q80）：它们在这里被判为待重转。
        meta = _complete_meta(target, sha=digest)
        if meta is not None and meta.get("encoder") == ENCODER_DESC:
            return False, ""
    if not force and target.exists() and target in live_generations():
        # 本进程已采纳过它（可能还有活 clip 在读）：自动供给绝不替换在用目录，
        # 宁可这一轮不转。``force`` 是显式维修口（构建期工具/人工），由调用方承担。
        return False, f"世代目录在用（可能被活 clip 读取），不替换: {target.name}"
    if cancelled is not None and cancelled():
        return False, ""
    if session_ending():
        return False, ""  # issue #111：会话结束绝不再派生 ffmpeg（静默降级）
    exe_path = exe or ffmpeg_exe()
    if not exe_path:
        return False, "ffmpeg 不可用"

    tmp_dir = target.with_name(target.name + TMP_SUFFIX)
    problem = _remove_path(tmp_dir)          # 上一轮的半成品/残留：清不净就别转
    if problem is not None:
        return False, f"半成品目录无法清理: {problem}"
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"半成品目录无法创建: {_err_text(exc)}"
    probe = fps_probe or probe_fps
    fps = probe(source)

    # 派生前最后一次复查（issue #111）：上面那道闸门之后还有半成品清理、mkdir 与
    # fps 探测（探测本身又派生一次 ffmpeg，几百毫秒级），会话结束完全可能落在这段
    # 窗口里——只在探针之前查一次，闸门置位后照样会 CreateProcess。探针之后、派生
    # 之前必须再看一次；这也是取消响应最快的落点（不必等转换跑完）。
    if session_ending() or (cancelled is not None and cancelled()):
        _discard(tmp_dir)
        return False, ""

    try:
        proc = _popen(
            _ffmpeg_argv(exe_path, source, tmp_dir),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
    except OSError as exc:  # exe 缺失/不可执行/进程上限：按失败收口，不掀整轮
        _discard(tmp_dir)
        return False, f"ffmpeg 无法启动: {_err_text(exc)}"
    if on_proc is not None:
        on_proc(proc)
    err: object = b""
    try:
        _out, err = proc.communicate()
    except Exception as exc:  # 子进程被 terminate/句柄异常：按失败收口
        err = str(exc)
    finally:
        if on_proc is not None:
            on_proc(None)

    if cancelled is not None and cancelled():
        _discard(tmp_dir)
        return False, ""
    if proc.returncode != 0:
        _discard(tmp_dir)
        return False, _err_text(err)

    # 发布闸门：身份必须在**产物落地这一刻**仍然成立。转换期间源被换掉（升级器
    # 顶素材、手工覆盖）时，帧来自新内容而戳还是旧的——写进 meta 就是一份假身份
    # 凭证：源将来回到旧版时它会被采纳，播的却是新版的帧（比"没有缓存"更糟）。
    # 调用方传进来的 ``sha`` 同样在这里对账（陈旧戳一律拒绝发布）。丢弃这一轮产物，
    # 下一轮按新身份重转；这条对账每次多读一遍源（0.5MB 级，≈1ms）。
    if source_sha256(source) != digest:
        _discard(tmp_dir)
        return False, "转换期间源身份已变，丢弃本轮产物（不发布戳与帧不符的世代）"

    # ---- 阶段二：逐帧白底反解 v6 + 无损重编码（原地覆盖 tmp 里的无损帧）------
    # 放在身份对账**之后**：对账是毫秒级、阶段二是秒级，先花便宜的；源若已漂移，
    # 这一段的 CPU 一个周期都不该花。整段失败 ⇒ 丢弃半成品（绝不发布一份
    # "meta 写着无损+反解 v6、帧却是别的档位"的假凭证）；**单帧失败同样整段作废**
    # （见下：混合处理产物不许发布）。
    # ``interrupted``（会话结束/取消落在阶段二里）同样丢弃：那一刻目录里是
    # 「一部分已处理 + 一部分未处理」的混合体，发布出去就是假 meta。
    # 注意这里**不**在阶段二成功后复查会话闸门：一趟已经完成的转换不因为会话结束
    # 就被丢掉（与阶段一的既有口径一致——闸门管的是"不再起新的活"，不是"销毁战果"）。
    encoded, skipped, interrupted, problem = unblend_frames_in_place(
        tmp_dir, cancelled=cancelled)
    if problem:
        _discard(tmp_dir)
        return False, problem
    if interrupted:
        _discard(tmp_dir)
        return False, ""
    if skipped:
        # 像素真值契约：整个 clip 必须同属一条处理链/一个档位。一帧处理失败
        # （读不出/解不开/写不进）就**整段作废**——发布「一部分重编、一部分仍是
        # 阶段一无损帧」的混合产物，等于让播放器同时读到两种像素来源；帧数对
        # 不代表档位对。丢弃后由既有失败退避（``.backoff.json``）重试。
        logger.warning('阶段二跳过 %d/%d 帧，整段作废（不发布混合处理产物）: %s',
                       skipped, encoded + skipped, target.name)
        _discard(tmp_dir)
        return False, f"阶段二有 {skipped} 帧处理失败，整段作废（不发布混合档位产物）"

    frames = frames_on_disk(tmp_dir)
    if frames <= 0:
        _discard(tmp_dir)
        return False, "ffmpeg 未产出任何帧"
    try:
        _write_meta(tmp_dir, source, sha=digest, fps=fps, frames=frames)
    except OSError as exc:                   # 盘满/只读：不掀整轮
        _discard(tmp_dir)
        return False, f"meta 写入失败: {_err_text(exc)}"
    # 只可能是 force（构建期/维修）或同名但不可采纳的残留：正常供给路径的世代
    # 目录名随源身份变，新世代永远落在新路径上（不覆盖在用目录）。tmp 全程就绪，
    # 不存在"半个目标目录"窗口；library 此刻回退 webm 解码。
    problem = _publish_generation(tmp_dir, target)
    if problem is not None:
        _discard(tmp_dir)
        return False, problem
    return True, ""


# ---------------------------------------------------------------- 磁盘准入
def disk_headroom_bytes(webm: Path | str, *,
                        floor: int = DISK_FREE_FLOOR_BYTES,
                        factor: int = DISK_SOURCE_FACTOR) -> int:
    """起手一个 clip 需要的剩余空间：``max(floor, 源 webm 大小 × factor)``。

    源大小拿不到（stat 失败）按 0 算 = 只剩地板。地板保证任何 clip 都有一份绝对
    余量（一次转换的中间产物 + meta + 发布期的双份）；倍数项让大素材（几十 MB 的
    长随机动作）不至于把"地板够但实际不够"当成够。
    """
    try:
        size = int(Path(webm).stat().st_size)
    except OSError:
        size = 0
    return max(int(floor), size * int(factor))


def disk_free_bytes(path: Path | str) -> int | None:
    """``path`` 所在卷的剩余字节；路径不存在时向上找最近的已存在父目录，查不到 None。"""
    probe = Path(path)
    while True:
        try:
            return int(shutil.disk_usage(str(probe)).free)
        except OSError:
            parent = probe.parent
            if parent == probe:
                return None
            probe = parent


def has_disk_headroom(frameseq_root: Path | str, webm: Path | str) -> bool:
    """帧序列根所在卷是否还有起手这个 clip 的余量。

    探针失败（异常路径/不支持的卷）时返回 True（按"有空间"处理）：一次 stat 探测
    失败不该把供给永久停掉——真写不进去时转换本身会报错、按失败退避收口。
    """
    free = disk_free_bytes(frameseq_root)
    if free is None:
        return True
    return free >= disk_headroom_bytes(webm)


@dataclass
class ProvisionReport:
    """单轮供给结果（供日志/测试断言）。"""

    converted: int = 0
    skipped: int = 0
    failed: int = 0
    retired: int = 0
    locked: bool = False
    # 本轮无事可做（范围内都有可用世代、无待清扫、无回朝旧账）：worker 立即结束，
    # 库侧据此不做收尾 rescan（rescan 要重算源哈希）
    idle: bool = False
    # 本轮预算（迁移配额 / 起手上限）搁置的 clip 数：留给下次启动，不是失败
    deferred_budget: int = 0
    # 退避窗口内跳过的 clip 数：连 ffmpeg 都没派生，同样不是失败
    deferred_backoff: int = 0
    # 磁盘余量不足搁置的 clip 数（见 has_disk_headroom）：同样不是失败
    deferred_disk: int = 0
    # 到起手上限收手（余下 clip 未起手；已起手的那个会跑完，不硬杀 ffmpeg）
    budget_stopped: bool = False
    # 本轮吃掉的迁移配额数（**进程内共享**台账，见 claim_migration_slot）
    migrations: int = 0


def provision_once(videos_dir: Path | str, frameseq_root: Path | str, *,
                   exe: str | None = None,
                   fps_probe: Callable[[Path], float] | None = None,
                   on_proc: Callable[[object], None] | None = None,
                   cancelled: Callable[[], bool] | None = None,
                   round_seconds: float = ROUND_BUDGET_S,
                   migration_clips: int = MIGRATION_CLIPS_PER_ROUND,
                   clock: Callable[[], float] = time.monotonic) -> ProvisionReport:
    """同步执行一轮供给（QLockFile 互斥 + 世代清扫 + 逐 clip 转换 + 会话闸门）。

    拿不到锁直接返回 ``locked=True``（另一实例在转，不等待）；PET_FRAMESEQ=0
    或会话结束时直接 no-op。逐个 clip 转换前复查会话闸门/取消谓词；无 ffmpeg
    exe 时编码静默 no-op，但世代清扫照做（不依赖 ffmpeg 的纯本地 I/O）。
    供给序 = ``plan_clips`` 的出列序（热集 idle → move → 其余 → events → random）。

    **"有没有活干"在这里判定**（``plan_clips`` / ``pending_retirements`` /
    ``stale_current_markers``）：三项都空 → ``report.idle`` 置位并立即返回（不清扫：
    清无可清），库侧据此跳过收尾 rescan。这三项判定的源哈希开销只该在 worker 线程里
    花（见模块线程模型）。

    **本轮预算（每次启动的代价上限）**：

    - 有等价旧产物的 clip（升级形态，见 ``legacy_frames_dir``）每个启动周期合计最多
      **尝试** ``migration_clips`` 个（按尝试记账：失败也吃配额，免得一轮里连着挂多个）
      ——旧产物在场说明播放不降级，没必要一次烧掉整份热集的重编码；余下留到下次启动
      （``deferred_budget`` 计数留痕）。配额记在**进程内共享**的台账上
      （``claim_migration_slot``，键 = 素材根）：一次启动 = 一个进程，三宠三库共用
      素材根 ⇒ 合计只迁 ``migration_clips`` 个，不是每库一份。
    - 无旧产物的 clip（全新安装/新素材）不受该配额限制：帧序列是那里唯一来源。但
      **不承诺一轮转完**——仍受起手上限约束，未起手的留到下次启动。
    - ``round_seconds``（``clock`` 单调钟，可注入）是**起手上限**，不是墙钟硬上限：
      只在起一个 clip 之前检查；已起手的 clip 跑完（**不硬杀 ffmpeg**），所以本轮实际
      墙钟可能超出它，最多超出"一个 clip 的转换时长"。到点后余下 clip 一个都不再起手
      （``budget_stopped``，日志把"可能溢出"一并说清）。
    - 退避窗口内的 clip 直接跳过（``deferred_backoff``），连进程都不派生，也不吃
      迁移配额；失败写退避表、成功销账（``read_backoff`` / ``note_failure`` /
      ``note_success``）。退避表写失败只 warning 说明未落盘，不谎称已退避。
    - 磁盘余量不足（``has_disk_headroom``，< ``max(1GiB, 源大小 × 20)``）的 clip 不起手
      （``deferred_disk``）：宁可晚点转，绝不把盘写满；也不吃迁移配额（配额是"起手"
      名额，不是"探测过"名额）。已转产物一个字节不删。
    """
    report = ProvisionReport()
    if provision_disabled() or session_ending():
        return report
    root = Path(frameseq_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return report
    lock = QLockFile(str(root / LOCK_NAME))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(0):
        report.locked = True
        return report
    try:
        # 「有没有活干」的判定（三项，都在 worker 线程里跑）：
        # 待转 clip（plan_clips：按范围逐个算源哈希）、待清扫世代（只 stat 退役标记）、
        # 回朝旧账（当前世代却还带着退役标记）。三项皆空 = 无事可做：不清扫
        # （清无可清）、不编码，report 留痕让库侧跳过收尾 rescan。
        plan = plan_clips(videos_dir, root)
        if (not plan
                and not pending_retirements(videos_dir, root)
                and not stale_current_markers(videos_dir, root)):
            report.idle = True
            return report
        # 世代清扫（只删已标记退役 + 满宽限 + 不在用的世代目录）：与编码同一把
        # 实例锁内，多实例不会互相踩清扫窗口。取消谓词一并传进去——库收尾时
        # 清扫是最大的一个阻塞点（见 _remove_generation_bounded）。
        report.retired += len(sweep_retired(videos_dir, root, cancelled=cancelled))
        exe_path = exe or ffmpeg_exe()
        if not exe_path:
            return report  # 无 ffmpeg exe（含 imageio 不可用）：编码静默 no-op
        backoff = read_backoff(root)          # 一次读表，本轮内只查内存
        started = clock()
        for index, (webm, out_dir) in enumerate(plan):
            if cancelled is not None and cancelled():
                break
            if session_ending():
                break  # issue #111：置位后不再为后续 clip 派生 ffmpeg
            if clock() - started >= round_seconds:
                report.budget_stopped = True
                report.deferred_budget += len(plan) - index
                break
            key = backoff_key(webm, videos_dir, root)
            if backoff_remaining(backoff, key) > 0:
                report.deferred_backoff += 1
                continue
            if not has_disk_headroom(root, webm):
                # 盘不够：不起手（连 ffmpeg 都不派生），也不吃迁移配额——配额是
                # "起手"名额，不是"探测过"名额；已转产物一个字节不删。
                report.deferred_disk += 1
                continue
            if legacy_frames_dir(webm, videos_dir, root) is not None:
                # 迁移配额按**尝试**记账（在退避闸之后）：失败也吃掉配额，免得一次
                # 启动在一份坏素材上连着拉好几个 ffmpeg。台账是**进程内共享**的
                # （三宠三库一个启动周期合计 migration_clips 个），用完就留到下次启动。
                if not claim_migration_slot(root, limit=migration_clips):
                    report.deferred_budget += 1
                    continue
                report.migrations += 1
            converted, err = convert_clip(
                webm, out_dir, exe=exe_path, fps_probe=fps_probe,
                on_proc=on_proc, cancelled=cancelled,
            )
            if err:
                report.failed += 1
                delay = note_failure(backoff, key, err)
                if write_backoff(root, backoff):
                    logger.warning('帧序列供给失败 %s: %s（%.0fs 内不再重试）',
                                   webm.name, err, delay)
                else:
                    # 退避表写不进：行为是"下次启动立刻重试这个 clip"，日志必须说这个，
                    # 不许照抄"Ns 内不再重试"——那样读日志的人会以为退避生效了。
                    logger.warning(
                        '帧序列供给失败 %s: %s；退避表写入失败（%s 不可写），'
                        '本次退避未落盘：下次启动会立刻重试这个 clip',
                        webm.name, err, root / BACKOFF_NAME)
            elif converted:
                report.converted += 1
                if note_success(backoff, key):
                    write_backoff(root, backoff)
                report.retired += len(_retire_superseded(
                    webm, videos_dir, root, keep_dir=out_dir, cancelled=cancelled))
            else:
                report.skipped += 1
        if report.budget_stopped:
            # 起手上限 ≠ 墙钟硬上限：已起手的那个 clip 跑完（不硬杀 ffmpeg），本轮
            # 实际墙钟必然可能超出——日志不说清，读日志的人会以为本轮没有溢出。
            logger.info(
                '帧序列供给本轮到起手上限 %.0fs 收手：已起手的 clip 会跑完'
                '（不硬杀 ffmpeg），实际墙钟可能超出该上限至多一个 clip 的转换时长；'
                '余下 %d 个 clip 未起手，留到下次启动',
                round_seconds, report.deferred_budget)
        if report.deferred_disk:
            logger.info(
                '帧序列供给因磁盘余量不足搁置 %d 个 clip（不转比写满盘好；'
                '已转产物不删，余下留到下次启动）', report.deferred_disk)
        if report.deferred_budget or report.deferred_backoff or report.deferred_disk:
            logger.info(
                '帧序列供给本轮未跑完全部 clip：预算搁置 %d 个 / 退避搁置 %d 个'
                '/ 磁盘搁置 %d 个（余下留到下次启动%s）',
                report.deferred_budget, report.deferred_backoff,
                report.deferred_disk,
                '，本轮迁移配额已用' if report.migrations else '')
    finally:
        lock.unlock()
    return report


class FrameseqProvisionWorker(QThread):
    """library 拥有的低优先级供给线程（run 内不创建跨线程 Qt 对象）。

    ``cancel()`` 由 GUI 线程调用：置取消谓词并 terminate 在飞 ffmpeg 子进程，
    使 reader ``communicate()`` 立即返回；收尾经 queued ``finished_work``
    信号回 GUI 线程触发 rescan。
    """

    finished_work = Signal()

    def __init__(self, videos_dir: Path | str, frameseq_root: Path | str,
                 parent=None) -> None:
        super().__init__(parent)
        self._videos_dir = Path(videos_dir)
        self._frameseq_root = Path(frameseq_root)
        self._cancel = threading.Event()
        self._proc_lock = threading.Lock()
        self._proc: object | None = None
        self.report = ProvisionReport()

    def cancel(self) -> None:
        """置取消谓词 + 终止在飞 ffmpeg（幂等，任意线程可调用）。"""
        self._cancel.set()
        with self._proc_lock:
            proc = self._proc
        if proc is not None:
            _terminate_quietly(proc)

    def _track_proc(self, proc: object) -> None:
        """登记在飞 ffmpeg（``None`` = 转换收尾摘除）。

        ``_popen`` 返回到本方法拿到锁之间有一段窗口：``cancel()`` 恰好在这段窗口
        里跑完时 ``_proc`` 还是 ``None``（它看不见这个刚派生出来的进程），只登记
        不复查就等于那个 ffmpeg 没人 terminate——``communicate()`` 会陪它跑到转换
        自然结束，库侧的有界等待随之失效（issue #111：关机窗口里还留着一个正在跑的
        转换进程）。所以同一把锁内复查取消位，两种到达顺序都不漏：cancel 先到 →
        这里补一次 terminate；这里先到 → cancel 自己看得见这个进程。
        """
        with self._proc_lock:
            self._proc = proc
            cancelled = proc is not None and self._cancel.is_set()
        if cancelled:
            _terminate_quietly(proc)

    def run(self) -> None:  # noqa: D401 - QThread 入口
        try:
            self.report = provision_once(
                self._videos_dir, self._frameseq_root,
                on_proc=self._track_proc, cancelled=self._cancel.is_set,
            )
        except Exception:
            logger.debug('帧序列供给线程异常结束', exc_info=True)
        finally:
            self.finished_work.emit()
