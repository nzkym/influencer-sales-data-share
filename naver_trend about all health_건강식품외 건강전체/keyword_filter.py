"""
키워드 관련성 AI 필터.

scraper.py가 건강식품을 제외한 전체 카테고리를 스윕해서 모아온 원본 키워드 후보 중,
건강기능식품 사업(건강기능식품/건강용품·실버용품/화장품·미용) 확장 기회와
실제로 관련 있는 것만 Claude로 골라낸다. 카테고리 단위가 아니라 키워드 단위로 판단하므로
"어린이아토피로션"처럼 예상 밖 카테고리에서 튀어나오는 기회 키워드도 놓치지 않는다.

비용: Haiku로 짧은 키워드 목록만 분류하므로 수천 개를 처리해도 토큰 비용은 미미함.
"""

import json
import os
import re

import anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
BATCH_SIZE = 150  # 한 번에 분류할 키워드 수
MAX_RETRIES = 2

BUSINESS_CONTEXT = """당신은 건강기능식품 제조·판매 사업자를 위한 키워드 선별 담당자입니다.
이 사업자는 건강기능식품이 본업이며, 아래 영역으로 사업을 확장하려고 합니다 (우선순위 순):
1. 실버산업 / 고령친화용품 — **최우선, 현재 가장 신경 쓰는 영역**
   예: 의료용품, 재활운동용품, 보행보조기(워커/지팡이), 안마용품, 건강측정용품(혈압계/혈당계),
   당뇨관리용품, 노안/돋보기, 틀니·구강관리, 요실금용품, 관절보호대, 낙상방지용품, 효도선물,
   좌욕/찜질용품, 시니어 영양(연하보조식/고령친화식) 등 — 노인·중장년이 쓰거나 자녀가 부모님께
   사드리는 물건이면 폭넓게 포함
2. 건강관리용품 전반 (위생, 구강건강, 반려동물 영양 등)
3. 화장품/뷰티
4. 건강 관련 식품 (다이어트식품, 이유식, 기능성 음료 등)

아래는 네이버 쇼핑에서 각 카테고리 상위권에 오른 키워드 후보 목록입니다.
이 중 위 사업 확장과 실제로 관련 있는 키워드만 골라주세요.

관련 있음 판단 기준 (예시):
- 성분/원료명, 건강기능식품·영양제 종류
- 의료기기·재활·안마·실버케어 용품
- 화장품/스킨케어/헤어케어 제품·성분
- 위생용품(구강청결, 손소독, 소독기 등)
- 다이어트/이유식/기능성 식품
- 특정 질환/증상 관리용품 (예: 아토피로션, 당뇨관리용품, 관절보호대)

관련 없음 (제외):
- 일반 의류, 가방, 신발, 액세서리, 주얼리, 시계
- 건강·미용과 무관한 일반 가전(TV, 청소기, 세탁기 등), PC/디지털기기
- 가구, 인테리어, 문구, 공구
- 건강과 무관한 일반 스포츠용품 (단, 헬스/요가/재활처럼 건강 관련성 있는 운동용품은 포함)
- 완구, 장난감, 유아동 일반 의류/잡화
- 해충퇴치기, 살충제 등 방역용품
- 가공 없는 일반 식재료(축산물/수산물/농산물 등), 주류, 과자·스낵류 등 단순 기호식품

애매하면 "이 키워드로 상품을 만들거나 판매를 검토할 가치가 있는가"를 기준으로 판단하세요.
**단, 1번(실버산업) 영역은 애매하면 관대하게 포함시켜주세요** — 지금 이 사업자가 가장 중요하게
보는 영역이라, 다른 영역보다 판단 기준을 낮춰서 놓치지 않는 쪽으로 판단해도 됩니다.

키워드 후보 목록:
{keyword_list}

반드시 아래 JSON 형식으로만 답하세요.
- relevant: 사업 확장과 관련 있는 키워드 전체 (후보 목록에 있는 표기 그대로)
- silver_industry: relevant 중에서도 실버산업/고령친화용품에 해당하는 키워드만 (relevant의 부분집합)

{{"relevant": ["키워드1", "키워드2", ...], "silver_industry": ["키워드2", ...]}}"""


def _classify_batch(keywords: list[str], api_key: str) -> tuple[list[str], list[str]]:
    """
    한 배치를 분류. 실패 시(파싱 오류 등) 배치 전체를 relevant로 그대로 반환한다 —
    "놓치는 것보다 몇 개 더 넘어가는 게 낫다"는 원칙 (기회 키워드 유실 방지).
    silver는 실패 시 알 수 없으므로 빈 목록.

    Returns: (relevant_keywords, silver_industry_keywords)
    """
    client = anthropic.Anthropic(api_key=api_key)
    keyword_list = "\n".join(f"- {kw}" for kw in keywords)
    prompt = BUSINESS_CONTEXT.format(keyword_list=keyword_list)
    candidate_set = set(keywords)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not json_match:
                raise ValueError("JSON 형식 응답 없음")
            parsed = json.loads(json_match.group())
            relevant = parsed.get("relevant", [])
            silver = parsed.get("silver_industry", [])
            # 모델이 후보에 없는 키워드를 지어내는 경우 방지 — 원본 후보에 있는 것만 인정
            relevant = [kw for kw in relevant if kw in candidate_set]
            silver = [kw for kw in silver if kw in candidate_set]
            return relevant, silver
        except Exception as e:
            print(f"[필터] 배치 분류 시도 {attempt}/{MAX_RETRIES} 실패: {e}")

    print(f"[필터] 배치 분류 최종 실패 — 안전하게 {len(keywords)}개 전체를 통과시킵니다 (유실 방지)")
    return keywords, []


def filter_relevant_keywords(keyword_candidates: list[dict]) -> list[dict]:
    """
    keyword_candidates: scraper.py의 combined 리스트
        [{"keyword":..., "score":..., "periods":[...], "categories":[...]}, ...]

    Returns: 관련 있다고 판단된 항목만 남긴 리스트. 각 항목에 is_silver: bool 필드가 추가됨.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[필터] ANTHROPIC_API_KEY 없음 — AI 필터를 건너뛰고 전체 키워드를 그대로 사용합니다.")
        for c in keyword_candidates:
            c["is_silver"] = False
        return keyword_candidates

    if not keyword_candidates:
        return []

    print(f"\n[필터] {len(keyword_candidates)}개 후보 키워드 AI 관련성 분류 시작...")

    all_keywords = [c["keyword"] for c in keyword_candidates]
    relevant_set: set[str] = set()
    silver_set: set[str] = set()

    batches = [all_keywords[i:i + BATCH_SIZE] for i in range(0, len(all_keywords), BATCH_SIZE)]
    for idx, batch in enumerate(batches):
        print(f"[필터] 배치 {idx + 1}/{len(batches)} ({len(batch)}개) 분류 중...")
        relevant, silver = _classify_batch(batch, api_key)
        relevant_set.update(relevant)
        silver_set.update(silver)
        print(f"[필터] 배치 {idx + 1}: {len(relevant)}/{len(batch)}개 관련 있음 (실버산업 {len(silver)}개)")

    filtered = []
    for c in keyword_candidates:
        if c["keyword"] not in relevant_set:
            continue
        c["is_silver"] = c["keyword"] in silver_set
        filtered.append(c)

    silver_count = sum(1 for c in filtered if c["is_silver"])
    silver_pct = (silver_count / len(filtered) * 100) if filtered else 0
    print(
        f"\n[필터] 완료: {len(keyword_candidates)}개 → {len(filtered)}개 관련 키워드 선별 "
        f"(제외 {len(keyword_candidates) - len(filtered)}개) — 이 중 실버산업 {silver_count}개 ({silver_pct:.0f}%)"
    )
    return filtered


if __name__ == "__main__":
    test = [
        {"keyword": "여아래쉬가드", "score": 10, "periods": [], "categories": ["패션잡화-여성신발"]},
        {"keyword": "실버워커", "score": 20, "periods": [], "categories": ["생활/건강-실버용품"]},
        {"keyword": "어린이아토피로션", "score": 15, "periods": [], "categories": ["출산/육아-스킨/바디용품"]},
        {"keyword": "노트북거치대", "score": 5, "periods": [], "categories": ["디지털/가전-노트북액세서리"]},
        {"keyword": "손소독제", "score": 8, "periods": [], "categories": ["생활/건강-의료용품"]},
        {"keyword": "노안돋보기안경", "score": 12, "periods": [], "categories": ["패션잡화-선글라스/안경테"]},
    ]
    result = filter_relevant_keywords(test)
    print("\n=== 관련 있음으로 판단된 키워드 ===")
    for r in result:
        tag = " [실버산업]" if r["is_silver"] else ""
        print(f"- {r['keyword']}{tag}")
