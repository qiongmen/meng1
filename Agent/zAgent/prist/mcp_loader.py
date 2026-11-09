"""MCP 配置加载器 + 启动注册入口。

读取 <项目根>/.mcp.json，解析为 _ServerConfig 列表，
并调用 mcp_client.register_mcp_tools 动态注册工具。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .mcp_client import _ServerConfig, register_mcp_tools


def load_mcp_config(project_root: Optional[Path] = None) -> list[_ServerConfig]:
    """读取 .mcp.json 并返回 ServerConfig 列表。

    找不到文件时返回空列表，不报错。
    单个 Server 配置格式错误时跳过该条目，不影响其他。
    """
    root = Path(project_root) if project_root else Path.cwd()
    cfg_path = root / '.mcp.json'

    if not cfg_path.is_file():
        return []

    try:
        data = json.loads(cfg_path.read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001
        print(f'[MCP] 读取 .mcp.json 失败: {exc}')
        return []

    servers = data.get('mcpServers') or {}
    if not isinstance(servers, dict):
        print('[MCP] .mcp.json 格式错误: mcpServers 应该是对象')
        return []

    result: list[_ServerConfig] = []
    for name, entry in servers.items():
        try:
            result.append(_parse_server_entry(name, entry))
        except Exception as exc:  # noqa: BLE001
            print(f'[MCP] 跳过 Server "{name}": {exc}')

    return result


def _parse_server_entry(name: str, entry) -> _ServerConfig:
    if not isinstance(entry, dict):
        raise ValueError('Server 配置必须是对象')

    # 兼容 type 别名
    raw_type = (entry.get('type') or 'streamable_http').strip().lower()
    type_aliases = {
        'http': 'streamable_http',
        'streamable-http': 'streamable_http',
        'streamable_http': 'streamable_http',
        'sse': 'sse',
        'stdio': 'stdio',
        'command': 'stdio',
    }
    normalized = type_aliases.get(raw_type, raw_type)

    if normalized == 'streamable_http':
        url = (entry.get('url') or '').strip()
        # 允许用户 url 里带反引号（markdown 复制时常见）
        url = url.strip('`').strip()
        if not url:
            raise ValueError('streamable_http 必须提供 url')
        return _ServerConfig(name=name, type='streamable_http', url=url)

    if normalized == 'stdio':
        cmd = (entry.get('command') or '').strip()
        args = entry.get('args') or []
        env = entry.get('env')
        if not cmd:
            raise ValueError('stdio 必须提供 command')
        return _ServerConfig(name=name, type='stdio', command=cmd, args=list(args), env=env)

    raise ValueError(f'不支持的 type: {raw_type}')


def auto_register_mcp_tools(registry, project_root: Optional[Path] = None) -> list[str]:
    """便捷入口：读 .mcp.json → 注册工具 → 返回成功连接的 Server 名列表。"""
    configs = load_mcp_config(project_root)
    if not configs:
        return []
    return register_mcp_tools(registry, configs)
