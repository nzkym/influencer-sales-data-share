#!/bin/bash
# Google Cloud VM 최초 세팅 스크립트
# 사용법: bash setup_cloud.sh

set -e

echo "=================================================="
echo "  건강관련 전체 트렌드 분석기 — 클라우드 세팅"
echo "=================================================="
echo ""

# 1. 시스템 패키지 업데이트 + 한글 폰트
echo "[1/6] 시스템 패키지 및 한글 폰트 설치..."
sudo apt-get update -q
sudo apt-get install -y fonts-nanum python3-venv python3-pip > /dev/null 2>&1
echo "  완료"

# 2. Python 가상환경
echo "[2/6] Python 가상환경 생성..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
echo "  완료"

# 3. Python 패키지
echo "[3/6] Python 패키지 설치 (시간이 걸릴 수 있습니다)..."
pip install -r requirements.txt -q
echo "  완료"

# 4. Playwright Chromium
echo "[4/6] Playwright Chromium 브라우저 설치..."
playwright install chromium
playwright install-deps chromium
echo "  완료"

# 5. 결과 폴더 생성
echo "[5/6] 결과 폴더 생성..."
mkdir -p 결과값
echo "  완료"

# 6. .env 파일 설정
echo "[6/6] .env 파일 설정..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
    else
        touch .env
    fi
    echo ""
    echo "  ⚠️  .env 파일이 생성되었습니다. 아래 명령어로 API 키를 입력해주세요:"
    echo ""
    echo "  nano .env"
    echo ""
    echo "  입력해야 할 항목:"
    echo "    NAVER_CLIENT_ID       — 네이버 API"
    echo "    NAVER_CLIENT_SECRET   — 네이버 API"
    echo "    ANTHROPIC_API_KEY     — Claude AI 브리핑"
    echo "    TELEGRAM_BOT_TOKEN    — 텔레그램 봇"
    echo "    TELEGRAM_CHAT_ID      — 텔레그램 채팅 ID"
    echo "    NAVER_AD_API_KEY      — 검색량 조회 (선택)"
    echo ""
else
    echo "  .env 파일 이미 존재 — 건너뜀"
fi

# 7. crontab 설정 (매주 목요일 리포트)
echo ""
echo "=================================================="
echo "  crontab 자동 등록 (주 1회 목요일 리포트)"
echo "=================================================="

# 목요일 10:00 UTC = 목요일 19:00 KST
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CRON_MAIN="0 10 * * 4 cd ${SCRIPT_DIR} && source .venv/bin/activate && bash run.sh >> 결과값/cron.log 2>&1"

(crontab -l 2>/dev/null | grep -v "${SCRIPT_DIR}"; echo ""; echo "# 건강관련 전체 트렌드 분석 (목요일 19:00 KST)"; echo "$CRON_MAIN") | crontab -
echo "  crontab 등록 완료 (매주 목요일 19:00 KST)"

echo ""
echo "=================================================="
echo "  세팅 완료!"
echo "=================================================="
echo ""
echo "다음 단계:"
echo "  1. nano .env  →  API 키 입력 후 저장 (Ctrl+X → Y → Enter)"
echo "  2. 테스트 실행:"
echo "     source .venv/bin/activate"
echo "     bash run.sh"
echo ""
echo "매주 목요일 저녁 텔레그램으로 리포트가 자동 발송됩니다."
echo ""
