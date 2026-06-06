"""
Naver SmartStore 리뷰 수집 API
URL: POST https://sell.smartstore.naver.com/api/v3/contents/reviews/search
인증: 브라우저 세션 쿠키

쿠키 갱신 방법:
  1. sell.smartstore.naver.com 로그인 → 리뷰 관리 페이지로 이동
  2. F12 → Network 탭 → Fetch/XHR 필터 → 페이지 새로고침
  3. "search" 요청 클릭 → Headers 탭 → Request Headers → Cookie 값 복사
  4. .env 파일의 해당 스토어 COOKIES에 붙여넣기
"""

import requests
from calendar import monthrange

SELL_REVIEW_URL = "https://sell.smartstore.naver.com/api/v3/contents/reviews/search"

_HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://sell.smartstore.naver.com",
    "Referer": "https://sell.smartstore.naver.com/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
}


def get_month_range(year: int, month: int) -> tuple[str, str]:
    days = monthrange(year, month)[1]
    return (
        f"{year}-{month:02d}-01T00:00:00.000+09:00",
        f"{year}-{month:02d}-{days:02d}T23:59:59.999+09:00",
    )


def get_year_range(year: int) -> tuple[str, str]:
    return (
        f"{year}-01-01T00:00:00.000+09:00",
        f"{year}-12-31T23:59:59.999+09:00",
    )


def _call_api(cookies: str, from_date: str, to_date: str, page: int = 0) -> dict:
    headers = {**_HEADERS_BASE, "Cookie": cookies}
    payload = {
        "reviewSearchSortType": "REVIEW_CREATE_DATE_DESC",
        "searchKeywordType": "IDS",
        "searchKeyword": "",
        "fromDate": from_date,
        "toDate": to_date,
        "useSelectedDate": False,
        "reviewTypes": [],
        "reviewContentClassTypes": [],
        "storeTypes": [],
        "reviewScores": [],
        "benefitKindTypeStringList": [],
        "contentsStatusTypes": [],
        "page": page,
        "size": 500,
        "sort": [],
    }
    r = requests.post(SELL_REVIEW_URL, json=payload, headers=headers, timeout=30)
    if r.status_code in (401, 403):
        raise PermissionError("쿠키 만료 — .env의 COOKIES를 갱신해주세요")
    r.raise_for_status()
    return r.json()


def _parse_review(item: dict) -> dict:
    """리뷰 객체에서 필요한 필드 추출 (필드명 여러 후보 시도)"""
    star = item.get("reviewScore") or 0
    content = (
        item.get("reviewBody")
        or item.get("content")
        or item.get("reviewContent")
        or item.get("textContent")
        or ""
    )
    raw_date = (
        item.get("createDate")
        or item.get("writeDate")
        or item.get("createdAt")
        or ""
    )
    date = str(raw_date)[:10] if raw_date else ""

    return {
        "star": int(star) if star else 0,
        "content": str(content).strip(),
        "date": date,
        "product_name": item.get("productName", ""),
        "product_no": str(item.get("productNo", "")),
        "group_product_no": str(item.get("groupProductNo", "")),
        "review_type": item.get("reviewType", ""),
    }


def get_reviews(
    cookies: str, from_date: str, to_date: str, store_name: str = ""
) -> list[dict]:
    """날짜 범위 내 스토어 전체 리뷰 수집 (자동 페이징)"""
    all_reviews: list[dict] = []
    page = 0

    while True:
        data = _call_api(cookies, from_date, to_date, page=page)
        contents = data.get("contents", [])
        total = data.get("totalElements", 0)
        total_pages = data.get("totalPages", 1)

        for item in contents:
            all_reviews.append(_parse_review(item))

        prefix = f"[{store_name}] " if store_name else ""
        print(
            f"  {prefix}페이지 {page + 1}/{total_pages}: "
            f"{len(contents)}건 (전체 {total}건)"
        )

        if data.get("last", True) or not contents:
            break
        page += 1

    return all_reviews
