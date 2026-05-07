#!/bin/bash
# 각 프로그램 최신 리포트를 git repo에 복사 후 GitHub push
# 호출: bash ~/influencer-sales-data-share/sync_reports.sh

REPO="/home/jdhealthtree/influencer-sales-data-share"

# ── naver_health_trend (standalone) ──────────────────────────
NHT_PDF=$(ls -t ~/naver-health-trend/결과값/*.pdf 2>/dev/null | head -1)
if [ -n "$NHT_PDF" ]; then
    mkdir -p "$REPO/naver_health_trend/결과값"
    cp "$NHT_PDF" "$REPO/naver_health_trend/결과값/latest.pdf"
    echo "[sync] naver_health_trend PDF 복사 완료"
fi

# ── patent-name-find (standalone) ────────────────────────────
PNF_HTML=$(ls -t ~/patent-name-find/reports/*.html 2>/dev/null | head -1)
if [ -n "$PNF_HTML" ]; then
    mkdir -p "$REPO/patent name find/results"
    cp "$PNF_HTML" "$REPO/patent name find/results/latest.html"
    echo "[sync] patent-name-find HTML 복사 완료"
fi

# ── naver_trend all health (이미 repo 안에 있음) ─────────────
NTAR="$REPO/naver_trend about all health_건강식품외 건강전체"
NTAR_PDF=$(ls -t "$NTAR/결과값"/*.pdf 2>/dev/null | head -1)
if [ -n "$NTAR_PDF" ]; then
    cp "$NTAR_PDF" "$NTAR/결과값/latest.pdf"
    echo "[sync] naver_trend all health PDF 복사 완료"
fi

# ── GitHub push ───────────────────────────────────────────────
cd "$REPO"
git add -f \
    "naver_health_trend/결과값/latest.pdf" \
    "patent name find/results/latest.html" \
    "naver_trend about all health_건강식품외 건강전체/결과값/latest.pdf" \
    2>/dev/null

git -c user.email="report@auto" -c user.name="auto-report" \
    commit -m "리포트 자동저장 $(date +%Y-%m-%d)" 2>/dev/null \
    && git push 2>/dev/null \
    && echo "[sync] GitHub push 완료 — 로컬 PC에서 git pull 시 동기화됩니다" \
    || echo "[sync] 변경사항 없거나 push 실패 (자격증명 확인 필요)"
