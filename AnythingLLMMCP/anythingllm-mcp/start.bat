@echo off
chcp 65001 >nul
cd /d %~dp0
title AnythingLLM MCP Server
echo Starting AnythingLLM MCP Server on http://127.0.0.1:8765/mcp
echo Press Ctrl+C to stop.
.venv\Scripts\python.exe server.py
pause
