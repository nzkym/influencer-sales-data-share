@echo off
chcp 65001 >nul
echo [월간 리뷰 리포트] 이번 달 리뷰 분석 시작...
cd /d "%~dp0"
call .venv\Scripts\activate.bat 2>nul
python main.py --monthly
pause
