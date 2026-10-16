@echo off
rem Runs the weekly lead job. Task Scheduler calls this file. Works from any folder.
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
if exist "venv\Scripts\python.exe" (set "PY=venv\Scripts\python.exe") else (set "PY=python")
if not exist logs mkdir logs
"%PY%" run_weekly.py %* >> logs\task_scheduler.out 2>&1
exit /b %ERRORLEVEL%
