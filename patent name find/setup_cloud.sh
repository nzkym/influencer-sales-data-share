#!/bin/bash
# GCP VM 초기 세팅 스크립트
# 실행: bash setup_cloud.sh

set -e

echo "================================================"
echo "  Patent Name Finder — GCP 서버 세팅"
echo "================================================"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 1. Python 패키지 설치 ─────────────────────────────────
echo "[1/3] Python 패키지 설치 중..."
python3 -m venv "$PROJECT_DIR/.venv"
source "$PROJECT_DIR/.venv/bin/activate"
pip install -q --upgrade pip
pip install -q -r "$PROJECT_DIR/requirements.txt"
echo "  → 완료"

# ── 2. 폴더 생성 ──────────────────────────────────────────
echo "[2/3] 폴더 생성..."
mkdir -p "$PROJECT_DIR/reports"
mkdir -p "$PROJECT_DIR/logs"
echo "  → 완료"

# ── 3. crontab 등록 (매주 목요일 09:00 KST = 목요일 00:00 UTC) ─
echo "[3/3] crontab 등록 중..."
CRON_CMD="0 0 * * 4 cd $PROJECT_DIR && source .venv/bin/activate && python3 main.py >> logs/cron.log 2>&1"

(crontab -l 2>/dev/null | grep -v "patent-name-find\|patent name find"; echo ""; echo "# 상표 선점 후보 분석 (목요일 09:00 KST)"; echo "$CRON_CMD") | crontab -
echo "  → 매주 목요일 09:00 KST 자동 실행 등록"

# ── 완료 ──────────────────────────────────────────────────
echo ""
echo "================================================"
echo "  세팅 완료!"
echo ""
echo "  다음 단계:"
echo "  1. .env 파일 생성:"
echo "     cp .env.example .env && nano .env"
echo ""
echo "  2. 테스트 실행:"
echo "     source .venv/bin/activate && python3 main.py --no-telegram"
echo "================================================"
