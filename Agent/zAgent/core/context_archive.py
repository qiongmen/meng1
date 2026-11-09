"""Token 估算 + 上下文窗口配置 + 历史归档 + recallContext 工具。

不引入 tiktoken / embedding，用简单估算：
- 英文单词 ≈ 1 token
- 中文字符 ≈ 2 token
- JSON/代码按字符数 // 3 估算
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from core.registry import ToolSpec


# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    规则：
    - 中文字符 → 每个 2 token
    - 纯 ASCII 字母/数字 → 按 4 字符 ≈ 1 token
    - 其他（标点、空格、emoji）→ 每个 1 token
    """
    if not text:
        return 0
    tokens = 0
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:  # CJK 统一汉字
            tokens += 2
        elif 0x3000 <= code <= 0x303F:  # CJK 标点
            tokens += 2
        elif 0xFF00 <= code <= 0xFFEF:  # 全角字符
            tokens += 2
        elif 0x00 <= code <= 0x7F:       # ASCII
            tokens += 1
        else:
            tokens += 2
    # 整体除以 4 做一次平滑（避免过长）
    return max(1, tokens // 2)


def estimate_messages_tokens(system_prompt: str, history: list[dict]) -> int:
    """估算整组 messages 的 token 数（含 system prompt + 所有历史消息）。

    估算项目：
    - 每条消息的 role 字段 ≈ 5 token（近似）
    - 每条 tool_call / tool 返回 ≈ 额外 50 token 固定开销
    """
    total = estimate_tokens(system_prompt) + 10  # system prompt 额外开销

    for msg in history:
        role = msg.get('role', '')
        total += estimate_tokens(role)
        content = msg.get('content') or ''
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            # multi-part
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(part.get('text') or json.dumps(part, ensure_ascii=False))
        # tool_calls
        tool_calls = msg.get('tool_calls') or []
        for tc in tool_calls:
            func = tc.get('function', tc)
            total += estimate_tokens(func.get('name', '') or '')
            total += estimate_tokens(func.get('arguments', '') or '')
            total += 20  # 工具调用额外开销
        if role == 'tool':
            total += 30  # tool 角色额外开销

    return total


# ---------------------------------------------------------------------------
# 默认窗口配置
# ---------------------------------------------------------------------------

DEFAULT_CONTEXT_WINDOW = 128 * 1024  # 128k tokens
COMPACT_THRESHOLD = 0.80             # 80% 触发警告


# ---------------------------------------------------------------------------
# 历史归档
# ---------------------------------------------------------------------------

class ContextArchive:
    """管理 .agents/context/ 下的历史归档文件。"""

    def __init__(self, project_root: Optional[Path] = None) -> None:
        root = Path(project_root) if project_root else Path.cwd()
        self.dir = root / '.agents' / 'context'
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_archive(self, archive_id: str, messages: list[dict]) -> Path:
        """把消息列表保存为 <archive_id>.jsonl。"""
        path = self.dir / f'{archive_id}.jsonl'
        with path.open('w', encoding='utf-8') as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + '\n')
        return path

    def list_archives(self) -> list[str]:
        """返回所有归档编号（按文件名排序，倒序新→旧）。"""
        files = sorted(self.dir.glob('*.jsonl'), reverse=True)
        return [f.stem for f in files]

    def load_archive(self, archive_id: str) -> list[dict]:
        """读取指定归档的所有消息。"""
        path = self.dir / f'{archive_id}.jsonl'
        if not path.is_file():
            return []
        messages: list[dict] = []
        with path.open('r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except Exception:
                    continue
        return messages

    def search(self, text: str, archive_id: Optional[str] = None, max_hits: int = 20) -> list[str]:
        """在归档中全文检索，返回相关的原始消息行片段。

        每个结果字符串包含：归档ID + 角色 + 内容片段。
        """
        keyword = text.strip()
        if not keyword:
            return []

        # 选择要搜索的归档
        if archive_id:
            ids = [archive_id] if (self.dir / f'{archive_id}.jsonl').is_file() else []
        else:
            ids = self.list_archives()

        hits: list[str] = []
        for aid in ids:
            path = self.dir / f'{aid}.jsonl'
            try:
                lines = path.read_text(encoding='utf-8').splitlines()
            except Exception:
                continue
            for idx, line in enumerate(lines):
                if keyword.lower() in line.lower():
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    role = msg.get('role', '?')
                    content = msg.get('content') or ''
                    if isinstance(content, list):
                        content = json.dumps(content, ensure_ascii=False)
                    # 截取匹配关键词附近的上下文
                    snippet = self._make_snippet(content, keyword)
                    hits.append(
                        f'[{aid} 第{idx+1}条 role={role}]\n{snippet}'
                    )
                    if len(hits) >= max_hits:
                        return hits

        return hits

    @staticmethod
    def _make_snippet(content: str, keyword: str, context_chars: int = 80) -> str:
        """截取关键词附近的片段。"""
        if not content:
            return '(空内容)'
        idx = content.lower().find(keyword.lower())
        if idx < 0:
            return content[:200] + ('…' if len(content) > 200 else '')
        start = max(0, idx - context_chars)
        end = min(len(content), idx + len(keyword) + context_chars)
        prefix = '…' if start > 0 else ''
        suffix = '…' if end < len(content) else ''
        return f'{prefix}{content[start:end]}{suffix}'


# ---------------------------------------------------------------------------
# recallContext 工具
# ---------------------------------------------------------------------------

def _recall_context(text: str, id: Optional[str] = None) -> str:
    archive = ContextArchive()
    if not archive.list_archives():
        return '没有找到任何历史归档。只有执行过 /compact 压缩后才会产生归档文件。'

    hits = archive.search(text, archive_id=id, max_hits=15)
    if not hits:
        scope = f'归档 {id}' if id else '所有历史归档'
        return f'在{scope}中没有找到匹配 "{text}" 的内容。'

    header = f'检索关键词: "{text}"  '
    if id:
        header += f'范围: {id}\n'
    else:
        header += f'范围: 所有归档\n'
    header += f'共找到 {len(hits)} 条匹配（已截断到最多 15 条）\n{"="*40}\n'

    # 控制总长度，避免再次占满上下文
    body = '\n\n'.join(hits)
    max_total = 6000
    if len(body) > max_total:
        body = body[:max_total] + '\n\n… (结果过长，已截断)'

    return header + body


RECALL_CONTEXT_SPEC = ToolSpec(
    name='recallContext',
    description=(
        '检索之前被压缩归档的历史上下文。\n'
        '- text: 要查找的关键词或文本（必需）\n'
        '- id: 可选的归档编号（不传则搜索所有归档）\n'
        '只有执行过 /compact 压缩后才会产生可检索的归档。\n'
        '返回匹配的原始消息片段，包含归档编号和所在位置。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'text': {'type': 'string', 'description': '要查找的关键词或文本（必需）'},
            'id': {
                'type': 'string',
                'description': '可选，指定归档编号（如 "20261103-153000"）',
            },
        },
        'required': ['text'],
    },
    handler=_recall_context,
)


def get_recall_context_spec() -> ToolSpec:
    """供 tools.py 注册调用。"""
    return RECALL_CONTEXT_SPEC
