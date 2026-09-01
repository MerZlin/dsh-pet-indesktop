# -*- coding: utf-8 -*-
"""看看屏幕：截屏 → 内存 JPEG → 发给视觉模型 → 返回人设口吻的回应。

隐私约定：截图只在内存中处理，全程不落盘，
除用户自己配置的聊天 API 外不发送到任何地方。
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageGrab

# 注意：不在此处顶层 import pet.chat —— 无 Chat 变体打包时排除 pet.chat，
# 顶层导入会导致 pet_entry_no_chat.py 启动即 ModuleNotFoundError。
# 需要的地方在 ask_about_screen 内延迟导入。

log = logging.getLogger('dsh-pet-standalone')

CURSOR_SHOWING = 0x00000001
CURSOR_SUPPRESSED = 0x00000002


class _CursorInfo(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.c_uint),
        ('flags', ctypes.c_uint),
        # ctypes.wintypes does not expose HCURSOR on every Python build;
        # HANDLE has the same pointer-sized layout.
        ('hCursor', ctypes.wintypes.HANDLE),
        ('ptScreenPos', ctypes.wintypes.POINT),
    ]


def get_cursor_visibility(user32=None) -> str:
    """Return SHOWING, HIDDEN, SUPPRESSED, or UNKNOWN without changing input."""
    if sys.platform != 'win32':
        return 'UNKNOWN'
    try:
        user32 = user32 or ctypes.windll.user32
        get_cursor_info = user32.GetCursorInfo
        get_cursor_info.argtypes = [ctypes.POINTER(_CursorInfo)]
        get_cursor_info.restype = ctypes.wintypes.BOOL
        info = _CursorInfo()
        info.cbSize = ctypes.sizeof(_CursorInfo)
        if not get_cursor_info(ctypes.byref(info)):
            return 'UNKNOWN'
        if info.flags & CURSOR_SHOWING:
            return 'SHOWING'
        if info.flags & CURSOR_SUPPRESSED:
            return 'SUPPRESSED'
        return 'HIDDEN'
    except (AttributeError, OSError, TypeError, RuntimeError):
        return 'UNKNOWN'

MAX_EDGE = 768        # 缩到最长边 768px：够看懂屏幕，token 又不贵
JPEG_QUALITY = 70
DEFAULT_VISION_MODEL = 'deepseek-v4-flash-vision-exp'


class VisionError(RuntimeError):
    pass


def resolve_vision_model(p) -> str:
    """推导视觉模型：取消「同聊天模型」且手填了就用filled值；
    否则按聊天模型推导——本身多模态的直接用，ds 文本模型映射到预览版视觉模型。"""
    if not p.vision_same_as_chat and p.vision_model.strip():
        return p.vision_model.strip()
    m = (p.model or '').strip()
    low = m.lower()
    if 'vision' in low:
        return m
    if low.endswith('deepseek-v4-flash'):
        return m + '-vision-exp'
    if low.startswith('deepseek'):
        return DEFAULT_VISION_MODEL
    return m  # kimi 等本身多模态的模型直接用聊天模型


def foreground_window_info() -> dict | None:
    """获取前台窗口详细信息：{hwnd, pid, process, title, rect(x,y,w,h)}。
    若窗口不可见/最小化/被 cloaked 或获取失败，返回 None。"""
    if sys.platform != 'win32':
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # 显式声明签名：默认 restype=c_int 会在 64 位下截断 HWND/HANDLE
        user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
        user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
        user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
        user32.GetWindowRect.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.RECT)]
        user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
        user32.GetWindowTextW.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.wintypes.HANDLE, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
        ]
        kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        # 检查窗口可见性与最小化
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return None

        # 检查是否被 DWM 隐藏/幽灵（如虚拟桌面切换、UWP 挂起）
        # DWMWA_CLOAKED = 14
        cloaked = ctypes.c_int(0)
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmGetWindowAttribute.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
        ]
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        if dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        ) == 0 and cloaked.value != 0:
            return None

        # 获取窗口矩形边界：优先 DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS = 9)
        # 注意：ctypes.wintypes 必须在模块顶部导入——函数体内 import 会让 ctypes
        # 成为局部变量，函数开头的 ctypes.windll 访问直接 UnboundLocalError。
        rect_dwm = ctypes.wintypes.RECT()
        rect: tuple[int, int, int, int] | None = None
        if dwmapi.DwmGetWindowAttribute(
            hwnd, 9, ctypes.byref(rect_dwm), ctypes.sizeof(rect_dwm)
        ) == 0:
            w = rect_dwm.right - rect_dwm.left
            h = rect_dwm.bottom - rect_dwm.top
            if w > 0 and h > 0:
                rect = (rect_dwm.left, rect_dwm.top, w, h)

        if rect is None:
            rect_raw = ctypes.wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect_raw)):
                w = rect_raw.right - rect_raw.left
                h = rect_raw.bottom - rect_raw.top
                if w > 0 and h > 0:
                    rect = (rect_raw.left, rect_raw.top, w, h)

        if rect is None:
            return None

        # 标题
        length = user32.GetWindowTextLengthW(hwnd)
        title = ''
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()

        # PID 与 进程名
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ''
        if pid.value:
            hproc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if hproc:
                try:
                    pbuf = ctypes.create_unicode_buffer(260)
                    size = ctypes.c_ulong(260)
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                        proc = Path(pbuf.value).name
                finally:
                    kernel32.CloseHandle(hproc)

        return {
            'hwnd': hwnd,
            'pid': pid.value,
            'process': proc,
            'title': title,
            'rect': rect,
        }
    except Exception:
        return None


def get_foreground_window_rect() -> tuple[int, int, int, int] | None:
    """获取前台窗口矩形 (x, y, w, h)；拿不到返回 None。"""
    info = foreground_window_info()
    return info['rect'] if info else None


def get_system_idle_seconds() -> float:
    """通过 GetLastInputInfo 获取系统键盘鼠标闲置秒数；非 Windows 恒返回 0.0。"""
    if sys.platform != 'win32':
        return 0.0
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ('cbSize', ctypes.c_uint),
                ('dwTime', ctypes.c_ulong),
            ]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            uptime_ms = ctypes.windll.kernel32.GetTickCount()
            idle_ms = uptime_ms - info.dwTime
            return max(0.0, idle_ms / 1000.0)
    except Exception:
        pass
    return 0.0


def capture_window_rect(rect: tuple[int, int, int, int] | None) -> Any:
    """抓取指定窗口区域（rect: (x, y, w, h) 屏幕全局坐标）。
    仅允许在后台 worker 线程调用！
    针对多屏负坐标：抓取全屏后按虚拟屏幕原点平移裁剪。
    """
    if not rect or rect[2] <= 0 or rect[3] <= 0:
        return None
    try:
        vx, vy = 0, 0
        if sys.platform == 'win32':
            # SM_XVIRTUALSCREEN = 76, SM_YVIRTUALSCREEN = 77
            user32 = ctypes.windll.user32
            vx = user32.GetSystemMetrics(76)
            vy = user32.GetSystemMetrics(77)

        # 抓取所有屏幕形成的虚拟大画布
        all_screen_img = ImageGrab.grab(all_screens=True)
        # 将屏幕全局坐标转换为相对于虚拟屏幕左上角 (vx, vy) 的局部像素坐标
        rx, ry, rw, rh = rect
        crop_left = rx - vx
        crop_top = ry - vy
        crop_right = crop_left + rw
        crop_bottom = crop_top + rh

        # 边界裁剪防越界
        im_w, im_h = all_screen_img.size
        crop_left = max(0, min(im_w, crop_left))
        crop_top = max(0, min(im_h, crop_top))
        crop_right = max(crop_left, min(im_w, crop_right))
        crop_bottom = max(crop_top, min(im_h, crop_bottom))

        if crop_right - crop_left <= 0 or crop_bottom - crop_top <= 0:
            return None

        return all_screen_img.crop((crop_left, crop_top, crop_right, crop_bottom))
    except Exception:
        return None


def foreground_app_info() -> str:
    """前台窗口「进程名 | 标题」（免费的上下文，随截图喂给模型）；拿不到返回空串。
    基于 foreground_window_info() 的薄封装，保持既有返回格式完全不变。"""
    info = foreground_window_info()
    if not info:
        return ''
    parts = [x for x in (info.get('process'), info.get('title')) if x]
    return ' | '.join(parts)


def capture_screen_bytes() -> bytes:
    """截全屏（含多显示器）→ 缩到最长边 MAX_EDGE → 内存 JPEG bytes，全程不落盘。"""
    import io
    img = ImageGrab.grab(all_screens=True)
    w, h = img.size
    scale = MAX_EDGE / max(w, h, 1)
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'JPEG', quality=JPEG_QUALITY)
    return buf.getvalue()


def _safe_detail(raw: str) -> str:
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            return str(data['error'].get('message', 'Provider 请求失败'))
    except Exception:
        pass
    return ' '.join(raw.split())[:300] or 'Provider 请求失败'


def _post_vision_request(
    jpeg_bytes: bytes,
    app_info: str,
    system_prompt: str,
    p,
    memory_context: str = "",
    consume_budget: Any = None,
) -> str:
    """把 JPEG 二进制数据 + 前台窗口信息发给视觉模型，返回人设口吻的回应（非流式）。
    核心内部函数：主动识屏与现有看看屏幕统一复用此函数。

    consume_budget: 可选，每次真实 HTTP 请求前被调用（主动识屏用于按真实请求
    次数消耗每日预算，避免一次触发多次重试却只记一次）。返回 False 表示预算
    已耗尽，函数直接抛出 VisionError 停止后续重试。"""
    # 延迟导入：无 Chat 变体（pet.chat 被排除）下本函数不会被调用
    from .chat.providers import _make_ssl_context, normalize_chat_endpoint, build_browser_headers
    # 视觉独立端点仅在「不同聊天模型」时生效；同聊天模型时强制跟随聊天配置，
    # 否则残留的 GLM 地址会配上 ds 的模型名发出（modelCode 不存在）
    base_url = p.base_url if p.vision_same_as_chat else (p.vision_base_url or p.base_url)
    endpoint = normalize_chat_endpoint(base_url, p.chat_path)
    b64 = base64.b64encode(jpeg_bytes).decode('ascii')
    note = app_info or '（拿不到前台窗口信息）'
    user_text = (
        f'（参考元数据：主人当前前台应用为 {note}）\n'
        '这是主人当前的屏幕截图。用你的人设口吻回应一两句就好'
        '（关心、吐槽、好奇都可以）。请主要根据画面里正在发生的事情来回应；'
        '窗口标题只是参考信息，不要逐字念出，更不要只围绕标题发挥；'
        '也不要把画面内容逐条罗列出来。'
    )
    if memory_context:
        user_text += f"\n（陪伴记忆：{memory_context}）"
    payload = {
        'model': resolve_vision_model(p),
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': user_text},
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
            ]},
        ],
        'stream': False,
        'temperature': p.temperature,
        # ds 视觉模型是推理模型：reasoning 会先吃掉一大段 token，
        # 给太少（如 512）会 finish_reason=length、content 为空 → 必须留足预算
        'max_tokens': max(4096, min(int(p.max_tokens), 8192)),
    }
    model_name = payload['model']
    if model_name.lower().startswith('deepseek') and 'deepseek' in base_url.lower():
        # ds 视觉模型默认开推理（思考十几秒才说话），关掉后 1~2 秒直答
        payload['thinking'] = {'type': 'disabled'}
    headers = build_browser_headers({'Content-Type': 'application/json'})
    # 安全（高优先）：独立视觉端点（vision_same_as_chat=False）绝不能把聊天 Key
    # 一起发过去。只有与聊天同模型时才允许复用聊天 Key；独立端点只认视觉自己的
    # Key（含钥匙串解析），缺失时直接报错，绝不回退到聊天 Key。
    if p.vision_same_as_chat:
        api_key = p.api_key
    else:
        vkey = p.vision_api_key
        if not vkey and p.vision_api_key_ref:
            from .chat.models import SecretStore
            vkey = SecretStore().get(p.vision_api_key_ref)
        api_key = vkey
        if not api_key:
            raise VisionError('独立视觉服务未配置 API Key')
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers=headers,
        method='POST',
    )
    data = None
    last_error: Exception | None = None
    for attempt in range(3):  # 网络错误退避重试；429/过载（免费模型高峰常见）额外重试一次
        # 每次真实 HTTP 请求前消耗预算：主动识屏按真实请求次数计费，
        # 一次触发多次重试各自占用额度；预算耗尽则立即停止，不再发起请求。
        if consume_budget is not None:
            if not consume_budget():
                raise VisionError('每日请求上限已到，今天先陪你到这儿了')
        try:
            with urllib.request.urlopen(req, timeout=max(float(p.timeout), 60.0),
                                        context=_make_ssl_context(p.verify_ssl)) as resp:
                data = json.loads(resp.read().decode('utf-8', 'replace'))
            break
        except urllib.error.HTTPError as exc:
            # CPython 3.11 官方 urllib/response.py 的 addbase 继承
            # tempfile._TemporaryFileWrapper（3.12 起重构为继承 object）；
            # HTTPError(fp=None) 时 addinfourl.__init__ 未执行、实例缺 file 键，
            # exc.read() 会触发 KeyError('file')。防御：读不到响应体就当空处理，
            # 不影响状态码判断。
            try:
                detail = exc.read(2048).decode('utf-8', 'replace')
            except Exception:
                detail = ""
            if exc.code == 429 and attempt < 1:  # 429 最多重试 1 次
                last_error = exc
                time.sleep(2.0)  # 免费视觉模型高峰过载：稍等重试
                continue
            raise VisionError(_safe_detail(detail)) from exc
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
        if attempt < 2:
            time.sleep(1.0)
    if data is None:
        if isinstance(last_error, urllib.error.HTTPError):
            raise VisionError('模型当前访问量大（免费档高峰限流），稍后再点一次试试') from last_error
        reason = getattr(last_error, 'reason', last_error)
        raise VisionError(f'网络连接失败：{reason}')

    choices = data.get('choices') if isinstance(data, dict) else None
    if not choices:
        raise VisionError('视觉模型没说话（无 choices）')
    msg = choices[0].get('message') or {}
    content = msg.get('content')
    if isinstance(content, list):  # 部分实现把 content 拆成 parts
        content = ''.join(
            str(part.get('text', '')) for part in content
            if isinstance(part, dict) and part.get('type') == 'text'
        )
    text = content.strip() if isinstance(content, str) else ''
    if not text:
        finish = str(choices[0].get('finish_reason', ''))
        reasoning = msg.get('reasoning_content')
        if finish == 'length' and reasoning:
            raise VisionError('她想太多把话噎住了（思考超 token），再点一次试试')
        raise VisionError('视觉模型没说话（空回复）')
    return text


def ask_about_screen(image, app_info: str, system_prompt: str, p) -> str:
    """把截图（JPEG bytes）+ 前台窗口信息发给视觉模型，返回人设口吻的回应（非流式，一次拿整段）。
    现为 _post_vision_request 的薄封装；历史兼容：传入 Path/str 时会读取文件 bytes。"""
    jpeg_bytes = (
        image if isinstance(image, (bytes, bytearray)) else Path(image).read_bytes()
    )
    return _post_vision_request(bytes(jpeg_bytes), app_info, system_prompt, p)
