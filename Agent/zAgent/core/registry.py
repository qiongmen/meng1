"""工具注册表 + 工具描述转换。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolSpec:
    """单个工具的规范（面向 LLM 的 function calling schema）。"""
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    handler: Callable  # 实际处理函数，签名: handler(**kwargs) -> str


class ToolRegistry:
    """管理所有可用工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def function_calling_tools(self) -> list[dict]:
        """转成 LLM API 认识的 function calling 格式。"""
        return [
            {
                'type': 'function',
                'function': {
                    'name': spec.name,
                    'description': spec.description,
                    'parameters': spec.parameters,
                },
            }
            for spec in self._tools.values()
        ]

    def invoke(self, name: str, arguments: dict) -> tuple[bool, str]:
        """执行工具，返回 (ok, result)。"""
        spec = self._tools.get(name)
        if spec is None:
            return False, f'未知工具: {name}'
        try:
            result = spec.handler(**arguments)
            if not isinstance(result, str):
                result = str(result)
            return True, result
        except Exception as exc:
            return False, f'{type(exc).__name__}: {exc}'
