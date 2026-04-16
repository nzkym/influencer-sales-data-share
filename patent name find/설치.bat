@echo off
cd /d "%~dp0"

py -m venv .venv
.venv\Scripts\pip install --upgrade pip -q
.venv\Scripts\pip install -r requirements.txt

if not exist .env (
    copy .env.example .env
)

echo Done.
pause
