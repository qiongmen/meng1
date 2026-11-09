"""TUI 前端（Textual）。

布局：顶部栏（上下文监控）
      主体：左 = 对话 + 输入 / 右 = 上半思考 + 下半 MCP+会话
      Popup：斜杠命令补全面板

新增：
- /new /save /resume /compact /quit /help 斜杠命令
- Tab 补全
- 侧边栏 MCP 异步刷新（2 秒轮询）
- 侧边栏会话列表（/resume 用）
- 首次启动 config 提示
"""

from __future__ import annotations

import sys
from typing import Iterable

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import Footer, Header, Input, RichLog, Static

from core.agent import Agent
from core.events import AgentError, AgentEvent, TextDelta, ToolCallBegin, ToolCallEnd, TurnEnd

MAX_RESULT_PREVIEW = 500
SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']


# ---------------------------------------------------------------------------
# 多行输入
# ---------------------------------------------------------------------------
class MultiLineInput(Input):
    """Enter 发送，Shift+Enter 换行，Tab 触发补全。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tui_ref: Optional['AgentTUI'] = None

    def on_key(self, event: Key) -> None:
        if event.key == 'shift+enter':
            self.insert_text_at_cursor('\n')
            event.prevent_default()
            event.stop()
        elif event.key == 'tab':
            # Tab 键触发斜杠命令补全
            val = self.value.strip()
            if val.startswith('/'):
                if self._tui_ref:
                    self._tui_ref._show_slash_completion()
                event.prevent_default()
                event.stop()


# ---------------------------------------------------------------------------
# 斜杠命令定义
# ---------------------------------------------------------------------------
SLASH_COMMANDS = {
    '/compact':  '手动压缩上下文并归档历史',
    '/new':      '新建会话（自动保存当前会话）',
    '/save':     '保存当前会话到 .agents/sessions/',
    '/resume':   '列出会话，选择并恢复',
    '/list':     '列出所有会话',
    '/mcp':      '显示 MCP 服务器与工具状态',
    '/tools':    '列出当前所有可用工具',
    '/context':  '显示当前上下文使用情况',
    '/clear':    '清空当前会话历史（不归档）',
    '/help':     '显示所有斜杠命令',
    '/quit':     '退出 zAgent',
    '/exit':     '同 /quit',
}


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------
class AgentTUI(App):
    """左对话 + 右 (思考 | MCP+会话) 布局。"""

    TITLE = 'zAgent · TUI'

    CSS = """
    Screen { layout: vertical; }
    #topbar {
        height: auto; background: $surface; color: $text-muted;
        padding: 0 1; border-bottom: tall $primary;
    }
    #topbar.at-risk { background: $surface; color: $error; border-bottom: tall $error; }
    #main { layout: horizontal; height: 1fr; }
    #left { width: 2fr; height: 100%; border-right: tall $primary; }
    #right { width: 1fr; height: 100%; layout: vertical; }
    #right-top { height: 1fr; border-bottom: tall $accent; }
    #right-bottom { height: 1fr; }

    #chat { height: 1fr; border: round $primary; padding: 0 1; scrollbar-size: 1 1; }
    #thinking { height: 1fr; border: round $accent; padding: 0 1; scrollbar-size: 1 1; }

    #thinking-title, #sidebar-title {
        height: 1; background: $surface; color: $text-muted;
        padding: 0 1; border-bottom: tall $accent;
    }

    #mcp-panel {
        height: 1fr; border: round $accent; padding: 0 1;
        scrollbar-size: 1 1;
    }

    #input-row { height: auto; layout: horizontal; align: left middle; padding: 0 1; }
    #spinner { width: 2; color: $warning; text-align: center; display: none; }
    #spinner.on { display: block; }
    #prompt { border: tall $accent; height: 4; }
    #prompt.paused { border: tall $error; color: $error; }
    """

    BINDINGS = [
        ('ctrl+l', 'clear_log', '清屏'),
        ('ctrl+q', 'quit', '退出'),
    ]

    def __init__(self, agent: Agent, first_run_warning: str | None = None) -> None:
        _ensure_windows_utf8()
        super().__init__()
        self._agent = agent
        self._stream_buffer = ''
        self._spinner_timer = None
        self._spinner_index = 0
        self._first_run_warning = first_run_warning
        self._await_resume_id = False  # /resume 后等待用户输入 id

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static('', id='topbar')
        with Horizontal(id='main'):
            with Vertical(id='left'):
                yield RichLog(id='chat', markup=True, wrap=True, highlight=False, auto_scroll=True)
                with Horizontal(id='input-row'):
                    yield Static('', id='spinner')
                    yield MultiLineInput(
                        placeholder='Enter 发送 | Shift+Enter 换行 | Tab 补全 | /help 所有命令',
                        id='prompt',
                    )
            with Vertical(id='right'):
                with Vertical(id='right-top'):
                    yield Static('思考 & 工具调用', id='thinking-title')
                    yield RichLog(id='thinking', markup=True, wrap=True, highlight=False, auto_scroll=True)
                with Vertical(id='right-bottom'):
                    yield Static('📡 MCP & 会话', id='sidebar-title')
                    yield RichLog(id='mcp-panel', markup=True, wrap=True, highlight=False, auto_scroll=True)
        yield Footer()

    def on_mount(self) -> None:
        chat = self.query_one('#chat', RichLog)
        chat.write('[dim]zAgent 已启动。左侧对话，右侧上半是思考/工具调用，下半是 MCP 状态与会话列表。[/]')
        chat.write('[dim]输入 /help 查看所有命令，Tab 触发斜杠命令补全。[/]')

        if self._first_run_warning:
            chat.write(f'[bold yellow]⚠ {_literal(self._first_run_warning)}[/]')

        prompt = self.query_one('#prompt', MultiLineInput)
        prompt._tui_ref = self
        prompt.focus()

        # 定时器：顶部栏 + MCP 侧边栏 每 2 秒刷新
        self.set_interval(2.0, self._refresh_all)
        self._refresh_all()

    # ------------------------------------------------------------------
    # 刷新（顶部栏 + MCP 侧边栏）
    # ------------------------------------------------------------------
    def _refresh_all(self) -> None:
        self._refresh_topbar()
        self._refresh_mcp_panel()

    def _refresh_topbar(self) -> None:
        topbar = self.query_one('#topbar', Static)
        prompt = self.query_one('#prompt', MultiLineInput)
        status = self._agent.context_status()

        used_k = _fmt_k(status['used'])
        window_k = _fmt_k(status['window'])
        pct = status['percent'] * 100
        is_default = (status['window'] == 128 * 1024)

        if status['at_risk']:
            tag = '[bold red]⚠️ 需要压缩[/]'
            pct_str = f'[bold red]{pct:.0f}%[/]'
            topbar.add_class('at-risk')
            prompt.add_class('paused')
            prompt.placeholder = '⏸ 上下文已满，请先执行 /compact 压缩'
            prompt.disabled = True
        else:
            tag = '[dim]正常[/]'
            pct_str = f'[dim]{pct:.0f}%[/]'
            topbar.remove_class('at-risk')
            prompt.remove_class('paused')
            if not self._agent.is_compacting:
                prompt.disabled = False
                prompt.placeholder = 'Enter 发送 | Shift+Enter 换行 | Tab 补全 | /help'

        default_tag = '（默认）' if is_default else ''
        session_info = f'📄 {self._agent.session_title} ({self._agent.session_id})'

        topbar.update(
            f'📚 {used_k} / {window_k} tokens {default_tag}  {pct_str}  {tag}    '
            f'{session_info}'
        )

    def _refresh_mcp_panel(self) -> None:
        panel = self.query_one('#mcp-panel', RichLog)
        panel.clear()
        mcp = self._agent.mcp_status()

        phase = mcp.get('phase', 'unknown')

        if phase == 'loading':
            panel.write('[yellow]⏳ MCP 正在加载…[/]')
            return
        if phase == 'disabled':
            panel.write('[dim]📡 MCP 未配置（无 .mcp.json）[/]')
            self._append_sessions(panel)
            return
        if phase == 'failed':
            panel.write(f'[bold red]❌ MCP 加载失败:[/] {_literal(str(mcp.get("error")))}')
            self._append_sessions(panel)
            return

        # ready
        servers = mcp.get('servers', [])
        if not servers:
            panel.write('[dim]📡 无 MCP Server 连接[/]')
        else:
            panel.write(f'[bold green]📡 MCP 已连接 ({len(servers)} 个 Server)[/]')
            for s in servers:
                if s.get('failed'):
                    panel.write(f'  [bold red]✗[/] {_literal(s["name"])} [dim](连接失败)[/]')
                    continue
                tools = s.get('tools', [])
                panel.write(f'  [bold green]●[/] {_literal(s["name"])}  [dim]({len(tools)} 工具)[/]')
                for t in tools:
                    panel.write(f'    [dim]- {_literal(t)}[/]')

        self._append_sessions(panel)

    def _append_sessions(self, panel: RichLog) -> None:
        sessions = self._agent.list_sessions()
        panel.write('')
        panel.write('[bold]📂 历史会话[/]')
        for s in sessions[:5]:
            panel.write(f'  [dim]• {_literal(s["title"])}[/] [dim]({s["updated_at"]}, {s["messages"]}条)[/]')
        if len(sessions) > 5:
            panel.write(f'  [dim]…共 {len(sessions)} 个，用 /list 查看全部[/]')

    # ------------------------------------------------------------------
    # 斜杠命令
    # ------------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        prompt = self.query_one('#prompt', MultiLineInput)
        prompt.value = ''
        prompt.disabled = True

        chat = self.query_one('#chat', RichLog)

        # /resume 等待状态
        if self._await_resume_id:
            self._await_resume_id = False
            if text == '/cancel':
                chat.write('[dim]已取消恢复。[/]')
                self._reset_input()
                self._refresh_all()
                return
            ok = self._agent.resume_session(text)
            if ok:
                chat.write(f'[bold green]✅ 已恢复会话 {text}[/]')
            else:
                chat.write(f'[bold red]❌ 恢复失败: 找不到会话 {text}[/]')
            self._reset_input()
            self._refresh_all()
            return

        if text.startswith('/'):
            result = self._handle_slash_command(text)
            chat.write(f'[bold yellow]【命令】[/] {_literal(text)}')
            if result:
                for line in result.split('\n'):
                    chat.write(f'[dim]{_literal(line)}[/]')
            self._reset_input()
            self._refresh_all()
            return

        chat.write(f'[bold blue]【你】[/] {_literal(text)}')
        self._stream_buffer = ''
        self._run_turn(text)
        self._start_spinner()

    def _handle_slash_command(self, cmd: str) -> str | None:
        """执行斜杠命令，返回显示给用户的字符串或 None。"""
        chat = self.query_one('#chat', RichLog)
        try:
            if cmd == '/help':
                return '\n'.join(f'  [dim]{k}[/]  {v}' for k, v in SLASH_COMMANDS.items())

            elif cmd == '/quit' or cmd == '/exit':
                self.exit()
                return None

            elif cmd == '/compact':
                self._run_compact_flow()
                return None

            elif cmd == '/new':
                # 先自动保存当前
                self._agent.save_current()
                self._agent.new_session()
                return f'✅ 已新建会话 {self._agent.session_id}，旧会话已保存。'

            elif cmd == '/save':
                path = self._agent.save_current()
                return f'✅ 已保存当前会话到 {path}'

            elif cmd == '/resume':
                sessions = self._agent.list_sessions()
                if not sessions:
                    return '暂无历史会话。'
                # 直接写进对话区让用户输入 id
                chat = self.query_one('#chat', RichLog)
                chat.write('[bold]📂 历史会话列表：[/]')
                for i, s in enumerate(sessions, 1):
                    chat.write(
                        f'  {i}. [bold]{_literal(s["title"])}[/]  '
                        f'[dim]· {s["updated_at"]} · {s["messages"]}条 · id={s["id"]}[/]'
                    )
                chat.write('[bold yellow]请输入要恢复的会话 id（或输入 /cancel 取消）:[/]')
                # 等用户下一次输入
                self._await_resume_id = True
                return None

            elif cmd == '/list':
                sessions = self._agent.list_sessions()
                if not sessions:
                    return '暂无历史会话。'
                out = ['📂 历史会话列表：']
                for i, s in enumerate(sessions, 1):
                    out.append(f'  {i}. {s["title"]}  ·  {s["updated_at"]}  ·  {s["messages"]}条  ·  id={s["id"]}')
                return '\n'.join(out)

            elif cmd == '/context':
                st = self._agent.context_status()
                pct = st['percent'] * 100
                is_default = '(默认)' if st['window'] == 128 * 1024 else ''
                return (
                    f'📚 上下文: {st["used"]} / {st["window"]} tokens {is_default}\n'
                    f'📊 使用率: {pct:.1f}%\n'
                    f'⚠️  阈值: 80% → {int(st["window"] * 0.8)} tokens\n'
                    f'📄 会话: {self._agent.session_title} ({self._agent.session_id})'
                )

            elif cmd == '/mcp':
                st = self._agent.mcp_status()
                phase = st['phase']
                if phase == 'disabled':
                    return '📡 MCP 未配置（当前目录无 .mcp.json）'
                if phase == 'loading':
                    return '📡 MCP 正在加载…请稍候。'
                if phase == 'failed':
                    return f'📡 MCP 加载失败: {st.get("error")}'
                out = [f'📡 MCP 状态: 已连接 ({len(st.get("servers", []))} 个 Server)']
                for s in st.get('servers', []):
                    if s.get('failed'):
                        out.append(f'  ✗ {s["name"]} (连接失败)')
                    else:
                        out.append(f'  ● {s["name"]}: {len(s["tools"])} 工具')
                return '\n'.join(out)

            elif cmd == '/tools':
                names = []
                try:
                    names = list(self._agent._registry.names)
                except Exception:
                    pass
                out = [f'🛠 可用工具 ({len(names)} 个):']
                for n in names:
                    out.append(f'  • {n}')
                return '\n'.join(out)

            elif cmd == '/clear':
                self._agent.history = []
                self._agent.session_title = '新会话'
                self._agent.session_id = self._agent._sessions.new_id()
                return '✅ 已清空会话历史（未归档）。'

            else:
                return f'未知命令: {cmd}。输入 /help 查看全部命令。'

        except Exception as exc:
            return f'命令执行出错: {exc}'

    def _run_compact_flow(self) -> None:
        """/compact 通过 send('/compact') 让 Agent 内部处理。"""
        self._stream_buffer = ''
        self._run_turn('/compact')

    # ------------------------------------------------------------------
    # 补全面板
    # ------------------------------------------------------------------
    def _show_slash_completion(self) -> None:
        prompt = self.query_one('#prompt', MultiLineInput)
        current = prompt.value.strip()
        prefix = current.lower()

        matches = [(k, v) for k, v in SLASH_COMMANDS.items() if k.startswith(prefix)]
        if not matches:
            return

        # 直接填第一个补全（因为是 Tab，比 Popup 更轻量）
        if len(matches) == 1:
            prompt.value = matches[0][0] + ' '
            prompt.action_end()
            return

        # 多个匹配 → 写进 chat 里，用户自己选
        chat = self.query_one('#chat', RichLog)
        chat.write('[dim]--- 命令补全提示 ---[/]')
        for k, v in matches:
            chat.write(f'  [bold cyan]{k}[/]  [dim]{v}[/]')
        chat.write('[dim]------------------------[/]')

    # ------------------------------------------------------------------
    # 事件分发
    # ------------------------------------------------------------------
    def on_input_submitted_wrapped(self, event):
        pass

    @work(thread=True, exclusive=True, group='turn')
    def _run_turn(self, text: str) -> None:
        for event in self._agent.send(text):
            self.call_from_thread(self._handle_event, event)

    # 转圈动画
    def _start_spinner(self) -> None:
        spinner = self.query_one('#spinner', Static)
        spinner.add_class('on')
        self._spinner_index = 0
        self._spinner_timer = self.set_interval(0.12, self._tick_spinner)

    def _tick_spinner(self) -> None:
        spinner = self.query_one('#spinner', Static)
        spinner.update(SPINNER_FRAMES[self._spinner_index % len(SPINNER_FRAMES)])
        self._spinner_index += 1

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        spinner = self.query_one('#spinner', Static)
        spinner.remove_class('on')
        spinner.update('')

    def _handle_event(self, event: AgentEvent) -> None:
        chat = self.query_one('#chat', RichLog)
        thinking = self.query_one('#thinking', RichLog)

        if isinstance(event, TextDelta):
            self._stream_buffer += event.text

        elif isinstance(event, ToolCallBegin):
            self._stop_spinner()
            thinking.write(f'[bold yellow]▶ 调用 {event.name}[/]')
            if event.arguments:
                thinking.write(f'  [dim]参数: {_literal(str(event.arguments))}[/]')

        elif isinstance(event, ToolCallEnd):
            ok = event.ok
            color = 'green' if ok else 'red'
            tag = '✓' if ok else '✗'
            preview = event.result
            if len(preview) > MAX_RESULT_PREVIEW:
                preview = preview[:MAX_RESULT_PREVIEW] + f'… [dim](截断,共 {len(event.result)} 字符)[/]'
            thinking.write(f'  [{color}]{tag} 结果 (长度 {len(event.result)})[/]')
            thinking.write(f'  [dim]{_literal(preview)}[/]')
            if not ok:
                chat.write(f'[bold red]⚠ 工具失败 {event.name}:[/] {_literal(event.result[:200])}')
            self._start_spinner()

        elif isinstance(event, TurnEnd):
            self._stop_spinner()
            if self._stream_buffer:
                chat.write(f'[bold green]【AI】[/] {_literal(self._stream_buffer.strip())}')
            thinking.write('[dim]──────────────────────────[/]')
            thinking.write('[dim]本轮结束[/]')
            self._reset_input()
            self._refresh_all()

        elif isinstance(event, AgentError):
            self._stop_spinner()
            chat.write(f'[bold red]【错误】[/] {_literal(event.message)}')
            thinking.write(f'[bold red]✗ Agent 错误:[/] {_literal(event.message)}')
            self._reset_input()

    def _reset_input(self) -> None:
        self._stream_buffer = ''
        prompt = self.query_one('#prompt', MultiLineInput)
        st = self._agent.context_status()
        if not st['at_risk'] and not self._agent.is_compacting:
            prompt.disabled = False
            prompt.remove_class('paused')
            prompt.focus()

    def action_clear_log(self) -> None:
        self.query_one('#chat', RichLog).clear()
        self.query_one('#thinking', RichLog).clear()
        self.query_one('#chat', RichLog).write('[dim]已清屏[/]')


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _ensure_windows_utf8() -> None:
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass


def _literal(text: str) -> str:
    return text.replace('[', '[[]').replace(']', '[]]')


def _fmt_k(n: int) -> str:
    if n >= 1024:
        return f'{n / 1024:.1f}k'
    return str(n)


def run(agent: Agent, first_run_warning: str | None = None) -> None:
    AgentTUI(agent, first_run_warning).run()
