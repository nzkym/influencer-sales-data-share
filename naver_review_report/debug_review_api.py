"""
리뷰 API 엔드포인트 탐색 스크립트 (v2)
서버에서 실행: python3 debug_review_api.py

v1 결과: /v1·v2/contents/reviews 모두 404 → 경로 자체가 다름
→ 더 넓은 범위로 경로 탐색
"""

import os, json, bcrypt, base64, time, requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://api.commerce.naver.com"

TEST_STORE   = "jdhealth"
TEST_PRODUCT = "339102001"   # 구글시트 URL 기반 번호
TEST_CHANNEL = "4877974075"  # 스마트스토어 UI에서 확인된 채널상품번호

STORE_CREDS = {
    "nutone":   (os.getenv("NUTONE_CLIENT_ID"),   os.getenv("NUTONE_CLIENT_SECRET")),
    "jdhealth": (os.getenv("JDHEALTH_CLIENT_ID"), os.getenv("JDHEALTH_CLIENT_SECRET")),
    "nutpet":   (os.getenv("NUTPET_CLIENT_ID"),   os.getenv("NUTPET_CLIENT_SECRET")),
}


def get_token(client_id, client_secret):
    ts = str(int(time.time() * 1000))
    pw = f"{client_id}_{ts}"
    hashed = bcrypt.hashpw(pw.encode(), client_secret.encode())
    sign = base64.standard_b64encode(hashed).decode()
    r = requests.post(f"{BASE_URL}/external/v1/oauth2/token", data={
        "client_id": client_id, "timestamp": ts,
        "client_secret_sign": sign,
        "grant_type": "client_credentials", "type": "SELF",
    }, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def probe(headers, url, params, label):
    r = requests.get(url, headers=headers, params=params, timeout=15)
    status = r.status_code
    try:
        data = r.json()
        code = data.get("code", "") if isinstance(data, dict) else ""
        contents = data.get("contents", data.get("data", [])) if isinstance(data, dict) else []
        cnt = len(contents) if isinstance(contents, list) else "?"
        keys = list(data.keys()) if isinstance(data, dict) else []
        result = f"[{status}] {code or '성공'} | contents={cnt} | keys={keys}"
        if status == 200 and contents:
            first = contents[0] if isinstance(contents, list) else contents
            result += f"\n        첫 항목 키: {list(first.keys()) if isinstance(first, dict) else first}"
    except Exception:
        result = f"[{status}] {r.text[:120]}"
    print(f"  {'✅' if status==200 else '❌'} {label}")
    print(f"     {result}")


def main():
    cid, csecret = STORE_CREDS[TEST_STORE]
    if not cid:
        print(f"[오류] {TEST_STORE} API 키가 .env에 없습니다.")
        return

    print("토큰 발급 중...")
    token = get_token(cid, csecret)
    headers = {"Authorization": f"Bearer {token}"}
    print("토큰 발급 완료\n")

    pno  = TEST_PRODUCT   # 339102001
    cpno = TEST_CHANNEL   # 4877974075

    print("=" * 55)
    print(f"원상품번호: {pno}  /  채널상품번호: {cpno}")
    print("=" * 55)

    # ── 그룹 A: contents 계열 ─────────────────────────────
    print("\n[A] contents 계열 경로")
    for ver in ["v1", "v2"]:
        for path in [
            "contents/reviews",
            "contents/product-reviews",
            "contents/channel-product-reviews",
            "contents/seller-reviews",
        ]:
            probe(headers, f"{BASE_URL}/external/{ver}/{path}",
                  {"page": 1, "pageSize": 3}, f"{ver}/{path}")

    # ── 그룹 B: seller 계열 ──────────────────────────────
    print("\n[B] seller 계열 경로")
    for ver in ["v1", "v2"]:
        for path in [
            "seller/reviews",
            "seller/product-reviews",
        ]:
            probe(headers, f"{BASE_URL}/external/{ver}/{path}",
                  {"page": 1, "pageSize": 3}, f"{ver}/{path}")

    # ── 그룹 C: products 계열 (상품번호 포함) ────────────
    print("\n[C] products 계열 — 원상품번호로")
    for ver in ["v1", "v2"]:
        for path in [
            f"products/{pno}/reviews",
            f"channel-products/{pno}/reviews",
        ]:
            probe(headers, f"{BASE_URL}/external/{ver}/{path}",
                  {"page": 1, "pageSize": 3}, f"{ver}/{path}")

    # ── 그룹 D: products 계열 (채널상품번호) ─────────────
    print("\n[D] products 계열 — 채널상품번호로")
    for ver in ["v1", "v2"]:
        for path in [
            f"products/{cpno}/reviews",
            f"channel-products/{cpno}/reviews",
        ]:
            probe(headers, f"{BASE_URL}/external/{ver}/{path}",
                  {"page": 1, "pageSize": 3}, f"{ver}/{path}")

    # ── 그룹 E: 채널상품번호로 파라미터 방식 ─────────────
    print("\n[E] 파라미터 방식 — 채널상품번호 사용")
    for ver in ["v1", "v2"]:
        for path in ["contents/reviews", "reviews"]:
            for param_name in ["channelProductId", "productId", "originProductNo"]:
                probe(headers, f"{BASE_URL}/external/{ver}/{path}",
                      {param_name: cpno, "page": 1, "pageSize": 3},
                      f"{ver}/{path} [{param_name}={cpno}]")

    # ── 그룹 F: 아예 다른 경로 ───────────────────────────
    print("\n[F] 기타 경로")
    for ver in ["v1", "v2"]:
        for path in [
            "reviews",
            "product-reviews",
            "shopping/reviews",
        ]:
            probe(headers, f"{BASE_URL}/external/{ver}/{path}",
                  {"page": 1, "pageSize": 3}, f"{ver}/{path}")

    print("\n" + "=" * 55)
    print("탐색 완료. ✅ 표시된 항목을 캡처해서 공유해주세요.")


if __name__ == "__main__":
    main()
