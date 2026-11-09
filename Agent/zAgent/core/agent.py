"""Agent 主体：LLM 对话 + Function Calling 循环。

新增能力：
- 上下文压缩（/compact 命令）
- 上下文长度监控 + 80% 阈值警告 + 暂停输入
- recallContext 工具
- 会话持久化（/new /save /resume 的底层状态）
- AGENTS.md 一次注入
- 所有硬编码参数改为 config.ini 优先
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from .context_archive import (
    COMPACT_THRESHOLD,
    DEFAULT_CONTEXT_WINDOW,
    estimate_messages_tokens,
)
from .events import (
    AgentError,
    AgentEvent,
    TextDelta,
    ToolCallBegin,
    ToolCallEnd,
    TurnEnd,
)
from .registry import ToolRegistry
from .session_store import SessionStore
from .skills import SkillRegistry, build_skills_prompt
from .tools import register_builtin_tools


DEFAULT_SYSTEM_PROMPT = """你是 zAgent，一个有用的 AI 助手。
你可以使用工具来执行 shell 命令、读写文件、以及加载技能。
"""


def _load_agents_md(project_root: Optional[Path] = None) -> Optional[str]:
    """读取项目根目录下的 AGENTS.md（如果存在）。"""
    root = Path(project_root) if project_root else Path.cwd()
    for name in ('AGENTS.md', '.agents.md'):
        p = root / name
        if p.is_file():
            try:
                return p.read_text(encoding='utf-8').strip()
            except Exception:
                continue
    return None


class Agent:
    """Agent = System Prompt + 对话历史 + 工具注册表 + LLM 客户端。"""

    def __init__(
        self,
        llm_client: Optional[Callable] = None,
        tool_registry: Optional[ToolRegistry] = None,
        skill_registry: Optional[SkillRegistry] = None,
        system_prompt: Optional[str] = None,
        max_tool_rounds: Optional[int] = None,
        context_window_size: Optional[int] = None,
        compact_threshold: Optional[float] = None,
        use_config: bool = True,
    ) -> None:
        # ---- 先从 config 加载默认值 ----
        cfg = None
        if use_config:
            try:
                from prist.config_loader import load_config
                cfg = load_config()
            except Exception:
                cfg = None

        # ---- 参数：显式传入 > config > 代码默认 ----
        if context_window_size is None:
            context_window_size = (
                cfg.context_window_size if cfg else DEFAULT_CONTEXT_WINDOW
            )
        if compact_threshold is None:
            compact_threshold = (
                cfg.compact_threshold if cfg else COMPACT_THRESHOLD
            )
        if max_tool_rounds is None:
            max_tool_rounds = (
                cfg.max_tool_rounds if cfg else 10
            )

        # ---- Skill 发现（含 config 指定的额外目录）----
        extra_paths = []
        if cfg:
            try:
                extra_paths = cfg.extra_skills_paths
            except Exception:
                pass
        self._skill_registry = skill_registry or SkillRegistry.discover(extra_dirs=extra_paths)

        # ---- 工具注册（MCP 异步加载）----
        self._registry = tool_registry or ToolRegistry()
        register_builtin_tools(self._registry, self._skill_registry)
        self._mcp_status_fn = None  # 稍后赋值
        try:
            from core.tools import start_mcp_background_load
            self._mcp_status_fn = start_mcp_background_load(self._registry)
        except Exception:
            self._mcp_status_fn = lambda: {'phase': 'disabled', 'servers': []}

        # ---- System Prompt ----
        base = system_prompt if system_prompt is not None else DEFAULT_SYSTEM_PROMPT
        skills_block = build_skills_prompt(self._skill_registry)
        self._system_prompt = (base + '\n\n' + skills_block).rstrip()

        # ---- LLM 客户端 ----
        self._llm_client = llm_client or _mock_llm_client
        self._max_tool_rounds = max_tool_rounds

        # ---- 对话历史 ----
        self.history: list[dict] = []

        # ---- 上下文窗口配置 ----
        self.context_window_size = context_window_size
        self.compact_threshold = compact_threshold

        # ---- 压缩状态 ----
        self.is_compacting = False

        # ---- 会话持久化 ----
        self._sessions = SessionStore(cfg.session_dir if cfg else None)
        self.session_id = self._sessions.new_id()
        self.session_title = '新会话'
        self._created_at = None

        # ---- AGENTS.md 一次注入 ----
        agents_content = _load_agents_md()
        if agents_content:
            self.history.append({
                'role': 'system',
                'content': (
                    '【项目约定文件 AGENTS.md 已加载，后续对话中需遵循以下约定】\n\n'
                    + agents_content
                ),
            })

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def mcp_status(self) -> dict:
        """返回 MCP 加载状态。"""
        if self._mcp_status_fn:
            return self._mcp_status_fn()
        return {'phase': 'disabled', 'servers': []}

    def current_context_tokens(self) -> int:
        return estimate_messages_tokens(self._system_prompt, self.history)

    def context_status(self) -> dict:
        used = self.current_context_tokens()
        window = self.context_window_size
        percent = used / window if window else 0
        return {
            'used': used,
            'window': window,
            'percent': percent,
            'at_risk': percent >= self.compact_threshold,
            'warning': (
                None
                if percent < self.compact_threshold
                else f'上下文已使用 {percent*100:.0f}%，请执行 /compact 压缩！'
            ),
        }

    @property
    def can_accept_new_input(self) -> bool:
        return not self.is_compacting

    # ------------------------------------------------------------------
    # 会话持久化接口（供 /new /save /resume 调用）
    # ------------------------------------------------------------------

    def save_current(self, title: Optional[str] = None) -> str:
        """保存当前会话，返回 session_id。"""
        if title:
            self.session_title = title
        path = self._sessions.save(
            self.session_id,
            title=self.session_title,
            system_prompt=self._system_prompt,
            history=self.history,
            created_at=self._created_at,
        )
        return str(path)

    def list_sessions(self) -> list[dict]:
        return self._sessions.list_sessions()

    def resume_session(self, session_id: str) -> bool:
        """恢复指定会话。成功返回 True。"""
        data = self._sessions.load(session_id)
        if not data:
            return False
        self.session_id = data.get('id', session_id)
        self.session_title = data.get('title', session_id)
        self._created_at = data.get('created_at')
        self._system_prompt = data.get('system_prompt', self._system_prompt)
        self.history = data.get('history', [])
        return True

    def new_session(self, title: str = '新会话') -> None:
        """开始新会话（清空 history，生成新 id，但保留 system prompt + AGENTS.md）。"""
        self.session_id = self._sessions.new_id()
        self.session_title = title
        self._created_at = None
        self.history = []

        # AGENTS.md 重新注入（如果存在）
        agents_content = _load_agents_md()
        if agents_content:
            self.history.append({
                'role': 'system',
                'content': (
                    '【项目约定文件 AGENTS.md 已加载，后续对话中需遵循以下约定】\n\n'
                    + agents_content
                ),
            })

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def send(self, text: str) -> Generator[AgentEvent, None, None]:
        stripped = text.strip()

        # /compact
        if stripped.startswith('/compact'):
            yield from self._run_compact()
            return

        # 阈值拦截
        status = self.context_status()
        if status['at_risk']:
            yield TurnEnd(
                text=(
                    f'⚠ 当前上下文已使用 {status["percent"]*100:.0f}% '
                    f'（{status["used"]} / {status["window"]} tokens）。\n'
                    f'请先执行 [/compact] 压缩后再继续对话。'
                )
            )
            return

        if not self.can_accept_new_input:
            yield TurnEnd(text='Agent 正在处理中，请稍候…')
            return

        # 正常对话
        self.history.append({'role': 'user', 'content': text})
        try:
            yield from self._run_loop()
        except Exception as exc:
            yield AgentError(message=f'{type(exc).__name__}: {exc}')

    # ------------------------------------------------------------------
    # 压缩流程
    # ------------------------------------------------------------------

    def _run_compact(self) -> Generator[AgentEvent, None, None]:
        from .compact import run_compact

        self.is_compacting = True
        try:
            yield TextDelta(text='🧹 开始上下文压缩…\n')
            yield ToolCallBegin(name='compact', arguments={'note': '开始压缩'})

            result = run_compact(
                llm_client=self._llm_client,
                history=self.history,
                system_prompt=self._system_prompt,
                context_window_size=self.context_window_size,
            )

            if not result.get('ok'):
                err = result.get('error', '未知错误')
                yield ToolCallEnd(name='compact', result=f'压缩失败: {err}', ok=False)
                yield TurnEnd(text=f'❌ 压缩失败: {err}\n原有上下文已保留。')
                return

            self.history = result['new_history']
            archive_id = result['archive_id']
            old_count = result['old_history_count']
            new_count = result['new_history_count']

            yield ToolCallEnd(
                name='compact',
                result=f'压缩完成，归档 {archive_id}',
                ok=True,
            )
            yield TurnEnd(
                text=(
                    f'✅ 压缩完成！\n'
                    f'  - 归档编号: {archive_id}\n'
                    f'  - 原消息数: {old_count} → 新消息数: {new_count}\n'
                    f'  - 摘要 tokens: 约 {result.get("summary_tokens", 0)}\n'
                    f'  - 原始上下文已保存到 .agents/context/{archive_id}.jsonl\n'
                    f'  - 如需检索历史，请用 recallContext 工具。'
                )
            )
        finally:
            self.is_compacting = False

    # ------------------------------------------------------------------
    # Function Calling 循环
    # ------------------------------------------------------------------

    def _run_loop(self) -> Generator[AgentEvent, None, None]:
        for round_idx in range(self._max_tool_rounds):
            messages = [{'role': 'system', 'content': self._system_prompt}] + self.history
            tools = self._registry.function_calling_tools()

            response = self._call_llm(messages, tools)
            content, tool_calls = _parse_response(response)

            if content:
                for ch in content:
                    yield TextDelta(text=ch)
                self.history.append({'role': 'assistant', 'content': content})

            if not tool_calls:
                yield TurnEnd(text=content or '')
                return

            for tc in tool_calls:
                yield from self._dispatch_tool_call(tc)

        yield TurnEnd(text='(达到最大工具轮次限制)')

    def _dispatch_tool_call(self, tc: dict) -> Generator[AgentEvent, None, None]:
        name = tc.get('name', '')
        arguments = tc.get('arguments', {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {'_raw': arguments}

        yield ToolCallBegin(name=name, arguments=arguments)
        ok, result = self._registry.invoke(name, arguments)
        yield ToolCallEnd(name=name, result=result, ok=ok)

        assistant_tool_msg: dict = {
            'role': 'assistant',
            'content': None,
            'tool_calls': [{
                'id': tc.get('id', f'call_{len(self.history)}'),
                'type': 'function',
                'function': {
                    'name': name,
                    'arguments': json.dumps(arguments, ensure_ascii=False),
                },
            }],
        }
        self.history.append(assistant_tool_msg)
        self.history.append({
            'role': 'tool',
            'tool_call_id': tc.get('id', f'call_{len(self.history)}'),
            'content': result,
        })

    def _call_llm(self, messages: list[dict], tools: list[dict]) -> Any:
        return self._llm_client(messages, tools)


# ---- 响应解析 ---------------------------------------------------------------

def _parse_response(response: Any) -> tuple[str, list[dict]]:
    if isinstance(response, str):
        return response, []
    if isinstance(response, dict):
        content = response.get('content') or ''
        tool_calls = response.get('tool_calls') or []
        normalized = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                func = tc.get('function', tc)
                normalized.append({
                    'id': tc.get('id', ''),
                    'name': func.get('name', ''),
                    'arguments': func.get('arguments', func.get('parameters', {})),
                })
        return content, normalized
    return str(response), []


# ---- Mock LLM ---------------------------------------------------------------

def _mock_llm_client(messages: list[dict], tools: list[dict]) -> Any:
    last_user = ''
    for msg in reversed(messages):
        if msg.get('role') == 'user':
            last_user = msg.get('content', '')
            break
    return f'[mock] 你说的是: {last_user}\n（未配置真实 LLM client）'
