# -*- coding: utf-8 -*-
"""「快速识屏」本地 OCR 的**子进程工作端**：一趟一格，用完就丢。

``python -m pet.screen_capture.ocr_worker``（冻结包是 ``<exe> --ocr-worker``）。
一次调用只认一张图，**读完成即退出**——进程退出是操作系统级的归还，native 堆、
ONNXRuntime 的 arena、cv2/numpy 已 import 的模块内存统统随进程消失。

## 为什么是子进程（切片 5 的用户拍板，B 方案）

切片 2 走的是"进程内懒加载 + 用完 unload"：``OcrEngine.unload()`` 丢掉引擎与三份
session 再 ``gc.collect()``。真机实测（本机 2026-10-13）那句话的边界是：加载后 RSS
+108 MB、unload 只回吐 **约 30 MB**（152.4 → 122.5 MB），再等 5 秒也不动——
释放的是 Python 引用与 session 对象，**native 堆不还给操作系统**。冻结包同一组
数字（42.6 → 137.3，unload 后 108.3），结论一样。

所以"用完就丢"要真的成立，只能让**进程**死掉。代价是每次触发多付一次进程启动 +
模型加载（实测见 ``.scratch/screen-capture/probe_ocr_subprocess.txt``），换来的是
RSS 回到触发前的水位——这是用户明确选择的取舍（"内存用完真归零"）。

## 协议（管道，图像**不落盘**）

宿主 → 本进程（stdin，二进制）::

    b"DSHOCR1"          # 7 字节魔数
    uint32 大端          # PNG 字节数
    PNG 字节             # 紧接着，正好那个长度；随后 EOF

本进程 → 宿主（stdout）：**一行** UTF-8 JSON::

    {"blocks": [{"text": "…", "score": 0.99, "box": [[x, y], [x, y], [x, y], [x, y]]}, …]}

日志与非结果输出一律走 stderr（见 :func:`reserve_stdout`：native 库往 fd 1 打的
任何东西都会被挪到 stderr，stdout 只留协议那一行）。

## 退出码语义（0/3 是成功，别再拿 1 当"没有文字"）

============================  ==================================================
码                             含义
============================  ==================================================
``0``  :data:`EXIT_TEXT`      识别完成，blocks 非空
``3``  :data:`EXIT_NO_TEXT`   识别完成，图里没有文字（**不是故障**）
``4``  :data:`EXIT_PROTOCOL`  stdin 数据不合协议（调用方 bug）
``5``  :data:`EXIT_UNAVAILABLE`  依赖没装全（rapidocr / onnxruntime）
``6``  :data:`EXIT_MODEL_MISSING`  随包模型文件缺失（安装不完整）
``7``  :data:`EXIT_LOAD_FAILED`    引擎/session 加载失败
``8``  :data:`EXIT_RECOGNIZE_FAILED`  引擎起来了但这次识别失败
============================  ==================================================

**1 与 2 刻意不分配给任何语义**：Windows 上 ``TerminateProcess``（``Popen.kill()``
走的就是它）把退出码写成 1，也见过 2。硬杀必须能被宿主一眼认成"被中止/崩溃"，
绝不能伪装成"图里没有文字"——那会让取消/超时看起来像一次正常的空结果。

## 边界

纯 rapidocr / onnxruntime / numpy / cv2，**不 import PySide**（见 ``ocr_models.py``
的说明）。不联网：加载路径上不存在下载分支（session 由本模块自建后注入，见
:func:`_attach_sessions`）。不落盘：图像只存在于管道与内存里。
"""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import sys
import time

from .ocr_models import MODEL_FILES, model_problem, resolve_model_dir

__all__ = [
    "EXIT_LOAD_FAILED",
    "EXIT_MODEL_MISSING",
    "EXIT_NO_TEXT",
    "EXIT_PROTOCOL",
    "EXIT_RECOGNIZE_FAILED",
    "EXIT_TEXT",
    "EXIT_UNAVAILABLE",
    "MAGIC",
    "MAX_FRAME_BYTES",
    "STDERR_PREFIX",
    "WorkerError",
    "decode_png",
    "load_engine",
    "main",
    "read_exact",
    "read_frame",
    "reserve_stdout",
    "stdin_stream",
    "to_blocks",
    "write_all",
    "write_result",
]

#: 帧头魔数：宿主写错、管道里出现别的数据时立刻判协议错，而不是拿一堆字节去解码。
MAGIC = b"DSHOCR1"

#: 单帧上限（PNG 字节数）。2560×1440 的桌面截图 PNG 实测在 1~3 MB，64 MiB 是三个
#: 数量级的余量；超了就是调用方给错了东西（或长度头被写坏），不能拿它去 malloc。
MAX_FRAME_BYTES = 64 * 1024 * 1024

EXIT_TEXT = 0
EXIT_NO_TEXT = 3
EXIT_PROTOCOL = 4
EXIT_UNAVAILABLE = 5
EXIT_MODEL_MISSING = 6
EXIT_LOAD_FAILED = 7
EXIT_RECOGNIZE_FAILED = 8

#: 本进程自己那几行结构化记录的 stderr 前缀（宿主要的是"哪一行是我说的"，而不是
#: 从 rapidocr/onnxruntime 的噪声里猜）。
STDERR_PREFIX = "dsh-ocr-worker: "

#: 进程启动时刻：每条记录都带上"距启动多少毫秒"。这一列是给"识屏怎么变慢了"准备的
#: ——它一眼分出慢在解释器启动、模型加载还是推理（宿主只读 stage/error 两个键，
#: 多出来的这一列不影响协议）。
_STARTED = time.monotonic()

#: 交给 rapidocr 的固定配置。GPU 相关的开关在这里显式关一遍，作为"执行提供者只有
#: CPU"的第二道防线（第一道是本模块自建 session 时写死的 providers）。日志放到
#: ``error``：rapidocr 在整张图没检出文字时会打一条 WARNING，而"截了一张没有文字的
#: 图"是**正常输入**，不该污染用户的日志。
_RAPIDOCR_PARAMS = {
    "Global.log_level": "error",
    "EngineConfig.onnxruntime.use_cuda": False,
    "EngineConfig.onnxruntime.use_dml": False,
    "EngineConfig.onnxruntime.enable_cpu_mem_arena": False,
}

#: 线程预算的上下界（设计约束：CPU 限定 1~2 线程）。桌宠是常驻后台程序，识别期间
#: 把整机 CPU 打满、或让工作线程空转让风扇起飞，都是不可接受的代价。
_MIN_CPU_THREADS = 1
_MAX_CPU_THREADS = 2
_DEFAULT_CPU_THREADS = _MAX_CPU_THREADS


class WorkerError(RuntimeError):
    """工作端内部的可预期失败：``code`` 就是该退的那个码，``str(exc)`` 是给用户的人话。"""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = int(code)


# ------------------------------------------------------------------ 协议读写


def read_exact(stream, size: int) -> bytes:
    """从二进制流**读满** ``size`` 字节；期间遇到 EOF 就返回已经读到的那些。

    ``read(n)`` 在管道/裸 ``FileIO`` 上可能**短读**（内核缓冲还没攒够就返回），
    把短读当"读完了"是协议层的经典坑：4 字节长度头被读成 2 字节，后面全错位。
    上游给的是 ``BufferedReader`` 时本函数等价于一次 ``read``，没有额外代价。
    """
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream) -> bytes:
    """从二进制流读一帧（魔数 + 大端长度 + PNG 字节）；不合协议抛 :class:`WorkerError`。

    每一步都判长度而不是"读一次信它"：管道会被截断（宿主被杀、磁盘/内存吃紧），
    半帧数据硬塞给解码器只会得到一句没头没脑的 cv2 报错。
    """
    magic = read_exact(stream, len(MAGIC))
    if magic != MAGIC:
        raise WorkerError(
            f"输入的帧头不对（期望 {MAGIC!r}，收到 {magic!r}）"
            "——调用方没有按协议写 stdin，这次识别没开始",
            EXIT_PROTOCOL,
        )
    header = read_exact(stream, 4)
    if len(header) != 4:
        raise WorkerError("输入的帧长度头不完整（读不到 4 字节）——管道被提前关闭", EXIT_PROTOCOL)
    (length,) = struct.unpack(">I", header)
    if length == 0:
        raise WorkerError("输入的帧长度为 0——调用方没写图像数据", EXIT_PROTOCOL)
    if length > MAX_FRAME_BYTES:
        raise WorkerError(
            f"输入的帧长度 {length} 字节超过上限 {MAX_FRAME_BYTES} 字节——"
            "长度头或数据可能损坏，拒绝分配这么多内存",
            EXIT_PROTOCOL,
        )
    payload = read_exact(stream, length)
    if len(payload) != length:
        raise WorkerError(
            f"图像数据不完整（声明 {length} 字节，只收到 {len(payload)} 字节）——管道被提前关闭",
            EXIT_PROTOCOL,
        )
    return payload


def write_all(fd: int, data: bytes) -> None:
    """把 ``data`` 全部写进 fd。

    ``os.write`` **允许短写**（管道写缓冲区一满就只写一半），而结果 JSON 里带的是
    整张截图的文字：一次写不完是常态而不是异常。不补写的下场是宿主收到半行
    JSON——那会被判成"工作进程输出无法解析"，排查方向直接跑偏。
    """
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def reserve_stdout() -> int:
    """把真正的 stdout 挪到一个私有 fd 上，让 fd 1 指向 stderr；返回那个 fd。

    stdout 是**协议通道**：里面只允许有那一行结果 JSON。cv2 / onnxruntime /
    rapidocr 都是 native 库，它们（或它们拉着的东西）往 fd 1 打一行就足以让 JSON
    解析失败——而那种故障看起来会像"识别坏了"，排查方向全错。这里在**任何重依赖
    被 import 之前**先把 fd 1 改道到 stderr：之后谁往 1 写都只是日志，协议通道保持
    干净。

    本机实测（2026-10-13）没有观察到 native 库往 stdout 写东西；这一步是廉价的
    结构性保险，不是被本地复现的故障修复。
    """
    saved = os.dup(1)
    os.dup2(2, 1)
    return saved


def write_result(fd: int, blocks: list[dict]) -> None:
    """把结果 JSON 写进保留的 stdout fd（不经过 ``sys.stdout``：冻结的 windowed 包里它可能是 None）。"""
    payload = json.dumps({"blocks": blocks}, ensure_ascii=False, separators=(",", ":"))
    write_all(fd, payload.encode("utf-8") + b"\n")


def emit(record: dict) -> None:
    """往 stderr 写一条本进程自己的结构化记录（宿主按 :data:`STDERR_PREFIX` 认）。

    直接写 fd 2 而不是 ``sys.stderr``：windowed 冻结包里 ``sys.stderr`` 可能是
    ``None``，而"报不出错"正是最难查的那种故障。fd 2 由宿主保证是个管道。
    """
    try:
        stamp = {"ms": int((time.monotonic() - _STARTED) * 1000)}
        line = STDERR_PREFIX + json.dumps({**stamp, **record}, ensure_ascii=False) + "\n"
        write_all(2, line.encode("utf-8"))
    except Exception:  # 连 stderr 都写不动时只能放弃：宿主会按退出码给人话
        pass


# ------------------------------------------------------------------ 引擎加载


def _describe(exc: BaseException) -> str:
    """异常 → 一句可读的原因；消息为空时退回到类型名。

    实测（冻结包缺 numpy，2026-10-13）：``ImportError`` 系异常在冻结环境里可能
    ``str(exc) == ""``，直接拼进人话里就得到"……加载失败（x.onnx）："这种以冒号
    结尾的空话——用户拿它什么都做不了，排查的人也无从下手。
    """
    detail = str(exc).strip()
    return detail if detail else type(exc).__name__


def _new_session(model_path, cpu_threads: int):
    """按设计约束建一个 CPU-only、关自旋的 ONNXRuntime session。"""
    from onnxruntime import GraphOptimizationLevel, InferenceSession, SessionOptions

    options = SessionOptions()
    # 4 = 只报 fatal：onnxruntime 的 info 级日志会把控制台刷满。
    options.log_severity_level = 4
    options.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
    options.intra_op_num_threads = cpu_threads
    options.inter_op_num_threads = 1
    options.enable_cpu_mem_arena = False
    for key in ("session.intra_op.allow_spinning", "session.inter_op.allow_spinning"):
        try:
            options.add_session_config_entry(key, "0")
        except Exception as exc:  # 不同 ORT 版本对配置键的接受度不一样
            emit({"note": f"{key} 设置失败，沿用 ONNXRuntime 默认值（{exc}）"})
    # 只挂 CPU：首版禁 GPU（没有实机验证过 GPU 路径，宁可慢也不给用户装一个
    # 会在别的机器上崩的默认值）。
    return InferenceSession(str(model_path), sess_options=options, providers=["CPUExecutionProvider"])


def _attach_sessions(engine, sessions: dict) -> None:
    """把本模块自建的 session 塞进 rapidocr 的配置。

    rapidocr 的 ``OrtInferSession`` **本来**就支持外部传入 session（``cfg["session"]``，
    上游 PR #451），但它的参数入口经 OmegaConf，拒收任意对象值（实测
    ``UnsupportedValueType: Value 'InferenceSession' is not a supported primitive
    type``），所以只能建好引擎之后写回它自己的 cfg；``allow_objects`` 正是
    OmegaConf 为这种"配置里要挂一个活对象"提供的开关。

    为什么非走这条路不可：rapidocr 自建 session 时会去读 ``Det/Rec/Cls`` 的
    ``model_path``，而该字段为空时它**会去网上下**（``DownloadFile.run``）。把
    session 从外面塞进去，那条分支直接短路——"断网可用"于是是结构保证，而不是
    "但愿文件都在"。
    """
    from omegaconf import OmegaConf

    for section, session in sessions.items():
        node = OmegaConf.create(
            OmegaConf.to_container(engine.cfg[section]), flags={"allow_objects": True}
        )
        node.session = session
        engine.cfg[section] = node


#: 与 ``ocr.OCR_NO_REC_FLAG`` 是**同一个字面量**。这里刻意不 import 那个模块：
#: ``ocr.py`` 会拉进 Qt，而这个进程只为识别而生，不该为解析一个开关多付 Qt 的导入。
#: 两处字面量由 tests/test_ocr_use_rec.py 的用例比对，防止悄悄写歪。
NO_REC_FLAG = "--ocr-no-rec"


def load_engine(cpu_threads: int = _DEFAULT_CPU_THREADS, use_rec: bool = True):
    """按模型目录建好引擎；失败一律抛 :class:`WorkerError`（人话即 ``str(exc)``）。"""
    directory = resolve_model_dir()
    problem = model_problem(directory)
    if problem:
        raise WorkerError(problem, EXIT_MODEL_MISSING)

    if importlib.util.find_spec("onnxruntime") is None:
        raise WorkerError(
            "本地 OCR 运行库 onnxruntime 没装——依赖随安装包分发，缺失说明安装不完整，请重新安装",
            EXIT_UNAVAILABLE,
        )
    try:
        import rapidocr
    except ImportError as exc:
        raise WorkerError(
            f"本地 OCR 库 rapidocr 不可用（{_describe(exc)}）——依赖随安装包分发，缺失请重新安装",
            EXIT_UNAVAILABLE,
        ) from exc

    threads = max(_MIN_CPU_THREADS, min(_MAX_CPU_THREADS, int(cpu_threads)))
    sessions = {}
    for section, file_name in MODEL_FILES.items():
        try:
            sessions[section] = _new_session(directory / file_name, threads)
        except Exception as exc:
            raise WorkerError(
                f"本地 OCR 模型加载失败（{file_name}）：{_describe(exc)}", EXIT_LOAD_FAILED
            ) from exc

    params = dict(_RAPIDOCR_PARAMS)
    if not use_rec:
        # 只检测：不跑识别模型。注意**仍然注入全部 session**（含识别模型）——
        # 注入是"断网可用"的承重结构：模型路径为空时 rapidocr 会转去联网下载。
        # 这里省的是识别那一半的**推理时间**（实测约占一半），不是加载时间。
        params["Global.use_rec"] = False
    try:
        engine = rapidocr.RapidOCR(params=params)
    except Exception as exc:
        raise WorkerError(f"本地 OCR 引擎初始化失败：{_describe(exc)}", EXIT_LOAD_FAILED) from exc
    try:
        _attach_sessions(engine, sessions)
    except Exception as exc:
        # 注入失败必须当**加载失败**报出来：它是"断网可用"的承重结构——没注入成功，
        # rapidocr 在第一次推理时会走"模型路径为空 → 去下载"那条分支。
        raise WorkerError(
            f"本地 OCR 引擎的 session 注入失败：{_describe(exc)}", EXIT_LOAD_FAILED
        ) from exc
    emit({"stage": "loaded", "model_dir": str(directory), "cpu_threads": threads,
          "use_rec": use_rec})
    return engine


# ---------------------------------------------------------------- 像素 → 数组


def decode_png(png: bytes):
    """PNG 字节 → 连续 BGR ``ndarray``（rapidocr 直接吃）。

    解码交给 cv2 而不是让宿主先转成裸像素：宿主侧只需要把 ``QImage`` 编码成 PNG
    （无损、Qt 自己保证通道序），解码这一半留在只有快照数据的地方——管道里过的
    始终是一张图本身，而不是一段带 stride 约定的原始内存。

    ``IMREAD_COLOR`` 是关键：它把带 alpha / 灰度 / 调色板的 PNG 一律折成 3 通道
    BGR，rapidocr 的预处理只认这一种形状。
    """
    import cv2
    import numpy as np

    buffer = np.frombuffer(png, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise WorkerError(
            f"图像解码失败（{len(png)} 字节的 PNG 解不开）——管道里的数据不是一张能读的图",
            EXIT_RECOGNIZE_FAILED,
        )
    return image


def to_blocks(result, use_rec: bool = True) -> list[dict]:
    """引擎原始输出 → 原始顺序的块字典列表（没文字就是空列表，**不是**错误）。

    只做"取出 + 校验形状"，**不排阅读顺序**：顺序是展示层的契约（``ocr.TextBlock``
    与 ``reading_order`` 都在宿主侧），工作端越薄，能出的错越少。

    ``use_rec=False``（只检测）时结果里没有文字、只有框：这时**不再要求文字**，把框
    原样发出去、``text`` 留空字符串——调用方要的如果只是"哪里有文字"，这就够了。
    """
    if result is None:
        raise WorkerError("本地 OCR 引擎没有返回结果", EXIT_RECOGNIZE_FAILED)
    if not use_rec:
        boxes = getattr(result, "boxes", None)
        # **不能写 `if not boxes`**：rapidocr 的 boxes 是 ndarray，多元素数组的真值
        # 判断会直接抛 "truth value of an array ... is ambiguous"（下面那几行特意用
        # `is None` / `len(...)` 就是这个原因）。实机验证时正是这里炸的。
        if boxes is None or len(boxes) == 0:
            return []
        return [{"text": "", "score": 0.0,
                 "box": [[float(point[0]), float(point[1])] for point in box]}
                for box in boxes]
    texts = getattr(result, "txts", None)
    if not texts:
        return []
    boxes = getattr(result, "boxes", None)
    scores = getattr(result, "scores", None)
    if boxes is None or len(boxes) < len(texts):
        raise WorkerError(
            f"识别结果缺框坐标（文字 {len(texts)} 块，框 {0 if boxes is None else len(boxes)} 个）",
            EXIT_RECOGNIZE_FAILED,
        )

    blocks: list[dict] = []
    for index, text in enumerate(texts):
        if not text:
            continue
        score = float(scores[index]) if scores is not None and index < len(scores) else 0.0
        box = [[float(point[0]), float(point[1])] for point in boxes[index]]
        blocks.append({"text": str(text), "score": score, "box": box})
    return blocks


# ---------------------------------------------------------------------- 入口


def run(stdin, use_rec: bool = True) -> tuple[int, list[dict]]:
    """跑完一次识别；返回 ``(退出码, blocks)``。

    退出码只有"成功"两种之一或某个故障码——人话原因由 :func:`main` 从
    :class:`WorkerError` 里取。分阶段 emit ``stage`` 记录是为了让宿主在**硬崩**
    （native 崩、被强杀）时还能说清是"在加载模型"还是"在识别"时出的事。
    """
    payload = read_frame(stdin)
    emit({"stage": "frame-read", "png_bytes": len(payload)})

    engine = load_engine(use_rec=use_rec)
    pixels = decode_png(payload)
    emit({"stage": "decoded", "shape": list(pixels.shape)})

    try:
        blocks = to_blocks(engine(pixels), use_rec=use_rec)
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError(f"本地 OCR 识别失败：{_describe(exc)}", EXIT_RECOGNIZE_FAILED) from exc
    return (EXIT_TEXT if blocks else EXIT_NO_TEXT), blocks


def stdin_stream():
    """本进程的二进制 stdin。

    ``sys.stdin`` 在**冻结的 windowed 包**里可能是 ``None``（没有控制台就没有标准
    流对象），而 fd 0 由宿主的 ``PIPE`` 保证是好的——所以取不到 Python 流时直接按
    fd 0 打开一个。``closefd=False``：这个流不是我们建的，不负责关。
    """
    stream = getattr(sys, "stdin", None)
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        return buffer
    return os.fdopen(0, "rb", buffering=0, closefd=False)


def main(argv=None) -> int:
    """工作端入口：读一帧、认一次、写出结果、退出。返回进程退出码。

    命令行只认一个开关：``--ocr-no-rec``（只检测、不跑识别模型，见
    ``ocr.worker_command``）。其余参数一律忽略——冻结态下宿主传给本进程的
    第一个参数是 ``--ocr-worker``（内部子命令），本进程不需要再解析它。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    use_rec = NO_REC_FLAG not in args
    result_fd = reserve_stdout()
    try:
        code, blocks = run(stdin_stream(), use_rec=use_rec)
    except WorkerError as exc:
        emit({"error": str(exc), "code": exc.code})
        return exc.code
    except Exception as exc:  # 没预料到的失败：也得给人话，不能让宿主去读栈
        emit({"error": f"本地 OCR 工作进程内部错误：{_describe(exc)}", "code": EXIT_RECOGNIZE_FAILED})
        return EXIT_RECOGNIZE_FAILED
    try:
        write_result(result_fd, blocks)
    except OSError as exc:  # 宿主已经把读端关了（超时/取消）：这次结果没人要，不是故障
        emit({"error": f"结果写不出去（{exc}）——宿主可能已经中止了这次识别"})
        return EXIT_RECOGNIZE_FAILED
    return code


if __name__ == "__main__":  # pragma: no cover - 进程入口，由宿主/测试以子进程方式驱动
    raise SystemExit(main())
