#!/bin/bash
# 실행 스크립트 (Google Cloud VM용)
# 사용법: bash run.sh [옵션]  /  nohup bash run.sh >> log 2>&1 & (SSH 종료 후에도 실행)

cd "$(dirname "$0")"
source .venv/bin/activate

mkdir -p 결과값
LOG=결과값/run.log

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 분석 시작..."
python3 main.py "$@" 2>&1 | tee -a "$LOG"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 분석 완료. 로컬 PC 동기화 중..."
bash /home/jdhealthtree/influencer-sales-data-share/sync_reports.sh
