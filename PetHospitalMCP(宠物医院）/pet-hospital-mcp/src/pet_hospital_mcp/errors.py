"""统一错误处理：错误码、ToolError 异常、错误结构序列化。

所有无效输入和上游异常最终都映射为统一结构：

```json
{"error": {"code": "ERROR_CODE", "message": "可读错误信息", "details": {}}}
```

并以 `CallToolResult(is_error=True)` 的形式返回给 MCP 客户端，不暴露
HTTPX/Pydantic/SDK/Python 堆栈。
"""

from __future__ import annotations

import json
from typing import Any

from mcp_types import CallToolResult, TextContent

# 错误码常量
VALIDATION_ERROR = "VALIDATION_ERROR"
BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
BACKEND_API_ERROR = "BACKEND_API_ERROR"
BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolError(Exception):
    """工具执行过程中产生的、需要返回给客户端的统一错误。

    携带错误码、可读消息和可选 details。捕获后转换为带 is_error=True 的
    CallToolResult，绝不把原始异常堆栈透传给 MCP 客户端。
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """构造统一错误结构 dict。"""
    return {"error": {"code": code, "message": message, "details": details or {}}}


def to_error_result(error: ToolError) -> CallToolResult:
    """把 ToolError 转换为返回给客户端的 CallToolResult（is_error=True）。

    内容为统一错误结构的 JSON 字符串，不包含任何堆栈信息。
    """
    payload = error_payload(error.code, error.message, error.details)
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
        is_error=True,
    )


def internal_error_result(message: str = "internal error") -> CallToolResult:
    """兜底：未预期的内部错误。"""
    return to_error_result(ToolError(INTERNAL_ERROR, message))
