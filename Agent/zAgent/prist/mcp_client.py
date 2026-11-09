"""MCP Client —— 以 sync 接口封装 MCP Python SDK。

设计要点：
- MCP SDK 是 async 的，通过 anyio.run() 在专用 worker 线程中执行。
- 每个 MCP Server 对应一个 _MCPServerAdapter：负责连接、工具发现、转发调用。
- 工具暴露名 = "{server_name}.{tool_name}"，避免多 Server 间的名称冲突。
- 单个 Server 连接失败时仅记录错误，不影响其他 Server 和本地工具。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from core.registry import ToolSpec


# ---------------------------------------------------------------------------
# 传输层适配
# ---------------------------------------------------------------------------

@dataclass
class _ServerConfig:
    """单个 MCP Server 的配置（从 .mcp.json 读入）。"""
    name: str               # Server 名，用于工具前缀
    type: str               # "streamable_http" | "stdio"
    url: Optional[str] = None                   # streamable_http
    command: Optional[str] = None               # stdio
    args: Optional[list[str]] = None            # stdio
    env: Optional[dict[str, str]] = None        # stdio


def _run_async(coro_factory: Callable):
    """在专用线程里 anyio.run(coro_factory()) 执行协程，返回结果。

    之所以需要线程，是因为 sync 工具 handler 可能被运行在已有 event loop 的
    上下文（例如某些 GUI 框架）中，anyio.run 在那种场景下会 conflict。
    """
    result_holder: list[Any] = [None]
    error_holder: list[Exception] = [None]

    def worker():
        try:
            import anyio
            result_holder[0] = anyio.run(coro_factory)
        except Exception as exc:  # noqa: BLE001 — 需要透传任意异常
            error_holder[0] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=60)

    if thread.is_alive():
        raise TimeoutError('MCP 操作超时（60s）')
    if error_holder[0] is not None:
        raise error_holder[0]  # type: ignore[misc]
    return result_holder[0]


# ---------------------------------------------------------------------------
# 单个 MCP Server 适配器
# ---------------------------------------------------------------------------

class _MCPServerAdapter:
    """管理一个 MCP Server 的连接与工具。"""

    def __init__(self, cfg: _ServerConfig) -> None:
        self.cfg = cfg
        self._connected = False
        self._tools: list[dict] = []  # [{llm_name, mcp_name, description, input_schema}]
        self._lock = threading.Lock()

    # -- 连接 & 发现 ---------------------------------------------------------

    def connect_and_discover(self) -> tuple[bool, str]:
        """连接 Server 并获取工具列表。返回 (ok, info_or_error)。"""
        try:
            tools = _run_async(self._async_list_tools)
        except Exception as exc:  # noqa: BLE001
            return False, f'MCP Server "{self.cfg.name}" 连接失败: {type(exc).__name__}: {exc}'

        discovered: list[dict] = []
        for t in tools:
            mcp_name = t.get('name', '')
            desc = t.get('description', '')
            schema = t.get('input_schema') or {'type': 'object', 'properties': {}}
            # 确保 schema 有 type: object
            if isinstance(schema, dict):
                schema.setdefault('type', 'object')
            llm_name = f'{self.cfg.name}.{mcp_name}'
            discovered.append({
                'llm_name': llm_name,
                'mcp_name': mcp_name,
                'description': desc,
                'input_schema': schema,
            })

        with self._lock:
            self._tools = discovered
            self._connected = True

        return True, f'MCP Server "{self.cfg.name}" 连接成功，发现 {len(discovered)} 个工具。'

    async def _async_list_tools(self) -> list[dict]:
        from mcp import ClientSession

        async with self._make_transport() as (read_s, write_s):
            async with ClientSession(read_s, write_s) as session:
                await session.initialize()
                result = await session.list_tools()
                return [
                    {
                        'name': t.name,
                        'description': t.description or '',
                        'input_schema': t.input_schema or {},
                    }
                    for t in result.tools
                ]

    def _make_transport(self):
        """根据配置返回 async context manager，yield (read_stream, write_stream)。"""
        cfg = self.cfg
        if cfg.type in ('streamable_http', 'http', 'streamable-http'):
            from mcp.client.streamable_http import streamable_http_client
            return streamable_http_client(cfg.url)  # type: ignore[arg-type]

        if cfg.type == 'stdio':
            from mcp.client.stdio import StdioServerParameters, stdio_client
            params = StdioServerParameters(
                command=cfg.command or '',
                args=cfg.args or [],
                env=cfg.env,
            )
            return stdio_client(params)

        raise ValueError(f'未知的 MCP transport type: {cfg.type}')

    # -- 调用 -----------------------------------------------------------------

    def call_tool(self, mcp_tool_name: str, arguments: dict) -> str:
        """转发工具调用，返回面向 LLM 的文本结果。"""
        if not self._connected:
            return f'错误: MCP Server "{self.cfg.name}" 尚未连接。'

        try:
            raw = _run_async(
                lambda: self._async_call_tool(mcp_tool_name, arguments)
            )
        except Exception as exc:  # noqa: BLE001
            return f'MCP 工具 {mcp_tool_name} 调用异常: {type(exc).__name__}: {exc}'

        return self._format_result(raw)

    async def _async_call_tool(self, mcp_tool_name: str, arguments: dict):
        from mcp import ClientSession

        async with self._make_transport() as (read_s, write_s):
            async with ClientSession(read_s, write_s) as session:
                await session.initialize()
                return await session.call_tool(mcp_tool_name, arguments=arguments or {})

    @staticmethod
    def _format_result(raw) -> str:
        """把 MCP CallToolResult 转成可读文本。"""
        if raw is None:
            return '(空结果)'

        # is_error 标记
        is_error = getattr(raw, 'is_error', False)

        # 提取 content
        content_list = getattr(raw, 'content', None) or []
        parts: list[str] = []
        for item in content_list:
            text = getattr(item, 'text', None) or str(item)
            if text:
                parts.append(text)

        if getattr(raw, 'is_error', False) and not parts:
            return f'(MCP 工具返回错误)'

        joined = '\n'.join(parts) if parts else str(raw)

        # 如果是 JSON 字符串尝试 pretty-print
        try:
            parsed = json.loads(joined)
            joined = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            pass

        if is_error:
            return f'[MCP 错误] {joined}'
        return joined

    # -- 查询 -----------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> list[dict]:
        with self._lock:
            return list(self._tools)


# ---------------------------------------------------------------------------
# 对外 API：把 MCP Server 注册到 ToolRegistry
# ---------------------------------------------------------------------------

def register_mcp_tools(registry, server_configs: list[_ServerConfig]) -> list[str]:
    """连接所有 MCP Server，把发现的工具动态注册到 ToolRegistry。

    返回成功连接的 Server 名称列表（失败的会被跳过）。
    """
    connected: list[str] = []

    for cfg in server_configs:
        adapter = _MCPServerAdapter(cfg)
        ok, info = adapter.connect_and_discover()
        print(f'[MCP] {info}')

        if not ok:
            continue

        for tool_info in adapter.tools:
            llm_name = tool_info['llm_name']
            mcp_name = tool_info['mcp_name']

            # 避免覆盖已有的同名工具
            if registry.get(llm_name) is not None:
                print(f'[MCP] 跳过 "{llm_name}"：已有同名工具注册。')
                continue

            # 闭包捕获 adapter 和 mcp_name
            def _make_handler(a: _MCPServerAdapter, mname: str):
                def handler(**kwargs) -> str:
                    # 去掉 None 值
                    args = {k: v for k, v in kwargs.items() if v is not None}
                    return a.call_tool(mname, args)
                return handler

            schema = tool_info['input_schema']
            # 确保 description 写进 schema 里让 LLM 看到
            spec = ToolSpec(
                name=llm_name,
                description=(
                    f"[MCP Server: {cfg.name}] {tool_info['description']}"
                    if tool_info['description']
                    else f"[MCP Server: {cfg.name}] {mcp_name}"
                ),
                parameters=schema if isinstance(schema, dict) else {'type': 'object', 'properties': {}},
                handler=_make_handler(adapter, mcp_name),
            )
            registry.register(spec)

        connected.append(cfg.name)

    return connected
