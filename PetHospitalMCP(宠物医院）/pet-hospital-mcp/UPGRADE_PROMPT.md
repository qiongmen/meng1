# 阶段二升级提示词（UPGRADE_PROMPT）

> 本文件是 **阶段二** 的开发提示词，供后续在阶段一已完成的基础上扩展更多 MCP 工具使用。
> 阶段一已交付：仅 `list_pets` 一个工具，严格遵循 MCP `2026-07-28` 无状态 Streamable HTTP 模型，全部 31 个单元测试通过。
> **在用户明确要求开始阶段二之前，不要执行本提示词。**

---

## 背景与起点

你是一位精通 Model Context Protocol（MCP）和 Python 的专家开发者。当前仓库 `pet-hospital-mcp/` 已经完成阶段一交付：

- Python 3.11+，官方 SDK `mcp==2.0.0`，协议版本 `2026-07-28`
- 使用 SDK 2.x 的 `MCPServer`（**非** FastMCP）
- 无状态 Streamable HTTP：不实现旧 `initialize`、`Mcp-Session-Id`、会话存储、`max_sessions`、有状态 SSE 恢复
- 唯一工具 `list_pets`，严格适配 Go 后端 `GET /api/v1/pets` 的 14 个查询参数
- 已建立的可复用基础设施：
  - `src/pet_hospital_mcp/config.py`：环境变量配置（`PET_HOSPITAL_BASE_URL` / `MCP_HOST` / `MCP_PORT`）
  - `src/pet_hospital_mcp/errors.py`：统一错误结构 `{error:{code,message,details}}` + 6 个错误码 + `CallToolResult(is_error=True)` 构造器
  - `src/pet_hospital_mcp/logging_config.py`：JSON 日志 + `ownerPhone`/`ownerAddr`/`chipNo` 递归脱敏
  - `src/pet_hospital_mcp/rest_client.py`：`PetHospitalClient`（httpx.AsyncClient + 超时 + 有限重试 + 错误映射），含 `ListPetsInput` 等模型
  - `src/pet_hospital_mcp/tools/list_pets.py`：工具装配范例（`_RawArgsMetadata` 严格校验 + `make_list_pets_tool` 工厂）
  - `src/pet_hospital_mcp/server.py`：`create_server` / `create_app` / `run`，已注册 `/health` 自定义路由
  - `tests/`：`helpers.py`（`LifespanManager` + Mock 工厂）+ `conftest.py` + `test_list_pets.py` + `test_server.py`

开始阶段二前，请：

1. 重新阅读 `E:\上课使用软件\prompt-step1.md`（阶段一原始提示词），确保不违背其中任何约束；
2. 阅读当前仓库的 `README.md`、`pyproject.toml` 与 `src/pet_hospital_mcp/` 全部源码，理解既有架构与约定；
3. 查阅 Go 宠物医院 REST API（`e:\trea项目\project1`）的实际路由定义，确认阶段二要新增的工具对应的后端接口确实存在；
4. 查阅官方 Python SDK 2.x 与 MCP `2026-07-28` 文档，确认所使用的 API 仍与阶段一一致；如遇 SDK 升级，先验证再升级，**不要凭旧经验猜测**。

## 阶段二目标

在保持阶段一架构、约定、错误处理、日志脱敏、无状态语义全部不变的前提下，**新增多个 MCP 工具**，把 Go 宠物医院 REST API 的更多能力暴露给 AI Agent。

候选工具（**最终交付清单以 Go 后端实际暴露的接口为准**，不要臆造接口；如果某个接口后端不存在，跳过并记录）：

- `get_pet_by_id` —— `GET /api/v1/pets/{id}`
- `create_pet` —— `POST /api/v1/pets`
- `update_pet` —— `PUT /api/v1/pets/{id}`（或 `PATCH`，以后端实际为准）
- `delete_pet` —— `DELETE /api/v1/pets/{id}`
- 以及后端真实存在的其他只读/写接口（例如医生、就诊记录、账单等，**以后端实际路由为准**）

## 强制约束（继承自阶段一，不得违反）

- Python 3.11+，`mcp==2.0.0`，协议 `2026-07-28`
- 使用 SDK 2.x 的 `MCPServer`；**禁止** `from mcp.server.fastmcp import FastMCP`
- **禁止** 实现旧协议的 `initialize`、`Mcp-Session-Id`、会话存储、会话过期、`max_sessions`、有状态 SSE 恢复
- 每个 HTTP 请求仍须带 `MCP-Protocol-Version` 头；无状态单次请求/响应
- 工具名一律 `snake_case`，不新增适配器私有业务参数
- 输入模型使用 Pydantic v2 严格校验：
  - `model_config = ConfigDict(extra="forbid")`
  - 枚举字段使用 `Literal[...]` 并使用**真实后端允许值**
  - 数值字段使用 `Field(ge=...)` / `le=...)` 约束
  - `@field_validator` 拒绝 `NaN` / `Infinity`（用 `math.isfinite`）
  - `@model_validator` 校验跨字段关系（如 `min <= max`）
  - 拒绝未知字段、类型错误
- 成功输出模型必须严格对应 Go API 的成功响应 `data` 结构；对后端可能为 `null` 或数组的可空字段，使用 `list[...] | None` 容错
- 错误结构沿用阶段一：
  ```json
  {"error": {"code": "ERROR_CODE", "message": "可读错误信息", "details": {}}}
  ```
  错误码集合在阶段一 6 个基础上**保持兼容**，新增需要时再追加：
  `VALIDATION_ERROR`、`BACKEND_TIMEOUT`、`BACKEND_UNAVAILABLE`、`BACKEND_API_ERROR`、`BACKEND_INVALID_RESPONSE`、`INTERNAL_ERROR`
- 后端调用必须有超时和有限重试（复用 `PetHospitalClient` 既有机制即可）
- 不得把 HTTPX / Pydantic / SDK / Python 堆栈原样暴露给 MCP 客户端
- 工具调用失败按 SDK 2.x 规定标记 `CallToolResult(is_error=True)`（阶段一已验证此写法在 SDK 2.0.0 下序列化为 `isError`）
- 配置仍走 `PET_HOSPITAL_BASE_URL` / `MCP_HOST` / `MCP_PORT`，默认 `http://127.0.0.1:8080` / `127.0.0.1` / `8000`
- 保留 `/health`，教学场景不实现认证 / CORS / Origin 校验
- 日志沿用阶段一 JSON 结构：`timestamp` / `level` / `logger` / `message` / `tool_name` / `params` / `status` / `duration_ms`；`ownerPhone` / `ownerAddr` / `chipNo` 及其 snake_case 写法递归脱敏

## 模块结构（阶段二扩展方式）

阶段一只需在 `tools/` 增加模块并复用 REST 客户端、日志、错误约定。阶段二遵循同样方式：

```text
pet_hospital_mcp/
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md            # 本文件
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── server.py
│       ├── rest_client.py        # 扩展：新增对应 HTTP 方法的客户端方法
│       ├── errors.py
│       ├── logging_config.py
│       └── tools/
│           ├── __init__.py        # 扩展：导出新工具的 make_*_tool
│           ├── list_pets.py       # 阶段一
│           ├── get_pet_by_id.py   # 阶段二新增
│           ├── create_pet.py     # 阶段二新增
│           ├── update_pet.py      # 阶段二新增
│           └── delete_pet.py      # 阶段二新增
└── tests/
    ├── helpers.py                 # 复用，按需扩展 sample_* 工厂
    ├── conftest.py                # 复用，按需扩展 fixture
    ├── test_list_pets.py          # 阶段一
    ├── test_get_pet_by_id.py      # 阶段二新增
    ├── test_create_pet.py         # 阶段二新增
    ├── test_update_pet.py         # 阶段二新增
    └── test_delete_pet.py         # 阶段二新增
```

**强制要求**：阶段二新增工具时，**禁止复制粘贴 `_RawArgsMetadata`** 等基础设施实现。应将其上提到 `rest_client.py` 或新建 `tools/_common.py` 共享，避免散落多处导致行为漂移。如果发现阶段一的某些私有实现（如 `_RawArgsMetadata`、`_params_from_input`、`_format_validation_error`、`_validation_details`）适合被多个工具复用，应先重构提取共享，再用于新工具，并确保 `list_pets` 原有测试仍全绿。

## 测试要求

使用 `pytest` + `pytest-asyncio` + `httpx.MockTransport`，**禁止**测试时访问真实 Go 服务。

每个新增工具至少覆盖：

1. 正常调用：确认请求方法、路径、路径参数、查询参数、请求体正确转发；
2. 输入参数校验失败（未知字段 / 非法枚举 / 数值越界 / 类型错误 / 跨字段约束）；
3. Go REST API 返回 4xx / 5xx；
4. 超时和连接异常；
5. 后端返回非法 JSON 或不符合数据模型；
6. 工具在 MCP HTTP 端点可被发现（`tools/list`）且 JSON Schema 正确；
7. 工具在 MCP HTTP 端点可被调用（`tools/call`），成功与失败路径均符合阶段一统一错误结构；
8. 写操作（`POST` / `PUT` / `PATCH` / `DELETE`）必须验证请求体被严格校验后才转发，且后端非 2xx 响应不会被误当成成功。

阶段一原有 31 个测试必须**继续全部通过**，不得因阶段二重构而回归。

运行命令仍必须是：

```bash
cd pet_hospital-mcp
pytest -q
```

## README 更新要求

阶段二完成后，更新 `README.md`：

- 在「工具列表」一节列出全部新增工具及对应后端接口；
- 为每个新增工具给出调用示例（含 `_meta` 信封与 `mcp-method` / `mcp-name` 路由头）；
- 在「模块结构」一节同步更新目录树；
- 明确声明：阶段二工具已实现，并给出对应后端接口的真实路由。

## 最终交付

阶段二完成后请输出：

1. 更新后的目录结构；
2. 实际修改/新增的文件列表（区分「新增」与「修改」）；
3. 测试命令和真实测试结果（包含阶段一回归 + 阶段二新增）；
4. 如何启动 Go 服务和 MCP 服务；
5. 如何通过无状态 MCP HTTP 端点发现并调用每个新增工具（给出可复制的 `curl` 或 SDK 客户端示例）；
6. 明确声明：每个新增工具对应的后端接口真实存在（贴出 Go 后端路由定义代码位置作为证据）；如某候选工具因后端无对应接口而被跳过，也明确列出。
