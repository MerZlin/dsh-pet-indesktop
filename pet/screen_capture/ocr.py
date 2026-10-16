# -*- coding: utf-8 -*-
"""「快速识屏」本地 OCR 的**宿主侧驱动**：截图像素 → 一个识别子进程 → 文本块。

设计定稿的五条硬约束，以及它们在这一层各自的落点：

1. **本地 + 断网可用**。引擎是 RapidOCR（PaddleOCR 的 PP-OCR 模型，ONNXRuntime
   推理），模型文件随包放在 ``resources/rapidocr_models/``。加载路径上**不存在**
   下载分支：每个模型都由工作端按显式路径建 session 再交给 rapidocr，而 rapidocr
   一旦拿到外部 session 就直接返回，连"模型路径 → 下载"那段代码都不会进
   （见 ``ocr_worker._attach_sessions``）。
2. **CPU、1~2 线程、关自旋、首版禁 GPU**。旋钮全在工作端
   （``ocr_worker._new_session``）：桌宠是常驻后台程序，识别期间把整机 CPU 打满、
   或让工作线程空转让风扇起飞，都是不可接受的代价。
3. **一次识别 = 一个子进程，用完就丢**（切片 5 的用户拍板）。识别的全部常驻内存
   都长在那个进程里，它退出即归零。**为什么非子进程不可**：切片 2 的进程内
   ``unload()`` 实测只回吐约 30 MB / 108 MB（152.4 → 122.5 MB，再等 5 秒不动）
   ——释放的是 Python 引用与 session 对象，native 堆与已 import 的 cv2/numpy
   模块内存**不还给操作系统**，冻结包里同一组数字（42.6 → 137.3，unload 后
   108.3）。要让"用完就丢"literally 成立，只能让进程死掉。代价是每次触发多付一次
   进程启动 + 模型加载，实测见 ``.scratch/screen-capture/probe_ocr_subprocess.txt``。
4. **错误说人话**。模型缺失 / 依赖缺失 / 加载失败 / 识别失败 / 超时 / 工作进程崩溃
   各有自己的异常类型与一句能直接给用户看的中文说明。人话优先取工作端自己报的
   那一条（它知道得最具体，比如是哪个模型文件），端口侧只兜底。
5. **不留孤儿**。子进程的生命周期只有三种收尾：正常退出（结果已读走）、超时被杀、
   被 :func:`release_engine` 中止。每一次 ``recognize`` 都在 ``finally`` 里确保
   进程已经被回收；:func:`release_engine` 是会话收口处的第二道网（"取消/失败杀子进程"）。

隐私与职责边界：本层只做"像素进、文本出"，不落盘（图像只走管道与内存）、不联网、
不管 UI，也不决定识别结果怎么展示。展示顺序的契约（:func:`reading_order` 与
:class:`TextBlock`）留在本层：工作端只负责"图里有哪些框和字"，"读起来是什么顺序"
是给人看的事。
"""

from __future__ import annotations

import json
import logging
import struct
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from PySide6.QtGui import QImage

# 模型事实（文件名 / 摘要 / 目录解析）住在 PySide 无关的 ``ocr_models`` 里，因为
# 识别子进程也要用同一份、却不许 import PySide（见该模块说明）。这里整组再导出：
# 构建期硬门（``scripts/verify_rapidocr_models.py``）与既有测试继续从 ``ocr.MODEL_*``
# 取判据，判据不会因为这次搬家变成两份。
from .ocr_models import (  # noqa: F401  -- 刻意的 re-export，见上
    MODEL_FILES,
    MODEL_RELATIVE_DIR,
    MODEL_SHA256,
    OCR_MODEL_DIR_ENV,
    bundled_model_dir,
    model_problem,
    resolve_model_dir,
)
from .ocr_worker import (
    EXIT_LOAD_FAILED,
    EXIT_MODEL_MISSING,
    EXIT_NO_TEXT,
    EXIT_PROTOCOL,
    EXIT_RECOGNIZE_FAILED,
    EXIT_TEXT,
    EXIT_UNAVAILABLE,
    MAGIC,
    STDERR_PREFIX,
)

log = logging.getLogger("dsh-pet-standalone")

__all__ = [
    "BUNDLE_MODULES",
    "MODEL_FILES",
    "MODEL_RELATIVE_DIR",
    "MODEL_SHA256",
    "OCR_MODEL_DIR_ENV",
    "OCR_WORKER_FLAG",
    "OcrEngine",
    "OcrError",
    "OcrLoadError",
    "OcrModelMissingError",
    "OcrRecognitionError",
    "OcrUnavailableError",
    "TextBlock",
    "WORKER_MODULE",
    "abandon_workers",
    "bundled_model_dir",
    "encode_png",
    "model_problem",
    "reading_order",
    "recognize",
    "release_engine",
    "resolve_model_dir",
    "shared_engine",
    "worker_command",
]

#: 识别工作端的模块路径（源码态 ``python -m <这个>``）。冻结态换成本 exe 的
#: ``--ocr-worker`` 内部子命令（见 :func:`worker_command`）。
WORKER_MODULE = f"{__package__}.ocr_worker"

#: 冻结态的 worker 子命令开关（登记在 ``pet/__main__.py`` 与 ``packaging/pet_entry*.py``）。
OCR_WORKER_FLAG = "--ocr-worker"

#: 让工作端**只做检测、不做文字识别**的内部开关（见 :func:`worker_command`）。
#: 只要"哪里有文字"、不需要文字内容的调用方用它换时间：同一张 1626×640 的图实测
#: 2672ms → 1297ms（识别模型不跑），代价是结果里 ``TextBlock.text`` 一律为空。
OCR_NO_REC_FLAG = "--ocr-no-rec"

#: 冻结包里必须存在的模块。两者都是 **PyInstaller 静态分析摸不到的目标**：
#: ``ocr`` 走 ``actions`` 的 importlib 动态 seam（模块名是字符串），``ocr_worker``
#: 只以 ``sys.executable -m ...`` 的形式出现在子进程命令行里。少任何一个，冻结包
#: 都不会崩——只是"截图 → 提取文字"永远置灰、或者每次点击都报"工作进程起不来"。
#: 构建脚本必须显式 ``--hidden-import`` 它们，产物侧由
#: ``scripts/verify_rapidocr_models.py --app-dir`` 从模块图里读回来核对。
BUNDLE_MODULES = (__name__, WORKER_MODULE)

#: 默认识别超时（秒）。覆盖"进程启动 + import + 模型加载 + 一次推理"的全程预算：
#: 本机实测 1600×900 截图端到端约 2.5 s（暖）/ 2.9 s（模型文件冷），见
#: ``.scratch/screen-capture/probe_ocr_subprocess.txt``。30 s 是给慢机器 + 杀毒软件
#: 首扫的宽预算。真挂住时也不能让调用方无限等下去——桌宠是个 GUI 程序，一次点击
#: 不能没有尽头。
DEFAULT_RECOGNIZE_TIMEOUT = 30.0

#: 结束子进程时给它的宽限（秒）：先 ``terminate``，超时再 ``kill``。
_KILL_GRACE = 2.0

#: 日志尾巴最多带多少字符进人话（native 崩栈可能很长，全塞进气泡没人看）。
_STDERR_TAIL_LIMIT = 400


# ---------------------------------------------------------------------- 异常


class OcrError(RuntimeError):
    """本地 OCR 的失败基类：``str(exc)`` 就是可以直接给用户看的那句话。"""


class OcrUnavailableError(OcrError):
    """本机没有 OCR 依赖（rapidocr / onnxruntime 未安装）——安装不完整。"""


class OcrModelMissingError(OcrError):
    """模型文件缺失——安装不完整，换图重试没有意义。"""


class OcrLoadError(OcrError):
    """模型/引擎加载失败（文件损坏、DLL 不匹配、工作进程加载期崩溃）。"""


class OcrRecognitionError(OcrError):
    """引擎起来了但这一次识别失败（含超时、被中止、协议不成立）。"""


# ------------------------------------------------------------------ 数据契约


@dataclass(frozen=True)
class TextBlock:
    """一块被识别出来的文字。

    ``box`` 是四个角点 ``((x, y) × 4)``，顺序为左上 → 右上 → 右下 → 左下
    （就是检测模型给出的四边形）。保留四边形而不是只留外接矩形，是因为倾斜的
    一块文字用外接矩形圈出来会盖住旁边的内容。
    """

    text: str
    score: float
    box: tuple[tuple[float, float], ...]

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """外接矩形 ``(left, top, width, height)``，与 ``QImage`` 的像素坐标同口径。"""
        if not self.box:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [point[0] for point in self.box]
        ys = [point[1] for point in self.box]
        return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    @property
    def center(self) -> tuple[float, float]:
        left, top, width, height = self.bounds
        return (left + width / 2, top + height / 2)


def reading_order(blocks: Iterable[TextBlock]) -> list[TextBlock]:
    """按阅读顺序重排文本块：先按行自上而下，行内自左而右。

    分行的判据是**竖向重叠比**（重叠高度 > 较短一方高度的一半）。用固定阈值去比
    上沿坐标会散架：同一行里字号/基线略有差别的两块（正文与行内代码、中英文混排）
    上沿本来就不齐，按阈值切会把一行拆成两行，输出读起来就是乱的。
    """
    ordered = sorted(blocks, key=lambda block: block.bounds[1])
    if not ordered:
        return []

    lines: list[list[TextBlock]] = []
    spans: list[tuple[float, float]] = []
    for block in ordered:
        _, top, _, height = block.bounds
        bottom = top + height
        if lines:
            line_top, line_bottom = spans[-1]
            overlap = min(line_bottom, bottom) - max(line_top, top)
            if overlap > 0 and overlap > 0.5 * min(line_bottom - line_top, height):
                lines[-1].append(block)
                spans[-1] = (min(line_top, top), max(line_bottom, bottom))
                continue
        lines.append([block])
        spans.append((top, bottom))

    result: list[TextBlock] = []
    for index in sorted(range(len(lines)), key=lambda i: (spans[i][0], min(b.bounds[0] for b in lines[i]))):
        result.extend(sorted(lines[index], key=lambda block: block.bounds[0]))
    return result


# ---------------------------------------------------------------- 像素 → PNG


def encode_png(image: QImage) -> bytes:
    """``QImage`` → 内存 PNG 字节（**不落盘**，切片 5 的红线）。

    为什么过一趟 PNG 而不是把裸像素喂进管道：裸像素要求两边对 stride、通道序、
    字节序逐条对齐，任何一条不一致都是"识别结果莫名其妙地错"，而且错得看不出来；
    PNG 把这些约定全部收进格式本身，宿主只管编码、工作端只管解码。

    PNG 是**无损**的，所以这一步不损失识别精度。先统一到 ``Format_RGB888`` 有两个
    理由：alpha 通道对截图没有意义（反而会让解码端拿到 4 通道），以及让编码结果
    的通道序与色彩空间确定下来（cv2 解码后就是 rapidocr 要的 BGR 3 通道）。
    """
    from PySide6.QtCore import QBuffer, QIODevice

    if image.format() != QImage.Format.Format_RGB888:
        converted = image.convertToFormat(QImage.Format.Format_RGB888)
        if converted.isNull():
            raise OcrRecognitionError("图像转换失败：无法把截图转成 8-bit RGB")
        image = converted
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OcrRecognitionError("截图编码失败：无法打开内存缓冲")
    try:
        if not image.save(buffer, "PNG"):
            raise OcrRecognitionError("截图编码失败：无法把选区编码成 PNG")
        return bytes(buffer.data())
    finally:
        buffer.close()


def frame_bytes(png: bytes) -> bytes:
    """按管道协议拼一帧：魔数 + 大端长度 + PNG 字节（见 ``ocr_worker`` 的协议说明）。"""
    return MAGIC + struct.pack(">I", len(png)) + png


# ------------------------------------------------------------- 子进程的一生


def worker_command(use_rec: bool = True) -> list[str]:
    """启动识别工作端的命令行。

    - **源码态**：``<python> -m pet.screen_capture.ocr_worker``；
    - **冻结态**：``<本 exe> --ocr-worker``——冻结包里没有可用的 ``python``，
      而 exe 自己就是解释器，所以给它一个内部子命令（照 ``--settings`` /
      ``--uninstall-cleanup`` 的先例，路由登记在 ``pet/__main__.py`` 与
      ``packaging/pet_entry*.py``）。

    两态都必须走**同一个解释器/同一个 exe**：换一个 python 就可能换一套
    onnxruntime / cv2 的 DLL，而"引擎在别的解释器里加载失败"是最难查的一类。

    ``use_rec=False`` 时附上 :data:`OCR_NO_REC_FLAG`：工作端只做检测、不跑识别模型。
    调用方要的如果只是"屏幕上哪里有文字"而不需要文字内容，这一档实测省掉约一半时间
    （同图 2672ms → 1297ms）；代价是 ``TextBlock.text`` 为空字符串，所以**需要文字
    内容（例如按"只避汉字"过滤）的调用方不能用它**。
    """
    if getattr(sys, "frozen", False):
        argv = [sys.executable, OCR_WORKER_FLAG]
    else:
        argv = [sys.executable, "-m", WORKER_MODULE]
    if not use_rec:
        argv.append(OCR_NO_REC_FLAG)
    return argv


def spawn_worker(argv: list[str]):
    """按给定命令行起一个识别子进程（三条管道都是 PIPE）。

    Windows 上必须带 ``CREATE_NO_WINDOW``：我们是个 GUI 程序，不带它每触发一次
    识别就会在屏幕上闪一下黑色控制台窗口——**这就是用户看得见的故障**。
    """
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs,
    )


def kill_worker(process, grace: float = _KILL_GRACE) -> bool:
    """结束一个识别子进程；返回是否真的动了手（已经退出的返回 False）。

    ``terminate`` 之后再 ``kill`` 是防"它不理会终止请求"：本地引擎没有要存盘的
    东西，硬杀不会丢数据，而留一个还在跑模型的 PID 是我们明确不要的。
    """
    if process.poll() is not None:
        return False
    try:
        process.terminate()
    except Exception:  # 已经退了 / 句柄没了：下面照样补一刀
        log.debug("快速识屏：终止 OCR 工作进程失败", exc_info=True)
    try:
        process.wait(timeout=grace)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=grace)
        except Exception:
            log.debug("快速识屏：强杀 OCR 工作进程失败", exc_info=True)
    return True


#: 仍在跑的识别子进程（:func:`release_engine` 的清扫对象）。用 set 是因为
#: 进程对象本身可哈希、且认的是"对象还在不在"，不是 pid。
_LIVE_LOCK = threading.Lock()
_LIVE_WORKERS: set = set()


def _register_worker(process) -> None:
    with _LIVE_LOCK:
        _LIVE_WORKERS.add(process)


def _unregister_worker(process) -> None:
    with _LIVE_LOCK:
        _LIVE_WORKERS.discard(process)


def abandon_workers() -> int:
    """中止所有仍在跑的识别子进程；返回中止了几个。

    调用方是会话收口（``actions._finish`` → :func:`release_engine`）：用户取消或
    整条链路失败时，那个可能还在跑模型的进程必须立刻消失，不能留在后台继续吃内存
    ——"用完就丢"里那个"丢"字包括它的进程。已经退出的进程不记账。

    **先打标、再动手**：``_dsh_ocr_abandoned`` 必须在进程真的死掉之前写上。识别
    线程的收尾顺序是"``communicate`` 返回 → 查标记"，而 ``communicate`` 在进程一死
    就返回——先杀后标记的话，那条线程有真实概率查到一个还没写上标记的进程，于是把
    一次**有意中止**报成"异常退出（退出码 1）"，用户看到的是一句莫名其妙的崩溃。
    """
    with _LIVE_LOCK:
        processes = list(_LIVE_WORKERS)
    killed = 0
    for process in processes:
        if process.poll() is not None:
            continue
        setattr(process, "_dsh_ocr_abandoned", True)
        if kill_worker(process):
            killed += 1
    return killed


# ------------------------------------------------------------ 结果的两端解析


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _STDERR_TAIL_LIMIT else text[:_STDERR_TAIL_LIMIT] + "…"


def _as_text(raw) -> str:
    """``communicate()`` 给的是 bytes；测试和调用方也可能直接塞 str。"""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace")
    return str(raw or "")


def parse_stderr(stderr) -> tuple[str, str]:
    """工作端 stderr → ``(stage, error)``。

    只认带 :data:`STDERR_PREFIX` 的那几行（工作端自己的结构化记录），其余是
    rapidocr/onnxruntime/cv2 的噪声——把它们当"错误消息"给用户看，等于把一段栈
    丢进气泡。``stage`` 是最后一条进度记录（``""`` = 一条都没走到），宿主靠它把
    "硬崩在哪一段"说清楚；``error`` 是工作端自己写的人话。
    """
    stage, error = "", ""
    for line in _as_text(stderr).splitlines():
        line = line.strip()
        if not line.startswith(STDERR_PREFIX):
            continue
        try:
            record = json.loads(line[len(STDERR_PREFIX):])
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("stage"):
            stage = str(record["stage"])
        if record.get("error"):
            error = str(record["error"])
    return stage, error


def parse_result(stdout) -> list[dict]:
    """工作端 stdout → 原始块列表（一行 JSON）。读不出来按协议故障报，不猜。"""
    text = _as_text(stdout).strip()
    if not text:
        raise OcrRecognitionError(
            "本地 OCR 工作进程没有输出结果（进程按成功退出，stdout 却是空的）"
            "——协议不成立，这次识别的结果不可信"
        )
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise OcrRecognitionError(
            f"本地 OCR 工作进程的输出不是合法 JSON（{exc}）：{_clip(text)}"
        ) from exc
    blocks = payload.get("blocks") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        raise OcrRecognitionError(f"本地 OCR 工作进程的输出缺少 blocks 列表：{_clip(text)}")
    return blocks


def blocks_from_payload(raw: Iterable,
                        keep_empty_text: bool = False) -> list[TextBlock]:
    """原始块字典 → 阅读顺序的 :class:`TextBlock` 列表（形状不对就报故障）。

    ``keep_empty_text=True`` 时**保留没有文字的块**。这是给"只做检测"那一档用的
    （见 :data:`OCR_NO_REC_FLAG`）：那时结果里只有框、文字一律为空，而"这里有一块
    文字"本身就是调用方要的信息——典型用途是"避开文字"，不是把文字展示出来。
    默认仍按老规矩丢掉空块：对展示类调用方，它们只是噪声。
    """
    blocks: list[TextBlock] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise OcrRecognitionError(f"本地 OCR 结果第 {index} 块不是对象：{_clip(item)}")
        text = str(item.get("text") or "")
        if not text and not keep_empty_text:
            continue
        try:
            score = float(item.get("score") or 0.0)
            box = tuple((float(point[0]), float(point[1])) for point in item.get("box") or ())
        except (TypeError, ValueError, IndexError) as exc:
            raise OcrRecognitionError(f"本地 OCR 结果第 {index} 块的坐标/置信度不可用：{exc}") from exc
        blocks.append(TextBlock(text, score, box))
    return reading_order(blocks)


#: 退出码 → 异常类型（**成功码不在这里**，见 :func:`interpret`）。
_FAILURE_TYPES: dict[int, type[OcrError]] = {
    EXIT_PROTOCOL: OcrRecognitionError,
    EXIT_UNAVAILABLE: OcrUnavailableError,
    EXIT_MODEL_MISSING: OcrModelMissingError,
    EXIT_LOAD_FAILED: OcrLoadError,
    EXIT_RECOGNIZE_FAILED: OcrRecognitionError,
}

#: 工作端没写人话时按退出码兜底的说明（写死了也不会误导：它只说类别与建议）。
_FAILURE_MESSAGES: dict[int, str] = {
    EXIT_PROTOCOL: "本地 OCR 工作进程收到的图像数据不合协议——这次识别没能开始，请重试",
    EXIT_UNAVAILABLE: "本地 OCR 依赖没装全（rapidocr / onnxruntime）——依赖随安装包分发，请重新安装",
    EXIT_MODEL_MISSING: "缺少本地 OCR 模型文件——模型随安装包分发，缺失说明安装不完整，请重新安装",
    EXIT_LOAD_FAILED: "本地 OCR 引擎加载失败",
    EXIT_RECOGNIZE_FAILED: "本地 OCR 识别失败",
}


def _stage_phrase(stage: str) -> str:
    """进度标记 → "崩在哪一段"（stage 的顺序见 ``ocr_worker.run``）。"""
    if stage == "frame-read":
        return "在加载识别模型期间"
    if stage in ("loaded", "decoded"):
        return "在识别期间"
    return "在启动或读取图像期间"


def interpret(returncode: int, stdout: bytes, stderr: bytes) -> tuple[int, list[dict]]:
    """子进程的 ``(退出码, stdout, stderr)`` → ``(退出码, 原始块列表)``；失败抛人话。

    退出码是**主判据**、JSON 是载荷：两者都表达"有没有文字"时必须一致，不一致说明
    协议坏了——这时宁可报故障，也不能拿一个可能错的结果去填结果浮层。硬杀（Windows
    上 ``TerminateProcess`` 落码 1）与崩溃（负码）永远落进"故障"那一支，绝不会被
    当成一次空结果。
    """
    stage, error = parse_stderr(stderr)
    if returncode in (EXIT_TEXT, EXIT_NO_TEXT):
        blocks = parse_result(stdout)
        if returncode == EXIT_NO_TEXT and blocks:
            raise OcrRecognitionError(
                f"本地 OCR 工作进程的退出码说「没有文字」，结果里却有 {len(blocks)} 块"
                "——协议自相矛盾，这次识别的结果不可信"
            )
        return returncode, blocks

    message = error or _FAILURE_MESSAGES.get(returncode)
    if not message:
        tail = _clip(_as_text(stderr))
        message = (
            f"本地 OCR 工作进程异常退出（退出码 {returncode}）——{_stage_phrase(stage)}崩了"
            "；这次识别没有结果"
        )
        if tail:
            message += f"（{tail}）"
    exception_type = _FAILURE_TYPES.get(
        returncode, OcrLoadError if stage in ("", "frame-read") else OcrRecognitionError)
    raise exception_type(message)


# -------------------------------------------------------------------- 引擎


class OcrEngine:
    """本地 OCR 的一次性驱动：``recognize`` 起一个子进程，拿到结果就等它退出。

    与切片 2 的进程内引擎相比，这里**没有**加载状态、代次、``unload``、串行化锁
    ——那些机制存在的前提是"引擎常驻在进程里，得管它的生死"；进程模型下每次识别
    自带一份全新引擎，跑完连进程一起消失，没有可以串台的状态，也没有需要守的常驻
    内存。剩下要管的只有两件事：**超时**与**不留孤儿**。

    并发：多个线程同时 ``recognize`` 会各起一个进程（真并行，各占自己那份内存）。
    产品面上不会发生——``actions`` 一次只跑一条链路，且识别在后台线程里、收口前
    不会再来一次。这里不为它加锁：加锁等于把两次识别排队，而排队节省的正是我们
    已经决定不省的那点内存。
    """

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_RECOGNIZE_TIMEOUT,
        command: Callable[[], list[str]] | None = None,
        spawn: Callable[[list[str]], Any] | None = None,
    ) -> None:
        self.timeout = float(timeout)
        self._command = command
        self._spawn = spawn if spawn is not None else spawn_worker

    @property
    def command(self) -> list[str]:
        """本次要跑的命令行（``command`` 是测试注入缝；默认真实两态解析）。"""
        factory = self._command if self._command is not None else worker_command
        return list(factory())

    def recognize(self, image: QImage,
                  keep_empty_text: bool = False) -> list[TextBlock]:
        """认一张图，返回按阅读顺序排好的文本块；没有文字返回空列表。

        ``keep_empty_text=True`` 保留"有框没文字"的块，供"只检测"档使用
        （见 :func:`blocks_from_payload` 与 :data:`OCR_NO_REC_FLAG`）；默认不变。

        空图在**起进程之前**就被拒掉：起一次进程要付几百毫秒到几秒，而空图是
        调用方的 bug，不该罚用户等。
        """
        if not isinstance(image, QImage):
            raise TypeError(f"recognize() 需要 QImage，收到 {type(image).__name__}")
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            raise ValueError("待识别的图像为空（宽高为 0 或 QImage.isNull()）")

        # 编码放在**起进程之前**：编码失败（格式不支持的图）时还没有子进程要收尸。
        # 反过来的话，那个刚起来的进程会一直阻塞在读 stdin 上，而它的父进程已经
        # 带着异常走了——一个后台孤儿就是这么做出来的。
        payload = frame_bytes(encode_png(image))
        argv = self.command
        try:
            process = self._spawn(argv)
        except OSError as exc:
            raise OcrUnavailableError(
                f"起不了本地 OCR 工作进程（{' '.join(argv)}）：{exc}"
                "——依赖随安装包分发，缺失说明安装不完整，请重新安装"
            ) from exc
        _register_worker(process)
        try:
            stdout, stderr = self._exchange(process, payload)
        except BaseException:
            # 超时那条路已经杀过（重复调用是幂等的）；其余任何出口都不许留下子进程。
            kill_worker(process)
            raise
        finally:
            _unregister_worker(process)

        if getattr(process, "_dsh_ocr_abandoned", False):
            raise OcrRecognitionError(
                "这次本地识别已被中止（会话已收口或宿主正在释放），没有结果"
            )
        _, blocks = interpret(process.returncode, stdout, stderr)
        return blocks_from_payload(blocks, keep_empty_text=keep_empty_text)

    def _exchange(self, process, frame: bytes) -> tuple[bytes, bytes]:
        """把**已经按协议拼好的一帧**喂进去、把两条输出读回来（超时即杀进程并报人话）。

        拼帧（``frame_bytes``）由调用方负责，这里只负责传——两处各拼一次的话管道里
        就变成"套了两层的帧"，工作端读到长度头后面的第一段是内层帧的前缀，解码必败
        （那条报错是"图像解不开"，排查方向会一路跑偏到图像格式上）。
        """
        try:
            return process.communicate(frame, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            kill_worker(process)
            try:  # 把半截管道读干净，别把 ResourceWarning 留给解释器退出期
                process.communicate()
            except Exception:
                log.debug("快速识屏：超时后回收 OCR 工作进程的输出失败", exc_info=True)
            raise OcrRecognitionError(
                f"本地 OCR 识别超时（等待 {self.timeout:g} 秒仍未返回）"
                "——已中止这次识别，可稍后重试"
            ) from None


# --------------------------------------------------------------- 进程内单例


_SHARED_LOCK = threading.Lock()
_SHARED_ENGINE: OcrEngine | None = None


def shared_engine() -> OcrEngine:
    """进程内共享的默认驱动（记录超时与命令行，本身不持有任何重资源）。

    子进程模型下它已经很轻——但"一次识屏只应该有一个驱动、配置只有一份"这条口径
    没有变，所以仍然只建一个：调用方各自 ``new`` 会得到几份可能不一样的超时设置，
    而"这次超时是 30 秒还是 5 秒"在排查时是个必须唯一的问题。
    """
    global _SHARED_ENGINE
    with _SHARED_LOCK:
        if _SHARED_ENGINE is None:
            _SHARED_ENGINE = OcrEngine()
        return _SHARED_ENGINE


def release_engine() -> bool:
    """「用完就丢」的模块级入口（**兼容层**）：中止仍在跑的识别子进程，返回是否真做了事。

    切片 2 的语义是"丢掉进程内那份常驻引擎"；切片 5 之后**进程内不再有引擎可丢**
    ——每次识别的内存都长在子进程里，它退出即归零。这个入口因此被保留成兼容层，
    承载的是一件事：**会话取消/失败时，那个还在跑模型的子进程必须死**。

    因此：

    - 没有任何识别在跑时返回 ``False``（每次截图收口都会调它，这条路径必须零成本，
      不该付一次目录扫描或 ``gc`` 的代价）；
    - 有识别在跑时中止它并返回 ``True``。被中止的那一次 ``recognize`` 会抛
      :class:`OcrRecognitionError`（"已被中止"），调用方按失败收口；
    - 生产路径上两者不重叠：``actions`` 只在识别那一步**收口之后**才调它
      （见 ``ScreenCaptureActions._finish``），所以正常识别不会被它半路掐掉。
      它真正的用武之地是异常/取消路径上的兜底清扫。
    """
    return abandon_workers() > 0


# -------------------------------------------------------------- 同包 seam


def recognize(image: QImage) -> str:
    """模块级入口：``recognize(QImage) -> str``——切片的接入点。

    这是同一功能面向 UI 的一层薄壳：结果按阅读顺序用换行拼成一段纯文本，没有文字
    返回空串，失败抛 :class:`OcrError`（``str(exc)`` 直接展示给用户）。
    ``pet/screen_capture/actions.py`` 就是按这个契约解析同包入口的
    （``OCR_MODULE = "ocr"`` / ``OCR_ENTRY = "recognize"``，同步、纯本地、不联网）。

    要**结构化**结果（每块的置信度与框坐标）请用 :meth:`OcrEngine.recognize`：
    那一层是契约测试钉死的主接口，本函数只是它的文本视图。
    """
    blocks = shared_engine().recognize(image)
    return "\n".join(block.text for block in blocks)
