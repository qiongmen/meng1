"""list_pets 工具 handler 级测试（场景 1-5）。

通过 httpx.MockTransport 替换真实 Go 后端，禁止访问真实服务。
"""

from __future__ import annotations

import json

import httpx
import pytest

from helpers import (
    go_envelope,
    json_response,
    parse_tool_result,
    sample_pet,
    sample_result,
)


# ---------------------------------------------------------------------------
# 场景 1：正常调用——确认请求路径与全部过滤/排序/分页参数正确转发
# ---------------------------------------------------------------------------
async def test_normal_call_forwards_all_params(make_tool):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return json_response(go_envelope(sample_result()))

    tool, client = make_tool(handler)

    arguments = {
        "q": "旺财",
        "name": "旺财",
        "ownerName": "张三",
        "ownerPhone": "13800001111",
        "species": "犬",
        "doctor": "李医生",
        "disease": "肠胃炎",
        "status": "就诊中",
        "min": 100,
        "max": 1000,
        "sortBy": "totalCost",
        "order": "desc",
        "page": 2,
        "pageSize": 15,
    }
    result = await tool.run(arguments, context=None)

    # 请求路径与参数转发
    assert captured["path"] == "/api/v1/pets"
    params = captured["params"]
    for key, value in arguments.items():
        sent = params.get(key)
        # 数值字段经 pydantic 可能 int→float（min/max 为 float），统一按数值比较
        try:
            assert float(sent) == float(value), f"参数 {key} 未正确转发"
        except (TypeError, ValueError):
            assert sent == str(value), f"参数 {key} 未正确转发"

    # 不应发送未提供的参数
    assert "breed" not in params

    # 成功结果
    assert result.is_error is False
    payload = parse_tool_result(result)
    assert set(payload.keys()) == {"items", "total", "page", "pageSize", "totalPages", "totalCost"}
    assert payload["items"][0]["id"] == "PET-000001"

    await client.aclose()


# ---------------------------------------------------------------------------
# 场景 2：输入参数校验失败
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "arguments, hint",
    [
        ({"species": "未知物种"}, "species 非法枚举"),
        ({"status": "已死亡"}, "status 非法枚举"),
        ({"sortBy": "unknown"}, "sortBy 非法枚举"),
        ({"order": "random"}, "order 非法枚举"),
        ({"page": 0}, "page < 1"),
        ({"pageSize": 0}, "pageSize < 1"),
        ({"pageSize": 501}, "pageSize > 500"),
        ({"min": -1}, "min 负数"),
        ({"max": -5}, "max 负数"),
        ({"min": 1000, "max": 100}, "min > max"),
        ({"min": float("nan")}, "min 为 NaN"),
        ({"min": float("inf")}, "min 为 Infinity"),
        ({"unknown_field": "x"}, "未知字段"),
        ({"page": "abc"}, "page 类型错误"),
    ],
)
async def test_input_validation_failures(make_tool, arguments, hint):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        # 校验失败不应触达后端
        raise AssertionError("校验失败不应调用后端")

    tool, client = make_tool(handler)
    result = await tool.run(arguments, context=None)

    assert result.is_error is True, f"{hint}: 应标记 is_error"
    payload = parse_tool_result(result)
    assert payload["error"]["code"] == "VALIDATION_ERROR", f"{hint}: 错误码应为 VALIDATION_ERROR"
    assert "details" in payload["error"]
    await client.aclose()


# ---------------------------------------------------------------------------
# 场景 3：Go REST API 返回 4xx/5xx
# ---------------------------------------------------------------------------
async def test_backend_http_4xx(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=404, content="not found", headers={"content-type": "text/plain"})

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    payload = parse_tool_result(result)
    assert payload["error"]["code"] == "BACKEND_API_ERROR"
    assert payload["error"]["details"]["status_code"] == 404
    await client.aclose()


async def test_backend_http_5xx(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, content="boom", headers={"content-type": "text/plain"})

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    assert parse_tool_result(result)["error"]["code"] == "BACKEND_API_ERROR"
    await client.aclose()


async def test_backend_envelope_business_error(make_tool):
    # HTTP 200 但信封 code 为业务错误
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(go_envelope(None, code=400, message="参数错误"))

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    payload = parse_tool_result(result)
    assert payload["error"]["code"] == "BACKEND_API_ERROR"
    await client.aclose()


# ---------------------------------------------------------------------------
# 场景 4：超时和连接异常
# ---------------------------------------------------------------------------
async def test_backend_timeout(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    tool, client = make_tool(handler, timeout=0.5, retries=2)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    assert parse_tool_result(result)["error"]["code"] == "BACKEND_TIMEOUT"
    await client.aclose()


async def test_backend_unreachable(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    tool, client = make_tool(handler, timeout=0.5, retries=2)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    assert parse_tool_result(result)["error"]["code"] == "BACKEND_UNAVAILABLE"
    await client.aclose()


# ---------------------------------------------------------------------------
# 场景 5：后端返回非法 JSON 或不符合数据模型
# ---------------------------------------------------------------------------
async def test_backend_invalid_json(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content="<html>not json</html>", headers={"content-type": "text/html"})

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    assert parse_tool_result(result)["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    await client.aclose()


async def test_backend_not_envelope(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        # 合法 JSON 但不是信封结构
        return json_response({"random": "stuff"})

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    assert parse_tool_result(result)["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    await client.aclose()


async def test_backend_data_model_mismatch(make_tool):
    def handler(request: httpx.Request) -> httpx.Response:
        # data 不是 Result 结构（缺 items）
        return json_response(go_envelope({"unexpected": "shape"}))

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is True
    assert parse_tool_result(result)["error"]["code"] == "BACKEND_INVALID_RESPONSE"
    await client.aclose()


# ---------------------------------------------------------------------------
# 兼容性：records/charges 可为 null 或数组
# ---------------------------------------------------------------------------
async def test_records_charges_null_and_array(make_tool):
    pet_null = sample_pet(records=None, charges=None)
    pet_empty = sample_pet(records=[], charges=[])

    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(go_envelope(sample_result([pet_null, pet_empty])))

    tool, client = make_tool(handler)
    result = await tool.run({}, context=None)
    assert result.is_error is False
    payload = parse_tool_result(result)
    assert payload["items"][0]["records"] is None
    assert payload["items"][1]["records"] == []
    await client.aclose()
