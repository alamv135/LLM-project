@echo off
rem Double-click to start the MedScout console. First start installs what is needed.
setlocal
cd /d "%~dp0"
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto run
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if defined PY goto run
echo.
echo Python is not installed. Download Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" while installing. Then double-click this file again.
goto end
:run
set "PYTHONUTF8=1"
%PY% launch.py %*
:end
echo.
pause
