@echo off
REM ============================================================
REM smd-web-hmi offline installer. Run from the bundle root.
REM ASCII only on purpose: zh-CN Windows cmd garbles UTF-8 batch
REM files (same lesson as the alembic.ini ASCII fix, da16b30).
REM Chinese docs: README.md in this directory.
REM Requires: Python 3.11 x64 already installed.
REM ============================================================
setlocal
cd /d "%~dp0"

echo [1/4] Creating venv ...
if exist venv\Scripts\python.exe goto deps
py -3.11 -m venv venv 2>nul
if exist venv\Scripts\python.exe goto deps
python -m venv venv
if not exist venv\Scripts\python.exe goto fail

:deps
echo [2/4] Installing dependencies (offline, from wheels\) ...
venv\Scripts\python -m pip install --no-index --find-links=wheels -r app\backend\requirements.txt
if errorlevel 1 goto fail

echo [3/4] Writing config ...
if not exist data mkdir data
if not exist backups mkdir backups
if exist app\backend\.env goto migrate
copy .env.example app\backend\.env >nul
venv\Scripts\python -c "import secrets;print('SMD_JWT_SECRET='+secrets.token_hex(32))" >> app\backend\.env
echo SMD_DB_PATH=%~dp0data\smd.db>> app\backend\.env
echo SMD_FRONTEND_DIST=%~dp0app\frontend_dist>> app\backend\.env

:migrate
echo [4/4] Database migration (alembic upgrade head) ...
cd app\backend
..\..\venv\Scripts\python -m alembic upgrade head
if errorlevel 1 goto fail
cd ..\..

echo.
echo Install OK. Start with run_server.bat then open http://THIS-MACHINE-IP:8000
echo Default account admin/admin - CHANGE THE PASSWORD after first login.
exit /b 0

:fail
echo.
echo INSTALL FAILED - see errors above. Docs: README.md
exit /b 1
