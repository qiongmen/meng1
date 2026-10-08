# AnythingLLM MCP Server (MVP)

一个极简的 Python MCP Server，通过 **Streamable HTTP** 传输（非 stdio），对接本机 AnythingLLM。
基于 MCP Python SDK `mcp 2.x`，支持 **MCP 协议 2026-07-28**（同时兼容旧版握手协议）。

## 功能（仅一个工具）

- **`ask_first_workspace(question: str)`**
  自动获取 AnythingLLM 中的**第一个工作区**，把问题发到该工作区（`mode=query`，即基于工作区文档检索后由 AI 回答 / RAG），返回 AI 答案及来源文档。

## 架构

```
TRAE / MCP 客户端  --HTTP-->  本 MCP Server (127.0.0.1:8765/mcp)  --HTTP-->  AnythingLLM (127.0.0.1:3001)
```

## 前提条件

1. AnythingLLM 已在本机运行：http://localhost:3001 ，且已生成 Developer API Key。
2. Python 3.10+（开发环境为 Python 3.13）。

## 安装（仅首次）

```powershell
cd e:\trea项目\anythingllm-mcp
C:\Users\Lenovo\AppData\Local\Programs\Python\Python313\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
# 若 pypi 慢，可加镜像： -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## 启动 / 停止

### 启动（推荐：独立窗口前台运行）

双击或在终端执行：

```powershell
e:\trea项目\anythingllm-mcp\start.bat
```

看到 `Uvicorn running on http://127.0.0.1:8765` 即启动成功。
**停止**：在该窗口按 `Ctrl+C`（或直接关闭窗口）。

### 停止（按端口结束，适用于后台/其他窗口启动的实例）

```powershell
e:\trea项目\anythingllm-mcp\stop.bat
```

脚本会查找并结束监听 8765 端口的进程。

### 命令行直接运行（不用脚本）

```powershell
.venv\Scripts\python.exe server.py      # 启动，Ctrl+C 停止
```

## 在 TRAE 中配置（项目级 MCP）

项目级配置文件为 `e:\trea项目\.trae\mcp.json`（该文件受保护需手动创建/编辑），
内容可直接复制 [mcp.json.example](mcp.json.example)：

```json
{
  "mcpServers": {
    "anythingllm": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

也可以在 TRAE 的 MCP 管理界面选择添加 **HTTP/URL 类型** 的服务，URL 填：
`http://127.0.0.1:8765/mcp`

配置后先运行 `start.bat` 启动服务，再在 TRAE 中启用/刷新该 MCP，即可看到 `ask_first_workspace` 工具。

## 配置项（环境变量，均可省略）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ANYTHINGLLM_BASE_URL` | `http://localhost:3001` | AnythingLLM 地址 |
| `ANYTHINGLLM_API_KEY` | 已内置当前本机 Key | AnythingLLM Developer API Key |
| `MCP_PORT` | `8765` | MCP Server 监听端口 |

## 常见问题

- **返回 "no relevant information / 未检索到相关内容"**：链路正常，说明第一个工作区里还没有嵌入文档，或文档与问题无关。在 AnythingLLM 中给该工作区上传文档并完成向量嵌入后再提问即可。
- **403 Invalid API Key**：通过环境变量 `ANYTHINGLLM_API_KEY` 设置正确的 Key。
- **端口被占用**：先运行 `stop.bat`；或用 `MCP_PORT=端口` 启动并同步修改 mcp.json 中的 URL。

## 文件说明

| 文件 | 作用 |
|---|---|
| `server.py` | MCP Server 全部逻辑（单文件） |
| `requirements.txt` | 依赖：mcp、httpx |
| `start.bat` / `stop.bat` | 启动 / 停止脚本 |
| `mcp.json.example` | TRAE 项目级 MCP 配置模板 |
