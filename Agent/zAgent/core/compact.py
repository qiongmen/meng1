"""上下文压缩 —— /compact 命令核心逻辑。

流程：
  1. 深拷贝备份当前 history（用于失败回滚）
  2. 生成归档编号 YYYYMMDD-HHMMSS，把原始 history 写入 .agents/context/<id>.jsonl
  3. 构造压缩 Prompt + 历史对话，调用 LLM 生成摘要
  4. 成功：用 "system 摘要消息 + 最近保留的历史消息" 替换 history
  5. 失败：恢复备份 history，报告错误

失败保护：归档写入成功后才继续；LLM 失败时不删旧历史。
"""

from __future__ import annotations

import copy
import datetime as _dt
from typing import Any, Callable, Optional

from .context_archive import ContextArchive, estimate_tokens


# ---------------------------------------------------------------------------
# 压缩专用 Prompt
# ---------------------------------------------------------------------------

COMPACT_SYSTEM_PROMPT = """你是一名 AI Agent 的上下文管理助手。当前会话即将达到上下文窗口限制，需要将历史对话压缩为一份高质量的工作摘要，以便 Agent 在新的上下文中继续执行任务。

请认真阅读提供的历史上下文，提取对后续工作仍然有价值的信息，并生成一份约 5,000 Tokens 的 Markdown 摘要。

摘要应重点保留以下内容：

1. **用户目标与要求**：用户希望完成什么任务？有哪些必须遵守的约束、偏好和验收条件？
2. **已经完成的工作**：哪些任务已经完成？实现了哪些功能？获得了哪些已经验证的结果？
3. **当前工作状态**：Agent 正在处理什么任务？当前进展到哪一步？是否存在尚未解决的问题？
4. **关键技术信息**：保留重要的文件路径、函数名称、接口定义、配置项、执行命令、错误信息和必要的代码片段。
5. **关键决策与原因**：记录用户和 Agent 已经确定的技术方案，以及重要的设计取舍。
6. **下一步任务**：明确接下来应该执行的操作，以及需要继续验证的事项。

请遵守以下要求：

- 优先保留对后续任务有直接影响的信息，删除寒暄、重复讨论、过时的尝试和无关内容。
- 对已经验证的事实与尚未验证的推测作出明确区分。
- 不要将计划完成的任务描述为已经完成。
- 重要的文件路径、函数名称、配置名称和命令应保留准确原文。
- 对失败的尝试，只保留有助于避免重复错误的信息。
- 如果信息存在冲突，应优先保留用户最新确认的要求，并简要说明仍然存在的冲突。
- 如果某些历史细节无法在摘要中完整保留，可简要说明它们已保存到原始上下文归档中，后续可以通过 recallContext 工具重新检索。
- 使用清晰的 Markdown 结构输出摘要，不要附加解释或无关内容。

**最终目标：使一个从未阅读过原始对话的 Agent，仅凭这份摘要，就能够理解当前任务、继承关键决策，并继续开展工作。**"""


# ---------------------------------------------------------------------------
# 压缩执行
# ---------------------------------------------------------------------------

def make_archive_id() -> str:
    return _dt.datetime.now().strftime('%Y%m%d-%H%M%S')


def make_archive_note(archive_id: str) -> str:
    """压缩摘要末尾追加的归档说明。"""
    return (
        f'\n\n---\n\n'
        f'此前上下文已于 {_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} '
        f'完成压缩，原始上下文保存在编号为 **{archive_id}** 的归档文件中。'
        f'如需查找历史信息，请使用 `recallContext` 工具。'
    )


def _compress_history_into_messages(summary: str, archive_id: str, keep_recent: int = 4) -> list[dict]:
    """把 history 替换成 "摘要 + 最近 N 条" 的新 history 列表。"""
    # 1. 构造摘要（作为一条 assistant 消息）
    summary_message = {
        'role': 'assistant',
        'content': summary + make_archive_note(archive_id),
    }
    # 2. 最近保留 N 条（从末尾取）
    #    注意 tool 消息需要紧挨着对应的 assistant tool_call 消息
    #    这里做简单处理：最后 keep_recent 条
    return [summary_message]


def run_compact(
    llm_client: Callable,
    history: list[dict],
    system_prompt: str,
    context_window_size: int,
    project_root: Optional[str] = None,
    max_tokens_for_compact: int = 5000,
) -> dict[str, Any]:
    """执行一次上下文压缩。

    返回 dict:
      {'ok': True, 'archive_id': str, 'summary_tokens': int, 'new_history': list[dict]}
      {'ok': False, 'error': str}
    """
    archive_id = make_archive_id()
    archive = ContextArchive(project_root)

    # ---- 1. 深拷贝备份（失败时回滚） ----
    backup = copy.deepcopy(history)

    # ---- 2. 保存归档（这一步必须成功）----
    try:
        archive.save_archive(archive_id, history)
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': f'归档保存失败（已中止压缩）: {exc}'}

    # ---- 3. 构造压缩请求 ----
    # 给 LLM 发：system = COMPACT_SYSTEM_PROMPT，user = 当前全部历史对话的文本版
    user_text_parts: list[str] = []
    for msg in history:
        role = msg.get('role', '?')
        content = msg.get('content') or ''
        if isinstance(content, list):
            content = '\n'.join(
                c.get('text') if isinstance(c, dict) else str(c)
                for c in content
            )
        tool_calls = msg.get('tool_calls') or []
        if tool_calls:
            tc_info = []
            for tc in tool_calls:
                func = tc.get('function', tc)
                tc_info.append(
                    f"  [tool_call] {func.get('name')}({json_dumps(func.get('arguments', {}))})"
                )
            content = content + '\n' + '\n'.join(tc_info)
        user_text_parts.append(f"**{role.upper()}**:\n{content}")
        if role == 'tool':
            tool_call_id = msg.get('tool_call_id', '')
            user_text_parts.append(f"  [tool_call_id: {tool_call_id}]")

    user_text = '\n\n'.join(user_text_parts)

    # 如果历史过长，截断中间部分，保留头尾
    est_tokens = estimate_tokens(user_text)
    compress_budget = context_window_size - max_tokens_for_compact - 2000  # 留出生成空间
    if est_tokens > compress_budget:
        # 粗略按字符截断
        ratio = compress_budget / est_tokens
        cut_len = int(len(user_text) * ratio * 0.8)
        user_text = user_text[:cut_len] + '\n\n…（历史过长，中间已截断）\n\n' + user_text[-cut_len:]

    # ---- 4. 调用 LLM ----
    # llm_client 期望 (messages, tools)
    # 压缩任务不需要 tools
    messages_for_compact = [
        {'role': 'system', 'content': COMPACT_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_text},
    ]

    try:
        import json as _json

        # 临时 monkey-patch llm_client：让它能正确调用 OpenAIClient
        response = llm_client(messages_for_compact, [])
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': f'LLM 压缩调用失败: {type(exc).__name__}: {exc}'}

    # ---- 5. 解析摘要 ----
    if isinstance(response, dict):
        if 'error' in response:
            return {'ok': False, 'error': f'LLM 压缩返回错误: {response["error"]}'}
        summary = (response.get('content') or '').strip()
    else:
        summary = str(response).strip()

    if not summary:
        return {'ok': False, 'error': 'LLM 返回了空摘要，未执行压缩。'}

    summary_tokens = estimate_tokens(summary)

    # ---- 6. 构造新 history ----
    # 策略：摘要作为一条 assistant 消息（带 archive note） + 保留最近 4 条原始消息
    # （tool 消息除外，tool 消息没有 tool_call_id 会导致 API 报错 —— 这里全部丢弃，
    #  因为摘要里已经包含了工具调用的结果要点）
    keep_recent = 4
    keep_messages: list[dict] = []
    for msg in reversed(history):
        if len(keep_messages) >= keep_recent:
            break
        role = msg.get('role')
        if role == 'tool':
            continue  # tool 消息必须紧跟对应的 assistant tool_call，跳过它
        # 避免保留 tool_call 的 assistant 消息（格式复杂）
        if role == 'assistant' and msg.get('tool_calls'):
            continue
        # 也要跳过紧跟着的 tool_call 的 user（它一般没有实际内容）
        keep_messages.insert(0, copy.deepcopy(msg))

    new_history: list[dict] = [
        {
            'role': 'assistant',
            'content': summary + make_archive_note(archive_id),
        },
        *keep_messages,
    ]

    return {
        'ok': True,
        'archive_id': archive_id,
        'summary_tokens': summary_tokens,
        'new_history': new_history,
        'old_history_count': len(history),
        'new_history_count': len(new_history),
    }


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
