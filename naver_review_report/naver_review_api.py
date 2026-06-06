"""
네이버 커머스 API 리뷰 수집
- 인증: 기존 인플루언서 프로그램과 동일한 bcrypt 방식
- 엔드포인트: /external/v2/contents/reviews (실패 시 v1 자동 시도)
- 상품번호 + 날짜 범위로 리뷰 전체 수집 (페이지네이션 처리)
"""

import bcrypt
import base64
import time
import requests
from datetime import datetime

BASE_URL = "https://api.commerce.naver.com"


def _get_access_token(client_id: str, client_secret: str) -> str:
    timestamp = str(int(time.time() * 1000))
    password = f"{client_id}_{timestamp}"
    hashed = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
    client_secret_sign = base64.standard_b64encode(hashed).decode("utf-8")
    resp = requests.post(
        f"{BASE_URL}/external/v1/oauth2/token",
        data={
            "client_id": client_id,
            "timestamp": timestamp,
            "client_secret_sign": client_secret_sign,
            "grant_type": "client_credentials",
            "type": "SELF",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _fetch_reviews_page(headers: dict, product_no: str, page: int,
                        date_from: str = None, date_to: str = None) -> dict:
    """리뷰 한 페이지 조회. v2 실패 시 v1으로 자동 재시도."""
    params = {
        "productId": product_no,
        "page": page,
        "pageSize": 100,
        "sort": "WRITE_DATE_DESC",
    }
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to

    for version in ["v2", "v1"]:
        url = f"{BASE_URL}/external/{version}/contents/reviews"
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.ok:
            return resp.json()
        if resp.status_code == 404:
            continue
        # 404가 아닌 에러는 상세 로그 후 종료
        print(f"  [리뷰 API {version} 오류] {resp.status_code}: {resp.text[:300]}")
        return {}

    return {}


def get_reviews(
    client_id: str,
    client_secret: str,
    product_no: str,
    date_from: str = None,
    date_to: str = None,
) -> list:
    """
    상품번호로 리뷰 전체 수집.
    date_from / date_to: "YYYY-MM-DD" 형식 (None이면 전체 조회)
    반환값: [{"star": 5, "content": "...", "date": "2024-01-15", "option": "..."}, ...]
    """
    token = _get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"  → 리뷰 조회 중 (상품번호: {product_no}"
          + (f", {date_from} ~ {date_to}" if date_from else "") + ")")

    all_reviews = []
    page = 1
    total_elements = None

    while True:
        data = _fetch_reviews_page(headers, product_no, page, date_from, date_to)
        if not data:
            break

        contents = data.get("contents", [])
        if total_elements is None:
            total_elements = data.get("totalElements", 0)

        for item in contents:
            star = (item.get("starScore")
                    or item.get("rating")
                    or item.get("starRating") or 0)
            content = (item.get("reviewContent")
                       or item.get("content")
                       or item.get("reviewText") or "")
            write_date = (item.get("writeDate")
                          or item.get("createdAt")
                          or item.get("registeredDate") or "")
            option = (item.get("optionContent")
                      or item.get("productOptionContent")
                      or item.get("option") or "")

            # 날짜를 YYYY-MM-DD 로 통일
            if write_date and "T" in str(write_date):
                write_date = str(write_date)[:10]
            elif write_date:
                write_date = str(write_date)[:10]

            # date_from 이전 리뷰가 나오면 수집 종료 (시간 역순 정렬 기준)
            if date_from and write_date and write_date < date_from:
                print(f"  → {write_date} 이전 리뷰 도달 → 수집 종료")
                return all_reviews

            all_reviews.append({
                "star": int(star) if star else 0,
                "content": str(content).strip(),
                "date": write_date,
                "option": str(option).strip(),
            })

        if not contents:
            break

        # 전체 수 대비 완료 여부 확인
        if total_elements and len(all_reviews) >= total_elements:
            break

        # hasNext 필드 확인 (API가 제공하는 경우)
        if not data.get("hasNext", True) and len(contents) < 100:
            break

        page += 1

    print(f"  → 리뷰 {len(all_reviews)}개 수집 완료 (전체 {total_elements}건 중)")
    return all_reviews
