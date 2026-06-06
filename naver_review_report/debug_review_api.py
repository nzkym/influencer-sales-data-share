"""
리뷰 API 테스트 스크립트 — sell.smartstore.naver.com
서버에서 실행: python3 debug_review_api.py

목적:
  1. 쿠키 인증이 정상 동작하는지 확인
  2. 리뷰 텍스트 필드명 확인 (reviewBody / content / 기타)
  3. 날짜 필드 확인 (createDate 등)
"""

import os, json, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

SELL_REVIEW_URL = "https://sell.smartstore.naver.com/api/v3/contents/reviews/search"
KST = timezone(timedelta(hours=9))

# 테스트할 스토어 (쿠키가 설정된 스토어)
TEST_STORE = "jdhealth"
COOKIES = os.getenv(f"{TEST_STORE.upper()}_COOKIES", "")


def main():
    if not COOKIES:
        print(f"[오류] .env에 {TEST_STORE.upper()}_COOKIES가 없습니다.")
        print("  .env.example을 참고해서 쿠키를 설정해주세요.")
        return

    now = datetime.now(KST)
    # 최근 7일 조회
    to_date   = now.strftime("%Y-%m-%dT23:59:59.999+09:00")
    from_date = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000+09:00")

    print(f"API 테스트: {TEST_STORE}")
    print(f"기간: {from_date[:10]} ~ {to_date[:10]}\n")

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": COOKIES,
        "Origin": "https://sell.smartstore.naver.com",
        "Referer": "https://sell.smartstore.naver.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
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
        "page": 0,
        "size": 5,  # 테스트용: 5개만
        "sort": [],
    }

    r = requests.post(SELL_REVIEW_URL, json=payload, headers=headers, timeout=30)
    print(f"HTTP 상태: {r.status_code}")

    if r.status_code == 401:
        print("[실패] 쿠키가 만료되었거나 잘못되었습니다. 쿠키를 다시 복사해주세요.")
        return
    if r.status_code == 403:
        print("[실패] 접근 권한 없음. 로그인 쿠키를 확인해주세요.")
        return
    if not r.ok:
        print(f"[실패] {r.text[:300]}")
        return

    data = r.json()
    total = data.get("totalElements", 0)
    total_pages = data.get("totalPages", 0)
    contents = data.get("contents", [])

    print(f"\n✅ 성공!")
    print(f"전체 리뷰 수: {total}건")
    print(f"전체 페이지 수: {total_pages}페이지 (size=500 기준 약 {(total + 499) // 500}페이지)")
    print(f"이번 응답: {len(contents)}건\n")

    if not contents:
        print("[경고] 해당 기간에 리뷰가 없습니다.")
        return

    # 첫 번째 리뷰의 모든 필드 출력 (텍스트 필드명 확인용)
    first = contents[0]
    print("=" * 50)
    print("첫 번째 리뷰 전체 필드:")
    print("=" * 50)
    for key, value in first.items():
        if key == "attachs":
            print(f"  attachs: [{len(value)}개 첨부]")
        elif isinstance(value, str) and len(value) > 80:
            print(f"  {key}: {value[:80]}...")
        else:
            print(f"  {key}: {value}")

    # 리뷰 텍스트 필드 후보 확인
    print("\n" + "=" * 50)
    print("리뷰 텍스트 필드 후보 확인:")
    text_candidates = ["reviewBody", "content", "reviewContent", "textContent",
                       "body", "reviewText", "text", "message", "reviewMessage"]
    found_text = None
    for field in text_candidates:
        val = first.get(field)
        if val:
            print(f"  ✅ {field}: {str(val)[:100]}")
            found_text = field
        else:
            print(f"  ❌ {field}: 없음")

    print("\n날짜 필드 후보 확인:")
    date_candidates = ["createDate", "writeDate", "createdAt", "registeredDate",
                       "reviewCreateDate", "modifyDate"]
    for field in date_candidates:
        val = first.get(field)
        if val:
            print(f"  ✅ {field}: {val}")
        else:
            print(f"  ❌ {field}: 없음")

    # 제품별 분포 (5건 기준)
    print(f"\n수집된 {len(contents)}건의 제품명:")
    for item in contents:
        name = item.get("productName", "?")
        score = item.get("reviewScore", "?")
        date_field = item.get("createDate") or item.get("writeDate") or "?"
        date_short = str(date_field)[:10] if date_field != "?" else "?"
        print(f"  [{score}점] {date_short} | {name[:40]}")

    if found_text:
        print(f"\n리뷰 텍스트 필드명: '{found_text}' ← naver_review_api.py에 반영됨")
    else:
        print("\n[주의] 리뷰 텍스트 필드를 찾지 못했습니다.")
        print("  위의 '첫 번째 리뷰 전체 필드' 목록을 확인해서 텍스트가 있는 필드명을 알려주세요.")


if __name__ == "__main__":
    main()
