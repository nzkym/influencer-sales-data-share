@echo off
chcp 65001 >nul
echo [리뷰 히스토리 분석] 5년 트렌드 분석 시작...
cd /d "%~dp0"
call .venv\Scripts\activate.bat 2>nul || python -m venv .venv && call .venv\Scripts\activate.bat && pip install -r requirements.txt
python main.py --history
pause
