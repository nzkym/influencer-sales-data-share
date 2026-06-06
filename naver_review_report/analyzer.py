"""
Claude API를 사용한 리뷰 분석
- 월간 리포트: 최근 30일 리뷰의 좋은점/나쁜점/개선사항/긴급이슈
- 히스토리 분석: 연도별 트렌드 (개선됨 / 유지 / 악화됨)
"""

import os
import json
from anthropic import Anthropic

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# 분석 비용 절감을 위해 Haiku 사용
MODEL = "claude-haiku-4-5-20251001"
MAX_REVIEWS_PER_ANALYSIS = 150  # 분석에 쓸 최대 리뷰 수


def _call_claude(prompt: str, max_tokens: int = 1200) -> str:
    """Claude API 호출 + JSON 블록 자동 정리."""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    # ```json ... ``` 블록 제거
    if "```" in text:
        parts = text.split("```")
        for i, part in enumerate(parts):
            if part.startswith("json"):
                text = part[4:].strip()
                break
            if i % 2 == 1:  # 홀수 인덱스 = 코드 블록 안
                text = part.strip()
                break
    return text


def analyze_reviews_monthly(product_name: str, reviews: list) -> dict:
    """
    최근 30일 리뷰 분석 — 월간 리포트용.
    반환: {avg_star, count, good, bad, improve, critical, summary}
    """
    if not reviews:
        return {
            "avg_star": 0,
            "count": 0,
            "good": [],
            "bad": [],
            "improve": [],
            "critical": [],
            "summary": "이번 달 리뷰 없음",
        }

    avg_star = round(sum(r["star"] for r in reviews) / len(reviews), 1)

    # 내용 있는 리뷰만 분석 (별점만 있는 리뷰 제외)
    text_reviews = [r for r in reviews if r.get("content")]

    if not text_reviews:
        return {
            "avg_star": avg_star,
            "count": len(reviews),
            "good": [],
            "bad": [],
            "improve": [],
            "critical": [],
            "summary": f"이번 달 리뷰 {len(reviews)}건 (내용 없이 별점만 있음)",
        }

    # 최대 150개까지만 분석
    sample = text_reviews[:MAX_REVIEWS_PER_ANALYSIS]
    lines = [f"[{r['star']}점] {r['content']}" for r in sample]

    prompt = f"""다음은 '{product_name}' 제품의 최근 고객 리뷰 {len(sample)}개입니다.
(전체 {len(reviews)}건 중 내용이 있는 {len(text_reviews)}건에서 샘플링)

---
{chr(10).join(lines)}
---

위 리뷰를 분석하여 아래 JSON 형식으로만 답변하세요 (설명 없이):
{{
  "good": ["고객이 자주 칭찬하는 점 최대 5개"],
  "bad": ["자주 불만족하는 점 최대 5개"],
  "improve": ["개선이 필요한 사항 최대 3개"],
  "critical": ["즉시 해결해야 할 심각한 문제 (없으면 빈 배열)"],
  "summary": "전반적인 리뷰 요약 1~2문장"
}}"""

    try:
        text = _call_claude(prompt)
        result = json.loads(text)
        result["avg_star"] = avg_star
        result["count"] = len(reviews)
        return result
    except Exception as e:
        print(f"  [월간 분석 오류] {e}")
        return {
            "avg_star": avg_star,
            "count": len(reviews),
            "good": [],
            "bad": [],
            "improve": [],
            "critical": [],
            "summary": f"분석 오류: {e}",
        }


def analyze_historical_trend(product_name: str, reviews_by_year: dict) -> dict:
    """
    연도별 리뷰 트렌드 분석.
    reviews_by_year: {"2021": [...], "2022": [...], ...}
    반환: {trend, trend_direction, key_changes, current_issues, strengths, years}
    """
    if not reviews_by_year:
        return {"trend": "데이터 없음", "trend_direction": "stable", "years": {}}

    # 연도별 통계 계산
    year_stats = {}
    for year, reviews in sorted(reviews_by_year.items()):
        if not reviews:
            continue
        avg = round(sum(r["star"] for r in reviews) / len(reviews), 1)
        year_stats[year] = {"avg_star": avg, "count": len(reviews)}

    if not year_stats:
        return {"trend": "데이터 없음", "trend_direction": "stable", "years": {}}

    # 연도별 리뷰 샘플 구성 (각 연도 최대 30개)
    year_blocks = []
    for year, stats in sorted(year_stats.items()):
        reviews = reviews_by_year.get(year, [])
        samples = [r["content"] for r in reviews if r.get("content")][:30]
        if samples:
            year_blocks.append(
                f"\n[{year}년 — 평균 {stats['avg_star']}점, {stats['count']}건]\n"
                + "\n".join(f"• {s[:100]}" for s in samples)
            )

    if not year_blocks:
        # 리뷰 내용이 없어도 별점 추세는 보고
        sorted_years = sorted(year_stats.keys())
        if len(sorted_years) >= 2:
            first = year_stats[sorted_years[0]]["avg_star"]
            last = year_stats[sorted_years[-1]]["avg_star"]
            direction = "up" if last > first + 0.2 else ("down" if last < first - 0.2 else "stable")
        else:
            direction = "stable"
        return {
            "trend": "별점 데이터만 있음 (리뷰 내용 없음)",
            "trend_direction": direction,
            "key_changes": [],
            "current_issues": [],
            "strengths": [],
            "years": year_stats,
        }

    prompt = f"""'{product_name}' 제품의 연도별 고객 리뷰 변화입니다.

{''.join(year_blocks)}

위 내용을 분석하여 아래 JSON 형식으로만 답변하세요 (설명 없이):
{{
  "trend": "전반적인 트렌드 설명 (1문장)",
  "trend_direction": "up 또는 down 또는 stable",
  "key_changes": ["시간에 따라 변화한 주요 사항 최대 3개"],
  "current_issues": ["현재 가장 많이 지적되는 문제 최대 3개 (없으면 빈 배열)"],
  "strengths": ["꾸준히 칭찬받는 강점 최대 3개 (없으면 빈 배열)"]
}}

trend_direction 판단 기준: 최근 리뷰가 초기보다 나아졌으면 up, 나빠졌으면 down, 비슷하면 stable."""

    try:
        text = _call_claude(prompt, max_tokens=800)
        result = json.loads(text)
        result["years"] = year_stats
        return result
    except Exception as e:
        print(f"  [트렌드 분석 오류] {e}")
        return {
            "trend": f"분석 오류: {e}",
            "trend_direction": "stable",
            "key_changes": [],
            "current_issues": [],
            "strengths": [],
            "years": year_stats,
        }
