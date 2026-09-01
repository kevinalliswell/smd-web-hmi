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
set "BACKUP_PATH="
set "ROLLBACK_APP="
if "%~1"=="" (
  echo Usage: upgrade.bat ^<existing install dir^>
  exit /b 1
)
set "OLD=%~1"
cd /d "%~dp0"
fltmc >nul 2>nul
if errorlevel 1 (
  echo ERROR: Run upgrade.bat as Administrator.
  exit /b 1
)
if not exist "%OLD%\app\backend\requirements.txt" (
  echo ERROR: %OLD% does not look like an smd-web-hmi install dir.
  exit /b 1
)

echo [1/6] Stopping the scheduled service. Close any run_server window,
echo       then press any key to continue.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0unregister_autostart.ps1"
if errorlevel 1 goto fail
pause >nul

echo [2/6] Backing up database ...
if not exist "%OLD%\data\backups" mkdir "%OLD%\data\backups"
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%t"
if exist "%OLD%\data\smd.db" (
  set "BACKUP_PATH=%OLD%\data\backups\smd-upgrade-%STAMP%.db"
  "%OLD%\venv\Scripts\python" "%~dp0backup_database.py" "%OLD%\data\smd.db" "%OLD%\data\backups\smd-upgrade-%STAMP%.db"
  if errorlevel 1 goto fail
)

echo [3/6] Staging rollback copy and replacing application ...
if not exist "%OLD%\rollback" mkdir "%OLD%\rollback"
set "ROLLBACK_APP=%OLD%\rollback\app-%STAMP%"
move "%OLD%\app\backend\.env" "%OLD%\.env-upgrade-%STAMP%" >nul
if errorlevel 1 goto fail
move "%OLD%\app" "%ROLLBACK_APP%" >nul
if errorlevel 1 goto fail
xcopy /e /i /q app "%OLD%\app" >nul
if errorlevel 1 goto fail
move "%OLD%\.env-upgrade-%STAMP%" "%OLD%\app\backend\.env" >nul
if errorlevel 1 goto fail
for %%f in (run_server.bat upgrade.bat start_server.ps1 register_autostart.ps1 unregister_autostart.ps1 health_check.ps1 backup_database.py verify_bundle.py .env.example CHANGELOG.md README.md) do (
  copy /y "%%f" "%OLD%\%%f" >nul
  if errorlevel 1 goto fail
)

echo [4/6] Updating dependencies (offline, from wheels\) ...
"%OLD%\venv\Scripts\python" -m pip install --require-hashes --no-index --find-links=wheels -r "%OLD%\app\backend\requirements.lock"
if errorlevel 1 goto fail

echo [5/6] Database migration (alembic upgrade head) ...
pushd "%OLD%\app\backend"
"%OLD%\venv\Scripts\python" -m alembic upgrade head
if errorlevel 1 ( popd & goto fail )
popd

echo [6/6] Registering and starting the scheduled service ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%OLD%\register_autostart.ps1" -InstallDir "%OLD%"
if errorlevel 1 goto fail

echo.
echo Upgrade OK. Pre-upgrade DB backup: %BACKUP_PATH%
echo Previous application retained at: %ROLLBACK_APP%
exit /b 0

:fail
echo.
echo UPGRADE FAILED - see errors above.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0unregister_autostart.ps1" >nul 2>nul
if defined BACKUP_PATH if exist "%BACKUP_PATH%" copy /y "%BACKUP_PATH%" "%OLD%\data\smd.db" >nul
if defined ROLLBACK_APP if exist "%ROLLBACK_APP%" (
  if exist "%OLD%\app\backend\.env" move "%OLD%\app\backend\.env" "%OLD%\.env-upgrade-%STAMP%" >nul
  if exist "%OLD%\app" rmdir /s /q "%OLD%\app"
  move "%ROLLBACK_APP%" "%OLD%\app" >nul
  if exist "%OLD%\.env-upgrade-%STAMP%" move "%OLD%\.env-upgrade-%STAMP%" "%OLD%\app\backend\.env" >nul
)
if exist "%OLD%\.env-upgrade-%STAMP%" if exist "%OLD%\app\backend" move "%OLD%\.env-upgrade-%STAMP%" "%OLD%\app\backend\.env" >nul
if exist "%OLD%\register_autostart.ps1" powershell -NoProfile -ExecutionPolicy Bypass -File "%OLD%\register_autostart.ps1" -InstallDir "%OLD%"
echo The pre-upgrade database and application were restored when available.
echo Docs: README.md
exit /b 1
