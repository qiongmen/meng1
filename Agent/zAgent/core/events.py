"""Agent 事件流定义。

Agent.send() 是一个生成器，产出以下事件，前端（TUI/Web/Mobile）消费它们。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class AgentEvent:
    """所有事件的基类（标记型，方便 isinstance 分发）。"""
    pass


@dataclass
class TextDelta(AgentEvent):
    """LLM 流式输出的一小段纯文本。"""
    text: str


@dataclass
class ToolCallBegin(AgentEvent):
    """LLM 请求调用一个工具（刚发出时）。"""
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolCallEnd(AgentEvent):
    """工具执行完毕。"""
    name: str
    result: str
    ok: bool = True


@dataclass
class TurnEnd(AgentEvent):
    """一轮对话结束（没有更多工具调用，LLM 给出最终回答）。"""
    text: str = ''


@dataclass
class AgentError(AgentEvent):
    """Agent 内部异常。"""
    message: str
