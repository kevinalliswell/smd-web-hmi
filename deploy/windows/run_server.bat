@echo off
REM Foreground start (commissioning / debugging).
REM For production auto-start register a scheduled task or a service - see README.md.
REM ASCII only on purpose (zh-CN cmd garbles UTF-8 batch files).
setlocal
title smd-web-hmi
cd /d "%~dp0app\backend"
"%~dp0venv\Scripts\python" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
