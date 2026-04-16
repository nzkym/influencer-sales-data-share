#!/bin/bash
# GCP VM 초기 세팅 스크립트
# 실행: bash setup_cloud.sh

set -e

echo "================================================"
echo "  Patent Name Finder — GCP 서버 세팅"
echo "================================================"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPORTS_DIR="$PROJECT_DIR/reports"

# ── 1. Python 패키지 설치 ─────────────────────────────────
echo "[1/4] Python 패키지 설치 중..."
python3 -m venv "$PROJECT_DIR/.venv"
source "$PROJECT_DIR/.venv/bin/activate"
pip install -q --upgrade pip
pip install -q -r "$PROJECT_DIR/requirements.txt"
echo "  → 완료"

# ── 2. reports 폴더 생성 ──────────────────────────────────
echo "[2/4] reports 폴더 생성..."
mkdir -p "$REPORTS_DIR"
echo "  → $REPORTS_DIR"

# ── 3. 웹서버 서비스 등록 (systemd) ──────────────────────
echo "[3/4] HTTP 서버 서비스 등록 중..."
SERVICE_FILE="/etc/systemd/system/patent-reports.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Patent Reports HTTP Server
After=network.target

[Service]
User=$USER
WorkingDirectory=$REPORTS_DIR
ExecStart=/usr/bin/python3 -m http.server 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable patent-reports
sudo systemctl restart patent-reports
echo "  → HTTP 서버 포트 8080 시작"

# ── 4. crontab 등록 (매주 월요일 오전 9시 KST = UTC 0시) ─
echo "[4/4] 주간 자동 실행 crontab 등록 중..."
CRON_CMD="0 0 * * 4 cd $PROJECT_DIR && source .venv/bin/activate && python3 main.py >> logs/cron.log 2>&1"
mkdir -p "$PROJECT_DIR/logs"

# 기존 항목 제거 후 재등록
(crontab -l 2>/dev/null | grep -v "patent name find"; echo "$CRON_CMD") | crontab -
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
echo "  2. GCP 방화벽 포트 8080 허용:"
echo "     Google Cloud Console → VPC 네트워크 → 방화벽"
echo "     → 규칙 만들기 → tcp:8080 허용"
echo ""
echo "  3. 즉시 테스트 실행:"
echo "     source .venv/bin/activate && python3 main.py --no-telegram"
echo ""
echo "  4. 보고서 접속 확인:"
echo "     http://35.222.61.113:8080/YYYY-MM-DD.html"
echo "================================================"
