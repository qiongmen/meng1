# Pet Hospital MCP

把 [Go 宠物医院 REST API](../project1) 的能力暴露给 AI Agent 的 MCP 服务。

- 语言：Python 3.11+（实际开发与测试使用 Python 3.13）
- 官方 Python SDK：`mcp==2.0.0`
- MCP 协议版本：`2026-07-28`
- 使用 SDK 2.x 的 **`MCPServer`**（非 FastMCP）
- 无状态 Streamable HTTP 模型：**不实现** 旧协议的 `initialize`、`Mcp-Session-Id`、
  会话存储、会话过期、`max_sessions` 或有状态 SSE 恢复
- 唯一工具：`list_pets`（snake_case），适配 `GET /api/v1/pets`

## 安装

```bash
cd pet-hospital-mcp
# 已自带 .venv（含 mcp==2.0.0、httpx、pydantic、pytest、pytest-asyncio、uvicorn）
# 如需重建：
python -m venv .venv
# Windows
.venv\Scripts\activate
# Unix
source .venv/bin/activate
pip install "mcp==2.0.0" httpx pydantic pytest pytest-asyncio uvicorn
```

## 启动

### 1. 先启动 Go 宠物医院 REST API（必须）

Go 服务是唯一业务后端，MCP 只能通过 HTTP 调用它：

```bash
cd ../project1
go run . -seed
# 默认监听 127.0.0.1:8080，无鉴权
```

### 2. 再启动 MCP 服务

```bash
cd pet-hospital-mcp
# Windows（PowerShell）
$env:PYTHONPATH="src"; .venv\Scripts\python.exe -m pet_hospital_mcp
# Unix
PYTHONPATH=src .venv/bin/python -m pet_hospital_mcp
# 或安装为命令（需 setuptools）：
#   pip install -e . && pet-hospital-mcp
```

默认监听 `127.0.0.1:8000`，MCP 端点为 `http://127.0.0.1:8000/mcp`。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | Go 宠物医院 REST API 地址 |
| `MCP_HOST` | `127.0.0.1` | MCP 服务监听地址（默认只监听本机） |
| `MCP_PORT` | `8000` | MCP 服务监听端口 |

教学场景不实现认证、权限、CORS 或 Origin 校验。

## MCP 端点

- MCP 端点：`POST http://127.0.0.1:8000/mcp`
- 健康检查：`GET http://127.0.0.1:8000/health`

## 验证步骤

### /health 示例

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","service":"pet-hospital-mcp","mcpProtocolVersion":"2026-07-28","backend":"http://127.0.0.1:8080"}
```

### 用 MCP Inspector / SDK 2.x 客户端验证

无状态 2026-07-28 客户端只需直接 POST `/mcp`，无需 `initialize` 握手。
请求需携带：

- 头 `MCP-Protocol-Version: 2026-07-28`
- 头 `Content-Type: application/json`、`Accept: application/json`
- 路由头 `mcp-method`（等于 JSON-RPC 的 method）；
  `tools/call` 还需 `mcp-name`（等于被调工具名）
- body 的 `params._meta` 携带 `io.modelcontextprotocol/protocolVersion`
  与 `io.modelcontextprotocol/clientCapabilities`（取代旧版 initialize 握手）

### list_pets 调用示例

发现工具（`tools/list`）：

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' -H 'mcp-method: tools/list' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

调用 `list_pets`（按种类过滤 + 排序 + 分页）：

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'mcp-method: tools/call' -H 'mcp-name: list_pets' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_pets","arguments":{"species":"犬","sortBy":"totalCost","order":"desc","page":1,"pageSize":10},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

成功响应（`isError: false`，content 为 JSON 文本）：

```json
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"items\":[...],\"total\":N,\"page\":1,\"pageSize\":10,\"totalPages\":N,\"totalCost\":N}"}],"isError":false,"resultType":"complete"}}
```

失败响应（`isError: true`，统一错误结构）：

```json
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"error\":{\"code\":\"VALIDATION_ERROR\",\"message\":\"...\",\"details\":{}}}"}],"isError":true,"resultType":"complete"}}
```

错误码：`VALIDATION_ERROR`、`BACKEND_TIMEOUT`、`BACKEND_UNAVAILABLE`、
`BACKEND_API_ERROR`、`BACKEND_INVALID_RESPONSE`、`INTERNAL_ERROR`。

## 单元测试

```bash
cd pet-hospital-mcp
pytest -q
```

预期：全部通过（31 项）。测试用 `httpx.MockTransport` 模拟 Go 后端，
**禁止**访问真实 Go 服务，覆盖：

1. 正常调用：请求路径与全部过滤/排序/分页参数正确转发
2. 输入参数校验失败（非法枚举、page<1、pageSize 越界、min>max、NaN/Infinity、未知字段、类型错误）
3. Go REST API 返回 4xx/5xx（含 HTTP 状态与信封业务错误）
4. 超时和连接异常（有限重试后映射为 `BACKEND_TIMEOUT`/`BACKEND_UNAVAILABLE`）
5. 后端返回非法 JSON 或不符合数据模型
6. MCP 工具注册、工具名与 JSON Schema（`additionalProperties: false`）
7. SDK 2.x 无状态连接流程：不发 `initialize`、不要求/不返回 `Mcp-Session-Id`、
   工具可经 HTTP 发现与调用、`/health` 可用

## 模块结构

```text
pet-hospital-mcp/
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md
├── src/pet_hospital_mcp/
│   ├── __init__.py
│   ├── __main__.py        # 入口：读 env，启动无状态 Streamable HTTP
│   ├── config.py          # 环境变量配置
│   ├── server.py          # MCPServer 装配、/health、streamable_http_app
│   ├── rest_client.py     # httpx 客户端 + Pydantic 输入/成功/错误模型
│   ├── errors.py          # 错误码、ToolError、统一错误结构
│   ├── logging_config.py  # JSON 日志 + ownerPhone/ownerAddr/chipNo 脱敏
│   └── tools/
│       ├── __init__.py
│       └── list_pets.py   # list_pets 工具（严格校验 + 统一错误）
└── tests/
    ├── conftest.py        # MockTransport 夹具
    ├── helpers.py         # 样本数据、LifespanManager、断言辅助
    ├── test_list_pets.py  # 场景 1-5
    └── test_server.py     # 场景 6-7（MCP HTTP）
```

## 声明

本次只实现阶段一的唯一工具 `list_pets`，**未实现阶段二工具**。
