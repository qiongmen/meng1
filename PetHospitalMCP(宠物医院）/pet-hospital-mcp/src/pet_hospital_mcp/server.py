"""MCP 服务装配：基于 SDK 2.x MCPServer，无状态 Streamable HTTP。

- 使用 `MCPServer`（非 FastMCP），协议版本 2026-07-28。
- 无状态 HTTP：无 initialize、无 Mcp-Session-Id、无会话存储。
- 保留 `/health` 健康检查端点（custom_route，不需鉴权）。
- MCP 端点路径：`/mcp`。
- 教学场景不实现认证、CORS、Origin 校验。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import Config, load_config
from .logging_config import configure_logging
from .rest_client import PetHospitalClient
from .tools import make_list_pets_tool

logger = logging.getLogger("pet_hospital_mcp.server")

# MCP 协议版本（2026-07-28），无状态 Streamable HTTP
MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_PATH = "/mcp"


def create_server(
    config: Config,
    *,
    backend_transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer[Any]:
    """根据配置构造 MCPServer，装配 list_pets 工具与 /health 路由。

    backend_transport 为 None 时走真实网络；测试可注入 httpx.MockTransport。
    返回的 MCPServer 同时持有 REST 客户端，供后续关闭复用。
    """
    # 长生命周期的 REST 客户端，供 list_pets 工具复用连接池
    client = PetHospitalClient(
        base_url=config.pet_hospital_base_url,
        timeout=config.backend_timeout,
        retries=config.backend_retries,
        transport=backend_transport,
    )

    list_pets_tool = make_list_pets_tool(client)

    server: MCPServer[Any] = MCPServer(
        name="pet-hospital-mcp",
        version="1.0.0",
        tools=[list_pets_tool],
        log_level="INFO",
    )

    # 保留客户端引用，便于关闭与测试访问
    server._pet_hospital_client = client  # type: ignore[attr-defined]

    @server.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> Response:
        """健康检查：返回服务与后端连通性的轻量探测。"""
        return JSONResponse(
            {
                "status": "ok",
                "service": "pet-hospital-mcp",
                "mcpProtocolVersion": MCP_PROTOCOL_VERSION,
                "backend": config.pet_hospital_base_url,
            }
        )

    return server


def create_app(
    config: Config,
    *,
    backend_transport: httpx.AsyncBaseTransport | None = None,
):
    """构造 Starlette ASGI app（测试与直连 uvicorn 用）。

    返回 (app, server)，server 供测试访问其持有的 REST 客户端以关闭连接池。
    """
    server = create_server(config, backend_transport=backend_transport)
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,  # 2026-07-28 单次交换：直接 JSON 响应
        stateless_http=True,
        host=config.mcp_host,
    )
    return app, server


def run(config: Config | None = None) -> None:
    """启动 MCP 服务（无状态 Streamable HTTP）。"""
    cfg = config or load_config()
    configure_logging("INFO")
    server = create_server(cfg)
    logger.info(
        "starting pet-hospital-mcp",
        extra={
            "mcp_host": cfg.mcp_host,
            "mcp_port": cfg.mcp_port,
            "mcp_path": MCP_PATH,
            "backend": cfg.pet_hospital_base_url,
        },
    )
    server.run(
        transport="streamable-http",
        host=cfg.mcp_host,
        port=cfg.mcp_port,
        streamable_http_path=MCP_PATH,
        json_response=True,  # 2026-07-28 单次交换：直接 JSON 响应
        stateless_http=True,
    )
