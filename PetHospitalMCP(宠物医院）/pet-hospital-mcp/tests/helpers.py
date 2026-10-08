"""测试辅助函数：构造 Go 响应信封、样本数据与 httpx 响应。

放在独立模块（而非 conftest.py），便于测试文件直接 import。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"


class LifespanManager:
    """驱动 ASGI app 的 lifespan 启动/关闭事件。

    httpx.ASGITransport 不会触发 ASGI lifespan，而 MCP 的 StreamableHTTP
    会话管理器需要在 lifespan 中初始化 task group，故测试中用它包裹 app。

    用 asyncio.create_task 而非 anyio.create_task_group，避免跨 task 退出
    cancel scope 的问题（pytest-asyncio 夹具 setup/teardown 可能不同 task）。
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._recv_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._startup_complete = asyncio.Event()
        self._shutdown_complete = asyncio.Event()
        self._exc: BaseException | None = None
        self._task: asyncio.Task | None = None

    async def __aenter__(self) -> "LifespanManager":
        self._task = asyncio.create_task(self._run())
        await self._recv_q.put({"type": "lifespan.startup"})
        await self._startup_complete.wait()
        if self._exc:
            raise self._exc
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self._recv_q.put({"type": "lifespan.shutdown"})
        await self._shutdown_complete.wait()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        try:
            await self.app({"type": "lifespan"}, self._receive, self._send)
        except BaseException as e:  # noqa: BLE001
            self._exc = e
        finally:
            self._startup_complete.set()
            self._shutdown_complete.set()

    async def _receive(self) -> dict[str, Any]:
        return await self._recv_q.get()

    async def _send(self, message: dict[str, Any]) -> None:
        mtype = message.get("type")
        if mtype == "lifespan.startup.complete":
            self._startup_complete.set()
        elif mtype == "lifespan.shutdown.complete":
            self._shutdown_complete.set()


def go_envelope(
    data: Any = None,
    *,
    code: int = 200,
    message: str = "ok",
    time: str = "2026-09-23T10:00:00Z",
) -> dict[str, Any]:
    """构造 Go API 的 Resp 信封。"""
    envelope: dict[str, Any] = {"code": code, "message": message, "time": time}
    if data is not None:
        envelope["data"] = data
    return envelope


def sample_pet(
    *,
    records: Any = "...SENTINEL...",
    charges: Any = "...SENTINEL...",
    **overrides: Any,
) -> dict[str, Any]:
    """构造单个宠物档案 dict，默认填充完整字段。"""
    pet: dict[str, Any] = {
        "id": "PET-000001",
        "name": "旺财",
        "species": "犬",
        "breed": "金毛",
        "gender": "公",
        "ageMonths": 24,
        "color": "金色",
        "chipNo": "CHIP-001",
        "ownerName": "张三",
        "ownerPhone": "13800001111",
        "ownerAddr": "北京市朝阳区",
        "doctor": "李医生",
        "disease": "急性肠胃炎",
        "status": "就诊中",
        "allergy": "无",
        "note": "需复诊",
        "records": [] if records == "...SENTINEL..." else records,
        "charges": [] if charges == "...SENTINEL..." else charges,
        "totalCost": 500.0,
        "visitCount": 1,
        "createdAt": "2026-09-01T08:00:00Z",
        "updatedAt": "2026-09-02T09:00:00Z",
    }
    pet.update(overrides)
    return pet


def sample_result(
    pets: list[dict[str, Any]] | None = None,
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """构造 Go store.Result（data 字段内容）。"""
    pets = pets if pets is not None else [sample_pet()]
    return {
        "items": pets,
        "total": total if total is not None else len(pets),
        "page": page,
        "pageSize": page_size,
        "totalPages": 1,
        "totalCost": sum(p["totalCost"] for p in pets),
    }


def json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    """构造 JSON httpx.Response。"""
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload, ensure_ascii=False),
        headers={"content-type": "application/json"},
    )


def make_mock_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    """用给定 handler 构造 httpx.MockTransport。"""
    return httpx.MockTransport(handler)


def parse_tool_result(result) -> dict[str, Any]:
    """把 CallToolResult 的文本内容解析回 dict。"""
    text = result.content[0].text
    return json.loads(text)
