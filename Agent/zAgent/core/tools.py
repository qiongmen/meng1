"""内置工具：RunCli、fileOps、loadSkill。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .registry import ToolSpec


# ---------------------------------------------------------------------------
# RunCli —— 执行本地 CLI 程序（列表参数，shell=False）
# ---------------------------------------------------------------------------

MAX_OUTPUT_LEN = 8000  # 单段输出最大字符数，超出截断


def _truncate(text: str, label: str) -> str:
    """截断过长的输出并标明截断情况。"""
    if not text:
        return text
    if len(text) <= MAX_OUTPUT_LEN:
        return text
    half = MAX_OUTPUT_LEN // 2
    kept_head = text[:half]
    kept_tail = text[-half:]
    return (
        kept_head
        + f'\n… [{label} 已截断: 总长 {len(text)} 字符, 保留前 {half} + 后 {half}] …\n'
        + kept_tail
    )


def _run_cli(command: str, args: list | None = None, timeout: int = 60) -> str:
    """用 subprocess.run([command, *args], shell=False) 执行本地命令。

    返回结构化文本：exit_code / stdout / stderr，超时或异常时返回错误说明。
    """
    if not command.strip():
        return '错误: command 不能为空。'

    # 拼装参数列表，不用 shell=True
    cmd_list = [command.strip()]
    if args:
        cmd_list.extend(str(a) for a in args)

    try:
        result = subprocess.run(
            cmd_list,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=Path.cwd(),
        )
    except FileNotFoundError:
        return f'错误: 未找到程序 "{command}"，请确认它已安装且在 PATH 中。'
    except subprocess.TimeoutExpired:
        return (
            f'错误: 命令执行超时（{timeout}s）。\n'
            f'  命令: {" ".join(cmd_list)}'
        )
    except Exception as exc:
        return f'错误: {type(exc).__name__}: {exc}'

    stdout = _truncate(result.stdout.rstrip('\n'), 'stdout') if result.stdout else ''
    stderr = _truncate(result.stderr.rstrip('\n'), 'stderr') if result.stderr else ''

    lines = [
        f'exit_code: {result.returncode}',
        f'command: {" ".join(cmd_list)}',
        '',
        '--- stdout ---',
        stdout if stdout else '(空)',
        '',
        '--- stderr ---',
        stderr if stderr else '(空)',
    ]
    return '\n'.join(lines)


RUN_CLI_SPEC = ToolSpec(
    name='RunCli',
    description=(
        '在本地执行一个命令行程序。\n'
        '- command: 程序名（如 python、git、dir、node 等）\n'
        '- args: 传递给程序的参数列表（可选）\n'
        '示例:\n'
        '  {"command": "python", "args": ["--version"]}\n'
        '  {"command": "git", "args": ["clone", "https://..."]}\n'
        '  {"command": "dir", "args": ["C:\\\\Users"]}\n'
        '返回 exit_code、stdout、stderr，过长输出会被截断。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'command': {'type': 'string', 'description': '要执行的程序名或可执行文件路径'},
            'args': {
                'type': 'array',
                'items': {'type': 'string'},
                'description': '传递给程序的参数列表（可选）',
            },
            'timeout': {'type': 'integer', 'description': '超时秒数（默认 60）'},
        },
        'required': ['command'],
    },
    handler=_run_cli,
)


# ---------------------------------------------------------------------------
# fileOps —— 文件读取/写入/列表
# ---------------------------------------------------------------------------

def _file_ops(action: str, path: str, content: Optional[str] = None) -> str:
    p = Path(path)
    if action == 'read':
        if not p.is_file():
            return f'文件不存在: {path}'
        try:
            return p.read_text(encoding='utf-8')
        except Exception as exc:
            return f'读取失败: {exc}'
    elif action == 'write':
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content or '', encoding='utf-8')
            return f'已写入 {len(content or "")} 字节到 {path}'
        except Exception as exc:
            return f'写入失败: {exc}'
    elif action == 'list':
        target = p if p.is_dir() else p.parent
        if not target.is_dir():
            return f'目录不存在: {path}'
        entries = sorted(target.iterdir())
        return '\n'.join(
            f"{'[D]' if e.is_dir() else '[F]'} {e.name}" for e in entries
        ) or '(空目录)'
    else:
        return f'未知 action: {action}，支持 read / write / list'


FILE_OPS_SPEC = ToolSpec(
    name='fileOps',
    description='文件操作：读取、写入、列出目录。',
    parameters={
        'type': 'object',
        'properties': {
            'action': {
                'type': 'string',
                'enum': ['read', 'write', 'list'],
                'description': '操作类型',
            },
            'path': {'type': 'string', 'description': '文件或目录路径'},
            'content': {'type': 'string', 'description': 'write 时的文件内容'},
        },
        'required': ['action', 'path'],
    },
    handler=_file_ops,
)


# ---------------------------------------------------------------------------
# loadSkill —— 动态加载 Skill 内容
# ---------------------------------------------------------------------------

def _make_load_skill_handler(skill_registry):
    """闭包：让 handler 持有 SkillRegistry 引用。"""

    def handler(name: str, filename: Optional[str] = None) -> str:
        if filename is None:
            # 只给 name → 返回 SKILL.md 全文
            content = skill_registry.load_skill_md(name)
            if content is None:
                available = ', '.join(s.name for s in skill_registry.all) or '(无)'
                return f'未找到技能 "{name}"。可用技能: {available}'
            return content

        # 同时给了 filename → 加载附加文件
        resolved, candidates = skill_registry.resolve_file(name, filename)
        if candidates:
            # 多个同名文件 → 返回候选列表让 LLM 选
            lines = [f'技能 "{name}" 中找到多个 "{filename}"，请用完整路径再试一次：']
            for c in candidates:
                lines.append(f'  - {c}')
            return '\n'.join(lines)
        if resolved is None:
            return f'未找到技能 "{name}" 中的文件 "{filename}"（或路径越界）'
        try:
            return resolved.read_text(encoding='utf-8')
        except Exception as exc:
            return f'读取 {resolved} 失败: {exc}'

    return handler


def make_load_skill_spec(skill_registry) -> ToolSpec:
    return ToolSpec(
        name='loadSkill',
        description=(
            '按需加载某个 Skill 的完整说明。\n'
            '- 只给 name: 返回该技能 SKILL.md 全文。\n'
            '- 同时给 filename: 返回技能目录下指定文件内容；支持相对于 skill 目录的路径、'
            '项目相对路径、绝对路径。若 filename 在技能目录内不唯一，会返回候选列表由你选择。'
        ),
        parameters={
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': '技能名称（必填）'},
                'filename': {
                    'type': 'string',
                    'description': '要加载的附加文件路径或文件名（可选）。不填则返回 SKILL.md 全文。',
                },
            },
            'required': ['name'],
        },
        handler=_make_load_skill_handler(skill_registry),
    )


# ---------------------------------------------------------------------------
# createSkill —— 创建新 Skill
# ---------------------------------------------------------------------------

import re as _re

_VALID_SKILL_NAME = _re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$')


def _slugify(name: str) -> str:
    """把任意名称转成小写+数字+连字符的合法格式。"""
    name = name.strip().lower()
    # 先把非字母数字的字符转成连字符
    name = _re.sub(r'[^a-z0-9]+', '-', name)
    # 去掉首尾连字符
    name = name.strip('-')
    return name


def _create_skill(name: str, skillContext: str) -> str:
    """在 <项目根>/.agents/skills/<name>/ 下创建 SKILL.md。"""
    # 1. 规整 name
    slug = _slugify(name)
    if not slug:
        return f'错误: 无法从 "{name}" 生成合法的 skill 名称（仅允许小写字母、数字、连字符）'
    if not _VALID_SKILL_NAME.match(slug):
        return f'错误: 名称 "{slug}" 格式不合法'

    # 2. 目标目录
    project_root = Path.cwd()
    skills_dir = project_root / '.agents' / 'skills'
    target_dir = skills_dir / slug
    target_md = target_dir / 'SKILL.md'

    # 3. 检查是否已存在
    if target_md.exists():
        return (
            f'Skill "{slug}" 已存在（{target_md}），未做任何改动。\n'
            f'如需覆盖，请先删除原目录或使用其他名称。'
        )
    if target_dir.exists():
        return (
            f'目录 "{target_dir}" 已存在但没有 SKILL.md，未做任何改动。\n'
            f'如需在此目录下创建 Skill，请确认目录内容或换一个新名称。'
        )

    # 4. 确保 skillContext 合法（至少有 frontmatter 的 name/description）
    from .skills import parse_frontmatter

    meta, _ = parse_frontmatter(skillContext)
    if not meta.get('name') or not meta.get('description'):
        return (
            '错误: skillContext 必须包含合法的 YAML frontmatter，至少要有 name 和 description 字段。\n'
            '示例:\n'
            '  ---\n'
            '  name: my-skill\n'
            '  description: 这是一个示例技能\n'
            '  ---\n'
            '  正文内容...\n'
        )

    # 5. 写入
    try:
        target_dir.mkdir(parents=True, exist_ok=False)
        target_md.write_text(skillContext, encoding='utf-8')
    except FileExistsError:
        return f'错误: 目录 "{target_dir}" 在创建过程中已存在，操作已取消。'
    except Exception as exc:
        return f'错误: 写入 {target_md} 失败: {exc}'

    return (
        f'成功创建 Skill "{slug}"。\n'
        f'  路径: {target_md}\n'
        f'  提示: 如需 references/、scripts/ 等附加目录或文件，请继续调用 fileOps 创建。\n'
        f'  提示: 新 Skill 将在下次启动时自动被发现；若要立即生效，可重启 Agent。'
    )


CREATE_SKILL_SPEC = ToolSpec(
    name='createSkill',
    description=(
        '创建一个新的 Skill。\n'
        '- name: Skill 名称，最终会被规整为小写字母、数字和连字符格式（如 "my-skill"）\n'
        '- skillContext: 要写入 SKILL.md 的完整内容，必须以 YAML frontmatter 开头，\n'
        '  至少包含 name 和 description 字段。正文是 Skill 的详细说明。\n'
        '如果目标 Skill 已存在，不会覆盖，会明确提示。\n'
        '如需额外创建 references/、scripts/ 等目录或辅助文件，请继续调用 fileOps 工具。'
    ),
    parameters={
        'type': 'object',
        'properties': {
            'name': {
                'type': 'string',
                'description': 'Skill 名称，将被规整为小写+数字+连字符格式',
            },
            'skillContext': {
                'type': 'string',
                'description': 'SKILL.md 的完整内容（含 YAML frontmatter）',
            },
        },
        'required': ['name', 'skillContext'],
    },
    handler=_create_skill,
)


# ---------------------------------------------------------------------------
# 注册所有内置工具
# ---------------------------------------------------------------------------

def register_builtin_tools(registry, skill_registry) -> None:
    """把内置工具注册到 ToolRegistry。MCP 工具异步注册，不阻塞。"""
    registry.register(RUN_CLI_SPEC)
    registry.register(FILE_OPS_SPEC)
    registry.register(make_load_skill_spec(skill_registry))
    registry.register(CREATE_SKILL_SPEC)

    # recallContext —— 历史上下文检索（用于压缩后的召回）
    try:
        from core.context_archive import get_recall_context_spec
        registry.register(get_recall_context_spec())
    except Exception:
        pass

    # 网络工具（searchWeb / fetchWeb）—— 来自 prist 包
    try:
        from prist.web_tools import register_web_tools
        register_web_tools(registry)
    except Exception:
        pass


def start_mcp_background_load(registry, timeout: int = 15):
    """在后台线程异步加载 MCP 工具。返回状态查询函数 mcp_status()。"""
    import threading
    from typing import Callable

    state: dict = {
        'phase': 'loading',       # loading | ready | failed | disabled
        'servers': [],            # [{'name': str, 'tools': [str]}]
        'error': None,
        'elapsed': 0,
    }

    def worker():
        import time
        t0 = time.time()
        try:
            from prist.mcp_loader import auto_register_mcp_tools, load_mcp_config
            configs = load_mcp_config()
            if not configs:
                state['phase'] = 'disabled'
                state['elapsed'] = time.time() - t0
                return

            connected = auto_register_mcp_tools(registry)
            servers = []
            any_failed = False
            for cfg in configs:
                if cfg.name in connected:
                    server_tools = [
                        n for n in registry.names if n.startswith(f'{cfg.name}.')
                    ]
                    servers.append({'name': cfg.name, 'tools': server_tools, 'failed': False})
                else:
                    servers.append({'name': cfg.name, 'tools': [], 'failed': True})
                    any_failed = True
            state['servers'] = servers
            if connected:
                state['phase'] = 'ready'
            elif any_failed:
                state['phase'] = 'failed'
                state['error'] = '全部 MCP Server 连接失败（URL 可能已过期或网络不可达）'
            else:
                state['phase'] = 'disabled'
            state['elapsed'] = time.time() - t0
        except Exception as exc:
            state['phase'] = 'failed'
            state['error'] = str(exc)
            state['elapsed'] = time.time() - t0

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    def mcp_status() -> dict:
        return dict(state)

    return mcp_status
