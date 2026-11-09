"""会话持久化：保存/加载/列出历史会话。

格式：<config.session_dir>/<session_id>.json
文件内容：{id, title, created_at, updated_at, system_prompt, history}
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional


class SessionStore:
    """管理 .agents/sessions/ 下的会话文件。"""

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        if sessions_dir is None:
            from prist.config_loader import load_config
            sessions_dir = load_config().session_dir
        self.dir = sessions_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def _safe_id(self, raw: str) -> str:
        """把任意字符串规整成合法文件名（保留中文、英文、数字、连字符）。"""
        import re
        raw = raw.strip().replace('/', '-').replace('\\', '-')
        raw = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', raw, flags=re.UNICODE)
        raw = raw.strip('-')
        return raw[:80] or _dt.datetime.now().strftime('%Y%m%d-%H%M%S')

    def new_id(self) -> str:
        return _dt.datetime.now().strftime('%Y%m%d-%H%M%S')

    def save(self, session_id: str, *, title: str, system_prompt: str,
             history: list[dict], created_at: Optional[str] = None) -> Path:
        now = _dt.datetime.now().isoformat(timespec='seconds')
        data = {
            'id': session_id,
            'title': title or f'会话 {session_id}',
            'created_at': created_at or now,
            'updated_at': now,
            'system_prompt': system_prompt,
            'history': history,
        }
        path = self.dir / f'{session_id}.json'
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    def load(self, session_id: str) -> Optional[dict]:
        path = self.dir / f'{session_id}.json'
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None

    def list_sessions(self) -> list[dict]:
        """返回 [{id, title, updated_at, messages}]，按 updated_at 倒序。"""
        results = []
        for f in self.dir.glob('*.json'):
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
            results.append({
                'id': data.get('id', f.stem),
                'title': data.get('title', f.stem),
                'updated_at': data.get('updated_at', ''),
                'messages': len(data.get('history', [])),
            })
        results.sort(key=lambda x: x['updated_at'], reverse=True)
        return results

    def delete(self, session_id: str) -> bool:
        path = self.dir / f'{session_id}.json'
        if path.is_file():
            path.unlink()
            return True
        return False
