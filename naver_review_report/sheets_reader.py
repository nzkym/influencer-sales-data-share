"""
구글 시트 '제품별링크' 탭에서 제품 목록 읽기.

시트 구조 (gid=1008723222 또는 '제품별링크' 탭):
  A열: 상품명 (참고용, 정확하지 않을 수 있음)
  B열: 네이버 스토어 링크 → 끝 숫자 = 상품번호
  D열: 스토어명 (nutone / jdhealth / nutpet 등)
"""

import re
import os
import gspread
from google.oauth2.service_account import Credentials

REVIEW_SHEET_ID = os.getenv(
    "REVIEW_PRODUCT_SHEET_ID",
    "17fFgSgzxaS72rwM003XbpCOlrWE2eAny_7zRlZj2AR8",
)

PRODUCT_TAB_GID = os.getenv("PRODUCT_TAB_GID", "1008723222")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 시트에 적힌 스토어명 → .env 키 이름 매핑
# D열 값이 이 중 하나이거나 포함되면 해당 스토어로 처리
STORE_ALIAS = {
    "nutone":   "nutone",
    "뉴트원":   "nutone",
    "jdhealth": "jdhealth",
    "정담건강": "jdhealth",
    "jd":       "jdhealth",
    "nutpet":   "nutpet",
    "뉴트펫":   "nutpet",
}


def _detect_store_from_url(url: str) -> str:
    """URL에서 스토어명 추출 (D열이 비어있을 때 fallback)."""
    url_lower = url.lower()
    for alias, store in STORE_ALIAS.items():
        if alias in url_lower:
            return store
    return ""


def _normalize_store(raw: str) -> str:
    """D열 스토어명 문자열을 .env 키 이름으로 변환."""
    raw = raw.strip().lower()
    # 정확 매칭
    if raw in STORE_ALIAS:
        return STORE_ALIAS[raw]
    # 부분 매칭
    for alias, store in STORE_ALIAS.items():
        if alias in raw:
            return store
    return raw  # 매칭 안 되면 그대로 반환 (경고는 main에서 처리)


def _product_no_from_url(url: str) -> str:
    """URL 끝 숫자 = 상품번호 추출."""
    match = re.search(r"/products/(\d+)", url)
    if match:
        return match.group(1)
    # fallback: URL의 마지막 7자리+ 숫자
    match = re.search(r"(\d{7,})", url)
    return match.group(1) if match else ""


def load_products(credentials_path: str) -> list:
    """
    '제품별링크' 탭에서 제품 목록 로드.
    반환값: [
        {
            "name":       "상품명",
            "product_no": "12345678",
            "store":      "nutone",   # .env 키 이름
            "url":        "https://...",
        }, ...
    ]
    """
    try:
        creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
        gc = gspread.authorize(creds)
        ss = gc.open_by_key(REVIEW_SHEET_ID)
    except Exception as e:
        print(f"[오류] 구글 시트 연결 실패: {e}")
        return []

    # 탭 찾기: gid 우선 → '제품별링크' 이름 순으로 시도
    ws = None
    for sheet in ss.worksheets():
        if str(sheet.id) == str(PRODUCT_TAB_GID):
            ws = sheet
            break
    if ws is None:
        for sheet in ss.worksheets():
            if "제품별링크" in sheet.title:
                ws = sheet
                break
    if ws is None:
        print(f"[오류] 제품별링크 탭을 찾을 수 없습니다 (gid={PRODUCT_TAB_GID})")
        return []

    print(f"  → '{ws.title}' 탭에서 제품 목록 읽는 중...")
    rows = ws.get_all_values()

    products = []
    # 첫 행이 헤더인지 확인 (A1이 "상품명" 같은 텍스트면 스킵)
    start_row = 0
    if rows and rows[0] and not rows[0][1].startswith("http"):
        start_row = 1

    for row in rows[start_row:]:
        # A열: 상품명, B열: URL, D열: 스토어명
        name = str(row[0]).strip() if len(row) > 0 else ""
        url  = str(row[1]).strip() if len(row) > 1 else ""
        store_raw = str(row[3]).strip() if len(row) > 3 else ""

        if not url or not url.startswith("http"):
            continue

        product_no = _product_no_from_url(url)
        if not product_no:
            print(f"  [경고] 상품번호 추출 실패: {url}")
            continue

        # 스토어명: D열 우선, 없으면 URL에서 추출
        if store_raw:
            store = _normalize_store(store_raw)
        else:
            store = _detect_store_from_url(url)

        if not store:
            print(f"  [경고] 스토어 판별 불가 (D열='{store_raw}', url={url[:60]})")
            continue

        products.append({
            "name":       name or f"상품{product_no}",
            "product_no": product_no,
            "store":      store,
            "url":        url,
        })

    print(f"  → 제품 {len(products)}개 로드 완료")
    return products
