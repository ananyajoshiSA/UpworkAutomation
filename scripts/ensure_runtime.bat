@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  ensure_runtime.bat - first-run setup for the zero-touch desktop app.
REM
REM  Installs a PRIVATE, embeddable Python and the app's dependencies into
REM  .\runtime\ exactly ONCE. The user needs nothing pre-installed.
REM
REM  Idempotent + self-healing:
REM    * The success marker (.\runtime\.deps_installed) is written ONLY after
REM      every step succeeds. An interrupted/failed setup leaves NO marker, so
REM      the next launch simply resumes. Each step also checks "already done".
REM    * Re-running when fully set up is an instant no-op (fast-path below).
REM
REM  Needs internet on first run (to fetch Python + dependency wheels).
REM  Offline install: see DEVELOPER.md (pre-place runtime\python-embed.zip and a
REM  wheelhouse).
REM
REM  NOTE: this is Windows-only and was authored without a Windows host to run
REM  it on. It follows the well-known embeddable-Python + get-pip recipe; verify
REM  on a clean Windows box (see the test checklist in DEVELOPER.md).
REM ===========================================================================

REM --- Resolve project root (this script lives in <root>\scripts\) -----------
pushd "%~dp0.."
set "ROOT=%CD%"
set "RT=%ROOT%\runtime"
set "MARKER=%RT%\.deps_installed"
set "PYEXE=%RT%\python.exe"
set "REQ=%ROOT%\requirements-windows.txt"

REM Pinned interpreter (embeddable build). Keep PYVER and PTHFILE
REM (python<major><minor>._pth) in step with each other.
set "PYVER=3.11.9"
set "PYZIP=%RT%\python-embed.zip"
set "PTHFILE=%RT%\python311._pth"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
set "GETPIP=%RT%\get-pip.py"
set "GETPIPURL=https://bootstrap.pypa.io/get-pip.py"

REM --- Fast path: already fully set up ---------------------------------------
if exist "%MARKER%" (
    popd
    exit /b 0
)

echo(
echo ============================================================
echo   Upwork Proposal Strategist - one-time setup
echo   This runs ONCE and may take a few minutes. Please wait...
echo ============================================================
echo(

if not exist "%RT%" mkdir "%RT%"

REM --- 1. Embeddable Python --------------------------------------------------
if not exist "%PYEXE%" (
    if not exist "%PYZIP%" (
        echo [setup] Downloading Python runtime...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYZIP%'; exit 0 } catch { Write-Host $_; exit 1 }"
        if errorlevel 1 goto :fail
    )
    echo [setup] Extracting Python runtime...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -LiteralPath '%PYZIP%' -DestinationPath '%RT%' -Force; exit 0 } catch { Write-Host $_; exit 1 }"
    if errorlevel 1 goto :fail
)

REM --- 2. Enable site-packages in the embeddable interpreter -----------------
REM Embeddable Python ships with 'import site' disabled and no site-packages on
REM the path, so pip-installed packages won't import until we re-enable them.
REM Rewrite the ._pth deterministically (safe to repeat).
> "%PTHFILE%" echo python311.zip
>>"%PTHFILE%" echo .
>>"%PTHFILE%" echo Lib\site-packages
>>"%PTHFILE%" echo import site

REM --- 3. pip ----------------------------------------------------------------
"%PYEXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [setup] Installing pip...
    if not exist "%GETPIP%" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GETPIPURL%' -OutFile '%GETPIP%'; exit 0 } catch { Write-Host $_; exit 1 }"
        if errorlevel 1 goto :fail
    )
    "%PYEXE%" "%GETPIP%" --no-warn-script-location
    if errorlevel 1 goto :fail
)

REM --- 4. Dependencies (base + desktop extras) in ONE pass -------------------
REM requirements-windows.txt begins with `-r requirements.txt`, so this single
REM command installs the full set the desktop window needs.
echo [setup] Installing app dependencies (this is the slow part)...
"%PYEXE%" -m pip install --no-warn-script-location --disable-pip-version-check -r "%REQ%"
if errorlevel 1 goto :fail

REM --- 5. Success marker (written LAST so a failed run self-heals) -----------
> "%MARKER%" echo installed %DATE% %TIME% python %PYVER%
echo(
echo [setup] Setup complete.
popd
exit /b 0

:fail
echo(
echo [setup] ********************************************************
echo [setup]  SETUP DID NOT COMPLETE.
echo [setup]  Check your internet connection, then launch the app
echo [setup]  again - setup resumes safely from where it stopped.
echo [setup] ********************************************************
popd
exit /b 1
