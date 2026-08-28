"""
네이버 쇼핑인사이트 전체 카테고리(건강식품 제외) 스윕 스크래퍼.

건강식품은 별도 프로그램(naver_health_trend)에서 전담하므로 여기서는 제외하고,
그 외 모든 카테고리를 순위 기준으로 훑어 원본 키워드 풀을 수집한다.
카테고리 단위로 미리 걸러내지 않는 이유: "어린이아토피로션"처럼 의외의 카테고리에서
갑자기 뜨는 기회 키워드를 놓치지 않기 위해서다. 관련성 판단은 이 파일이 아니라
keyword_filter.py의 AI 필터가 키워드 단위로 수행한다 (2026-07-03 아키텍처 변경).
"""

import asyncio
import json
import re
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import requests
from playwright.async_api import async_playwright, Page, Route, Response


CACHE_FILE = Path(__file__).parent / "keyword_cache.json"
CACHE_MAX_AGE_HOURS = 24

DATALAB_URL = "https://datalab.naver.com/shoppingInsight/sCategory.naver"

# 스윕 대상 대분류 → 중분류 목록 (2026-07-03 네이버 데이터랩 드롭다운 실측값).
# 도서 / 면세점 / 여가·생활편의 는 구조적으로 실물 상품 카테고리가 아니라서 제외.
# 식품 > 건강식품 은 naver_health_trend가 전담하므로 제외.
#
# ⚠️ 순서 = 사업 우선순위 (2026-08-28 재배치): 203개를 다 훑기 전에 네이버 차단으로
# 스윕이 중간에 끊기는 경우가 실제로 있는데(레이트리밋), 예전 순서는 생활/건강(실버산업이
# 몰려있는 대분류)이 맨 끝(170~203번째)이라 차단이 오면 실버산업 카테고리가 통째로
# 스캔조차 안 되는 구조였다. 그래서 중요도 높은 대분류를 앞으로 옮겼다 — 차단이 오더라도
# 이미 우선순위 높은 카테고리는 끝난 뒤이도록.
SWEEP_CATEGORY_MAP: dict[str, list[str]] = {
    "생활/건강": [
        # 실버산업 관련 항목을 이 리스트 안에서도 맨 앞으로 — 대분류 자체가 밀리는 경우 대비
        "실버용품", "의료용품", "재활운동용품", "안마용품", "건강측정용품", "당뇨관리용품",
        "구강위생용품", "물리치료/저주파용품", "좌욕/좌훈용품", "냉온/찜질용품",
        "눈건강용품", "발건강용품", "건강관리용품", "반려동물",
        "공구", "문구/사무용품", "화방용품", "자동차용품", "수집품", "관상어용품", "악기",
        "음반", "DVD", "종교", "주방용품", "세탁용품",
        "원예/식물", "정원/원예용품", "블루레이", "욕실용품", "수납/정리용품",
        "청소용품", "생활용품", "자동차",
    ],
    "식품": [
        "다이어트식품", "가루/분말류", "음료",
        # "건강식품" 제외 — naver_health_trend 전담
        "축산물", "수산물", "농산물", "반찬", "김치", "유가공품", "냉동/간편조리식품",
        "통조림/캔", "제과/제빵재료", "조미료", "식용유/오일", "소스/드레싱",
        "잼/시럽", "라면/면류", "장류", "밀키트", "주류", "떡류", "즉석밥/즉석국",
        "젤리/사탕/초콜릿", "스낵/과자", "전통과자", "빵/베이커리", "아이스크림/빙수",
    ],
    "화장품/미용": [
        "스킨케어", "베이스메이크업", "색조메이크업", "클렌징", "마스크/팩", "선케어",
        "남성화장품", "향수", "바디케어", "헤어케어", "헤어스타일링", "네일케어", "뷰티소품",
    ],
    "출산/육아": [
        "위생/건강용품", "이유식", "아기간식",
        "분유", "기저귀", "물티슈", "수유용품", "유모차", "카시트",
        "외출용품", "목욕용품", "스킨/바디용품", "구강청결용품", "유아세제",
        "소독/살균용품", "매트/안전용품", "유아가구/소품", "이유식용품", "임부복", "임산부용품",
        "유아침구", "출산/돌기념품", "신생아의류", "유아동의류", "유아동언더웨어/잠옷",
        "유아동잡화", "수영복/용품", "유아동 주얼리", "유아발육용품", "완구/인형", "교구",
    ],
    "디지털/가전": [
        "이미용가전", "생활가전",
        "노트북", "노트북액세서리", "휴대폰액세서리", "PC", "모니터", "영상가전", "음향가전",
        "카메라/캠코더용품", "주방가전", "자동차기기", "계절가전",
        "휴대폰", "학습기기", "게임기/타이틀", "PC액세서리", "태블릿PC", "태블릿PC액세서리",
        "모니터주변기기", "주변기기", "멀티미디어장비", "저장장치", "PC부품", "네트워크장비",
        "소프트웨어", "광학기기/용품", "청소기",
    ],
    "스포츠/레저": [
        "헬스", "요가/필라테스",
        "등산", "캠핑", "골프", "자전거", "스키/보드", "낚시", "수영",
        "스케이트/보드/롤러", "오토바이/스쿠터", "축구", "야구", "농구", "배구", "탁구",
        "배드민턴", "테니스", "스쿼시", "족구", "볼링", "스킨스쿠버", "검도", "댄스", "권투",
        "보호용품", "무술용품", "수련용품", "스포츠액세서리", "러닝용품", "당구용품", "기타스포츠용품",
    ],
    "패션잡화": [
        "선글라스/안경테",  # 노안돋보기안경 발견 카테고리
        "여성신발", "남성신발", "신발용품", "여성가방", "남성가방", "여행용가방/소품",
        "지갑", "벨트", "모자", "장갑", "양말", "헤어액세서리",
        "패션소품", "시계", "순금", "주얼리",
    ],
    "패션의류": ["여성의류", "여성언더웨어/잠옷", "남성의류", "남성언더웨어/잠옷"],
    "가구/인테리어": [
        "침실가구", "거실가구", "주방가구", "수납가구", "아동/주니어가구", "서재/사무용가구",
        "아웃도어가구", "DIY자재/용품", "인테리어소품", "침구단품", "침구세트", "솜류",
        "카페트/러그", "커튼/블라인드", "홈데코", "수예", "베개",
    ],
}

# (대분류, 중분류, 표시명) 튜플 리스트로 평탄화
SWEEP_CATEGORIES: list[tuple[str, str, str]] = [
    (main_cat, sub_cat, f"{main_cat}-{sub_cat}")
    for main_cat, sub_list in SWEEP_CATEGORY_MAP.items()
    for sub_cat in sub_list
]

# 스윕 단계 수집 기간 — 급상승 포착에 집중 (장기 트렌드는 필터 통과 후 별도 API로 정밀 조회하므로
# 여기서는 최근 변화만 보면 충분. 2026-07-03: 3개 기간 → 2개로 단축해 스캔 시간 절반 이하로 축소)
SWEEP_PERIODS = ["3개월", "1개월"]

# 실버산업(고령친화) 우선순위 카테고리 — 통합 점수 산정 시 가중치 부여
SILVER_PRIORITY_CATEGORIES = {
    "생활/건강-실버용품",
    "생활/건강-의료용품",
    "생활/건강-재활운동용품",
    "생활/건강-안마용품",
    "생활/건강-건강측정용품",
    "생활/건강-당뇨관리용품",
}
SILVER_PRIORITY_WEIGHT = 1.5

# 실버산업 카테고리는 일반 카테고리보다 훨씬 깊게(순위 하위까지) 훑는다.
# 1차 스윕 결과(2026-07-04)에서 실버 관련 키워드가 노안돋보기안경 정도만 나오고 기대보다
# 적게 잡혀서, 얕은 순위 컷(카테고리당 30개)이 병목이라고 판단해 깊이를 늘림.
# SILVER_PRIORITY_CATEGORIES(6개, 가중치 대상)보다 넓게 — 생활/건강 대분류 전체(34개, 실버 관련
# 용품 대부분이 여기 있음) + 노안돋보기안경이 실제로 나온 패션잡화-안경류를 深스캔 대상에 포함.
SILVER_DEEP_SCAN_RANK = 100
DEEP_SCAN_CATEGORIES = {
    f"생활/건강-{sub}" for sub in SWEEP_CATEGORY_MAP["생활/건강"]
} | {"패션잡화-선글라스/안경테"}

# 카테고리당 수집 최대 키워드 수
MAX_RANK_PER_CATEGORY = 500

# 연속으로 이 횟수만큼 "카테고리 선택은 됐는데 키워드가 0개"가 반복되면
# 이름 불일치가 아니라 네이버 차단(레이트리밋/캡차) 가능성이 높다고 판단하고 중단 + 알림
BLOCK_SUSPECT_STREAK = 6

# 차단 의심 시 완전히 포기하지 않고 대기 후 재시도한다 — 어차피 새벽 사이 여유 시간이 있고
# 아침에 보고서만 받으면 되므로, 짧은 간격으로 재시도해서 또 차단되는 것보다 충분히 식힌 뒤
# 이어서 진행하는 쪽이 낫다는 판단 (2026-08-28). 30분 → 60분 → 90분 → 120분으로 점점 늘려가며
# 최대 4회 재시도(최대 총 대기 5시간) 후에도 안 풀리면 그때 최종 포기하고 부분 데이터로 진행.
BLOCK_COOLDOWN_MINUTES_BASE = 30
BLOCK_MAX_RETRY_ROUNDS = 4


def _send_telegram_alert(message: str) -> None:
    """차단 의심 등 긴급 상황을 텔레그램으로 즉시 알림."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("[알림] TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 텔레그램 알림 건너뜀")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        print("[알림] 텔레그램 알림 발송 완료")
    except Exception as e:
        print(f"[알림] 텔레그램 알림 발송 실패: {e}")


def _load_cache() -> Optional[dict]:
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        cached_at = datetime.fromisoformat(cache.get("cached_at", "2000-01-01"))
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours > CACHE_MAX_AGE_HOURS:
            print(f"[캐시] 캐시가 {age_hours:.1f}시간 전 생성됨 (만료: {CACHE_MAX_AGE_HOURS}시간). 재스크래핑합니다.")
            return None
        print(f"[캐시] 유효한 캐시 발견 ({age_hours:.1f}시간 전 생성). 캐시를 사용합니다.")
        return cache
    except Exception as e:
        print(f"[캐시] 캐시 로드 오류: {e}")
        return None


def _save_cache(data: dict) -> None:
    data["cached_at"] = datetime.now().isoformat()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[캐시] 키워드 캐시 저장 완료: {CACHE_FILE}")


def _parse_api_response(data: dict) -> list[dict]:
    keywords = []

    if "ranks" in data and isinstance(data["ranks"], list):
        for item in data["ranks"]:
            if isinstance(item, dict) and "keyword" in item:
                rank = item.get("rank", len(keywords) + 1)
                keywords.append({"rank": int(rank), "keyword": str(item["keyword"]).strip()})
        return keywords

    if "result" in data and isinstance(data["result"], list):
        for item in data["result"]:
            if isinstance(item, dict) and "keyword" in item:
                rank = item.get("rank", len(keywords) + 1)
                keywords.append({"rank": int(rank), "keyword": str(item["keyword"]).strip()})

    elif "data" in data and isinstance(data["data"], dict):
        kw_list = data["data"].get("keywordList", data["data"].get("keywords", []))
        for i, item in enumerate(kw_list):
            if isinstance(item, dict):
                kw = item.get("keyword", item.get("name", item.get("title", "")))
                rank = item.get("rank", i + 1)
                if kw:
                    keywords.append({"rank": int(rank), "keyword": str(kw).strip()})
            elif isinstance(item, str) and item:
                keywords.append({"rank": i + 1, "keyword": item.strip()})

    elif "keywords" in data and isinstance(data["keywords"], list):
        for i, item in enumerate(data["keywords"]):
            if isinstance(item, dict):
                kw = item.get("keyword", item.get("name", ""))
                rank = item.get("rank", i + 1)
                if kw:
                    keywords.append({"rank": int(rank), "keyword": str(kw).strip()})
            elif isinstance(item, str):
                keywords.append({"rank": i + 1, "keyword": item.strip()})

    return keywords


async def _click_dropdown_option(page: Page, dropdown_idx: int, target: str) -> str:
    """지정된 인덱스의 드롭다운에서 텍스트가 일치하는 옵션을 클릭. 결과 문자열 반환."""
    # 드롭다운 열기
    await page.evaluate(
        """(idx) => {
            var selects = document.querySelectorAll('.set_period.category .select');
            if (selects.length > idx) {
                var t = selects[idx].querySelector('.select_btn');
                if (t) t.click();
            }
        }""",
        dropdown_idx,
    )
    await asyncio.sleep(0.5)

    for attempt in range(3):
        result = await page.evaluate(
            """([idx, target]) => {
                var selects = document.querySelectorAll('.set_period.category .select');
                if (selects.length <= idx) return 'no_dropdown:' + idx;
                var opts = selects[idx].querySelectorAll('a.option');
                var texts = [];
                for (var i = 0; i < opts.length; i++) {
                    var t = opts[i].textContent.trim();
                    texts.push(t);
                    if (t === target) {
                        opts[i].click();
                        return 'clicked:' + t;
                    }
                }
                if (texts.length === 0) return 'no_options_yet';
                return 'not_found:available=[' + texts.join('|') + ']';
            }""",
            [dropdown_idx, target],
        )
        if "clicked:" in result:
            return result
        if "no_options_yet" in result and attempt < 2:
            await asyncio.sleep(2)
            await page.evaluate(
                """(idx) => {
                    var selects = document.querySelectorAll('.set_period.category .select');
                    if (selects.length > idx) {
                        var t = selects[idx].querySelector('.select_btn');
                        if (t) t.click();
                    }
                }""",
                dropdown_idx,
            )
            await asyncio.sleep(0.5)
            continue
        break

    return result


async def _select_naver_category_by_name(
    page: Page,
    main_cat: str,
    sub_cat: Optional[str] = None,
    sub_sub_cat: Optional[str] = None,
) -> tuple[bool, str]:
    """
    네이버 데이터랩 쇼핑인사이트에서 대분류 → 중분류 → 소분류(선택)를 선택합니다.

    Returns:
        (success: bool, note: str)
        note는 실패 또는 부분 성공 시 사유 메시지.
    """
    # 1단계: 대분류 선택
    r = await _click_dropdown_option(page, 0, main_cat)
    print(f"[스크래퍼] 대분류 '{main_cat}': {r}")
    if "not_found" in r or "no_dropdown" in r:
        return False, f"대분류 '{main_cat}' 없음 — 카테고리명 변경 가능성"

    # 2단계: 중분류 AJAX 대기
    print(f"[스크래퍼] '{main_cat}' 중분류 로딩 대기...")
    await asyncio.sleep(2)

    if sub_cat is None:
        # 중분류 전체: '전체' 또는 첫 번째 옵션
        r2 = await page.evaluate("""
            () => {
                var selects = document.querySelectorAll('.set_period.category .select');
                if (selects.length < 2) return 'no_2nd';
                var opts = selects[1].querySelectorAll('a.option');
                for (var i = 0; i < opts.length; i++) {
                    var t = opts[i].textContent.trim();
                    if (t === '전체' || t === '전체보기') { opts[i].click(); return 'all:' + t; }
                }
                if (opts.length > 0) { opts[0].click(); return 'first:' + opts[0].textContent.trim(); }
                return 'no_options';
            }
        """)
        print(f"[스크래퍼] 중분류 전체: {r2}")
        await asyncio.sleep(1)
        return True, ""

    # 2단계: 지정된 중분류 클릭
    r2 = await _click_dropdown_option(page, 1, sub_cat)
    print(f"[스크래퍼] 중분류 '{sub_cat}': {r2}")
    if "not_found" in r2:
        # 실제 존재하는 옵션 목록 추출해서 메모
        available = r2.split("available=[")[-1].rstrip("]") if "available=" in r2 else "확인필요"
        return False, f"중분류 '{sub_cat}' 없음 (실제 목록: {available}) — 카테고리명 변경 가능성"
    if "no_dropdown" in r2:
        return False, f"중분류 드롭다운 없음"

    await asyncio.sleep(2)

    if sub_sub_cat is None:
        return True, ""

    # 3단계: 소분류 AJAX 대기 후 클릭
    print(f"[스크래퍼] '{sub_cat}' 소분류 로딩 대기...")
    await asyncio.sleep(2)

    r3 = await _click_dropdown_option(page, 2, sub_sub_cat)
    print(f"[스크래퍼] 소분류 '{sub_sub_cat}': {r3}")
    if "clicked:" in r3:
        await asyncio.sleep(1)
        return True, ""
    if "no_dropdown" in r3:
        # 소분류 드롭다운 자체가 없음 → 중분류까지만 선택된 상태로 진행
        note = f"소분류 '{sub_sub_cat}' 드롭다운 없음 — 중분류 '{sub_cat}' 전체로 수집"
        print(f"[스크래퍼] {note}")
        return True, note
    if "not_found" in r3:
        available = r3.split("available=[")[-1].rstrip("]") if "available=" in r3 else "확인필요"
        note = f"소분류 '{sub_sub_cat}' 없음 (실제 목록: {available}) — 카테고리명 변경 가능성"
        print(f"[스크래퍼] {note}")
        return False, note

    return True, ""


async def _scrape_keywords_from_dom(page: Page) -> list[dict]:
    keywords = []
    selectors_to_try = [
        ".ranking_list .item",
        ".keyword_rank_list li",
        ".list_rank li",
        "[class*='rank'] li",
        ".item_keyword",
        "ol.list_rank li",
        ".keyword_list li",
        "[class*='keyword'] li",
    ]
    for selector in selectors_to_try:
        try:
            elements = await page.query_selector_all(selector)
            if not elements:
                continue
            for i, el in enumerate(elements):
                text = await el.inner_text()
                text = text.strip()
                if not text:
                    continue
                rank_match = re.match(r"^(\d+)[.\s]+(.+)$", text)
                if rank_match:
                    rank = int(rank_match.group(1))
                    keyword = rank_match.group(2).strip()
                else:
                    rank_el = await el.query_selector("[class*='rank'], .num, .number")
                    keyword_el = await el.query_selector("[class*='keyword'], [class*='text'], span")
                    if rank_el and keyword_el:
                        rank_text = await rank_el.inner_text()
                        keyword = await keyword_el.inner_text()
                        try:
                            rank = int(re.sub(r"[^\d]", "", rank_text))
                        except (ValueError, TypeError):
                            rank = i + 1
                    else:
                        rank = i + 1
                        keyword = text
                keyword = keyword.strip()
                if keyword:
                    keywords.append({"rank": rank, "keyword": keyword})
            if keywords:
                print(f"[스크래퍼] DOM 스크래핑 성공 ({selector}, {len(keywords)}개)")
                break
        except Exception as e:
            print(f"[스크래퍼] DOM 셀렉터 {selector} 실패: {e}")
            continue
    return keywords


async def get_top_keywords(
    period: str = "3개월",
    main_cat: str = "식품",
    sub_cat: Optional[str] = None,
    sub_sub_cat: Optional[str] = None,
    max_rank: int = MAX_RANK_PER_CATEGORY,
) -> tuple[list[dict], str]:
    """
    지정된 카테고리의 네이버 쇼핑인사이트 상위 키워드를 스크래핑합니다.

    Returns:
        (keywords: list[dict], failure_note: str)
        failure_note는 카테고리 선택 실패/변경 시 사유 메시지 (정상이면 빈 문자열).
    """
    cat_label = main_cat
    if sub_cat:
        cat_label += f" > {sub_cat}"
    if sub_sub_cat:
        cat_label += f" > {sub_sub_cat}"
    print(f"\n[스크래퍼] === {cat_label} | {period} 기간 스크래핑 시작 ===")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="ko-KR",
        )
        page = await context.new_page()
        keywords = []
        failure_note = ""

        try:
            print(f"[스크래퍼] 네이버 데이터랩 접속 중...")
            await page.goto(DATALAB_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # 네이버 차단/캡차 페이지 감지 (레이트리밋 의심 시 즉시 상위로 보고)
            page_text = await page.inner_text("body")
            if any(marker in page_text for marker in ["자동입력 방지", "비정상적인 접근", "일시적으로 제한", "이용이 제한"]):
                print("[스크래퍼] ⚠️ 네이버 차단/캡차 페이지로 추정됨")
                return [], "BLOCKED"

            # 카테고리 선택
            success, note = await _select_naver_category_by_name(page, main_cat, sub_cat, sub_sub_cat)
            if note:
                failure_note = note

            if not success:
                # 카테고리 선택 실패 시 절대 진행하지 않음 — 엉뚱한 카테고리(예: 기본 화면에 남은
                # 카테고리)로 스크래핑되어 무관한 키워드가 섞이는 것을 방지 (2026-07-03 수정)
                print(f"[스크래퍼] 카테고리 선택 실패 — {note}")
                print(f"[스크래퍼] {cat_label} 스크래핑을 건너뜁니다 (오염 방지)")
                return [], failure_note

            print(f"[스크래퍼] 카테고리 선택 완료: {cat_label}{' (부분)' if note else ''}")
            await asyncio.sleep(2)

            # 기간 선택
            print(f"[스크래퍼] 기간 '{period}' 선택 중...")
            period_clicked = False
            for sel in [
                f"button:has-text('{period}')",
                f"a:has-text('{period}')",
                f"label:has-text('{period}')",
                f"text={period}",
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    await page.click(sel)
                    period_clicked = True
                    print(f"[스크래퍼] 기간 '{period}' 선택 완료")
                    break
                except Exception:
                    continue
            if not period_clicked:
                print(f"[스크래퍼] 기간 버튼 '{period}'을 찾지 못했습니다.")

            await asyncio.sleep(1)

            # API 인터셉션 설정
            captured_from_api = []
            api_event = asyncio.Event()

            async def capture_response(response: Response):
                url = response.url
                if "datalab.naver.com" not in url:
                    return
                if "getCategoryKeywordRank" in url:
                    try:
                        body = await response.body()
                        import json as json_lib
                        text = body.decode("utf-8", errors="replace")
                        data = json_lib.loads(text)
                        parsed = _parse_api_response(data)
                        if parsed:
                            captured_from_api.extend(parsed)
                            api_event.set()
                            print(f"[스크래퍼] 키워드 랭킹 API 캡처: {len(parsed)}개")
                    except Exception as e:
                        print(f"[스크래퍼] 키워드 랭킹 파싱 오류: {e}")

            page.on("response", capture_response)

            # 조회하기 클릭
            print("[스크래퍼] '조회하기' 버튼 클릭 중...")
            search_clicked = False
            for sel in [
                "a.btn_submit",
                "button.btn_submit",
                "a:has-text('조회하기')",
                "a:has-text('조회')",
                "button:has-text('조회하기')",
            ]:
                try:
                    await page.wait_for_selector(sel, timeout=5000)
                    await page.click(sel)
                    search_clicked = True
                    print(f"[스크래퍼] '조회하기' 클릭 완료 ({sel})")
                    break
                except Exception:
                    continue
            if not search_clicked:
                print("[스크래퍼] '조회하기' 버튼을 찾지 못했습니다.")

            print("[스크래퍼] API 응답 대기 중...")
            try:
                await asyncio.wait_for(api_event.wait(), timeout=15)
            except asyncio.TimeoutError:
                print("[스크래퍼] API 응답 타임아웃. DOM 스크래핑으로 전환합니다.")

            page.remove_listener("response", capture_response)

            if captured_from_api:
                keywords = captured_from_api[:max_rank]
                print(f"[스크래퍼] API에서 {len(keywords)}개 키워드 수집 완료")
            else:
                print("[스크래퍼] API 캡처 실패. DOM에서 키워드 수집 시도...")
                await asyncio.sleep(3)
                keywords = await _scrape_keywords_from_dom(page)

        except Exception as e:
            print(f"[스크래퍼] 스크래핑 오류: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await context.close()
            await browser.close()

    print(f"[스크래퍼] {cat_label} / {period}: 총 {len(keywords)}개 키워드 수집 완료")
    return keywords[:max_rank], failure_note


async def get_all_period_keywords(
    max_rank: int = MAX_RANK_PER_CATEGORY,
    use_cache: bool = True,
) -> dict:
    """
    건강식품 제외 전체 카테고리(SWEEP_CATEGORIES) × SWEEP_PERIODS 스크래핑하여
    원본 키워드 풀을 수집합니다. 관련성 필터링은 여기서 하지 않고 keyword_filter.py가 담당.

    Returns:
        {
            "3개월":  [{"rank":1, "keyword":"...", "category":"..."},  ...],
            "1개월":  [...],
            "combined": [unique keywords sorted by composite score],
            "category_notes": {"display_name": "failure_note", ...},  # 카테고리 이슈 메모
            "blocked": bool,  # 네이버 차단 의심으로 중간에 중단했는지 여부
            "cached_at": "ISO datetime"
        }
    """
    if use_cache:
        cached = _load_cache()
        if cached and all(p in cached for p in SWEEP_PERIODS):
            return cached

    results: dict[str, list] = {p: [] for p in SWEEP_PERIODS}
    period_weights = {"3개월": 2, "1개월": 3}
    category_notes: dict[str, str] = {}  # display_name → 최초 발견된 이슈 메모
    blocked = False
    consecutive_empty_selected = 0  # 카테고리 선택은 성공했는데 키워드가 0개인 연속 횟수
    retry_round = 0  # 차단 감지 후 대기·재시도한 횟수

    total = len(SWEEP_CATEGORIES)
    i = 0
    while i < total:
        idx = i + 1
        main_cat, sub_cat, display_name = SWEEP_CATEGORIES[i]
        print(f"\n[스크래퍼] ===== [{idx}/{total}] 카테고리: {display_name} =====")
        category_max_rank = max(max_rank, SILVER_DEEP_SCAN_RANK) if display_name in DEEP_SCAN_CATEGORIES else max_rank

        block_hit = False
        for period in SWEEP_PERIODS:
            try:
                keywords, note = await get_top_keywords(
                    period=period,
                    main_cat=main_cat,
                    sub_cat=sub_cat,
                    sub_sub_cat=None,
                    max_rank=category_max_rank,
                )

                if note == "BLOCKED":
                    block_hit = True
                    break

                # 카테고리 이슈 메모 수집 (중복 저장 방지)
                if note and display_name not in category_notes:
                    category_notes[display_name] = note

                # 차단 의심 감지: 카테고리 선택은 됐는데(note가 이름불일치가 아님) 결과가 0개인 경우
                if not keywords and not note:
                    consecutive_empty_selected += 1
                else:
                    consecutive_empty_selected = 0

                # 카테고리 정보 태그 추가
                for kw in keywords:
                    kw["category"] = display_name
                results[period].extend(keywords)
                print(f"[스크래퍼] {display_name}/{period}: {len(keywords)}개 수집")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"[스크래퍼] {display_name}/{period} 스크래핑 실패: {e}")

            if consecutive_empty_selected >= BLOCK_SUSPECT_STREAK:
                block_hit = True
                break

        if not block_hit:
            i += 1
            await asyncio.sleep(5)  # 카테고리 전환 간 대기
            continue

        # === 차단 의심 감지 — 완전히 포기하지 않고 대기 후 같은 카테고리부터 재시도 ===
        if retry_round >= BLOCK_MAX_RETRY_ROUNDS:
            blocked = True
            print(f"\n[스크래퍼] ⚠️ 네이버 차단/레이트리밋 — {BLOCK_MAX_RETRY_ROUNDS}회 재시도 모두 실패, {idx}/{total}번째에서 최종 중단")
            _send_telegram_alert(
                "❌ [네이버 트렌드 스윕] 네이버 차단이 계속돼서 최종 포기했습니다.\n\n"
                f"진행 상황: {idx}/{total}개 카테고리 처리 중 중단 (재시도 {BLOCK_MAX_RETRY_ROUNDS}회 모두 실패)\n"
                f"지금까지 수집된 데이터만으로 리포트를 생성합니다."
            )
            break

        retry_round += 1
        cooldown_min = BLOCK_COOLDOWN_MINUTES_BASE * retry_round
        print(f"\n[스크래퍼] ⚠️ 네이버 차단/레이트리밋 의심 — {idx}/{total}번째, {cooldown_min}분 대기 후 재시도 ({retry_round}/{BLOCK_MAX_RETRY_ROUNDS})")
        _send_telegram_alert(
            "⚠️ [네이버 트렌드 스윕] 네이버 차단이 의심되어 잠시 쉬었다가 이어서 진행합니다.\n\n"
            f"진행 상황: {idx}/{total}개 카테고리\n"
            f"대기 시간: {cooldown_min}분 (재시도 {retry_round}/{BLOCK_MAX_RETRY_ROUNDS})\n"
            f"완료까지는 계속 백그라운드로 진행됩니다 — 별도 조치 필요 없습니다."
        )
        await asyncio.sleep(cooldown_min * 60)
        consecutive_empty_selected = 0
        # i는 그대로 두어 같은 카테고리부터 재시도

    results["blocked"] = blocked

    # 중복 제거 및 점수 계산
    keyword_scores: dict[str, dict] = {}

    for period, kw_list in results.items():
        if period not in period_weights:
            continue
        weight = period_weights.get(period, 1)
        for kw_data in kw_list:
            kw = kw_data["keyword"]
            rank = kw_data["rank"]
            category = kw_data.get("category", "")
            rank_score = max(0, 100 - rank) * weight
            if category in SILVER_PRIORITY_CATEGORIES:
                rank_score *= SILVER_PRIORITY_WEIGHT
            if kw not in keyword_scores:
                keyword_scores[kw] = {"keyword": kw, "score": 0, "periods": [], "categories": set()}
            keyword_scores[kw]["score"] += rank_score
            keyword_scores[kw]["periods"].append(period)
            keyword_scores[kw]["categories"].add(category)

    combined = sorted(keyword_scores.values(), key=lambda x: x["score"], reverse=True)
    results["combined"] = [
        {
            "rank": i + 1,
            "keyword": kw["keyword"],
            "score": kw["score"],
            "periods": kw["periods"],
            "categories": list(kw["categories"]),
        }
        for i, kw in enumerate(combined)
    ]

    results["category_notes"] = category_notes

    print(f"\n[스크래퍼] 전체 고유 키워드 수: {len(combined)}개")
    if combined[:5]:
        print(f"[스크래퍼] 상위 5개: {', '.join(k['keyword'] for k in combined[:5])}")
    if category_notes:
        print(f"[스크래퍼] 카테고리 이슈 감지: {len(category_notes)}건")
        for name, note in category_notes.items():
            print(f"  - {name}: {note}")

    _save_cache(results)
    return results


if __name__ == "__main__":
    async def main():
        results = await get_all_period_keywords(max_rank=20)
        print("\n=== 수집된 키워드 TOP 20 ===")
        for i, kw in enumerate(results.get("combined", [])[:20]):
            cats = ", ".join(kw.get("categories", []))
            print(f"{i+1}. {kw['keyword']} (점수: {kw['score']:.0f}, 카테고리: {cats})")

    asyncio.run(main())
