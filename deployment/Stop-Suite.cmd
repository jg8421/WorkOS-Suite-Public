@echo off
cd /d "%~dp0"
if not exist "%~dp0runtime\python\python.exe" (
  echo Portable runtime missing. Please extract the complete ZIP first.
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" -I -B "%~dp0app\tools\deployment_preflight.py" --stop
if errorlevel 1 pause
