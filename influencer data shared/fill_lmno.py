# -*- coding: utf-8 -*-
"""
캠페인 실적 탭의 기존 행 L~O열 일괄 채우기 (1회 실행용)
- L: 네이버 API 매출 조회 (L이 비어있는 행만)
- M: 시트1 J열 공구수수료
- N: 이익 수식
- O: 이익률 수식
"""
import re, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

sys.path.insert(0, str(BASE_DIR))
import naver_api
import sheets as sheets_mod

MASTER_SHEET_URL  = os.getenv("MASTER_SHEET_URL")
CREDENTIALS_PATH  = str(BASE_DIR / "credentials" / "google-credentials.json")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

STORE_CREDENTIALS = {
    "nutone":   (os.getenv("NUTONE_CLIENT_ID"),   os.getenv("NUTONE_CLIENT_SECRET")),
    "jdhealth": (os.getenv("JDHEALTH_CLIENT_ID"), os.getenv("JDHEALTH_CLIENT_SECRET")),
    "nutpet":   (os.getenv("NUTPET_CLIENT_ID"),   os.getenv("NUTPET_CLIENT_SECRET")),
}

def get_gs_client():
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)

def extract_product_no(url):
    m = re.search(r"/products/(\d+)", url)
    return m.group(1) if m else ""

def extract_commission(raw):
    m = re.search(r"(\d+(?:\.\d+)?)", str(raw))
    return float(m.group(1)) if m else ""

def main():
    KST = timezone(timedelta(hours=9))
    today_str = datetime.now(KST).date().strftime("%Y-%m-%d")

    sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
    client = get_gs_client()
    ss = client.open_by_key(sid)

    # ── 이익계산참고사항 로드 ──────────────────────────────────
    profit_params = sheets_mod.read_profit_params(MASTER_SHEET_URL)
    print(f"이익계산참고사항: {len(profit_params)}개 제품 로드")

    # ── 시트1 J열(공구수수료) 로드 ────────────────────────────
    ws1 = ss.sheet1
    master1_rows = ws1.get_all_records()
    comm_map = {}   # {제목: 공구수수료 float}
    for row in master1_rows:
        t = str(row.get("제목", "")).strip()
        if t:
            comm_map[t] = extract_commission(row.get("공구수수료(%,vat포함)", ""))
    print(f"시트1 공구수수료 {len(comm_map)}개 로드")

    # ── 캠페인 실적 탭 읽기 ───────────────────────────────────
    ws_camp = ss.worksheet("캠페인 실적")
    all_vals = ws_camp.get_all_values()
    if not all_vals:
        print("캠페인 실적 탭이 비어있습니다.")
        return

    header = all_vals[0]
    print(f"헤더: {header[:15]}")

    # 열 인덱스 확인
    col = {h: i for i, h in enumerate(header)}
    print(f"열 인덱스: {col}")

    data_rows = all_vals[1:]  # 헤더 제외

    # ── 각 행 처리 ────────────────────────────────────────────
    updates = []   # (sheet_row_number, L_val, M_val, N_formula, O_formula)

    for r_idx, row in enumerate(data_rows, start=2):  # 시트 행 번호 (1=헤더, 2=첫 데이터)
        if not row or not row[0]:   # No 열이 비면 스킵
            continue

        title       = row[col.get("제목", 1)]         if len(row) > col.get("제목", 1) else ""
        product_name= row[col.get("제품명", 2)]       if len(row) > col.get("제품명", 2) else ""
        store       = row[col.get("스토어", 3)]       if len(row) > col.get("스토어", 3) else ""
        date_from   = row[col.get("시작일", 4)]       if len(row) > col.get("시작일", 4) else ""
        date_to     = row[col.get("종료일", 5)]       if len(row) > col.get("종료일", 5) else ""
        g_orders    = row[col.get("주문수", 6)]       if len(row) > col.get("주문수", 6) else "0"
        h_products  = row[col.get("제품수", 7)]       if len(row) > col.get("제품수", 7) else "0"
        naver_url   = row[col.get("진행링크", 9)]     if len(row) > col.get("진행링크", 9) else ""
        l_revenue   = row[col.get("매출", 11)]        if len(row) > col.get("매출", 11) else ""

        if not title or not date_from or not date_to:
            continue

        # L이 이미 있으면 스킵
        l_clean = str(l_revenue).replace(",", "").strip()
        if l_clean and l_clean.lstrip("-").isdigit() and int(l_clean) > 0:
            print(f"  [{r_idx}] {title[:30]}: L열 이미 있음 ({l_clean}원) → 스킵")
            continue

        is_ended = date_to < today_str
        if not is_ended:
            print(f"  [{r_idx}] {title[:30]}: 진행중 → 스킵")
            continue

        product_no = extract_product_no(naver_url)
        if not product_no:
            print(f"  [{r_idx}] {title[:30]}: 상품번호 없음 → 스킵")
            continue

        store_lower = store.strip().lower()
        creds = STORE_CREDENTIALS.get(store_lower)
        if not creds or not creds[0]:
            print(f"  [{r_idx}] {title[:30]}: 스토어 '{store}' 인증정보 없음 → 스킵")
            continue

        # ── L열 매출 API 조회 ─────────────────────────────
        print(f"  [{r_idx}] {title[:30]}: 매출 조회 중...")
        try:
            revenue = naver_api.get_campaign_revenue(
                creds[0], creds[1], product_no, date_from, date_to
            )
        except Exception as e:
            print(f"       오류: {e}")
            revenue = ""

        # ── M열 공구수수료 ───────────────────────────────
        commission = comm_map.get(title, "")

        # ── N열 이익 수식 ────────────────────────────────
        pp = sheets_mod._match_profit(product_name or title, profit_params)
        n_formula = ""
        if pp and isinstance(revenue, int) and revenue > 0 and commission != "":
            cost     = int(pp.get("cost", 0) or 0)
            ch_comm  = float(pp.get("channel_comm", 0) or 0)
            delivery = int(pp.get("delivery", 3000) or 3000)
            if cost > 0:
                n_formula = (
                    f"=L{r_idx}"
                    f"-({cost}*H{r_idx})"
                    f"-(L{r_idx}*(M{r_idx}/100))"
                    f"-(L{r_idx}*{ch_comm})"
                    f"-({delivery}*G{r_idx})"
                )

        # ── O열 이익률 수식 ──────────────────────────────
        o_formula = f"=IFERROR(N{r_idx}/L{r_idx},\"\")" if n_formula else ""

        updates.append((r_idx, revenue, commission, n_formula, o_formula))
        rev_str = f"{revenue:,}원" if isinstance(revenue, int) else str(revenue)
        print(f"       매출={rev_str}, 수수료={commission}%, N={'수식' if n_formula else '없음'}")

    # ── 시트에 일괄 반영 ──────────────────────────────────────
    if not updates:
        print("\n채울 항목이 없습니다.")
        return

    print(f"\n총 {len(updates)}개 행 업데이트 중...")
    for r_idx, revenue, commission, n_formula, o_formula in updates:
        # L~O 4개 셀을 USER_ENTERED 모드로 한 번에 업데이트
        ws_camp.update(
            f"L{r_idx}:O{r_idx}",
            [[
                revenue    if revenue    != "" else "",
                commission if commission != "" else "",
                n_formula,
                o_formula,
            ]],
            value_input_option="USER_ENTERED",
        )
        print(f"  행{r_idx} 완료")

    # ── O열 이익률 % 서식 ────────────────────────────────────
    try:
        requests_body = {"requests": []}
        sheet_id = ws_camp.id
        # O열 전체 데이터 행에 % 서식 적용
        requests_body["requests"].append({"repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1, "endRowIndex": len(data_rows) + 1,
                "startColumnIndex": 14, "endColumnIndex": 15,  # O열
            },
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "PERCENT", "pattern": "0.00%"},
            }},
            "fields": "userEnteredFormat(numberFormat)",
        }})
        # L, N열 숫자 서식 (#,##0)
        for col_idx in [11, 13]:  # L=11, N=13
            requests_body["requests"].append({"repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1, "endRowIndex": len(data_rows) + 1,
                    "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1,
                },
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                }},
                "fields": "userEnteredFormat(numberFormat)",
            }})
        ss.batch_update(requests_body)
        print("서식 적용 완료")
    except Exception as e:
        print(f"서식 적용 실패 (데이터는 정상 기재됨): {e}")

    print("\n완료!")

if __name__ == "__main__":
    main()
