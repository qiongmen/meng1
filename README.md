# LLM 课程实践项目

本仓库收录 LLM 系列课程的实践代码，包含三个子项目。

## 子项目

| 目录 | 说明 |
| --- | --- |
| `AnythingLLMServer/` | 网页客户端：调用 AnythingLLM Server 的 `POST /v1/document/upload` 接口，将文档上传到第一个工作区并自动完成向量化嵌入。 |
| `AnythingLLMMCP/anythingllm-mcp/` | AnythingLLM 的 MCP（Model Context Protocol）服务器，封装常用接口供 AI 调用。 |
| `PetHospitalMCP(宠物医院）/pet-hospital-mcp/` | 宠物医院 MCP 服务器，提供宠物 / 就诊等数据的工具接口。 |

## 环境约定

- Python 子项目各自在 `.venv` 虚拟环境中运行；依赖不上传（已加入 `.gitignore`）。
- 各子项目内附有独立 `README.md`，说明其安装与运行方式。

## 目录结构

```
LLM/
├─ AnythingLLMServer/                              # 上传嵌入网页
├─ AnythingLLMMCP/anythingllm-mcp/                 # AnythingLLM MCP 服务
└─ PetHospitalMCP(宠物医院）/pet-hospital-mcp/     # 宠物医院 MCP 服务
```
