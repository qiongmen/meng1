"""运行时配置：从环境变量读取，全部带默认值。

环境变量：
- PET_HOSPITAL_BASE_URL：Go 宠物医院 REST API 地址，默认 http://127.0.0.1:8080
- MCP_HOST：MCP 服务监听地址，默认 127.0.0.1
- MCP_PORT：MCP 服务监听端口，默认 8000
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return value.strip() if value and value.strip() else default


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    if not value:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    pet_hospital_base_url: str
    mcp_host: str
    mcp_port: int
    # 后端调用超时（秒）与重试次数
    backend_timeout: float = 5.0
    backend_retries: int = 2


def load_config() -> Config:
    """从环境变量加载配置。"""
    base_url = _env_str("PET_HOSPITAL_BASE_URL", "http://127.0.0.1:8080")
    # 去掉末尾斜杠，避免拼接出双斜杠
    base_url = base_url.rstrip("/")
    return Config(
        pet_hospital_base_url=base_url,
        mcp_host=_env_str("MCP_HOST", "127.0.0.1"),
        mcp_port=_env_int("MCP_PORT", 8000),
    )
