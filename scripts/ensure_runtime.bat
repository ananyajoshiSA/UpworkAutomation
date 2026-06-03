@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  ensure_runtime.bat - first-run setup for the browser desktop app.
REM
REM  Python is BUNDLED inside this folder (runtime\python.exe), staged by
REM  scripts\prepare_bundle.sh before the folder is zipped. So this step does
REM  NOT download an interpreter - it only:
REM    1. enables site-packages in the embeddable interpreter (._pth),
REM    2. installs pip (from the bundled get-pip.py) if needed,
REM    3. pip-installs the app's libraries from PyPI - the ONE thing that needs
REM       internet, and only on the very first run.
REM
REM  Deliberately NO PowerShell and NO "download-an-exe-from-the-web" step: that
REM  download-then-execute pattern is what antivirus/SmartScreen flags. pip
REM  fetching wheels from PyPI is ordinary and is not flagged the same way.
REM
REM  Idempotent + self-healing: the success marker (runtime\.deps_installed) is
REM  written ONLY after every step succeeds, so an interrupted/failed first run
REM  leaves NO marker and simply resumes on the next launch. A completed setup
REM  is an instant no-op (fast path below).
REM
REM  NOTE: Windows-only and authored on macOS - verify on a clean Windows box
REM  (see the checklist in DEVELOPER.md).
REM ===========================================================================

REM --- Resolve project root (this script lives in <root>\scripts\) -----------
pushd "%~dp0.."
set "ROOT=%CD%"
set "RT=%ROOT%\runtime"
set "MARKER=%RT%\.deps_installed"
set "PYEXE=%RT%\python.exe"
set "REQ=%ROOT%\requirements.txt"

REM Embeddable build is pinned to 3.11 - keep PTHFILE (python<major><minor>._pth)
REM in step with the Python staged by prepare_bundle.sh.
set "PTHFILE=%RT%\python311._pth"
set "GETPIP=%RT%\get-pip.py"

REM --- Fast path: already fully set up ---------------------------------------
if exist "%MARKER%" (
    popd
    exit /b 0
)

REM --- The bundled runtime must be present -----------------------------------
REM Python is shipped inside runtime\. If it is missing, the folder was not
REM fully extracted, the bundle was never staged, or antivirus removed files.
if not exist "%PYEXE%" goto :noruntime

echo(
echo ============================================================
echo   Upwork Proposal Strategist - one-time setup
echo   This runs ONCE and may take a few minutes. Please wait...
echo   (It downloads the app's libraries - internet is needed
echo    this first time only.)
echo ============================================================
echo(

REM --- 1. Enable site-packages in the embeddable interpreter -----------------
REM Embeddable Python ships with 'import site' disabled and no site-packages on
REM the path, so pip-installed packages won't import until we re-enable them.
REM Rewrite the ._pth deterministically (safe to repeat).
> "%PTHFILE%" echo python311.zip
>>"%PTHFILE%" echo .
>>"%PTHFILE%" echo Lib\site-packages
>>"%PTHFILE%" echo import site

REM --- 2. pip (from the bundled get-pip.py; no web download) -----------------
"%PYEXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    if not exist "%GETPIP%" goto :noruntime
    echo [setup] Preparing the installer...
    "%PYEXE%" "%GETPIP%" --no-warn-script-location
    if errorlevel 1 goto :fail
)

REM --- 3. Dependencies from PyPI in ONE pass ---------------------------------
REM requirements.txt is the single pinned set the app needs.
echo [setup] Installing app libraries (this is the slow part)...
"%PYEXE%" -m pip install --no-warn-script-location --disable-pip-version-check -r "%REQ%"
if errorlevel 1 goto :fail

REM --- 4. Success marker (written LAST so a failed run self-heals) -----------
> "%MARKER%" echo installed %DATE% %TIME%
echo(
echo [setup] Setup complete.
popd
exit /b 0

:noruntime
echo(
echo [setup] ********************************************************
echo [setup]  THE APP'S RUNTIME FILES ARE MISSING (runtime\python.exe).
echo(
echo [setup]  - If you opened this as a ZIP: it was not fully unzipped,
echo [setup]    or antivirus removed files. Re-extract the WHOLE folder
echo [setup]    ("Extract All"), allow it in antivirus, then launch again.
echo(
echo [setup]  - If you downloaded the SOURCE from GitHub: that download has
echo [setup]    no bundled Python. Double-click  scripts\prepare_bundle.bat
echo [setup]    once to fetch it, then launch again.
echo [setup] ********************************************************
popd
exit /b 1

:fail
echo(
echo [setup] ********************************************************
echo [setup]  SETUP DID NOT COMPLETE.
echo [setup]  Check your internet connection, then launch the app
echo [setup]  again - setup resumes safely from where it stopped.
echo [setup] ********************************************************
popd
exit /b 1
