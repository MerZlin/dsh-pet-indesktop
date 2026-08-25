# -*- coding: utf-8 -*-
"""看看屏幕：截屏 → 本地存档 → 发给视觉模型 → 返回人设口吻的回应。

隐私约定：截图只存本地（只留最近 KEEP_SHOTS 张），
除用户自己配置的聊天 API 外不发送到任何地方。
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageGrab

from .chat.models import ProviderConfig
from .chat.providers import _make_ssl_context, normalize_chat_endpoint

log = logging.getLogger('dsh-pet-standalone')

MAX_EDGE = 768        # 缩到最长边 768px：够看懂屏幕，token 又不贵
JPEG_QUALITY = 70
KEEP_SHOTS = 20
DEFAULT_VISION_MODEL = 'deepseek-v4-flash-vision-exp'


class VisionError(RuntimeError):
    pass


def resolve_vision_model(p: ProviderConfig) -> str:
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


def foreground_app_info() -> str:
    """前台窗口「进程名 | 标题」（免费的上下文，随截图喂给模型）；拿不到返回空串。"""
    if sys.platform != 'win32':
        return ''
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ''
        length = user32.GetWindowTextLengthW(hwnd)
        title = ''
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ''
        if pid.value:
            h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                try:
                    pbuf = ctypes.create_unicode_buffer(260)
                    size = ctypes.c_ulong(260)
                    if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                        proc = Path(pbuf.value).name
                finally:
                    kernel32.CloseHandle(h)
        parts = [x for x in (proc, title) if x]
        return ' | '.join(parts)
    except Exception:
        return ''


def capture_screen(directory: Path) -> Path:
    """截全屏（含多显示器）→ 缩到最长边 MAX_EDGE → 存 JPEG，只留最近 KEEP_SHOTS 张。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    img = ImageGrab.grab(all_screens=True)
    w, h = img.size
    scale = MAX_EDGE / max(w, h, 1)
    if scale < 1.0:
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    path = directory / time.strftime('screen-%Y%m%d-%H%M%S.jpg')
    img.convert('RGB').save(path, 'JPEG', quality=JPEG_QUALITY)
    shots = sorted(directory.glob('screen-*.jpg'), key=lambda x: x.stat().st_mtime, reverse=True)
    for old in shots[KEEP_SHOTS:]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def _safe_detail(raw: str) -> str:
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            return str(data['error'].get('message', 'Provider 请求失败'))
    except Exception:
        pass
    return ' '.join(raw.split())[:300] or 'Provider 请求失败'


def ask_about_screen(image_path, app_info: str, system_prompt: str, p: ProviderConfig) -> str:
    """把截图 + 前台窗口信息发给视觉模型，返回人设口吻的回应（非流式，一次拿整段）。
    视觉可用独立端点/密钥（vision_base_url/vision_api_key），未配置则复用聊天 provider。"""
    # 视觉独立端点仅在「不同聊天模型」时生效；同聊天模型时强制跟随聊天配置，
    # 否则残留的 GLM 地址会配上 ds 的模型名发出（modelCode 不存在）
    base_url = p.base_url if p.vision_same_as_chat else (p.vision_base_url or p.base_url)
    endpoint = normalize_chat_endpoint(base_url, p.chat_path)
    b64 = base64.b64encode(Path(image_path).read_bytes()).decode('ascii')
    note = app_info or '（拿不到前台窗口信息）'
    user_text = (
        f'主人现在的前台窗口：{note}。\n'
        '这是主人当前的屏幕截图。用你的人设口吻回应一两句就好'
        '（关心、吐槽、好奇都可以），不要把画面内容逐条罗列出来。'
    )
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
    vkey = '' if p.vision_same_as_chat else p.vision_api_key
    if not vkey and not p.vision_same_as_chat and p.vision_api_key_ref:
        from .chat.models import SecretStore
        vkey = SecretStore().get(p.vision_api_key_ref)
    api_key = vkey or p.api_key
    headers = {'Content-Type': 'application/json'}
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
        try:
            with urllib.request.urlopen(req, timeout=max(float(p.timeout), 60.0),
                                        context=_make_ssl_context(p.verify_ssl)) as resp:
                data = json.loads(resp.read().decode('utf-8', 'replace'))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode('utf-8', 'replace')
            if exc.code == 429 and attempt < 2:
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
