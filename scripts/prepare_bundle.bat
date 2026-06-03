@echo off
setlocal EnableExtensions
REM ===========================================================================
REM  prepare_bundle.bat - (re)create the bundled Python in runtime\ on Windows.
REM
REM  You normally DON'T need this: runtime\ (the embeddable Python + get-pip.py)
REM  is committed to the repo, so a GitHub clone / Download ZIP and the packaged
REM  UpworkProposalStrategist.zip both already contain it.
REM
REM  Use this only to rebuild a deleted/corrupted runtime (e.g. antivirus removed
REM  python.exe), or to bump the Python version. It downloads an embeddable
REM  Python + get-pip.py into runtime\.
REM
REM  Needs internet. Windows equivalent of scripts/prepare_bundle.sh.
REM  Usage: double-click this file, or run it from a terminal.
REM ===========================================================================

pushd "%~dp0.."
set "ROOT=%CD%"
set "RT=%ROOT%\runtime"
set "PYVER=3.11.9"
set "PYZIP=%RT%\python-embed.zip"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
set "GETPIP=%RT%\get-pip.py"
set "GETPIPURL=https://bootstrap.pypa.io/get-pip.py"

if not exist "%RT%" mkdir "%RT%"

REM --- 1. Embeddable Python -> runtime\ --------------------------------------
if exist "%RT%\python.exe" (
    echo [prepare] runtime\python.exe already present - skipping Python download.
) else (
    echo [prepare] Downloading embeddable Python %PYVER% ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYZIP%'; exit 0 } catch { Write-Host $_; exit 1 }"
    if errorlevel 1 goto :fail
    echo [prepare] Extracting into runtime\ ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Expand-Archive -LiteralPath '%PYZIP%' -DestinationPath '%RT%' -Force; exit 0 } catch { Write-Host $_; exit 1 }"
    if errorlevel 1 goto :fail
    del "%PYZIP%" >nul 2>&1
)

REM --- 2. get-pip.py -> runtime\ ---------------------------------------------
if exist "%GETPIP%" (
    echo [prepare] runtime\get-pip.py already present - skipping.
) else (
    echo [prepare] Downloading get-pip.py ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GETPIPURL%' -OutFile '%GETPIP%'; exit 0 } catch { Write-Host $_; exit 1 }"
    if errorlevel 1 goto :fail
)

echo(
echo [prepare] Done. runtime\ is staged.
echo [prepare] Now double-click "Start Upwork Proposal Strategist".
popd
echo(
echo Press any key to close.
pause >nul
exit /b 0

:fail
echo(
echo [prepare] Download failed. Check your internet connection and try again.
popd
echo(
echo Press any key to close.
pause >nul
exit /b 1
