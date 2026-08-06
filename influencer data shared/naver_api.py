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


def get_product_name(client_id: str, client_secret: str, product_no: str) -> str:
    """네이버 커머스 API로 상품명 조회."""
    try:
        token = _get_access_token(client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(
            f"{BASE_URL}/external/v1/products/channel-products/{product_no}",
            headers=headers,
            timeout=10,
        )
        if resp.ok:
            data = resp.json()
            print(f"  [상품API] 응답 키: {list(data.keys())}")
            return (data.get("channelProductName")
                    or data.get("name")
                    or data.get("originProductName")
                    or data.get("productName") or "")
        else:
            print(f"  [상품API] 실패: {resp.status_code} {resp.text[:100]}")
    except Exception as e:
        print(f"  [상품API] 오류: {e}")
    return ""


def get_product_option_prices(client_id: str, client_secret: str, product_no: str) -> dict:
    """네이버 커머스 API로 상품 옵션별 가격 조회.
    반환: {"선택: 3BOX(30%)": 33300, "선택: 6BOX(50%)": 55500, ...}
    """
    try:
        token = _get_access_token(client_id, client_secret)
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(
            f"{BASE_URL}/external/v1/products/channel-products/{product_no}",
            headers=headers,
            timeout=10,
        )
        if not resp.ok:
            print(f"  [옵션가격] API 실패: {resp.status_code}")
            return {}
        data = resp.json()
        result = {}

        # originProduct.optionInfo.optionCombinations
        origin = data.get("originProduct") or data
        opt_info = origin.get("optionInfo") or {}
        combos = opt_info.get("optionCombinations") or []
        base_price = int(origin.get("salePrice") or data.get("salePrice") or 0)

        for combo in combos:
            names = []
            for k in ["optionName1", "optionName2", "optionName3"]:
                v = combo.get(k, "")
                if v:
                    names.append(v)
            opt_name = " / ".join(names) if names else ""
            price = int(combo.get("price") or 0)
            if price == 0:
                price = base_price
            if opt_name:
                result[opt_name] = price

        # supplementProducts (추가상품)
        for sp in (origin.get("supplementProducts") or []):
            sp_name = sp.get("name") or sp.get("productName") or ""
            sp_price = int(sp.get("price") or 0)
            if sp_name and sp_price:
                result[sp_name] = sp_price

        if result:
            print(f"  [옵션가격] {len(result)}개 옵션: {result}")
        else:
            print(f"  [옵션가격] 옵션 없음 (단일상품, 기본가: {base_price})")
            if base_price:
                result["기본"] = base_price
        return result
    except Exception as e:
        print(f"  [옵션가격] 오류: {e}")
        return {}

# 실제 판매로 집계할 주문 상태 (취소/반품 제외)
SALE_STATUSES = {"PAYED", "DELIVERING", "DELIVERED", "PURCHASE_DECIDED"}

# 주문 상태 한글 변환 (파마브로스 엑셀용)
ORDER_STATUS_KO = {
    "PAYMENT_WAITING":           "결제대기",
    "PAYED":                     "결제완료",
    "DELIVERING":                "배송중",
    "DELIVERED":                 "배송완료",
    "PURCHASE_DECIDED":          "구매확정",
    "EXCHANGED":                 "교환완료",
    "CANCEL_DONE":               "취소완료",
    "RETURN_DONE":               "반품완료",
    "PURCHASE_DECISION_HOLDBACK":"구매확정보류",
    "DELIVERING_RETURNED":       "반송중",
    "COLLECT_WAITED":            "수거대기",
    "CANCEL_REQUESTED":          "취소요청",
    "RETURN_INITIATED":          "반품신청",
}


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
    if not resp.ok:
        print(f"  [인증 오류] status={resp.status_code}, body={resp.text[:300]}")
        print(f"  [인증 오류] client_id 길이={len(client_id)}, secret 길이={len(client_secret)}")
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


def _get_option_names(headers: dict, order_ids: list) -> tuple:
    """productOrderIds 배열로 옵션명 + 상품명 + 단가 조회.
    반환: (option_map, product_name, price_map)
      option_map = {orderId: 옵션명}
      price_map  = {orderId: unitPrice(int)}
    """
    if not order_ids:
        return {}, "", {}
    result = {}
    product_name = ""
    price_map = {}
    for start in range(0, len(order_ids), 100):
        chunk = order_ids[start:start + 100]
        resp = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers={**headers, "Content-Type": "application/json"},
            json={"productOrderIds": chunk},
            timeout=30,
        )
        if resp.status_code != 200:
            continue
        for order in resp.json().get("data", []):
            po = order.get("productOrder", {})
            oid = str(po.get("productOrderId", ""))
            option = po.get("productOption") or ""
            result[oid] = option
            pay_amt = int(po.get("totalPaymentAmount") or po.get("productAmount") or 0)
            qty_val = int(po.get("quantity") or 1)
            price_map[oid] = pay_amt // qty_val if qty_val else pay_amt
            if not product_name:
                product_name = po.get("productName") or ""
    return result, product_name, price_map


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
    product_name = ""
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
            pay_dt = order.get("paymentDate") or ""
            hour = int(pay_dt[11:13]) if len(pay_dt) >= 13 and pay_dt[10:11] == "T" else 0
            matched.append({
                "order_id": str(item.get("productOrderId", "")),
                "date": pay_dt[:10],
                "hour": hour,
                "quantity": int(po.get("quantity") or 1),
            })

        # 3단계: query 엔드포인트로 옵션명 + 상품명 보완
        order_ids = [m["order_id"] for m in matched]
        option_map, pname, _ = _get_option_names(headers, order_ids)
        if pname and not product_name:
            product_name = pname

        for m in matched:
            option = option_map.get(m["order_id"]) or "기본 옵션"
            result.append({
                "date":     m["date"],
                "hour":     m["hour"],
                "option":   option,
                "quantity": m["quantity"],
            })

        print(f"  → {current.strftime('%Y-%m-%d')}: {len(matched)}건")
        current = next_day

    print(f"  → 합계 {len(result)}건 수집 완료")
    return result, product_name


def get_campaign_revenue(
    client_id: str,
    client_secret: str,
    product_no: str,
    date_from: str,
    date_to: str,
) -> int:
    """
    캠페인 기간 동안 특정 상품의 총 매출(결제금액) 반환.
    취소·반품 제외(SALE_STATUSES 기준). 반환값: 원 단위 정수.
    """
    token = _get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"  → [매출조회] 상품번호: {product_no}, 기간: {date_from} ~ {date_to}")

    current = datetime.strptime(date_from, "%Y-%m-%d")
    end     = datetime.strptime(date_to,   "%Y-%m-%d")
    today   = datetime.now()

    order_ids = []
    while current <= min(end, today):
        next_day = current + timedelta(days=1)
        from_str = current.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        to_str   = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

        day_items = _query_one_day(headers, from_str, to_str)
        for item in day_items:
            content = item.get("content", {})
            po      = content.get("productOrder", {})
            if str(po.get("productId", "")) != str(product_no):
                continue
            if po.get("productOrderStatus", "") not in SALE_STATUSES:
                continue
            oid = str(item.get("productOrderId", ""))
            if oid:
                order_ids.append(oid)
        current = next_day

    if not order_ids:
        print(f"  → [매출조회] 주문 없음 → 0원")
        return 0

    # 100개씩 나눠서 query 엔드포인트 호출 → 결제금액 합산
    total_revenue = 0
    for start in range(0, len(order_ids), 100):
        chunk = order_ids[start:start + 100]
        resp = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers={**headers, "Content-Type": "application/json"},
            json={"productOrderIds": chunk},
            timeout=30,
        )
        if not resp.ok:
            print(f"  → [매출조회] 쿼리 실패: {resp.status_code}")
            continue
        for order in resp.json().get("data", []):
            po = order.get("productOrder", {})
            # 결제금액 필드 우선순위 탐색
            amount = (
                po.get("totalPaymentAmount")
                or po.get("productAmount")
                or (int(po.get("unitPrice", 0) or 0)
                    * int(po.get("quantity", 1) or 1))
            )
            total_revenue += int(amount or 0)

    print(f"  → [매출조회] {len(order_ids)}건, 합계 {total_revenue:,}원")
    return total_revenue


def get_pharmabros_orders(
    client_id: str,
    client_secret: str,
    product_no: str,
    date_from: str,   # "2026-05-14"
    date_to: str,     # "2026-05-20"
) -> list:
    """
    파마브로스 파일공유용: 개별 주문 상세 목록 반환 (미집계 raw 데이터).
    반환값: [
        {
            "주문번호": "2026051400000001",
            "주문일시": "2026-05-14 14:23:11",
            "주문상태": "배송중",
            "옵션":     "선택: 3BOX(30%)",
            "주문수량": 2,
        }, ...
    ]
    ALL 상태 포함 (취소·반품 포함, 상태값 그대로 노출).
    """
    token = _get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"  → [파마브로스] 주문 상세 조회 (상품번호: {product_no}, 기간: {date_from} ~ {date_to})")

    current = datetime.strptime(date_from, "%Y-%m-%d")
    end     = datetime.strptime(date_to,   "%Y-%m-%d")
    today   = datetime.now()

    raw_orders = []   # {order_id, pay_dt, status, quantity}
    while current <= min(end, today):
        next_day = current + timedelta(days=1)
        from_str = current.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        to_str   = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

        day_items = _query_one_day(headers, from_str, to_str)
        matched = 0
        for item in day_items:
            content = item.get("content", {})
            po      = content.get("productOrder", {})
            order   = content.get("order", {})

            if str(po.get("productId", "")) != str(product_no):
                continue

            pay_dt = order.get("paymentDate") or ""
            # paymentDate 형식: "2026-05-14T14:23:11+09:00" → "2026-05-14 14:23:11"
            if "T" in pay_dt:
                pay_dt_clean = pay_dt[:19].replace("T", " ")
            else:
                pay_dt_clean = pay_dt[:19]

            raw_orders.append({
                "order_id": str(item.get("productOrderId", "")),
                "pay_dt":   pay_dt_clean,
                "status":   po.get("productOrderStatus", ""),
                "quantity": int(po.get("quantity") or 1),
            })
            matched += 1

        print(f"  → {current.strftime('%Y-%m-%d')}: {matched}건")
        current = next_day

    # 옵션명 + 단가 보완 (query 엔드포인트)
    order_ids = [r["order_id"] for r in raw_orders]
    option_map, _, price_map = _get_option_names(headers, order_ids)

    result = []
    for r in raw_orders:
        result.append({
            "주문번호": r["order_id"],
            "주문일시": r["pay_dt"],
            "주문상태": ORDER_STATUS_KO.get(r["status"], r["status"]),
            "옵션":     option_map.get(r["order_id"]) or "기본 옵션",
            "주문수량": r["quantity"],
            "단가":     price_map.get(r["order_id"], 0),
        })

    # 주문일시 오름차순 정렬
    result.sort(key=lambda x: x["주문일시"])
    print(f"  → [파마브로스] 합계 {len(result)}건 수집 완료")
    return result


# 사은품 추첨 시 제외할 취소/반품 상태
_CANCEL_STATUSES = {
    "CANCEL_DONE", "RETURN_DONE", "CANCEL_REQUESTED", "RETURN_INITIATED",
    "DELIVERING_RETURNED", "COLLECT_WAITED",
}


def get_saeunpum_orders(
    client_id: str,
    client_secret: str,
    product_no: str,
    date_from: str,
    date_to: str,
) -> tuple:
    """사은품 추첨용: 구매자 정보 포함 주문 목록 (취소·반품 제외).

    반환: (orders, product_name)
      orders = [
        {"주문번호": ..., "수령자명": ..., "전화번호": ..., "주소": ..., "옵션": ...},
        ...
      ]
    """
    token = _get_access_token(client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"  → [사은품] 주문 조회 (상품번호: {product_no}, {date_from} ~ {date_to})")

    current = datetime.strptime(date_from, "%Y-%m-%d")
    end     = datetime.strptime(date_to,   "%Y-%m-%d")
    today   = datetime.now()

    valid_ids = []
    while current <= min(end, today):
        next_day = current + timedelta(days=1)
        from_str = current.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        to_str   = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

        day_items = _query_one_day(headers, from_str, to_str)
        for item in day_items:
            content = item.get("content", {})
            po      = content.get("productOrder", {})
            if str(po.get("productId", "")) != str(product_no):
                continue
            if po.get("productOrderStatus", "") in _CANCEL_STATUSES:
                continue
            oid = str(item.get("productOrderId", ""))
            if oid:
                valid_ids.append(oid)
        current = next_day

    if not valid_ids:
        print(f"  → [사은품] 유효 주문 없음")
        return [], ""

    # query 엔드포인트 → 구매자 정보 포함 상세 조회
    orders = []
    product_name = ""
    for start in range(0, len(valid_ids), 100):
        chunk = valid_ids[start:start + 100]
        resp = requests.post(
            f"{BASE_URL}/external/v1/pay-order/seller/product-orders/query",
            headers={**headers, "Content-Type": "application/json"},
            json={"productOrderIds": chunk},
            timeout=30,
        )
        if not resp.ok:
            print(f"  → [사은품] query 실패: {resp.status_code}")
            continue
        for item in resp.json().get("data", []):
            po       = item.get("productOrder", {})
            order    = item.get("order", {})
            delivery = item.get("delivery", {})

            if not product_name:
                product_name = po.get("productName") or ""

            recv_name = (delivery.get("receiverName")
                         or order.get("ordererName") or "")
            recv_tel  = (delivery.get("receiverTel1")
                         or delivery.get("receiverTel")
                         or delivery.get("receiverMobile")
                         or order.get("ordererTel") or "")
            base_addr   = delivery.get("receiverBaseAddress") or ""
            detail_addr = delivery.get("receiverDetailAddress") or ""
            full_addr   = f"{base_addr} {detail_addr}".strip()
            option      = po.get("productOption") or "기본 옵션"
            order_id    = str(po.get("productOrderId", ""))

            orders.append({
                "주문번호": order_id,
                "수령자명": recv_name,
                "전화번호": recv_tel,
                "주소":     full_addr,
                "옵션":     option,
            })

    print(f"  → [사은품] {len(orders)}건 조회 완료 (상품명: {product_name[:30]})")
    return orders, product_name
