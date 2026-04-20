"""
네이버 쇼핑인사이트 건강 관련 다중 카테고리 스크래퍼.
식품 전체 / 생활·건강 / 출산·육아 / 화장품·미용 / 디지털·가전 5개 대분류를
순차적으로 스크래핑하여 상위 키워드를 수집합니다.
"""

import asyncio
import json
import re
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Page, Route, Response


CACHE_FILE = Path(__file__).parent / "keyword_cache.json"
CACHE_MAX_AGE_HOURS = 24

DATALAB_URL = "https://datalab.naver.com/shoppingInsight/sCategory.naver"

# 수집 대상 카테고리 목록 (대분류, 중분류 or None, 소분류 or None, 표시명)
# - 중분류 None → 대분류 전체
# - 소분류 None → 중분류 전체
HEALTH_CATEGORIES = [
    # 식품 전체 (건강식품·다이어트식품·음료·가루분말 등)
    ("식품",        None,       None,              "식품전체"),
    # 생활/건강 전체 (건강관리·당뇨·실버용품·반려동물 등)
    ("생활/건강",   None,       None,              "생활건강전체"),
    # 출산/육아 전체 (아기간식·이유식·유아위생 등)
    ("출산/육아",   None,       None,              "출산육아전체"),
    # 화장품/미용 전체 (약국화장품·선케어·마스크팩 등)
    ("화장품/미용", None,       None,              "화장품미용전체"),
    # 디지털/가전 — 건강·미용 관련 소분류만 선별
    ("디지털/가전", "이미용가전",  None,            "이미용가전"),
    ("디지털/가전", "생활가전",   "구강청정기",      "생활가전-구강청정기"),
    ("디지털/가전", "생활가전",   "손소독기/손세정기","생활가전-손소독기"),
    ("디지털/가전", "생활가전",   "적외선소독기",    "생활가전-적외선소독기"),
    ("디지털/가전", "생활가전",   "이온수기",        "생활가전-이온수기"),
    ("디지털/가전", "생활가전",   "해충퇴치기",      "생활가전-해충퇴치기"),
]

# 카테고리당 수집 최대 키워드 수
MAX_RANK_PER_CATEGORY = 100


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
    period: str = "1년",
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

            # 카테고리 선택
            success, note = await _select_naver_category_by_name(page, main_cat, sub_cat, sub_sub_cat)
            if note:
                failure_note = note
            if success:
                print(f"[스크래퍼] 카테고리 선택 완료: {cat_label}{' (부분)' if note else ''}")
            else:
                print(f"[스크래퍼] 카테고리 선택 실패 — {note}")

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
    건강 관련 카테고리 × 3개 기간 스크래핑하여 키워드를 수집합니다.

    Returns:
        {
            "1년":    [{"rank":1, "keyword":"...", "category":"..."},  ...],
            "3개월":  [...],
            "1개월":  [...],
            "combined": [unique keywords sorted by composite score],
            "category_notes": {"display_name": "failure_note", ...},  # 카테고리 이슈 메모
            "cached_at": "ISO datetime"
        }
    """
    if use_cache:
        cached = _load_cache()
        if cached and all(p in cached for p in ["1년", "3개월", "1개월"]):
            return cached

    results: dict[str, list] = {"1년": [], "3개월": [], "1개월": []}
    periods = ["1년", "3개월", "1개월"]
    period_weights = {"1년": 1, "3개월": 2, "1개월": 3}
    category_notes: dict[str, str] = {}  # display_name → 최초 발견된 이슈 메모

    for main_cat, sub_cat, sub_sub_cat, display_name in HEALTH_CATEGORIES:
        print(f"\n[스크래퍼] ===== 카테고리: {display_name} =====")
        for period in periods:
            try:
                keywords, note = await get_top_keywords(
                    period=period,
                    main_cat=main_cat,
                    sub_cat=sub_cat,
                    sub_sub_cat=sub_sub_cat,
                    max_rank=max_rank,
                )
                # 카테고리 이슈 메모 수집 (중복 저장 방지)
                if note and display_name not in category_notes:
                    category_notes[display_name] = note
                # 카테고리 정보 태그 추가
                for kw in keywords:
                    kw["category"] = display_name
                results[period].extend(keywords)
                print(f"[스크래퍼] {display_name}/{period}: {len(keywords)}개 수집")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"[스크래퍼] {display_name}/{period} 스크래핑 실패: {e}")
        await asyncio.sleep(5)  # 카테고리 전환 간 대기

    # 중복 제거 및 점수 계산
    keyword_scores: dict[str, dict] = {}

    for period, kw_list in results.items():
        weight = period_weights.get(period, 1)
        for kw_data in kw_list:
            kw = kw_data["keyword"]
            rank = kw_data["rank"]
            category = kw_data.get("category", "")
            rank_score = max(0, 100 - rank) * weight
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
