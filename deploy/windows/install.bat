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
fltmc >nul 2>nul
if errorlevel 1 (
  echo ERROR: Run install.bat as Administrator.
  exit /b 1
)

echo [1/4] Creating venv ...
if exist venv\Scripts\python.exe goto deps
py -3.11 -m venv venv 2>nul
if exist venv\Scripts\python.exe goto deps
python -m venv venv
if not exist venv\Scripts\python.exe goto fail

:deps
echo [2/4] Installing dependencies (offline, from wheels\) ...
venv\Scripts\python -m pip install --require-hashes --no-index --find-links=wheels -r app\backend\requirements.lock
if errorlevel 1 goto fail

echo [3/4] Writing config ...
if not exist data mkdir data
if not exist data\backups mkdir data\backups
icacls data /inheritance:r /grant:r "%USERNAME%:(OI)(CI)F" "SYSTEM:(OI)(CI)F" >nul 2>nul
if errorlevel 1 goto fail
if exist app\backend\.env goto migrate
copy .env.example app\backend\.env >nul
venv\Scripts\python -c "import secrets;print('SMD_JWT_SECRET='+secrets.token_hex(32))" >> app\backend\.env
for /f %%p in ('venv\Scripts\python -c "import secrets;print(secrets.token_hex(12))"') do set "ADMIN_PASSWORD=%%p"
echo SMD_BOOTSTRAP_ADMIN_PASSWORD_FILE=%~dp0initial-admin-password.txt>> app\backend\.env
echo %ADMIN_PASSWORD%> initial-admin-password.txt
icacls initial-admin-password.txt /inheritance:r /grant:r "%USERNAME%:F" "SYSTEM:F" >nul 2>nul
if errorlevel 1 goto fail
echo SMD_DB_PATH=%~dp0data\smd.db>> app\backend\.env
echo SMD_FRONTEND_DIST=%~dp0app\frontend_dist>> app\backend\.env

:migrate
icacls app\backend\.env /inheritance:r /grant:r "%USERNAME%:F" "SYSTEM:F" >nul 2>nul
if errorlevel 1 goto fail
echo [4/4] Database migration (alembic upgrade head) ...
cd app\backend
..\..\venv\Scripts\python -m alembic upgrade head
if errorlevel 1 goto fail
cd ..\..

echo Registering production auto-start task ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0register_autostart.ps1" -InstallDir "%~dp0"
if errorlevel 1 goto fail

echo.
echo Install OK. The scheduled service is starting; open http://THIS-MACHINE-IP:8000
echo One-time admin credentials: %~dp0initial-admin-password.txt
echo Change the password after first login, then securely delete that file.
exit /b 0

:fail
echo.
echo INSTALL FAILED - see errors above. Docs: README.md
exit /b 1
