"""
리뷰 API 진단 스크립트
서버에서 실행: python3 debug_review_api.py

실제 API 응답 구조를 출력해서 올바른 파라미터명과 필드명을 확인합니다.
"""

import os, json, bcrypt, base64, time, requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://api.commerce.naver.com"

# 진단할 스토어 + 상품번호 (sheets_reader 없이 직접 지정)
# 스크린샷에서 보인 첫 번째 상품번호로 테스트
TEST_STORE   = "jdhealth"
TEST_PRODUCT = "339102001"   # 뉴트키즈타민 상품번호

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
    print(f"\n{'─'*50}")
    print(f"테스트: {label}")
    print(f"URL   : {url}")
    print(f"Params: {json.dumps(params, ensure_ascii=False)}")
    r = requests.get(url, headers=headers, params=params, timeout=30)
    print(f"Status: {r.status_code}")
    try:
        data = r.json()
        # 최상위 키 출력
        print(f"응답 최상위 키: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        if isinstance(data, dict):
            for k, v in data.items():
                if k != "contents":
                    print(f"  {k}: {v}")
            contents = data.get("contents", [])
            print(f"  contents 개수: {len(contents)}")
            if contents:
                print(f"  첫 번째 리뷰 키: {list(contents[0].keys())}")
                print(f"  첫 번째 리뷰 샘플:")
                for fk, fv in contents[0].items():
                    val = str(fv)[:80]
                    print(f"    {fk}: {val}")
    except Exception as e:
        print(f"JSON 파싱 실패: {e}")
        print(f"응답 본문: {r.text[:500]}")


def main():
    cid, csecret = STORE_CREDS[TEST_STORE]
    if not cid:
        print(f"[오류] {TEST_STORE} API 키가 .env에 없습니다.")
        return

    print(f"토큰 발급 중 ({TEST_STORE})...")
    token = get_token(cid, csecret)
    headers = {"Authorization": f"Bearer {token}"}
    print("토큰 발급 완료")

    # ── 테스트 1: 필터 없이 v2 전체 조회 (어떤 데이터가 오는지 확인) ──
    probe(headers,
          f"{BASE_URL}/external/v2/contents/reviews",
          {"page": 1, "pageSize": 3},
          "v2 / 필터 없음")

    # ── 테스트 2: v1 필터 없이 ─────────────────────────────────────────
    probe(headers,
          f"{BASE_URL}/external/v1/contents/reviews",
          {"page": 1, "pageSize": 3},
          "v1 / 필터 없음")

    # ── 테스트 3: productId 파라미터 ──────────────────────────────────
    probe(headers,
          f"{BASE_URL}/external/v2/contents/reviews",
          {"productId": TEST_PRODUCT, "page": 1, "pageSize": 3},
          f"v2 / productId={TEST_PRODUCT}")

    # ── 테스트 4: channelProductId 파라미터 ───────────────────────────
    probe(headers,
          f"{BASE_URL}/external/v2/contents/reviews",
          {"channelProductId": TEST_PRODUCT, "page": 1, "pageSize": 3},
          f"v2 / channelProductId={TEST_PRODUCT}")

    # ── 테스트 5: originProductNo 파라미터 ────────────────────────────
    probe(headers,
          f"{BASE_URL}/external/v2/contents/reviews",
          {"originProductNo": TEST_PRODUCT, "page": 1, "pageSize": 3},
          f"v2 / originProductNo={TEST_PRODUCT}")

    # ── 테스트 6: v2 다른 엔드포인트 시도 ────────────────────────────
    probe(headers,
          f"{BASE_URL}/external/v2/contents/channel-product-reviews",
          {"channelProductId": TEST_PRODUCT, "page": 1, "pageSize": 3},
          f"v2 / channel-product-reviews / channelProductId")

    print(f"\n{'='*50}")
    print("진단 완료. 위 결과를 복사해서 공유해주세요.")


if __name__ == "__main__":
    main()
