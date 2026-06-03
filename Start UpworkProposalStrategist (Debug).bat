@echo off
REM ===========================================================================
REM  Start UpworkProposalStrategist (Debug).bat
REM
REM  Troubleshooting launcher. Same app as the normal (.vbs) launcher, but it
REM  KEEPS a console window showing the full Streamlit / Python logs - use this
REM  when the normal launcher shows a blank window or fails, and share the log.
REM ===========================================================================
title Upwork Proposal Strategist (Debug)
call "%~dp0scripts\run.bat" debug
echo(
echo (App window closed.) Press any key to exit.
pause >nul
