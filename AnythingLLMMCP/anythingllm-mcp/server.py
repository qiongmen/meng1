"""AnythingLLM MCP Server (MVP)

仅暴露一个工具 ask_first_workspace：
向本机 AnythingLLM 的第一个工作区提问，基于工作区内文档检索后由 AI 回答。

传输方式：Streamable HTTP（非 stdio），默认监听 http://127.0.0.1:8765/mcp
配置（均有默认值，可用环境变量覆盖）：
  ANYTHINGLLM_BASE_URL  默认 http://localhost:3001
  ANYTHINGLLM_API_KEY   AnythingLLM Developer API Key
  MCP_PORT              默认 8765
"""

import os

import httpx
from mcp.server.mcpserver import MCPServer

BASE_URL = os.environ.get("ANYTHINGLLM_BASE_URL", "http://localhost:3001").rstrip("/")
API_KEY = os.environ.get("ANYTHINGLLM_API_KEY", "TE4DC69-KHDMH0Y-QP3JXM7-105T68E")
PORT = int(os.environ.get("MCP_PORT", "8765"))

mcp = MCPServer("anythingllm-mcp")


@mcp.tool()
def ask_first_workspace(question: str) -> str:
    """向 AnythingLLM 的第一个工作区提问，基于该工作区文档检索后由 AI 回答（RAG）。

    Args:
        question: 要提问的问题。
    """
    headers = {"Authorization": f"Bearer {API_KEY}"}
    try:
        with httpx.Client(base_url=BASE_URL, headers=headers, timeout=120) as client:
            resp = client.get("/api/v1/workspaces")
            resp.raise_for_status()
            workspaces = resp.json().get("workspaces", [])
            if not workspaces:
                return "错误：AnythingLLM 中不存在任何工作区。"

            ws = workspaces[0]
            slug, name = ws["slug"], ws.get("name", ws["slug"])

            resp = client.post(
                f"/api/v1/workspace/{slug}/chat",
                json={"message": question, "mode": "query"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        return f"调用 AnythingLLM 失败：{exc}"

    if data.get("error"):
        return f"AnythingLLM 返回错误：{data['error']}"

    answer = data.get("textResponse", "").strip()
    if not answer:
        answer = "（工作区中未检索到与问题相关的内容，AnythingLLM 未给出回答。）"

    sources = data.get("sources") or []
    src_titles = [s.get("title", "") for s in sources if s.get("title")]
    extra = f"\n\n来源工作区：{name}"
    if src_titles:
        extra += "\n参考文档：" + "、".join(dict.fromkeys(src_titles))
    return answer + extra


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=PORT,
        streamable_http_path="/mcp",
    )
