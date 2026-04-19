@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
python main.py %*
echo.
pause
