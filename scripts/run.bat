@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  run.bat - ensure the runtime, then start the app in the web browser.
REM    run.bat          normal mode  (quiet console)
REM    run.bat debug    debug mode   (full Streamlit / Python logs)
REM
REM  Called by the launcher in the project root. You can also double-click this
REM  directly. It runs in the FOREGROUND on purpose: this console window is the
REM  "app is running" indicator - closing it quits the app.
REM ===========================================================================

REM Work from the project root (this script is in <root>\scripts\).
cd /d "%~dp0.."

REM Sanity check: confirm the folder was fully extracted. If core files are
REM missing the user almost certainly ran this from inside the .zip, or only
REM partially unzipped it - say so clearly instead of failing deep in setup.
if not exist "desktop_app.py"            goto :notextracted
if not exist "%~dp0ensure_runtime.bat"   goto :notextracted

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

REM Debug mode just raises the log level (desktop_app.py reads UPS_DEBUG).
if /I "%~1"=="debug" set "UPS_DEBUG=1"

echo(
echo  ============================================================
echo    Upwork Proposal Strategist is running.
echo    Your web browser will open in a few seconds.
echo(
echo    If Windows asks to allow "python" through the firewall,
echo    click "Allow access" - the app only uses your own computer.
echo(
echo    ^>^>  KEEP THIS WINDOW OPEN while you use the app.
echo    ^>^>  To quit the app, close this window.
echo  ============================================================
echo(

REM Run the server in the FOREGROUND with the console interpreter. This call
REM blocks until the window is closed (or Ctrl+C), which stops the server -
REM there is no separate process to leave orphaned.
"runtime\python.exe" "desktop_app.py"
exit /b 0

:notextracted
echo(
echo ============================================================
echo   This folder looks incomplete - core files are missing.
echo(
echo   Please right-click the downloaded .zip, choose "Extract All",
echo   and launch the app from the FULLY extracted folder - not from
echo   inside the .zip. If your antivirus removed files, re-download
echo   the folder and allow it.
echo ============================================================
echo(
echo Press any key to close.
pause >nul
exit /b 1
