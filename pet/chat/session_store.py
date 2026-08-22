from __future__ import annotations
import json, os, time
from pathlib import Path
from .models import ChatSession, utc_now
class SessionStore:
    def __init__(self,config_dir): self.root=Path(config_dir)/'sessions'
    def _path(self,character_id,session_id): return self.root/character_id/f'{session_id}.json'
    def create(self,character_id,provider_id,system_prompt): return ChatSession.create(character_id,provider_id,system_prompt)
    def save(self,session):
        session.updated_at=utc_now(); path=self._path(session.character_id,session.session_id); path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix('.json.tmp')
        with temp.open('w',encoding='utf-8',newline='\n') as f: json.dump(session.to_dict(),f,ensure_ascii=False,indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(temp,path)
    def load(self,session_id,character_id=None):
        paths=[self._path(character_id,session_id)] if character_id else list(self.root.glob(f'*/{session_id}.json'))
        if not paths: return None
        path=paths[0]
        try: return ChatSession.from_dict(json.loads(path.read_text(encoding='utf-8')))
        except (OSError,ValueError,KeyError,TypeError):
            try: os.replace(path,path.with_name(f'{path.stem}.corrupt-{int(time.time())}{path.suffix}'))
            except OSError: pass
            return None
    def list(self,character_id):
        result=[]; folder=self.root/character_id
        for path in folder.glob('*.json') if folder.is_dir() else []:
            x=self.load(path.stem,character_id)
            if x: result.append(x)
        return sorted(result,key=lambda x:x.updated_at,reverse=True)
    def delete(self,session):
        try: self._path(session.character_id,session.session_id).unlink()
        except FileNotFoundError: pass
    def clear(self,session): session.messages.clear(); self.save(session); return session