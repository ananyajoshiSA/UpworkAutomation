@echo off
setlocal EnableExtensions
title Uninstall Upwork Proposal Strategist
REM ===========================================================================
REM  Uninstall Upwork Proposal Strategist.bat
REM
REM  Cleanly removes EVERYTHING this app placed on the computer:
REM    1. per-user settings + API key + logs in
REM       %APPDATA%\UpworkProposalStrategist
REM    2. the Windows Firewall allowance for the app's python.exe (if any)
REM    3. the Desktop shortcut (if any)
REM    4. this whole app folder (bundled Python + installed libraries + code)
REM
REM  Nothing else is touched: the app writes NO registry keys and installs
REM  nothing into Program Files or the Start Menu, so there is nothing else to
REM  clean. Double-click this file to run it.
REM ===========================================================================

REM --- Resolve paths (this file sits at the app-folder root) -----------------
set "APPDIR=%~dp0"
if "%APPDIR:~-1%"=="\" set "APPDIR=%APPDIR:~0,-1%"
set "STATE=%APPDATA%\UpworkProposalStrategist"
set "RTPY=%APPDIR%\runtime\python.exe"
set "RTPYW=%APPDIR%\runtime\pythonw.exe"
set "SHORTCUT=%USERPROFILE%\Desktop\Start Upwork Proposal Strategist.lnk"

echo ============================================================
echo   Uninstall Upwork Proposal Strategist
echo ============================================================
echo(
echo This will PERMANENTLY remove:
echo(
echo   1. Your saved settings, API key and logs:
echo        %STATE%
echo   2. The firewall allowance for this app (if any)
echo   3. The Desktop shortcut (if any)
echo   4. This entire app folder:
echo        %APPDIR%
echo(
echo Please CLOSE the app window first if it is still open.
echo(
set /p "CONFIRM=Type  YES  and press Enter to uninstall (anything else cancels): "
if /I not "%CONFIRM%"=="YES" (
    echo(
    echo Cancelled - nothing was removed.
    echo Press any key to close.
    pause >nul
    exit /b 0
)

echo(
echo [uninstall] Stopping the app if it is still running...
powershell -NoProfile -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith('%APPDIR%\runtime\') } | Stop-Process -Force -ErrorAction SilentlyContinue" >nul 2>&1

echo [uninstall] Removing saved settings, API key and logs...
if exist "%STATE%" (
    rmdir /s /q "%STATE%"
    echo   removed: %STATE%
) else (
    echo   (none found)
)

echo [uninstall] Removing Desktop shortcut (if any)...
if exist "%SHORTCUT%" (
    del /f /q "%SHORTCUT%" >nul 2>&1
    echo   removed Desktop shortcut
) else (
    echo   (none found)
)

echo [uninstall] Removing firewall allowance (if any)...
netsh advfirewall firewall delete rule name=all program="%RTPY%"  >nul 2>&1
netsh advfirewall firewall delete rule name=all program="%RTPYW%" >nul 2>&1
echo   done (any leftover rule is harmless once the app folder is gone)

echo(
echo ============================================================
echo   Uninstall complete. This window will close and the app
echo   folder will be deleted in a couple of seconds.
echo ============================================================

REM --- Self-delete the app folder --------------------------------------------
REM A tiny temp helper waits for THIS script to exit (so the folder is no longer
REM in use), then removes the whole app folder and finally itself.
set "DELBAT=%TEMP%\ups_uninstall_%RANDOM%.bat"
> "%DELBAT%" echo @echo off
>>"%DELBAT%" echo cd /d "%TEMP%"
>>"%DELBAT%" echo ping 127.0.0.1 -n 3 ^>nul
>>"%DELBAT%" echo rd /s /q "%APPDIR%"
>>"%DELBAT%" echo del /f /q "%%~f0"
start "" /min cmd /c "%DELBAT%"
exit /b 0
