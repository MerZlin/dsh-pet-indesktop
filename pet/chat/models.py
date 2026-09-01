from __future__ import annotations
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

def utc_now(): return datetime.now(timezone.utc).isoformat()

def _safe_float(v, default, lo=None, hi=None):
    """容错数值解析：配置被手改/损坏时回退默认值而不是抛异常。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if lo is not None: x = max(lo, x)
    if hi is not None: x = min(hi, x)
    return x

def _safe_int(v, default, lo=None, hi=None):
    try:
        x = int(float(v))
    except (TypeError, ValueError):
        return default
    if lo is not None: x = max(lo, x)
    if hi is not None: x = min(hi, x)
    return x

@dataclass
class ProviderConfig:
    provider_id: str
    name: str='DeepSeek'; base_url: str='https://api.deepseek.com'; chat_path: str='/v1/chat/completions'; model: str='deepseek-v4-flash'; api_key_ref: str=''; api_key: str=''; timeout: float=60.0; temperature: float=0.7; max_tokens: int=2048; vision_model: str=''; vision_same_as_chat: bool=True; vision_base_url: str=''; vision_api_key_ref: str=''; vision_api_key: str=''; verify_ssl: bool=True
    @classmethod
    def from_dict(cls,pid,raw):
        c = cls(str(pid), str(raw.get('name', pid)), str(raw.get('base_url', 'https://api.deepseek.com')), str(raw.get('chat_path', '/v1/chat/completions')), str(raw.get('model', 'deepseek-v4-flash')), str(raw.get('api_key_ref', f'provider/{pid}')), str(raw.get('api_key', '')), _safe_float(raw.get('timeout', 60), 60.0, lo=1.), _safe_float(raw.get('temperature', .7), 0.7, lo=0., hi=2.), _safe_int(raw.get('max_tokens', 2048), 2048, lo=1), verify_ssl=bool(raw.get('verify_ssl', True)))
        c.vision_model=str(raw.get('vision_model','')); c.vision_same_as_chat=bool(raw.get('vision_same_as_chat',True))
        c.vision_base_url=str(raw.get('vision_base_url','')); c.vision_api_key_ref=str(raw.get('vision_api_key_ref','')); c.vision_api_key=str(raw.get('vision_api_key',''))
        return c
    def to_dict(self,include_secret=True):
        d=asdict(self); d.pop('provider_id',None)
        if not include_secret: d.pop('api_key',None); d.pop('vision_api_key',None)
        return d

@dataclass
class ChatSettings:
    enabled: bool=True; active_provider: str='openai-main'; default_system_prompt: str='你是一只可爱的桌面宠物，请用自然、友善的中文和用户交流。'; history_message_limit: int=40; history_char_limit: int=24000; providers: dict[str,ProviderConfig]=field(default_factory=dict)
    @classmethod
    def defaults(cls):
        p=ProviderConfig('openai-main'); return cls(providers={p.provider_id:p})
    @classmethod
    def from_dict(cls,raw):
        raw=raw if isinstance(raw,dict) else {}; d=cls.defaults(); pr=raw.get('providers') if isinstance(raw.get('providers'),dict) else {}
        providers={str(k):ProviderConfig.from_dict(k,v) for k,v in pr.items() if isinstance(v,dict)} or d.providers
        active=str(raw.get('active_provider',next(iter(providers)))); active=active if active in providers else next(iter(providers))
        return cls(bool(raw.get('enabled',True)),active,str(raw.get('default_system_prompt',d.default_system_prompt)),_safe_int(raw.get('history_message_limit',40),40,lo=1),_safe_int(raw.get('history_char_limit',24000),24000,lo=100),providers)
    def to_dict(self,include_secrets=True):
        return {'enabled':self.enabled,'active_provider':self.active_provider,'default_system_prompt':self.default_system_prompt,'history_message_limit':self.history_message_limit,'history_char_limit':self.history_char_limit,'providers':{k:v.to_dict(include_secrets) for k,v in self.providers.items()}}
    @property
    def active_config(self): return self.providers[self.active_provider]

@dataclass
class ChatMessage:
    role: str; content: str; created_at: str=field(default_factory=utc_now); message_id: str=field(default_factory=lambda:uuid.uuid4().hex)
    def to_dict(self): return asdict(self)
    @classmethod
    def from_dict(cls,raw): return cls(str(raw.get('role','user')),str(raw.get('content','')),str(raw.get('created_at',utc_now())),str(raw.get('message_id',uuid.uuid4().hex)))

@dataclass
class ChatSession:
    session_id: str; character_id: str; provider_id: str; system_prompt: str; messages: list[ChatMessage]=field(default_factory=list); created_at: str=field(default_factory=utc_now); updated_at: str=field(default_factory=utc_now); custom_title: str=''; pinned: bool=False
    @classmethod
    def create(cls,character_id,provider_id,system_prompt): return cls(uuid.uuid4().hex,character_id,provider_id,system_prompt)
    def to_dict(self): return {'session_id':self.session_id,'character_id':self.character_id,'provider_id':self.provider_id,'system_prompt':self.system_prompt,'created_at':self.created_at,'updated_at':self.updated_at,'title':self.custom_title,'pinned':self.pinned,'messages':[m.to_dict() for m in self.messages]}
    @classmethod
    def from_dict(cls,raw): return cls(str(raw['session_id']),str(raw['character_id']),str(raw.get('provider_id','')),str(raw.get('system_prompt','')),[ChatMessage.from_dict(x) for x in raw.get('messages',[]) if isinstance(x,dict)],str(raw.get('created_at',utc_now())),str(raw.get('updated_at',utc_now())),str(raw.get('title',raw.get('custom_title',''))),bool(raw.get('pinned',False)))

    @property
    def title(self):
        """Upstream-compatible alias while modern UI keeps custom_title."""
        return self.custom_title

    @title.setter
    def title(self, value):
        self.custom_title = str(value or '')

class SecretStore:
    def __init__(self,service_name='dsh-pet-standalone'):
        self.service_name=service_name
        try: import keyring
        except Exception: keyring=None
        self._keyring=keyring
    @property
    def available(self): return self._keyring is not None
    def get(self,ref):
        if not self._keyring or not ref: return ''
        try: return str(self._keyring.get_password(self.service_name,ref) or '')
        except Exception: return ''
    def set(self,ref,value):
        if not self._keyring or not ref: return False
        try: self._keyring.set_password(self.service_name,ref,value); return True
        except Exception: return False
