@echo off
chcp 65001 >nul
echo Stopping AnythingLLM MCP Server (port 8765)...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="0" (echo No running server found on port 8765.) else (echo Stopped.)
