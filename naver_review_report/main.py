"""
네이버 리뷰 분석 리포트 — 메인 실행 파일

실행 방법:
  python main.py --history    → 5년 히스토리 분석 (최초 1회 실행)
  python main.py --monthly    → 월간 리포트 (매달 1일 cron 실행)
  python main.py              → 월간 리포트 (인수 없을 때 기본값)

서버 cron 등록 예시 (매달 1일 오전 9시):
  0 9 1 * * cd ~/influencer-sales-data-share/naver_review_report && ...
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import naver_review_api
import analyzer
import sheets_reader
import reporter

# 스토어명 → API 키 매핑 (.env에서 읽기)
STORE_CREDENTIALS = {
    "nutone":   (os.getenv("NUTONE_CLIENT_ID"),   os.getenv("NUTONE_CLIENT_SECRET")),
    "jdhealth": (os.getenv("JDHEALTH_CLIENT_ID"), os.getenv("JDHEALTH_CLIENT_SECRET")),
    "nutpet":   (os.getenv("NUTPET_CLIENT_ID"),   os.getenv("NUTPET_CLIENT_SECRET")),
}

# 기존 인플루언서 프로그램의 credentials 파일 공유
CREDENTIALS_PATH = os.getenv(
    "CREDENTIALS_PATH",
    str(BASE_DIR.parent / "influencer data shared" / "credentials" / "google-credentials.json"),
)


def _get_creds(store: str):
    """스토어 API 키 반환. 없으면 None."""
    creds = STORE_CREDENTIALS.get(store)
    if not creds or not creds[0]:
        return None, None
    return creds


# ── 5년 히스토리 분석 ───────────────────────────────────────

def run_history():
    print("\n" + "=" * 50)
    print("  5년 리뷰 트렌드 분석 시작")
    print("=" * 50)

    products = sheets_reader.load_products(CREDENTIALS_PATH)
    if not products:
        print("[오류] 제품 목록을 불러올 수 없습니다.")
        return

    date_from = "2021-01-01"
    date_to   = datetime.now().strftime("%Y-%m-%d")
    print(f"  조회 기간: {date_from} ~ {date_to}")
    print(f"  제품 수: {len(products)}개\n")

    trend_analyses = []

    for i, product in enumerate(products, 1):
        name       = product["name"]
        product_no = product["product_no"]
        store      = product["store"]
        print(f"[{i}/{len(products)}] {name} (상품번호: {product_no}, 스토어: {store})")

        client_id, client_secret = _get_creds(store)
        if not client_id:
            print(f"  [스킵] '{store}' 스토어 API 키 없음\n")
            continue

        try:
            reviews = naver_review_api.get_reviews(
                client_id, client_secret, product_no, date_from, date_to
            )
        except Exception as e:
            print(f"  [오류] 리뷰 수집 실패: {e}\n")
            continue

        if not reviews:
            print(f"  → 수집된 리뷰 없음, 스킵\n")
            continue

        # 연도별 분류
        reviews_by_year = {}
        for r in reviews:
            year = r["date"][:4] if r.get("date") and len(r["date"]) >= 4 else "unknown"
            if year != "unknown":
                reviews_by_year.setdefault(year, []).append(r)

        year_summary = ", ".join(
            f"{y}년 {len(v)}건" for y, v in sorted(reviews_by_year.items())
        )
        print(f"  → 연도별: {year_summary}")

        print(f"  → Claude로 트렌드 분석 중...")
        trend = analyzer.analyze_historical_trend(name, reviews_by_year)
        trend_analyses.append({"name": name, "trend": trend})
        print(f"  → 완료: {trend.get('trend_direction', '')} ({trend.get('trend', '')[:40]})\n")

    if trend_analyses:
        print("\n텔레그램으로 리포트 전송 중...")
        report = reporter.format_history_report(trend_analyses)
        reporter.send_telegram(report)
        print("=== 히스토리 리포트 전송 완료 ===\n")
    else:
        print("[경고] 분석할 리뷰 데이터가 없습니다.")


# ── 월간 리포트 ─────────────────────────────────────────────

def run_monthly():
    print("\n" + "=" * 50)
    print("  월간 리뷰 분석 리포트 시작")
    print("=" * 50)

    products = sheets_reader.load_products(CREDENTIALS_PATH)
    if not products:
        print("[오류] 제품 목록을 불러올 수 없습니다.")
        return

    date_to   = datetime.now().strftime("%Y-%m-%d")
    date_from = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    print(f"  조회 기간: {date_from} ~ {date_to}")
    print(f"  제품 수: {len(products)}개\n")

    monthly_analyses = []

    for i, product in enumerate(products, 1):
        name       = product["name"]
        product_no = product["product_no"]
        store      = product["store"]
        print(f"[{i}/{len(products)}] {name} (상품번호: {product_no}, 스토어: {store})")

        client_id, client_secret = _get_creds(store)
        if not client_id:
            print(f"  [스킵] '{store}' 스토어 API 키 없음\n")
            continue

        try:
            reviews = naver_review_api.get_reviews(
                client_id, client_secret, product_no, date_from, date_to
            )
        except Exception as e:
            print(f"  [오류] 리뷰 수집 실패: {e}\n")
            continue

        print(f"  → Claude로 분석 중...")
        analysis = analyzer.analyze_reviews_monthly(name, reviews)
        monthly_analyses.append({"name": name, "analysis": analysis})

        avg  = analysis.get("avg_star", 0)
        cnt  = analysis.get("count", 0)
        print(f"  → 완료: {avg}점 / {cnt}건\n")

    if monthly_analyses:
        print("\n텔레그램으로 리포트 전송 중...")
        report = reporter.format_monthly_report(monthly_analyses)
        reporter.send_telegram(report)
        print("=== 월간 리포트 전송 완료 ===\n")
    else:
        print("[경고] 분석할 데이터가 없습니다.")


# ── 진입점 ──────────────────────────────────────────────────

def main():
    if "--history" in sys.argv:
        run_history()
    else:
        run_monthly()


if __name__ == "__main__":
    main()
