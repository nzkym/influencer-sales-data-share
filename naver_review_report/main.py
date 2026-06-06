"""
네이버 리뷰 분석 리포트 — 메인 실행 파일

실행 방법:
  python main.py              → 월간 리포트 (전월 집계)
  python main.py --monthly    → 동일
  python main.py --history    → 5년 히스토리 분석 (최초 1회 실행)

서버 cron 등록 (매달 1일 오전 9시 KST):
  0 0 1 * * cd ~/influencer-sales-data-share/naver_review_report && python3 main.py
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

import naver_review_api
import analyzer
import reporter

KST = timezone(timedelta(hours=9))

STORE_COOKIES = {
    "nutone":   os.getenv("NUTONE_COOKIES", ""),
    "jdhealth": os.getenv("JDHEALTH_COOKIES", ""),
    "nutpet":   os.getenv("NUTPET_COOKIES", ""),
}

STORE_DISPLAY = {
    "nutone":   "뉴톤",
    "jdhealth": "정담건강",
    "nutpet":   "뉴트펫",
}


def _group_by_product(reviews: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in reviews:
        name = r.get("product_name", "").strip()
        if name:
            grouped[name].append(r)
    return dict(grouped)


# ── 월간 리포트 ─────────────────────────────────────────────

def run_monthly():
    now = datetime.now(KST)
    year, month = now.year, now.month
    if month == 1:
        year, month = year - 1, 12
    else:
        month -= 1

    from_date, to_date = naver_review_api.get_month_range(year, month)

    print("\n" + "=" * 50)
    print(f"  월간 리뷰 분석 — {year}년 {month}월")
    print("=" * 50 + "\n")

    monthly_analyses = []

    for store, cookies in STORE_COOKIES.items():
        if not cookies:
            print(f"[{store}] .env에 쿠키 없음 — 스킵\n")
            continue

        print(f"[{store}] 리뷰 수집 중... ({from_date[:10]} ~ {to_date[:10]})")
        try:
            reviews = naver_review_api.get_reviews(
                cookies, from_date, to_date, store_name=store
            )
        except PermissionError as e:
            print(f"  [오류] {e}\n")
            reporter.send_telegram(f"⚠️ [{store}] 쿠키 만료 — .env 갱신 필요")
            continue
        except Exception as e:
            print(f"  [오류] {e}\n")
            continue

        if not reviews:
            print(f"  → 이번 달 리뷰 없음\n")
            continue

        grouped = _group_by_product(reviews)
        print(f"  → 제품 {len(grouped)}종, 총 {len(reviews)}건\n")

        for product_name, product_reviews in sorted(grouped.items()):
            print(f"    분석 중: {product_name[:30]} ({len(product_reviews)}건)")
            analysis = analyzer.analyze_reviews_monthly(product_name, product_reviews)
            monthly_analyses.append({
                "name": product_name,
                "store": STORE_DISPLAY.get(store, store),
                "analysis": analysis,
            })

    if monthly_analyses:
        print("\n텔레그램으로 리포트 전송 중...")
        report = reporter.format_monthly_report(monthly_analyses)
        reporter.send_telegram(report)
        print("=== 월간 리포트 전송 완료 ===\n")
    else:
        print("[경고] 분석할 데이터가 없습니다.")


# ── 5년 히스토리 분석 ───────────────────────────────────────

def run_history():
    now_year = datetime.now(KST).year

    print("\n" + "=" * 50)
    print("  5년 리뷰 트렌드 분석 시작")
    print("=" * 50 + "\n")

    all_product_reviews: dict[str, list[dict]] = defaultdict(list)

    for store, cookies in STORE_COOKIES.items():
        if not cookies:
            print(f"[{store}] .env에 쿠키 없음 — 스킵\n")
            continue

        for year in range(now_year - 4, now_year + 1):
            from_date, to_date = naver_review_api.get_year_range(year)
            print(f"[{store}] {year}년 리뷰 수집 중...")
            try:
                reviews = naver_review_api.get_reviews(
                    cookies, from_date, to_date, store_name=store
                )
            except PermissionError as e:
                print(f"  [오류] {e}\n")
                reporter.send_telegram(f"⚠️ [{store}] 쿠키 만료 — .env 갱신 필요")
                break
            except Exception as e:
                print(f"  [오류] {e}\n")
                continue

            for r in reviews:
                r["year"] = str(year)
                all_product_reviews[r["product_name"]].append(r)

    if not all_product_reviews:
        print("[경고] 수집된 리뷰가 없습니다.")
        return

    print(f"\n수집 완료: 제품 {len(all_product_reviews)}종 발견\n")

    trend_analyses = []
    products_sorted = sorted(
        all_product_reviews.items(), key=lambda x: -len(x[1])
    )

    for i, (product_name, reviews) in enumerate(products_sorted, 1):
        reviews_by_year: dict[str, list[dict]] = defaultdict(list)
        for r in reviews:
            reviews_by_year[r.get("year", "")].append(r)

        year_summary = ", ".join(
            f"{y}년 {len(v)}건" for y, v in sorted(reviews_by_year.items())
        )
        print(f"[{i}/{len(all_product_reviews)}] {product_name[:40]}")
        print(f"  연도별: {year_summary}")
        print(f"  Claude 분석 중...")

        trend = analyzer.analyze_historical_trend(product_name, dict(reviews_by_year))
        trend_analyses.append({"name": product_name, "trend": trend})
        direction = trend.get("trend_direction", "")
        summary = trend.get("trend", "")[:50]
        print(f"  완료: {direction} — {summary}\n")

    print("텔레그램으로 리포트 전송 중...")
    report = reporter.format_history_report(trend_analyses)
    reporter.send_telegram(report)
    print("=== 히스토리 리포트 전송 완료 ===\n")


# ── 진입점 ──────────────────────────────────────────────────

def main():
    if "--history" in sys.argv:
        run_history()
    else:
        run_monthly()


if __name__ == "__main__":
    main()
