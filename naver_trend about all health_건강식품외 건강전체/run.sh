#!/bin/bash
# 실행 스크립트 (Google Cloud VM용)
# 사용법: bash run.sh [옵션]
#   bash run.sh              — 기본 실행 (카테고리당 상위 30개 키워드)
#   bash run.sh --top 20     — 카테고리당 상위 20개만
#   bash run.sh --no-scrape  — 캐시 키워드 사용
#
# SSH 닫아도 계속 실행되며, 완료 시 텔레그램으로 결과 전송

cd "$(dirname "$0")"
source .venv/bin/activate

mkdir -p 결과값
LOG=결과값/run.log

echo "분석 시작 (백그라운드 실행 중)..."
echo "로그 확인: tail -f $LOG"
echo ""

nohup python3 main.py "$@" > "$LOG" 2>&1 &
echo "PID $! 로 실행 중 — SSH 닫아도 계속 돌아갑니다."
