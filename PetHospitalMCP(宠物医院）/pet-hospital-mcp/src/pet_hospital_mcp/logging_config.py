"""日志配置：标准库 JSON 日志 + 敏感字段递归脱敏。

日志条目至少包含：timestamp、tool_name、params、status、duration_ms。
ownerPhone、ownerAddr、chipNo 及其 snake_case 写法在日志中递归脱敏，
不把完整敏感数据写入日志。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

# 需要脱敏的字段名（含 snake_case 写法），大小写不敏感匹配
SENSITIVE_KEYS = {
    "ownerphone",
    "owneraddr",
    "chipno",
    "owner_phone",
    "owner_addr",
    "chip_no",
}

_MASKED = "***MASKED***"


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    return k in SENSITIVE_KEYS


def sanitize(value: Any) -> Any:
    """递归脱敏：dict 命中敏感键置为掩码，list/dict 递归处理。

    针对 Go API 的 ownerPhone/ownerAddr/chipNo 字段，及其 snake_case 写法。
    """
    if isinstance(value, dict):
        return {
            k: (_MASKED if _is_sensitive(k) else sanitize(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """输出单行 JSON 日志，包含 timestamp、level、name、message 及 extra 字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # 合并 extra 字段（tool_name/params/status/duration_ms 等）
        for key in ("tool_name", "params", "status", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        # 其它非标准属性
        for key, val in record.__dict__.items():
            if key in payload or key in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "tool_name",
                "params", "status", "duration_ms",
            }:
                continue
            payload[key] = val
        # 脱敏
        payload = sanitize(payload)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """配置根 logger 输出 JSON 日志。"""
    root = logging.getLogger()
    # 清理已有 handler，避免重复输出
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
