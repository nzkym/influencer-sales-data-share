@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv" (
    echo 가상환경 생성 중...
    python -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
)

echo 행사 매출증감 확인 실행 중...
.venv\Scripts\python main.py
pause
