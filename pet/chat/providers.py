from __future__ import annotations
import json, ssl, threading, urllib.error, urllib.request
from collections.abc import Iterator
from typing import Any
from .models import ProviderConfig

import re as _re

try:
    import certifi
except Exception:  # 未安装/未打进包时回退系统默认 CA 库
    certifi = None

def _make_ssl_context(verify: bool):
    """按配置构造 SSL 上下文：verify=False 跳过证书校验（本地网关/自签名）；
    verify=True 优先使用 certifi 的 CA 包（PyInstaller 需 --collect-all certifi）。"""
    if not verify:
        return ssl._create_unverified_context()
    try:
        if certifi is not None:
            return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    return ssl.create_default_context()

def _is_cert_verify_error(reason) -> bool:
    text = str(reason).lower()
    return 'ssl' in text or 'certificate' in text or 'tls' in text

_CERT_HINT = '；如为代理/梯子拦截或自签名证书，可在 AI 设置中勾选"跳过 SSL 证书验证"后重试'

def test_connection(config, timeout: float = 10.0):
    """发送一个最小的非流式请求验证端点连通性（含 TLS 校验）。
    返回 (ok: bool, message: str)，供设置界面"测试连接"使用，不写入任何状态。"""
    try:
        endpoint = normalize_chat_endpoint(config.base_url, config.chat_path)
        payload = {'model': config.model, 'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 1, 'stream': False}
        headers = {'Content-Type': 'application/json'}
        if config.api_key: headers['Authorization'] = f'Bearer {config.api_key}'
        req = urllib.request.Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=timeout, context=_make_ssl_context(config.verify_ssl)) as resp:
            resp.read(4096)
            return True, f'连接成功（HTTP {resp.status}）'
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode('utf-8', 'replace')
        msg = _safe_error_detail(detail)
        if exc.code in (401, 403): return False, f'认证失败（HTTP {exc.code}）：{msg}'
        return False, f'请求失败（HTTP {exc.code}）：{msg}'
    except urllib.error.URLError as exc:
        reason = exc.reason
        hint = _CERT_HINT if _is_cert_verify_error(reason) else ''
        return False, f'网络连接失败：{reason}{hint}'
    except OSError as exc:
        return False, f'网络请求失败：{exc}'

def normalize_chat_endpoint(base_url,chat_path='/v1/chat/completions'):
    base=str(base_url or '').strip().rstrip('/'); path=str(chat_path or '/v1/chat/completions').strip(); path=path if path.startswith('/') else '/'+path
    if base.endswith('/chat/completions'): return base
    # base 已带版本段（/v1、/v4 等 OpenAI 兼容路径，如智谱 /api/paas/v4）→ 只补 /chat/completions
    if path=='/v1/chat/completions' and _re.search(r'/v\d+$',base): return base+'/chat/completions'
    return base+path

class ProviderError(RuntimeError):
    def __init__(self,message,status=None): self.status=status; super().__init__(f'HTTP {status}: {message}' if status else message)

class SSEParser:
    def __init__(self): self._buffer=b''; self.done=False
    def feed(self,chunk:bytes):
        self._buffer+=chunk; out=[]
        while True:
            found=None
            for marker in (b'\r\n\r\n',b'\n\n'):
                i=self._buffer.find(marker)
                if i>=0: found=(i,marker); break
            if not found: break
            i,marker=found; event=self._buffer[:i]; self._buffer=self._buffer[i+len(marker):]; data=[]
            for line in event.replace(b'\r\n',b'\n').split(b'\n'):
                if line and not line.startswith(b':') and line.startswith(b'data:'): data.append(line[5:].lstrip().decode('utf-8','replace'))
            if not data: continue
            payload_text='\n'.join(data).strip()
            if payload_text=='[DONE]': self.done=True; continue
            try: payload=json.loads(payload_text)
            except json.JSONDecodeError as exc: raise ProviderError('Provider 返回了无效 JSON') from exc
            if isinstance(payload,dict) and payload.get('error'):
                err=payload['error']; msg=err.get('message','Provider 返回错误') if isinstance(err,dict) else str(err); raise ProviderError(str(msg))
            choices=payload.get('choices',[]) if isinstance(payload,dict) else []
            if choices:
                delta=choices[0].get('delta',{}) or {}; content=delta.get('content') if isinstance(delta,dict) else None
                if content: out.append(str(content))
        return out

class OpenAICompatibleProvider:
    def stream(self,messages:list[dict[str,Any]],config:ProviderConfig,cancel_event:threading.Event,response_holder:list|None=None)->Iterator[str]:
        endpoint=normalize_chat_endpoint(config.base_url,config.chat_path)
        payload:dict[str,Any]={'model':config.model,'messages':messages,'stream':True,'temperature':config.temperature,'max_tokens':config.max_tokens}
        headers={'Content-Type':'application/json','Accept':'text/event-stream'}
        if config.api_key: headers['Authorization']=f'Bearer {config.api_key}'
        req=urllib.request.Request(endpoint,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers=headers,method='POST')
        try: response=urllib.request.urlopen(req,timeout=config.timeout,context=_make_ssl_context(config.verify_ssl))
        except urllib.error.HTTPError as exc:
            detail=exc.read(2048).decode('utf-8','replace'); raise ProviderError(_safe_error_detail(detail),exc.code) from exc
        except urllib.error.URLError as exc:
            reason=exc.reason; hint=_CERT_HINT if _is_cert_verify_error(reason) else ''
            raise ProviderError(f'网络连接失败：{reason}{hint}') from exc
        except OSError as exc: raise ProviderError(f'网络请求失败：{exc}') from exc
        if response_holder is not None:
            response_holder.append(response)
        parser=SSEParser()
        try:
            while not cancel_event.is_set():
                chunk=response.read(4096)
                if not chunk: break
                for delta in parser.feed(chunk):
                    if cancel_event.is_set(): return
                    yield delta
                if parser.done: break
        finally:
            try: response.close()
            except Exception: pass

def _safe_error_detail(raw):
    try:
        data=json.loads(raw)
        if isinstance(data,dict) and isinstance(data.get('error'),dict): return str(data['error'].get('message','Provider 请求失败'))
    except Exception: pass
    return ' '.join(raw.split())[:300] or 'Provider 请求失败'
