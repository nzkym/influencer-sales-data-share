"""
AI briefing generator using Claude API.
Generates Korean-language market trend reports.
"""

import os
import json
from datetime import datetime
from typing import Optional
import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000


def _format_trend_summary(keyword: str, analysis: dict, raw_trends: dict) -> str:
    """Format trend data for a single keyword as readable text for the prompt."""
    lines = [f"[{keyword}]"]
    lines.append(f"  - 트렌드 단계: {analysis['trend_phase']}")
    lines.append(f"  - 기회 점수: {analysis['opportunity_score']:.1f}/100")
    lines.append(f"  - 얼리무버 점수: {analysis['early_mover_score']:.1f}/100")
    lines.append(f"  - 최근 성장률 (최근3개월 vs 이전3개월): {analysis['recent_growth_rate']:+.1f}%")
    lines.append(f"  - 장기 트렌드: {analysis['longterm_trend']}")
    lines.append(f"  - 일관성 점수: {analysis['consistency_score']:.1f}/100")
    lines.append(f"  - 현재 수준/최고점 비율: {analysis['peak_ratio']:.1%}")
    lines.append(f"  - 3개월 평균 검색량: {analysis['avg_ratio_3mo']:.1f}")
    lines.append(f"  - 1년 평균 검색량: {analysis['avg_ratio_1yr']:.1f}")
    lines.append(f"  - 역대 최고 검색량: {analysis['max_ratio_alltime']:.1f}")
    total_vol = analysis.get("monthly_total_search", 0)
    if total_vol > 0:
        pc_vol = analysis.get("monthly_pc_search", 0)
        mob_vol = analysis.get("monthly_mobile_search", 0)
        comp = analysis.get("ad_competition", "")
        lines.append(f"  - 월간 실검색량: 총 {total_vol:,}회 (PC {pc_vol:,} / 모바일 {mob_vol:,}){' / 광고경쟁: ' + comp if comp else ''}")

    # Add some recent data points if available
    lt_data = raw_trends.get("longterm", {}).get(keyword, [])
    if lt_data:
        recent_lt = lt_data[-6:]  # Last 6 months
        if recent_lt:
            trend_str = " → ".join([f"{d['ratio']:.0f}" for d in recent_lt])
            lines.append(f"  - 최근 6개월 추이 (월별): {trend_str}")

    mo3_data = raw_trends.get("shortterm_3mo", {}).get(keyword, [])
    if mo3_data:
        recent_mo3 = mo3_data[-8:]  # Last 8 weeks
        if recent_mo3:
            trend_str = " → ".join([f"{d['ratio']:.0f}" for d in recent_mo3])
            lines.append(f"  - 최근 8주 추이 (주별): {trend_str}")

    return "\n".join(lines)


def _build_prompt(analyzed_keywords: list[dict], raw_trends: dict) -> str:
    """Build the prompt for Claude."""
    today = datetime.now().strftime("%Y년 %m월 %d일")

    # Early rising keywords — 선점 기회
    early_rising = [k for k in analyzed_keywords if k["trend_phase"] == "early_rising"][:20]

    # Steady growth keywords — 꾸준한 우상향 (안정 성장)
    steady_growers = sorted(
        [k for k in analyzed_keywords
         if k.get("steady_growth_score", 0) >= 50
         and k["trend_phase"] not in ("declining", "unknown")
         and k["longterm_trend"] == "rising"],
        key=lambda x: x.get("steady_growth_score", 0),
        reverse=True
    )[:15]

    # Growing keywords
    growing = [k for k in analyzed_keywords if k["trend_phase"] == "growing"][:15]

    # Flash trends (high growth but potentially unstable)
    flash_candidates = [
        k for k in analyzed_keywords
        if k["recent_growth_rate"] > 100 and k["consistency_score"] < 40
    ][:5]

    # Top opportunity keywords (to fill up context if early_rising is few)
    already_included = {k["keyword"] for k in early_rising + steady_growers + growing + flash_candidates}
    top_remaining = [k for k in analyzed_keywords if k["keyword"] not in already_included][:10]

    # Format keyword data
    priority_keywords = early_rising + steady_growers + growing + top_remaining
    keyword_data_text = "\n\n".join([
        _format_trend_summary(kw["keyword"], kw, raw_trends)
        for kw in priority_keywords
    ])

    early_rising_text = "\n".join([
        f"- {k['keyword']}: 얼리무버점수={k['early_mover_score']:.0f}, 성장률={k['recent_growth_rate']:+.0f}%, 단계={k['trend_phase']}"
        for k in early_rising
    ]) or "해당 없음"

    steady_growers_text = "\n".join([
        f"- {k['keyword']}: 안정성장점수={k.get('steady_growth_score', 0):.0f}, 일관성={k['consistency_score']:.0f}, 성장률={k['recent_growth_rate']:+.0f}%"
        for k in steady_growers
    ]) or "해당 없음"

    flash_text = "\n".join([
        f"- {k['keyword']}: 성장률={k['recent_growth_rate']:+.0f}%, 일관성={k['consistency_score']:.0f}"
        for k in flash_candidates
    ]) or "해당 없음"

    # Phase distribution
    phases = [k["trend_phase"] for k in analyzed_keywords]
    phase_counts = {}
    for p in phases:
        phase_counts[p] = phase_counts.get(p, 0) + 1

    phase_summary = ", ".join([f"{p}: {c}개" for p, c in phase_counts.items()])

    prompt = f"""당신은 건강 관련 제품 시장의 전문 시장 분석가입니다. 약사 출신 사업자가 운영하는 건강 제품 브랜드를 위해 네이버 데이터랩 검색 트렌드 데이터를 분석합니다.

분석 범위: 건강식품(비타민·영양제·한방식품 등) 뿐 아니라 건강과 연관된 식품 전체(다이어트식품·음료·가루/분말류 등), 생활/건강(건강관리용품·당뇨용품·구강위생·반려동물 등), 출산/육아(아기간식·이유식·어린이 건강식품 등), 화장품/미용(약국 화장품·선케어·마스크팩 등), 디지털/가전(이미용가전·메디큐브형 가정용 의료기기 등) 카테고리를 종합적으로 포함합니다.

핵심 목표: 아직 경쟁이 적지만 검색량이 빠르게 오르는 성분/제품을 조기에 발굴하여, 경쟁자들이 몰리기 전에 생산·출시하는 것. 특히 약사 전문성으로 차별화 가능한 카테고리(약국용 화장품, 어린이 건강식품, 반려동물 영양제, 건강관리 기기 등)에 주목합니다.

오늘 날짜: {today}
분석 대상: 네이버 쇼핑 인사이트 — 식품전체·생활건강·출산육아·화장품미용·디지털가전 5개 대분류 통합 키워드
총 분석 키워드 수: {len(analyzed_keywords)}개
트렌드 단계 분포: {phase_summary}

=== 키워드별 트렌드 데이터 (기회 점수 순) ===

{keyword_data_text}

=== 초반 선점 유력 키워드 (빠른 성장, 초기 단계) ===
{early_rising_text}

=== 안정 성장 키워드 (꾸준한 우상향, 검증된 수요) ===
{steady_growers_text}

=== 반짝 급등 후보 (불안정 고성장) ===
{flash_text}

---

위 데이터를 바탕으로 건강 관련 시장 전반의 신규 기회를 탐색하는 사업자를 위한 전략 브리핑을 작성해주세요.

⚠️ 작성 원칙 (반드시 준수):
- 위에 제공된 실제 데이터(기회점수·성장률·얼리무버점수·장기트렌드·최근추이)만을 근거로 작성합니다.
- 데이터에 없는 키워드는 언급하지 않습니다. 브랜드명이 높게 나왔다고 해서 연관 키워드를 임의로 추론하거나 추천하지 않습니다.
- 성장률, 기회점수, 검색추이 수치를 반드시 함께 인용합니다. 예: "홍삼 (성장률 +32%, 기회점수 74점, 3개월 평균 45)"
- 데이터가 "성장 중"임을 뒷받침하지 않으면 "성장 중"이라고 쓰지 않습니다.
- 추측·예측·업계 일반론은 쓰지 않습니다. 오직 이번 수집 데이터에서 확인된 사실만 씁니다.

다음 구조로 한국어 보고서를 작성하세요:

1. 📊 시장 전반 현황 요약
   - 이번 수집 데이터에서 확인된 사실 위주로 (얼리라이징 몇 개, 성장중 몇 개, 하락 몇 개 등)
   - 카테고리를 넘나드는 공통 흐름이 데이터에서 보인다면 수치와 함께 기술

2. ⚡ 초반 선점 유력 키워드 (우선순위 높음)
   - 실제 기회점수·성장률·얼리무버점수 수치를 함께 제시
   - 어느 카테고리에서 나온 키워드인지 명시
   - 왜 선점 기회인지 데이터로 설명 (낮은 과거 기반 + 최근 급등 패턴 등)

3. 📈 안정 성장 키워드 (꾸준한 우상향)
   - 실제 성장률·일관성점수·장기트렌드 수치를 함께 제시
   - 데이터로 확인된 사실 위주로 서술

4. 🔍 카테고리별 주목 키워드 (데이터 기반)
   - 각 카테고리에서 기회점수 상위 키워드와 실제 수치를 나열
   - 식품전체 / 생활건강 / 출산육아 / 화장품미용 / 이미용가전 각각 별도 서술

5. ⚠️ 주의: 반짝 급등 또는 이미 꺾인 키워드
   - 급등했지만 지속 가능성이 낮거나 이미 하락 중인 키워드
   - 주의해야 할 이유

6. 💡 시장 진입 전략 제안
   - 단기 (3개월 이내), 중기 (6-12개월), 장기 (1-2년) 전략
   - 포트폴리오 구성 제안: 건강식품(핵심) + 건강연관 식품 + 신규 카테고리 확장
   - 약사 전문성을 활용한 차별화 포인트 (성분 신뢰도, 처방 연계, 반려동물 영양제 등)

보고서는 실용적이고 구체적으로 작성하되, 데이터에 기반한 근거를 반드시 포함하세요. 마케팅 용어보다는 실제 데이터 수치를 인용하며 분석하세요."""

    return prompt


def generate_briefing(
    analyzed_keywords: list[dict],
    raw_trends: dict,
    api_key: Optional[str] = None,
) -> str:
    """
    Generate a Korean-language market briefing using Claude API.

    Args:
        analyzed_keywords: List of analyzed keyword dicts (from TrendAnalyzer.analyze_keywords)
        raw_trends: Raw trend data dict (from naver_api.get_all_trend_data)
        api_key: Optional Anthropic API key (uses ANTHROPIC_API_KEY env var if not provided)

    Returns:
        Korean-language briefing report as a string.
    """
    if not analyzed_keywords:
        return "분석할 키워드 데이터가 없습니다."

    # Get API key
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다. "
            ".env 파일을 확인하세요."
        )

    client = anthropic.Anthropic(api_key=key)

    print(f"\n[리포터] Claude AI 브리핑 생성 중... (모델: {MODEL})")
    print(f"[리포터] 분석 키워드 수: {len(analyzed_keywords)}개")

    prompt = _build_prompt(analyzed_keywords, raw_trends)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        )

        briefing = response.content[0].text
        print(f"[리포터] 브리핑 생성 완료 ({len(briefing)}자)")
        return briefing

    except anthropic.AuthenticationError:
        raise ValueError("Anthropic API 키가 올바르지 않습니다. ANTHROPIC_API_KEY를 확인하세요.")
    except anthropic.RateLimitError:
        raise RuntimeError("Anthropic API 요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
    except anthropic.APIError as e:
        raise RuntimeError(f"Anthropic API 오류: {e}")


def _is_missing_data_critical(
    analyzed_keywords: list[dict],
    trend_data: Optional[dict],
) -> tuple[bool, list[str]]:
    """
    누락 데이터가 리포트 신뢰성에 심각한 영향을 주는지 판단합니다.

    기준:
    - 전체 키워드의 30% 이상이 no_data 상태이거나
    - API 실패 키워드가 10개 이상인 경우

    Returns:
        (is_critical: bool, missing_keywords: list[str])
    """
    no_data_kws = [k["keyword"] for k in analyzed_keywords if k.get("data_quality") == "no_data"]
    api_failures: dict = (trend_data or {}).get("_api_failures", {})

    # 모든 누락 키워드 합산 (중복 제거)
    all_missing = list({*no_data_kws, *api_failures.keys()})

    total = len(analyzed_keywords)
    if total == 0:
        return False, []

    missing_ratio = len(all_missing) / total
    is_critical = missing_ratio >= 0.30 or len(api_failures) >= 10

    return is_critical, all_missing


def format_missing_data_warning(
    analyzed_keywords: list[dict],
    trend_data: Optional[dict],
) -> str:
    """
    중요 데이터 누락 시 출력할 1페이지 경고 리포트를 반환합니다.
    """
    today = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    separator = "=" * 70
    _, missing_keywords = _is_missing_data_critical(analyzed_keywords, trend_data)
    api_failures: dict = (trend_data or {}).get("_api_failures", {})
    no_data_kws = [k["keyword"] for k in analyzed_keywords if k.get("data_quality") == "no_data"]
    total = len(analyzed_keywords)

    lines = [
        "",
        separator,
        "  네이버 데이터랩 건강식품 트렌드 분석 리포트",
        f"  생성일시: {today}",
        separator,
        "",
        "⚠️  데이터 누락으로 인해 정식 리포트를 생성하지 않았습니다.",
        "",
        f"  전체 분석 키워드: {total}개",
        f"  데이터 수집 실패: {len(missing_keywords)}개 ({len(missing_keywords)/total*100:.0f}%)",
        "",
        "  누락 항목 상세:",
    ]

    if no_data_kws:
        lines.append(f"  - 트렌드 데이터 없음 ({len(no_data_kws)}개): {', '.join(no_data_kws[:15])}")
        if len(no_data_kws) > 15:
            lines.append(f"    ... 외 {len(no_data_kws) - 15}개")

    if api_failures:
        lines.append(f"  - API 수집 실패 ({len(api_failures)}개):")
        for kw, periods in list(api_failures.items())[:10]:
            lines.append(f"      {kw}: {', '.join(periods)}")
        if len(api_failures) > 10:
            lines.append(f"    ... 외 {len(api_failures) - 10}개")

    lines += [
        "",
        "  판단 근거:",
        "  누락된 데이터 비중이 30% 이상이거나 API 실패 키워드가 10개 이상일 경우,",
        "  불완전한 데이터로 생성된 리포트는 오히려 잘못된 시장 판단으로 이어질 수 있습니다.",
        "  따라서 이번 실행에서는 리포트 생성을 건너뜁니다.",
        "",
        "  다음 실행 시 선택 가능한 옵션:",
        "  [누락 데이터 보완] 프로그램을 다시 실행하면 '누락 데이터 보완' 옵션이 제공됩니다.",
        "                    수집 실패 키워드만 재수집하여 완전한 리포트를 생성할 수 있습니다.",
        "  [새로 시작]       처음부터 전체 재수집을 원하면 '새로 시작'을 선택하세요.",
        "",
        separator,
    ]

    return "\n".join(lines)


def format_report(
    briefing: str,
    analyzed_keywords: list[dict],
    scrape_results: Optional[dict] = None,
    trend_data: Optional[dict] = None,
    tv_data: Optional[dict] = None,
    category_notes: Optional[dict] = None,
) -> str:
    """
    Format the final report combining the AI briefing with structured data.

    Args:
        briefing: AI-generated briefing text
        analyzed_keywords: List of analyzed keyword dicts
        scrape_results: Raw scraping results by period (optional)
        trend_data: Full trend data dict including _api_failures (optional)

    Returns:
        Complete formatted report string.
    """
    today = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    separator = "=" * 70

    sections = []

    # Header
    sections.append(f"""
{separator}
  네이버 데이터랩 건강 관련 전체 트렌드 분석 리포트
  생성일시: {today}
  분석 카테고리: 식품전체·생활건강·출산육아·화장품미용·디지털가전 (5개 대분류)
  데이터 출처: 네이버 쇼핑 인사이트 + 네이버 데이터랩 API
{separator}
""")

    # Glossary / Term Definitions
    sections.append("""## 📖 주요 용어 정의

  ▸ 기회점수 (Opportunity Score, 0~100점)
    시장 진입 기회의 종합 점수. 아래 5가지 요소를 가중 합산하여 산출합니다.
      - 얼리무버점수 40% + 성장률 25% + 일관성점수 15% + 성장여력(1-최고점비율) 10% + 장기트렌드 보정 10%
    점수가 높을수록 지금이 진입 적기이고, 선점 가능성이 높습니다.

  ▸ 성장률 (Growth Rate, %)
    [기간: 최근 3개월 평균 검색량] vs [직전 3개월 평균 검색량]의 변화율.
    예) +50%면 최근 3개월이 직전 3개월보다 검색량이 50% 더 많음.
    주간 데이터(3개월 주별)를 기준으로 계산하며, 없을 경우 1년 주별 데이터의 후반/전반을 비교합니다.

  ▸ 얼리무버점수 (Early Mover Score, 0~100점)
    경쟁자가 아직 많지 않은 초기 성장 단계에서 선점할 수 있는 가능성 점수.
    ① 최근 성장률이 높고 ② 과거 검색량이 낮은 저점에서 출발했으며
    ③ 최고점 대비 현재 검색량이 낮아 아직 성장 여력이 있을수록 높게 산출됩니다.

  ▸ 3개월평균 (3-Month Avg Search Index)
    최근 3개월 동안의 평균 검색량 지수 (네이버 기준 0~100 상대 지수).
    100이 해당 기간 최고 검색량 기준이며, 시장 절대 규모를 간접 비교하는 데 활용합니다.

  ▸ 일관성점수 (Consistency Score, 0~100점)
    장기간에 걸쳐 검색량이 얼마나 꾸준히 우상향했는지를 나타냅니다.
    점수가 높을수록 일시적 유행이 아닌 지속 성장 트렌드임을 의미합니다.

  ▸ 월간검색량 (Monthly Search Volume)
    네이버 검색광고 키워드도구 기준 해당 키워드의 최근 월간 PC + 모바일 검색 횟수.
    트렌드 지수(0~100 상대값)와 달리 실제 절대 검색량으로, 시장 규모를 직접 파악할 수 있습니다.
    광고 API 키 미설정 시 표시되지 않습니다.

  ▸ 트렌드 단계 (Trend Phase)
    early_rising: 초기 급성장 (얼리무버 기회, 경쟁 진입 전 단계)
    growing     : 성장 지속 (검색량 꾸준히 증가 중)
    peak        : 정점 도달 (최고점 근접, 이미 경쟁 치열)
    stable      : 안정적 유지 (큰 변동 없이 유지)
    declining   : 하락 중 (검색량 감소 추세)
""")

    # Keyword rankings if available
    if scrape_results:
        sections.append("## 📋 수집된 키워드 현황\n")
        for period in ["1년", "3개월", "1개월"]:
            kw_list = scrape_results.get(period, [])
            if kw_list:
                top5 = ", ".join([k["keyword"] for k in kw_list[:5]])
                sections.append(f"  {period} TOP 5: {top5}")
        sections.append("")

    # Early rising keywords — highlighted at the top
    early_rising_kws = [k for k in analyzed_keywords if k["trend_phase"] == "early_rising"]
    growing_kws = [k for k in analyzed_keywords if k["trend_phase"] == "growing"]

    has_volume = any(kw.get("monthly_total_search", 0) > 0 for kw in analyzed_keywords)

    def _vol_pc(kw):
        v = kw.get("monthly_pc_search", 0)
        return f"{v:,}" if v else "-"

    def _vol_mob(kw):
        v = kw.get("monthly_mobile_search", 0)
        return f"{v:,}" if v else "-"

    if early_rising_kws:
        sections.append(f"## ⚡ 지금 당장 주목할 얼리라이징 키워드 ({len(early_rising_kws)}개)\n")
        sections.append("  * 성장률: 최근3개월 vs 직전3개월 검색량 변화율  |  3개월평균: 최근3개월 평균 검색지수(0~100)")
        if has_volume:
            sections.append(f"{'순위':<4} {'키워드':<15} {'기회점수':>8} {'성장률':>12} {'얼리무버':>8} {'3개월평균':>10} {'PC검색량':>12} {'모바일검색량':>13}")
            sections.append("-" * 92)
            for i, kw in enumerate(early_rising_kws):
                sections.append(
                    f"{i+1:<4} {kw['keyword']:<15} {kw['opportunity_score']:>8.1f} "
                    f"{kw['recent_growth_rate']:>+11.1f}% {kw['early_mover_score']:>8.1f} "
                    f"{kw['avg_ratio_3mo']:>10.1f} {_vol_pc(kw):>12} {_vol_mob(kw):>13}"
                )
        else:
            sections.append(f"{'순위':<4} {'키워드':<15} {'기회점수(0-100)':>14} {'성장률(3개월비교)':>16} {'얼리무버(0-100)':>14} {'3개월평균지수':>12}")
            sections.append("-" * 80)
            for i, kw in enumerate(early_rising_kws):
                sections.append(
                    f"{i+1:<4} {kw['keyword']:<15} {kw['opportunity_score']:>14.1f} "
                    f"{kw['recent_growth_rate']:>+15.1f}% {kw['early_mover_score']:>14.1f} {kw['avg_ratio_3mo']:>12.1f}"
                )
        sections.append("")
    else:
        sections.append("## ⚡ 얼리라이징 키워드\n")
        sections.append("  현재 분석 기간 기준 얼리라이징 단계 키워드 없음\n")

    if growing_kws:
        sections.append(f"## 🚀 성장 중인 키워드 ({len(growing_kws)}개)\n")
        sections.append("  * 성장률: 최근3개월 vs 직전3개월 검색량 변화율")
        if has_volume:
            sections.append(f"{'순위':<4} {'키워드':<15} {'기회점수':>8} {'성장률':>12} {'얼리무버':>8} {'PC검색량':>12} {'모바일검색량':>13}")
            sections.append("-" * 80)
            for i, kw in enumerate(growing_kws[:20]):
                sections.append(
                    f"{i+1:<4} {kw['keyword']:<15} {kw['opportunity_score']:>8.1f} "
                    f"{kw['recent_growth_rate']:>+11.1f}% {kw['early_mover_score']:>8.1f} "
                    f"{_vol_pc(kw):>12} {_vol_mob(kw):>13}"
                )
        else:
            sections.append(f"{'순위':<4} {'키워드':<15} {'기회점수(0-100)':>14} {'성장률(3개월비교)':>16} {'얼리무버(0-100)':>14}")
            sections.append("-" * 68)
            for i, kw in enumerate(growing_kws[:20]):
                sections.append(
                    f"{i+1:<4} {kw['keyword']:<15} {kw['opportunity_score']:>14.1f} "
                    f"{kw['recent_growth_rate']:>+15.1f}% {kw['early_mover_score']:>14.1f}"
                )
        sections.append("")

    # Full opportunity ranking table
    sections.append("## 📊 전체 키워드 기회 점수 랭킹\n")
    sections.append("  * 성장률: 최근3개월 vs 직전3개월 검색량 변화율  |  기회점수/얼리무버: 0~100점 척도")
    if has_volume:
        sections.append(f"{'순위':<4} {'키워드':<15} {'기회점수':>8} {'성장률':>12} {'트렌드단계':>12} {'얼리무버':>8} {'3개월평균':>10} {'PC검색량':>12} {'모바일검색량':>13}")
        sections.append("-" * 102)
        for i, kw in enumerate(analyzed_keywords):
            sections.append(
                f"{i+1:<4} {kw['keyword']:<15} {kw['opportunity_score']:>8.1f} "
                f"{kw['recent_growth_rate']:>+11.1f}% {kw['trend_phase']:>12} "
                f"{kw['early_mover_score']:>8.1f} {kw['avg_ratio_3mo']:>10.1f} "
                f"{_vol_pc(kw):>12} {_vol_mob(kw):>13}"
            )
    else:
        sections.append(f"{'순위':<4} {'키워드':<15} {'기회점수':>8} {'성장률(3개월)':>13} {'트렌드단계':>12} {'얼리무버':>8} {'3개월평균지수':>12}")
        sections.append("-" * 80)
        for i, kw in enumerate(analyzed_keywords):
            sections.append(
                f"{i+1:<4} {kw['keyword']:<15} {kw['opportunity_score']:>8.1f} "
                f"{kw['recent_growth_rate']:>+12.1f}% {kw['trend_phase']:>12} {kw['early_mover_score']:>8.1f} {kw['avg_ratio_3mo']:>12.1f}"
            )

    sections.append("")

    # AI Briefing
    sections.append(f"{separator}")
    sections.append("  AI 시장 분석 브리핑")
    sections.append(f"{separator}\n")
    sections.append(briefing)

    # Category name change / collection issue notes
    if category_notes:
        sections.append(f"\n## 🔔 카테고리 수집 이슈 ({len(category_notes)}건) — 확인 권장\n")
        sections.append("  네이버 쇼핑인사이트 카테고리명이 변경되었거나 수집이 부분적으로 이루어진 항목입니다.")
        sections.append("  카테고리명이 변경됐을 가능성이 있으면 scraper.py의 HEALTH_CATEGORIES를 확인해 주세요.\n")
        for name, note in category_notes.items():
            sections.append(f"  ▸ [{name}] {note}")
        sections.append("")

    # API failure report
    api_failures = (trend_data or {}).get("_api_failures", {})
    if api_failures:
        sections.append(f"\n## ⚠️ API 오류로 데이터 누락된 키워드 ({len(api_failures)}개)\n")
        sections.append("  아래 키워드는 API 할당량 초과 또는 오류로 트렌드 데이터를 가져오지 못했습니다.")
        sections.append("  재실행하거나 --no-scrape 옵션으로 다시 시도하면 수집될 수 있습니다.\n")
        sections.append(f"  {'키워드':<15} {'누락된 기간'}")
        sections.append("  " + "-" * 45)
        for kw, periods in sorted(api_failures.items()):
            sections.append(f"  {kw:<15} {', '.join(periods)}")
        sections.append("")

    # TV/홈쇼핑 통합 언급 성분 섹션
    if tv_data:
        days = tv_data.get("days", 7)
        collected = tv_data.get("collected_sources", 0)
        all_agg = tv_data.get("aggregated_ingredients", {})

        ranked = sorted(
            [(ing, m) for ing, m in all_agg.items() if len(m) >= 5],
            key=lambda x: len(x[1]), reverse=True
        )
        if not ranked:
            ranked = sorted(all_agg.items(), key=lambda x: len(x[1]), reverse=True)[:10]

        if ranked:
            sections.append(f"\n{separator}")
            sections.append(f"  📺 TV/홈쇼핑 건강 성분 언급 순위 (최근 {days}일 | 소스 {collected}개)")
            sections.append(f"  ※ 5회 이상 언급된 성분만 표시 | 참고용")
            sections.append(separator)
            sections.append(f"\n{'순위':<4} {'성분명':<15} {'언급수':>6}  출처")
            sections.append("-" * 65)
            for i, (ing, mentions) in enumerate(ranked[:10]):
                sources = ", ".join(list({m["program"] for m in mentions})[:4])
                sections.append(f"{i+1:<4} {ing:<15} {len(mentions):>6}회  {sources}")
            sections.append("")

    sections.append(f"\n{separator}")
    sections.append("  분석 완료")
    sections.append(f"{separator}")

    return "\n".join(sections)


if __name__ == "__main__":
    # Test with mock data
    mock_analyzed = [
        {
            "keyword": "마그네슘",
            "recent_growth_rate": 85.3,
            "longterm_trend": "rising",
            "early_mover_score": 72.0,
            "consistency_score": 68.0,
            "peak_ratio": 0.45,
            "trend_phase": "early_rising",
            "opportunity_score": 81.2,
            "data_quality": "good",
            "avg_ratio_3mo": 42.3,
            "avg_ratio_1yr": 28.1,
            "max_ratio_alltime": 95.0,
            "current_ratio": 42.3,
        },
        {
            "keyword": "홍삼",
            "recent_growth_rate": 2.1,
            "longterm_trend": "stable",
            "early_mover_score": 15.0,
            "consistency_score": 80.0,
            "peak_ratio": 0.92,
            "trend_phase": "peak",
            "opportunity_score": 35.0,
            "data_quality": "good",
            "avg_ratio_3mo": 88.0,
            "avg_ratio_1yr": 82.0,
            "max_ratio_alltime": 96.0,
            "current_ratio": 88.0,
        },
    ]

    mock_raw = {"longterm": {}, "shortterm_3mo": {}}

    print("[테스트] 브리핑 생성 중...")
    try:
        briefing = generate_briefing(mock_analyzed, mock_raw)
        report = format_report(briefing, mock_analyzed)
        print(report[:500] + "...")
    except Exception as e:
        print(f"테스트 오류: {e}")
