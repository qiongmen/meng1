"""SSE 流式聊天 + 静态页面。

SSE 事件格式严格遵循 `event: type\\ndata: {...}\\n\\n`，
保证前端按标准浏览器 EventSource / ReadableStream 都能正确解析。
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.factory import build_agent

STATIC_DIR = Path(__file__).resolve().parent / 'static'
INDEX_HTML = STATIC_DIR / 'index.html'


class ChatRequest(BaseModel):
    text: str


def _format_sse(event_type: str, payload: dict) -> str:
    """统一 SSE 序列化器。"""
    data = json.dumps(payload, ensure_ascii=False)
    return f'event: {event_type}\ndata: {data}\n\n'


def _iter_sse(text: str, agent) -> queue.Queue:
    """后台线程消费 agent.send()，把事件塞进队列。调用端用 StreamingResponse 把队列内容逐块 yield。"""
    q: queue.Queue = queue.Queue()

    def run() -> None:
        try:
            for event in agent.send(text):
                q.put(_event_to_sse(event))
        except Exception as exc:  # 兜底，避免线程静默挂掉
            q.put(_format_sse('error', {'message': f'{type(exc).__name__}: {exc}'}))
        finally:
            q.put(None)  # Sentinel：流结束

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return q


def _event_to_sse(event) -> str:
    from core.events import TextDelta, ToolCallBegin, ToolCallEnd, TurnEnd, AgentError

    if isinstance(event, TextDelta):
        return _format_sse('text_delta', {'text': event.text})
    if isinstance(event, ToolCallBegin):
        return _format_sse('tool_call_begin', {'name': event.name, 'arguments': event.arguments})
    if isinstance(event, ToolCallEnd):
        return _format_sse('tool_call_end', {'name': event.name, 'result': event.result, 'ok': event.ok})
    if isinstance(event, TurnEnd):
        return _format_sse('turn_end', {'text': event.text})
    if isinstance(event, AgentError):
        return _format_sse('error', {'message': event.message})
    return _format_sse('error', {'message': f'未知事件类型: {type(event).__name__}'})


def create_app(config_path: str | None = None) -> FastAPI:
    """每个浏览器会话一个 Agent 实例（Agent 非线程安全）。"""
    app = FastAPI(title='zAgent Web', version='1.0.0')
    app.state.config_path = config_path

    def _get_agent():
        return build_agent(app.state.config_path)

    @app.get('/')
    def index() -> FileResponse:
        if not INDEX_HTML.exists():
            raise HTTPException(404, detail='index.html 未找到')
        return FileResponse(INDEX_HTML, media_type='text/html; charset=utf-8')

    @app.post('/api/chat')
    async def chat(req: ChatRequest) -> StreamingResponse:
        if not req.text.strip():
            raise HTTPException(400, detail='text 不能为空')

        # 每次请求独立 Agent，避免并发串话
        agent = _get_agent()
        q = _iter_sse(req.text, agent)

        def event_stream():
            while True:
                item = q.get()
                if item is None:
                    break
                yield item.encode('utf-8')

        return StreamingResponse(
            event_stream(),
            media_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            },
        )

    # 工具列表（前端显示用）— 临时建一个 agent 只为拿工具名
    @app.get('/api/tools')
    def tools() -> dict:
        agent = _get_agent()
        return {'tools': agent._registry.names}

    # 历史（调试/后续扩展用）
    @app.get('/api/history')
    def history() -> dict:
        agent = _get_agent()
        return {'messages': agent.history}

    return app


def run(host: str = '127.0.0.1', port: int = 8000, config_path: str | None = None) -> None:
    import uvicorn
    uvicorn.run(create_app(config_path), host=host, port=port, log_level='info')
