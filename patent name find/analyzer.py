"""
Claude API 웹 검색 기반 트렌드 분석 모듈
- 1단계: Claude가 웹 검색하여 트렌드 텍스트 수집
- 2단계: 수집된 텍스트를 TOP 10 JSON으로 변환
"""

import os
import re
import json
import time
import anthropic


def _extract_text(message) -> str:
    """응답에서 모든 텍스트 블록 합쳐서 반환"""
    parts = []
    for block in message.content:
        if hasattr(block, "text") and block.text.strip():
            parts.append(block.text.strip())
    return "\n\n".join(parts)


def _search_trends(client: anthropic.Anthropic) -> str:
    """1단계: Claude 웹 검색으로 최신 트렌드 텍스트 수집"""

    prompt = """웹 검색으로 아래를 조사하고 한국어로 상세히 정리해주세요.

조사 항목:
1. 2025~2026년 미국/유럽/일본 건강식품 시장에서 새로 부상 중인 성분명, 키워드, 개념어
2. 해외에서 급성장 중이지만 한국에서 아직 인지도 낮은 건강 트렌드
3. 학술 용어 중 소비자 마케팅 용어로 전환 가능한 것들

각 항목마다 영어 원어, 한국어 표기, 해외 시장 현황(수치 포함), 국내 현황을 포함해주세요.
최소 10개 이상 항목을 찾아주세요."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3
            }
        ],
        messages=[{"role": "user", "content": prompt}]
    )

    result = _extract_text(message)
    return result


def _convert_to_json(client: anthropic.Anthropic, trend_text: str) -> dict:
    """2단계: 트렌드 텍스트를 TOP 10 JSON으로 변환"""

    # trend_text가 너무 짧으면 Claude 자체 지식 활용
    if len(trend_text) < 100:
        trend_text = "웹 검색 결과 없음. Claude의 최신 건강식품 트렌드 지식을 활용해주세요."

    prompt = f"""아래 건강식품 트렌드 조사 결과를 바탕으로 한국 상표 선점 가치 TOP 10을 선정하세요.

[조사 결과]
{trend_text}

[출력 형식 - 반드시 아래 형식 그대로]
각 후보를 아래 형식으로 10개 출력하세요. 각 줄은 파이프(|)로 구분합니다.
번호와 콜론 뒤에 값을 넣고, 값 안에는 파이프(|) 문자를 절대 사용하지 마세요.

CANDIDATE:1
TIER:★★★
EN:영어원어
KO:한국어표기
SUMMARY:요약 50자 이내
OVERSEAS:해외현황 한 문장
KOREA:국내현황 한 문장
STRATEGY:상표전략 한 문장
POTENTIAL:시장잠재력 한 문장
URGENCY:high
---
CANDIDATE:2
TIER:★★★
EN:영어원어
KO:한국어표기
SUMMARY:요약 50자 이내
OVERSEAS:해외현황 한 문장
KOREA:국내현황 한 문장
STRATEGY:상표전략 한 문장
POTENTIAL:시장잠재력 한 문장
URGENCY:high
---
(3번~10번도 동일한 형식으로)

마지막에 아래를 추가하세요:
INSIGHT:이번주 트렌드 한 줄 코멘트"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    return _parse_candidates(raw)


def _parse_candidates(raw: str) -> dict:
    """구조화된 텍스트 → dict 파싱"""
    candidates = []
    insight = ""

    # INSIGHT 추출
    insight_match = re.search(r'INSIGHT:(.+)', raw)
    if insight_match:
        insight = insight_match.group(1).strip()

    # 각 CANDIDATE 블록 파싱
    blocks = re.split(r'---+', raw)
    for block in blocks:
        block = block.strip()
        if not block or 'CANDIDATE:' not in block:
            continue

        def get_field(name):
            m = re.search(rf'{name}:(.+)', block)
            return m.group(1).strip() if m else ""

        rank_m = re.search(r'CANDIDATE:(\d+)', block)
        if not rank_m:
            continue

        rank = int(rank_m.group(1))
        if rank > 10:
            continue

        candidates.append({
            "rank": rank,
            "tier": get_field("TIER"),
            "term_en": get_field("EN"),
            "term_ko": get_field("KO"),
            "summary": get_field("SUMMARY"),
            "overseas_status": get_field("OVERSEAS"),
            "korea_status": get_field("KOREA"),
            "trademark_strategy": get_field("STRATEGY"),
            "market_potential": get_field("POTENTIAL"),
            "urgency": get_field("URGENCY") or "medium",
        })

    candidates.sort(key=lambda x: x["rank"])

    return {
        "candidates": candidates,
        "weekly_insight": insight
    }


def analyze_trends(articles: list = None) -> dict:
    """웹 검색 → 텍스트 수집 → JSON 변환 2단계 실행"""

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # 1단계: 웹 검색
    print("  [1단계] 웹 검색 중...")
    trend_text = _search_trends(client)
    print(f"  → 트렌드 텍스트 수집 완료 ({len(trend_text)}자)")

    # 토큰 한도 여유를 위해 대기
    time.sleep(10)

    # 2단계: 구조화된 텍스트 변환 + 파싱
    print("  [2단계] 분석 중...")
    data = _convert_to_json(client, trend_text)

    print(f"  → {len(data.get('candidates', []))}개 후보 선별 완료")
    return data
