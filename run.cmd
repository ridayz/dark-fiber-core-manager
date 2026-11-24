@echo off
rem Jalankan Dark Fiber Core Manager di Windows (tanpa bash, tanpa python3 alias).
rem Pakai: run.cmd            -> http://localhost:4173
rem        set PORT=4174 && run.cmd
setlocal
cd /d "%~dp0"

if "%PORT%"=="" set "PORT=4173"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY=%PYTHON%"
if not defined PY if exist "D:\tools\python312\python.exe" set "PY=D:\tools\python312\python.exe"
if not defined PY set "PY=python"

echo Dark Fiber Core Manager -^> http://localhost:%PORT%
"%PY%" "src\server\app.py"
