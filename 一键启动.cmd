@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
py -3 scripts\launch.py
if errorlevel 1 python scripts\launch.py
echo.
pause
