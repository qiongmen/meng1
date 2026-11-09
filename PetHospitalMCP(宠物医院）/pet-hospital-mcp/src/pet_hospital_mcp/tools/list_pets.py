"""list_pets 工具：适配 Go REST API `GET /api/v1/pets`。

设计要点：
- 工具名 snake_case：`list_pets`。
- 通过子类化 `FuncMetadata` 覆写 `call_fn_with_arg_validation`，把原始 arguments
  dict 直接交给 handler，绕过 SDK 默认的 pydantic 校验（其 ArgModelBase 默认
  `extra="ignore"`，无法拒绝未知字段，且校验失败会把 pydantic 堆栈暴露给客户端）。
- handler 内部用 `ListPetsInput`（strict）严格校验，捕获 ValidationError 转为
  统一 `VALIDATION_ERROR` 结果；上游异常映射为统一错误码。
- `Tool.parameters` 用 `ListPetsInput.model_json_schema()` 作为面向客户端的严格 schema。
"""

from __future__ import annotations

import functools
import json
import logging
import time
from typing import Any

import anyio.to_thread
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp_types import CallToolResult, TextContent
from pydantic import ValidationError

from ..errors import (
    INTERNAL_ERROR,
    ToolError,
    VALIDATION_ERROR,
    internal_error_result,
    to_error_result,
)
from ..logging_config import sanitize
from ..rest_client import ListPetsInput, ListPetsSuccess, PetHospitalClient

logger = logging.getLogger("pet_hospital_mcp.list_pets")

LIST_PETS_DESCRIPTION = """查询宠物医院在档宠物列表，支持模糊搜索、多维度过滤、排序与分页。

用途：
- 适配后端 `GET /api/v1/pets`，把其过滤/排序/分页能力暴露给 AI Agent。
- 适合在「按主人电话查宠物」「按种类/疾病/就诊状态筛选」「消费区间查询」
  「分页浏览」「排序查看」等场景下使用。

参数（全部可选，不传则不作为查询条件发送给后端）：
- q：全局模糊搜索关键词。
- name / ownerName / ownerPhone / species / doctor / disease / status：按字段精确过滤。
- min / max：按宠物在医院总花费（totalCost）区间过滤，非负，min<=max。
- sortBy：排序字段，可选 id/name/ownerName/species/doctor/disease/status/
  totalCost/visitCount/createdAt/updatedAt。
- order：asc 或 desc。
- page：页码，从 1 开始。
- pageSize：每页条数，1~500。

返回值：
- 成功：JSON 文本，结构为 {items,total,page,pageSize,totalPages,totalCost}，
  items 为宠物档案数组（records/charges 可能为 null 或数组）。
- 失败：JSON 文本，结构为 {error:{code,message,details}}，is_error=true。

校验：species/status/sortBy/order 必须是后端真实允许值；page>=1；
1<=pageSize<=500；min/max 非负且 min<=max；拒绝未知字段、NaN、Infinity 和类型错误。
"""


class _RawArgsMetadata(FuncMetadata):
    """把原始 arguments dict 直接传给 handler，由 handler 自行严格校验。

    这样可以：
    - 用 `extra="forbid"` 的 ListPetsInput 真正拒绝未知字段；
    - 捕获 ValidationError 后返回统一错误结构，不暴露 pydantic 堆栈；
    - 不让 SDK 默认的 `extra="ignore"` 静默丢弃未知字段。
    """

    async def call_fn_with_arg_validation(
        self,
        fn: Any,
        fn_is_async: bool,
        arguments_to_validate: dict[str, Any],
        arguments_to_pass_directly: dict[str, Any] | None,
        pre_validated: dict[str, Any] | None = None,
    ) -> Any:
        direct = dict(arguments_to_pass_directly or {})
        if fn_is_async:
            return await fn(arguments=arguments_to_validate, **direct)
        return await anyio.to_thread.run_sync(
            functools.partial(fn, arguments=arguments_to_validate, **direct)
        )


def _params_from_input(data: ListPetsInput) -> dict[str, Any]:
    """从校验后的输入模型构造查询参数，跳过 None。"""
    params: dict[str, Any] = {}
    for field in (
        "q", "name", "ownerName", "ownerPhone", "species", "doctor", "disease",
        "status", "min", "max", "sortBy", "order", "page", "pageSize",
    ):
        value = getattr(data, field)
        if value is not None:
            params[field] = value
    return params


def _format_validation_error(e: ValidationError) -> str:
    parts: list[str] = []
    for err in e.errors(include_url=False):
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "输入校验失败：" + "; ".join(parts)


def _validation_details(e: ValidationError) -> dict[str, Any]:
    """把 pydantic 校验错误转为可 JSON 序列化的 dict。

    e.errors() 的 ctx 可能携带不可序列化的异常对象，故用 pydantic 自带的
    .json()（含其序列化器）转一圈得到纯 dict，避免把堆栈暴露给客户端。
    """
    return {"errors": json.loads(e.json(include_url=False))}


def make_list_pets_tool(client: PetHospitalClient) -> Tool:
    """构造 list_pets 工具，绑定到给定 REST 客户端。"""

    async def list_pets_handler(arguments: dict[str, Any]) -> CallToolResult:
        start = time.perf_counter()

        # 1. 严格校验输入
        try:
            data = ListPetsInput.model_validate(arguments)
        except ValidationError as e:
            message = _format_validation_error(e)
            logger.warning(
                "list_pets validation failed",
                extra={
                    "tool_name": "list_pets",
                    "params": sanitize(arguments),
                    "status": VALIDATION_ERROR,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                },
            )
            return to_error_result(ToolError(VALIDATION_ERROR, message, _validation_details(e)))

        params = _params_from_input(data)

        # 2. 调用后端
        try:
            success: ListPetsSuccess = await client.list_pets(params)
        except ToolError as e:
            # 上游已映射好的统一错误（超时/不可达/4xx5xx/非法响应）
            logger.warning(
                "list_pets backend error",
                extra={
                    "tool_name": "list_pets",
                    "params": sanitize(params),
                    "status": e.code,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                },
            )
            return to_error_result(e)
        except Exception:
            # 兜底：未预期异常统一为 INTERNAL_ERROR，不暴露堆栈
            logger.exception(
                "list_pets unexpected error",
                extra={
                    "tool_name": "list_pets",
                    "params": sanitize(params),
                    "status": INTERNAL_ERROR,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                },
            )
            return internal_error_result("list_pets 执行出现未预期错误")

        # 3. 成功：返回结构化 JSON 文本
        logger.info(
            "list_pets success",
            extra={
                "tool_name": "list_pets",
                "params": sanitize(params),
                "status": "ok",
                "duration_ms": int((time.perf_counter() - start) * 1000),
            },
        )
        payload = success.model_dump(mode="json")
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            is_error=False,
        )

    return Tool(
        fn=list_pets_handler,
        name="list_pets",
        description=LIST_PETS_DESCRIPTION,
        parameters=ListPetsInput.model_json_schema(),
        fn_metadata=_RawArgsMetadata(arg_model=ArgModelBase),
        is_async=True,
        context_kwarg=None,
    )
