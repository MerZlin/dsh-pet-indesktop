from __future__ import annotations
import json
from pathlib import Path
from .models import ChatMessage, ChatSettings

def load_character_manifest(root:Path,character_id:str):
    try: raw=json.loads((Path(root)/character_id/'manifest.json').read_text(encoding='utf-8'))
    except (OSError,ValueError): return {}
    return raw if isinstance(raw,dict) else {}

def load_character_prompt(root:Path,character_id:str):
    chat=load_character_manifest(root,character_id).get('chat',{})
    return str(chat.get('system_prompt','')) if isinstance(chat,dict) else ''

class PromptBuilder:
    def __init__(self,characters_root=None): self.characters_root=Path(characters_root) if characters_root else None
    def effective_system_prompt(self,settings,character_id,role_prompt=None):
        if role_prompt and role_prompt.strip(): return role_prompt.strip()
        if self.characters_root:
            prompt=load_character_prompt(self.characters_root,character_id)
            if prompt.strip(): return prompt.strip()
        return settings.default_system_prompt.strip()
    def build_messages(self,settings,character_id,history,user_text,role_prompt=None):
        return [{'role':'system','content':self.effective_system_prompt(settings,character_id,role_prompt)},*({'role':m.role,'content':m.content} for m in self.trim_history(history,settings.history_message_limit,settings.history_char_limit)),{'role':'user','content':user_text.strip()}]
    @staticmethod
    def trim_history(history,message_limit,char_limit):
        result=[]; used=0; limit=max(100,char_limit)
        for m in reversed(history):
            if result and (len(result)>=max(1,message_limit) or used+len(m.content)>limit): break
            if not result and len(m.content)>limit:
                result.append(ChatMessage(m.role,m.content[-limit:],m.created_at,m.message_id)); break
            result.append(m); used+=len(m.content)
        return list(reversed(result))