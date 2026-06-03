@echo off
REM ===========================================================================
REM  Start Upwork Proposal Strategist.bat
REM
REM  THE file an end user double-clicks. It opens the app in your web browser.
REM  A small console window stays open while the app runs - that window IS the
REM  "app is running" indicator. Close it to quit the app.
REM
REM  It just hands off to scripts\run.bat (you can double-click that directly
REM  too, if you ever need to).
REM ===========================================================================
title Upwork Proposal Strategist
call "%~dp0scripts\run.bat"
echo(
echo (App closed.) Press any key to exit.
pause >nul
