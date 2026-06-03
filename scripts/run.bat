@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  run.bat - ensure the runtime, then launch the desktop window.
REM    run.bat          normal mode  (no console; uses pythonw.exe)
REM    run.bat debug    debug mode   (console + logs; uses python.exe)
REM
REM  Called by the launchers in the project root. You can also double-click this
REM  directly if the .vbs launcher is blocked by policy/antivirus.
REM ===========================================================================

REM Work from the project root (this script is in <root>\scripts\).
cd /d "%~dp0.."

REM Tell the app this is the packaged desktop run, so it stores the API-key
REM .env and logs under %APPDATA% (never inside this folder). The Python child
REM inherits this.
set "UPS_PACKAGED=1"

REM First-run setup (no-op once installed). Bail out cleanly on failure.
call "%~dp0ensure_runtime.bat"
if errorlevel 1 (
    echo(
    echo Setup did not complete. Press any key to close.
    pause >nul
    exit /b 1
)

if /I "%~1"=="debug" (
    REM Debug: console + full logs, and ask the launcher to keep the Streamlit
    REM server's console visible too. Runs in THIS window (blocks until close).
    set "UPS_DEBUG=1"
    echo [run] Debug mode - launching with console logs...
    "runtime\python.exe" "desktop_app.py"
) else (
    REM Normal: launch detached with the console-less interpreter, then return
    REM so the (possibly hidden) launching window can close.
    start "" "runtime\pythonw.exe" "desktop_app.py"
)
exit /b 0
