from __future__ import annotations
import json, threading, urllib.error, urllib.request
from collections.abc import Iterator
from typing import Any
from .models import ProviderConfig

def normalize_chat_endpoint(base_url,chat_path='/v1/chat/completions'):
    base=str(base_url or '').strip().rstrip('/'); path=str(chat_path or '/v1/chat/completions').strip(); path=path if path.startswith('/') else '/'+path
    if base.endswith('/chat/completions'): return base
    if path=='/v1/chat/completions' and base.endswith('/v1'): return base+'/chat/completions'
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
    def stream(self,messages:list[dict[str,str]],config:ProviderConfig,cancel_event:threading.Event)->Iterator[str]:
        endpoint=normalize_chat_endpoint(config.base_url,config.chat_path)
        payload:dict[str,Any]={'model':config.model,'messages':messages,'stream':True,'temperature':config.temperature,'max_tokens':config.max_tokens}
        headers={'Content-Type':'application/json','Accept':'text/event-stream'}
        if config.api_key: headers['Authorization']=f'Bearer {config.api_key}'
        req=urllib.request.Request(endpoint,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),headers=headers,method='POST')
        try: response=urllib.request.urlopen(req,timeout=config.timeout)
        except urllib.error.HTTPError as exc:
            detail=exc.read(2048).decode('utf-8','replace'); raise ProviderError(_safe_error_detail(detail),exc.code) from exc
        except urllib.error.URLError as exc: raise ProviderError(f'网络连接失败：{exc.reason}') from exc
        except OSError as exc: raise ProviderError(f'网络请求失败：{exc}') from exc
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