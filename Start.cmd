@echo off
title Opal — T24 project reader
cd /d "%~dp0"
echo Starting Opal...  (first run installs dependencies, please wait)
py run.py
if errorlevel 1 (
  echo.
  echo Opal exited with an error. Press any key to close.
  pause >nul
)
