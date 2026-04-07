"""
네이버 커머스 API 클라이언트
- 인증: bcrypt 서명 → Bearer 토큰
- 주문 데이터를 상품번호 + 기간으로 조회 (하루씩 분할, 최대 24시간 제한)
- 2단계 조회: GET(날짜범위) → POST query(옵션명 보완)
"""

import bcrypt
import base64
import time
import requests
from datetime import datetime, timedelta


BASE_URL = "https://api.commerce.naver.com"

# 실제 판매로 집계할 주문 상태 (취소/반품 제외)
SALE_STATUSES = {"PAYED", "DELIVERING", "DELIVERED", "PURCHASE_DECIDED"}


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


def _query_one_day(headers: dict, from_str: str, to_str: str) -> list:
    """하루치 주문 전체 수집 (페이지네이션 처리)"""
    all_items = []
    page = 1
    while True:
        url = (
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders"
            f"?from={from_str}&to={to_str}"
            f"&rangeType=PAYED_DATETIME&pageSize=100&page={page}"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"  [오류] {resp.status_code}: {resp.text[:200]}")
            break
        data = resp.json().get("data", {})
        contents = data.get("contents", [])
        all_items.extend(contents)
        if not data.get("pagination", {}).get("hasNext", False):
            break
        page += 1
    return all_items


def _get_option_names(headers: dict, order_ids: list) -> dict:
    """productOrderIds 배열로 옵션명 조회 (query 엔드포인트)"""
    if not order_ids:
        return {}
    resp = requests.post(
        f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
        headers={**headers, "Content-Type": "application/json"},
        json={"productOrderIds": order_ids},
        timeout=30,
    )
    if resp.status_code != 200:
        return {}
    result = {}
    for order in resp.json().get("data", []):
        po = order.get("productOrder", {})
        oid = str(po.get("productOrderId", ""))
        option = po.get("productOption") or ""
        result[oid] = option
    return result


def get_sales_data(
    client_id: str,
    client_secret: str,
    product_no: str,
    date_from: str,  # "2026-04-06"
    date_to: str,    # "2026-04-10"
) -> list:
    """
    상품번호와 기간에 해당하는 판매 데이터를 반환합니다.
    반환값: [{"date": "2026-04-06", "option": "선택:3BOX(30%)", "quantity": 3}, ...]
    """
    token = _get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"  → 네이버 API 조회 중 (상품번호: {product_no}, 기간: {date_from} ~ {date_to})")

    current = datetime.strptime(date_from, "%Y-%m-%d")
    end = datetime.strptime(date_to, "%Y-%m-%d")
    today = datetime.now()

    result = []
    while current <= min(end, today):
        next_day = current + timedelta(days=1)
        from_str = current.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        to_str = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

        # 1단계: 날짜범위로 전체 주문 조회
        day_items = _query_one_day(headers, from_str, to_str)

        # 2단계: 해당 상품 + 정상상태 필터링 → 주문ID 수집
        matched = []
        for item in day_items:
            content = item.get("content", {})
            po = content.get("productOrder", {})
            order = content.get("order", {})
            if str(po.get("productId", "")) != str(product_no):
                continue
            if po.get("productOrderStatus", "") not in SALE_STATUSES:
                continue
            matched.append({
                "order_id": str(item.get("productOrderId", "")),
                "date": (order.get("paymentDate") or "")[:10],
                "quantity": int(po.get("quantity") or 1),
            })

        # 3단계: query 엔드포인트로 옵션명 보완
        order_ids = [m["order_id"] for m in matched]
        option_map = _get_option_names(headers, order_ids)

        for m in matched:
            option = option_map.get(m["order_id"]) or "기본 옵션"
            result.append({
                "date": m["date"],
                "option": option,
                "quantity": m["quantity"],
            })

        print(f"  → {current.strftime('%Y-%m-%d')}: {len(matched)}건")
        current = next_day

    print(f"  → 합계 {len(result)}건 수집 완료")
    return result
