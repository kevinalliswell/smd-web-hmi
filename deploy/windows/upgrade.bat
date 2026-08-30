@echo off
REM ============================================================
REM smd-web-hmi offline upgrader. Run from the NEW bundle root:
REM     upgrade.bat C:\path\to\existing\install
REM Steps: backup DB - keep .env - replace app\ - update deps
REM (offline) - alembic upgrade head. Rollback: see README.md.
REM ASCII only on purpose (zh-CN cmd garbles UTF-8 batch files).
REM NOT yet verified on real hardware - rehearse before D4.
REM ============================================================
setlocal
if "%~1"=="" (
  echo Usage: upgrade.bat ^<existing install dir^>
  exit /b 1
)
set "OLD=%~1"
cd /d "%~dp0"
if not exist "%OLD%\app\backend\requirements.txt" (
  echo ERROR: %OLD% does not look like an smd-web-hmi install dir.
  exit /b 1
)

echo [1/5] STOP the running server first (close run_server window / stop the service),
echo       then press any key to continue.
pause >nul

echo [2/5] Backing up database ...
if not exist "%OLD%\backups" mkdir "%OLD%\backups"
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%t"
if exist "%OLD%\data\smd.db" copy "%OLD%\data\smd.db" "%OLD%\backups\smd-%STAMP%.db" >nul

echo [3/5] Replacing application (keeping .env) ...
copy "%OLD%\app\backend\.env" "%TEMP%\smd-env.bak" >nul
rmdir /s /q "%OLD%\app"
xcopy /e /i /q app "%OLD%\app" >nul
copy "%TEMP%\smd-env.bak" "%OLD%\app\backend\.env" >nul

echo [4/5] Updating dependencies (offline, from wheels\) ...
"%OLD%\venv\Scripts\python" -m pip install --no-index --find-links=wheels -r "%OLD%\app\backend\requirements.txt"
if errorlevel 1 goto fail

echo [5/5] Database migration (alembic upgrade head) ...
pushd "%OLD%\app\backend"
"%OLD%\venv\Scripts\python" -m alembic upgrade head
if errorlevel 1 ( popd & goto fail )
popd

echo.
echo Upgrade OK. Pre-upgrade DB backup: %OLD%\backups\smd-%STAMP%.db
echo Start with: %OLD%\run_server.bat
exit /b 0

:fail
echo.
echo UPGRADE FAILED - see errors above.
echo Rollback: restore backups\smd-%STAMP%.db and the previous bundle's app\ dir.
echo Docs: README.md
exit /b 1
