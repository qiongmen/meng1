"""MCP 服务级 HTTP 测试（场景 6-7）。

通过 httpx.ASGITransport 直连 Starlette app，验证 SDK 2.x 无状态 HTTP 流程：
- 工具注册、工具名与 JSON Schema（场景 6）
- 无状态：不发送 initialize、不要求/不返回 Mcp-Session-Id、可经 HTTP 发现并调用
  list_pets、/health 可用（场景 7）

后端用 httpx.MockTransport 模拟，禁止访问真实 Go 服务。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pet_hospital_mcp.server import MCP_PATH, MCP_PROTOCOL_VERSION, create_app

from helpers import LifespanManager, go_envelope, json_response, sample_result


# ---------------------------------------------------------------------------
# 夹具：构造带 MockTransport 后端的 MCP app + httpx ASGI 客户端
# ---------------------------------------------------------------------------
@pytest.fixture
async def mcp_stack(config):
    """返回 (httpx_client, server)，后端用 MockTransport 返回固定宠物列表。"""

    def backend_handler(request: httpx.Request) -> httpx.Response:
        # 校验请求路径
        assert request.url.path == "/api/v1/pets"
        return json_response(go_envelope(sample_result()))

    app, server = create_app(config, backend_transport=httpx.MockTransport(backend_handler))
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app):
        # base_url 带 8000 端口，使 Host 头匹配 DNS rebinding 的 127.0.0.1:* 白名单
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            yield client, server
    # 关闭后端连接池
    await server._pet_hospital_client.aclose()


def mcp_headers(method: str, name: str | None = None) -> dict[str, str]:
    """2026-07-28 现代路径必需的请求头：JSON + Accept + 协议版本 + 路由头。

    路由头 mcp-method/mcp-name 取代旧版 initialize，与请求体的 method/name 必须一致。
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "mcp-method": method,
    }
    if name is not None:
        headers["mcp-name"] = name
    return headers


def _meta_envelope() -> dict[str, Any]:
    """2026-07-28 以 _meta 携带协议版本与客户端能力，取代旧版 initialize 握手。"""
    return {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }


async def mcp_post(client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
    """向 /mcp 发送一个 JSON-RPC 请求并返回响应。

    自动注入 2026-07-28 现代路径所需的 _meta 与路由头（mcp-method/mcp-name），
    取代旧版 initialize 握手。
    """
    body = dict(body)
    params = dict(body.get("params") or {})
    params.setdefault("_meta", _meta_envelope())
    body["params"] = params

    method = body.get("method", "")
    name = None
    if method == "tools/call":
        name = params.get("name")
    return await client.post(MCP_PATH, content=json.dumps(body), headers=mcp_headers(method, name))


# ---------------------------------------------------------------------------
# 场景 6：MCP 工具注册、工具名和 JSON Schema
# ---------------------------------------------------------------------------
async def test_tool_registration_via_http(mcp_stack):
    client, _ = mcp_stack
    response = await mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1

    tools = body["result"]["tools"]
    assert len(tools) == 1
    tool = tools[0]
    # 工具名 snake_case
    assert tool["name"] == "list_pets"
    # 描述存在且非空
    assert tool.get("description")
    # 严格 schema：拒绝未知字段
    schema = tool["inputSchema"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    expected = {
        "q", "name", "ownerName", "ownerPhone", "species", "doctor", "disease",
        "status", "min", "max", "sortBy", "order", "page", "pageSize",
    }
    assert set(properties.keys()) == expected

    # 枚举字段携带真实允许值（Optional Literal 的 enum 嵌在 anyOf[0] 下）
    def enum_of(prop: dict[str, Any]) -> list[Any]:
        if "enum" in prop:
            return prop["enum"]
        for branch in prop.get("anyOf", []):
            if "enum" in branch:
                return branch["enum"]
        return []

    assert "犬" in enum_of(properties["species"])
    assert "就诊中" in enum_of(properties["status"])
    assert "totalCost" in enum_of(properties["sortBy"])
    assert set(enum_of(properties["order"])) == {"asc", "desc"}


# ---------------------------------------------------------------------------
# 场景 7：SDK 2.x 无状态连接流程
# ---------------------------------------------------------------------------
async def test_stateless_no_initialize_required(mcp_stack):
    """直接发 tools/list，无需先 initialize。"""
    client, _ = mcp_stack
    response = await mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    body = response.json()
    # 没有 JSON-RPC 错误（即不要求 initialize）
    assert "error" not in body
    assert "result" in body


async def test_stateless_no_mcp_session_id(mcp_stack):
    """响应不携带 Mcp-Session-Id 头。"""
    client, _ = mcp_stack
    response = await mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "mcp-session-id" not in {k.lower() for k in response.headers.keys()}
    assert "mcp-session-id" not in response.headers


async def test_stateless_request_without_session_header(mcp_stack):
    """请求不携带 Mcp-Session-Id 头也能成功（无状态）。"""
    client, _ = mcp_stack
    # mcp_headers 不含 Mcp-Session-Id
    response = await mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 200
    assert "result" in response.json()


async def test_health_endpoint(mcp_stack):
    """/health 返回 200 与状态信息。"""
    client, _ = mcp_stack
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mcpProtocolVersion"] == MCP_PROTOCOL_VERSION


async def test_list_pets_discoverable_and_callable_via_http(mcp_stack):
    """list_pets 可经 HTTP MCP 端点被发现并调用。"""
    client, _ = mcp_stack

    # 1) 发现
    list_resp = await mcp_post(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = list_resp.json()["result"]["tools"]
    assert any(t["name"] == "list_pets" for t in tools)

    # 2) 调用（带过滤/排序/分页参数）
    call_resp = await mcp_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "list_pets",
                "arguments": {"species": "犬", "sortBy": "totalCost", "order": "desc", "page": 1, "pageSize": 10},
            },
        },
    )
    assert call_resp.status_code == 200
    result = call_resp.json()["result"]
    # SDK 2.x：isError 标记失败；此处成功
    assert result.get("isError") is False
    text = result["content"][0]["text"]
    payload = json.loads(text)
    assert set(payload.keys()) == {"items", "total", "page", "pageSize", "totalPages", "totalCost"}
    assert payload["items"][0]["id"] == "PET-000001"


async def test_list_pets_validation_error_via_http(mcp_stack):
    """经 HTTP 调用时输入校验失败返回统一错误结构。"""
    client, _ = mcp_stack
    response = await mcp_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "list_pets", "arguments": {"species": "未知物种"}},
        },
    )
    result = response.json()["result"]
    assert result.get("isError") is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "VALIDATION_ERROR"
