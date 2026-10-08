"""命令行入口：`python -m pet_hospital_mcp` 或 `pet-hospital-mcp`。

读取环境变量配置，启动无状态 Streamable HTTP MCP 服务。
"""

from __future__ import annotations

from .config import load_config
from .server import run


def main() -> None:
    """从环境变量加载配置并启动 MCP 服务。"""
    config = load_config()
    run(config)


if __name__ == "__main__":
    main()
