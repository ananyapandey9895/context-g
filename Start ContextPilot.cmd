@echo off
title ContextPilot
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up ContextPilot. This takes a minute...
  where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
  if not exist ".venv\Scripts\python.exe" (
    echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ and run this again.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -e ".[all]"
)
".venv\Scripts\python.exe" -m contextpilot ui %*
pause
