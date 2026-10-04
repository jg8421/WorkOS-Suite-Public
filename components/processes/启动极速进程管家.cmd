@echo off
setlocal
set "HERE=%~dp0"
set "PYW="
for /f "delims=" %%I in ('where pythonw 2^>nul') do if not defined PYW set "PYW="%%I""
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PYW="%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe""
if not defined PYW if exist "C:\Python313\pythonw.exe" set "PYW="C:\Python313\pythonw.exe""
if not defined PYW (
  echo [ERROR] pythonw.exe not found. Please install Python 3 first.
  pause
  exit /b 1
)
start "" %PYW% "%HERE%process_manager.py"
