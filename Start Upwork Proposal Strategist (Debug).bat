@echo off
REM ===========================================================================
REM  Start Upwork Proposal Strategist (Debug).bat
REM
REM  Troubleshooting launcher. Same app as the normal launcher, but it shows the
REM  FULL Streamlit / Python logs in this window - use it when the normal
REM  launcher fails, and share what it prints.
REM ===========================================================================
title Upwork Proposal Strategist (Debug)
call "%~dp0scripts\run.bat" debug
echo(
echo (App closed.) Press any key to exit.
pause >nul
