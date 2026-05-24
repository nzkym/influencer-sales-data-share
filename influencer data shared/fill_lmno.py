# -*- coding: utf-8 -*-
"""
캠페인 실적 탭의 기존 행 L~P열 일괄 채우기 (1회 실행용)
- L: 네이버 API 매출 조회 (L이 비어있는 행만)
- M: 공구수수료 시트에서 수수료 조회
- N: 이익 수식
- O: 이익률 수식
- P: 매칭된 원가 제품명 (확인용) ← L,M,N 기존 값 있어도 P가 비면 채움

수수료 매칭 우선순위:
  1순위: 채널명 정확 매칭
  2순위: 채널명 부분 매칭
  3순위: 상품번호 + 캠페인 월 매칭 (같은 링크라도 다른 인플루언서 구분)
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
    m = re.search(r"/products/(\d+)", str(url))
    return m.group(1) if m else ""

def extract_commission(raw):
    s = str(raw).strip()
    m = re.search(r'공구수수료\s*(\d+(?:\.\d+)?)\s*%', s)
    if m:
        return float(m.group(1))
    if '컨텐츠' not in s:
        m = re.search(r'(\d+(?:\.\d+)?)', s)
        if m:
            return float(m.group(1))
    return ""

def tab_year_month(tab_title: str) -> str:
    t = tab_title.strip()
    m = re.match(r'(\d{2})\.(\d{1,2})', t)
    if m:
        return f"20{m.group(1)}-{int(m.group(2)):02d}"
    m2 = re.match(r'(\d{1,2})\s*월', t)
    if m2:
        return f"2026-{int(m2.group(1)):02d}"
    return ""

def main():
    KST = timezone(timedelta(hours=9))
    today_str = datetime.now(KST).date().strftime("%Y-%m-%d")

    sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
    client = get_gs_client()
    ss = client.open_by_key(sid)

    # ── 이익계산참고사항 로드 ──────────────────────────────────
    profit_params = sheets_mod.read_profit_params(MASTER_SHEET_URL)
    print(f"이익계산참고사항: {len(profit_params)}개 제품 로드")

    # ── 공구수수료 시트 로드 ───────────────────────────────────
    COMM_SHEET_URL = "https://docs.google.com/spreadsheets/d/1tNYLgKB-rXcwF5ZdjxT3xti4ygvNl6SEJ-x0w8Gmjt0/edit"
    comm_sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", COMM_SHEET_URL).group(1)
    ss_comm = client.open_by_key(comm_sid)

    comm_map = {}
    comm_by_product_no = {}  # {product_no: [(commission, year_month)]}

    for ws_tab in ss_comm.worksheets():
        title_lower = ws_tab.title.lower()
        if not any(x in title_lower for x in ["월", ".1", ".2", ".3", ".4", ".5", ".6"]):
            continue
        ym = tab_year_month(ws_tab.title)
        try:
            tab_rows = ws_tab.get_all_values()
        except Exception:
            continue

        i = 0
        while i < len(tab_rows):
            row = tab_rows[i]
            b_col = row[1].strip() if len(row) > 1 else ""
            d_col = row[3].strip() if len(row) > 3 else ""

            if b_col == "공구" and d_col:
                channel = d_col
                j = i + 1
                while j < len(tab_rows):
                    nrow = tab_rows[j]
                    nb = nrow[1].strip() if len(nrow) > 1 else ""
                    if nb == "공구":
                        break
                    if nb == "공구링크":
                        m_val = nrow[12].strip() if len(nrow) > 12 else ""
                        comm = extract_commission(m_val)
                        if comm != "":
                            if channel:
                                comm_map[channel.lower()] = comm
                            for cell in nrow:
                                pno = extract_product_no(str(cell))
                                if pno and ym:
                                    if pno not in comm_by_product_no:
                                        comm_by_product_no[pno] = []
                                    comm_by_product_no[pno].append((comm, ym))
                    j += 1
                i = j
            else:
                i += 1

    print(f"공구수수료 시트: {len(comm_map)}개 채널, {len(comm_by_product_no)}개 상품번호 로드")
    print("채널 목록:")
    for k, v in comm_map.items():
        print(f"  '{k}' → {v}%")

    # ── 캠페인 실적 탭 읽기 ───────────────────────────────────
    ws_camp = ss.worksheet("캠페인 실적")
    all_vals = ws_camp.get_all_values()
    if not all_vals:
        print("캠페인 실적 탭이 비어있습니다.")
        return

    header = all_vals[0]
    col = {h: i for i, h in enumerate(header)}
    data_rows = all_vals[1:]

    # P열 헤더 확인/추가
    p1_val = str(header[15]).strip() if len(header) > 15 else ""
    if not p1_val:
        ws_camp.update(values=[["매칭원가제품명"]], range_name="P1")
        print("P1 헤더 추가")

    # ── 각 행 처리 ────────────────────────────────────────────
    # p_only=True: P열만 업데이트 (L~O는 이미 정상)
    # p_only=False: L~P 전체 업데이트
    updates = []

    for r_idx, row in enumerate(data_rows, start=2):
        if not row or not row[0]:
            continue

        title        = row[col.get("제목", 1)]        if len(row) > col.get("제목", 1)    else ""
        product_name = row[col.get("제품명", 2)]      if len(row) > col.get("제품명", 2)  else ""
        store        = row[col.get("스토어", 3)]      if len(row) > col.get("스토어", 3)  else ""
        date_from    = row[col.get("시작일", 4)]      if len(row) > col.get("시작일", 4)  else ""
        date_to      = row[col.get("종료일", 5)]      if len(row) > col.get("종료일", 5)  else ""
        naver_url    = row[col.get("진행링크", 9)]    if len(row) > col.get("진행링크", 9) else ""
        l_revenue    = row[col.get("매출", 11)]       if len(row) > col.get("매출", 11)   else ""

        col_m = col.get("공구수수료(vat포함)", 12)
        m_existing = row[col_m] if len(row) > col_m else ""
        m_clean = str(m_existing).strip()
        m_filled = bool(m_clean and m_clean.replace(".", "").isdigit())

        col_n = col.get("이익", 13)
        n_existing = row[col_n] if len(row) > col_n else ""
        n_filled = bool(str(n_existing).strip())

        # P열 기존값
        p_existing = row[15] if len(row) > 15 else ""
        p_filled = bool(str(p_existing).strip())

        if not title or not date_from or not date_to:
            continue

        l_clean = str(l_revenue).replace(",", "").strip()
        l_positive = l_clean and l_clean.lstrip("-").isdigit() and int(l_clean) > 0

        is_ended = date_to < today_str

        # ── Case 1: L,M,N,P 모두 있음 → 완전 스킵
        if l_positive and m_filled and n_filled and p_filled:
            print(f"  [{r_idx}] {title[:30]}: 모두 기재됨 → 스킵")
            continue

        # ── Case 2: L,M,N 있고 P만 비었음 → P만 채움
        if l_positive and m_filled and n_filled and not p_filled:
            pp = sheets_mod._match_profit(product_name or title, profit_params)
            matched_name = str(pp.get("name", "")) if pp else ""
            updates.append({"r_idx": r_idx, "p_only": True, "matched_name": matched_name})
            print(f"  [{r_idx}] {title[:30]}: P열만 채움 → '{matched_name}'")
            continue

        # ── Case 3: 진행중 → 스킵
        if not is_ended:
            print(f"  [{r_idx}] {title[:30]}: 진행중 → 스킵")
            continue

        # ── Case 4: L~P 전체 처리
        # revenue 결정
        if l_positive:
            revenue = int(l_clean)
            print(f"  [{r_idx}] {title[:30]}: L열 재사용({revenue:,}원)")
        else:
            product_no = extract_product_no(naver_url)
            if not product_no:
                print(f"  [{r_idx}] {title[:30]}: 상품번호 없음 → 스킵")
                continue
            store_lower = store.strip().lower()
            creds = STORE_CREDENTIALS.get(store_lower)
            if not creds or not creds[0]:
                print(f"  [{r_idx}] {title[:30]}: 스토어 '{store}' 인증정보 없음 → 스킵")
                continue
            print(f"  [{r_idx}] {title[:30]}: 매출 조회 중...")
            try:
                revenue = naver_api.get_campaign_revenue(
                    creds[0], creds[1], product_no, date_from, date_to
                )
            except Exception as e:
                print(f"       오류: {e}")
                revenue = ""

        # commission 결정
        if m_filled:
            commission = float(m_clean)
            print(f"       수수료: M열 기존값 재사용({commission}%)")
        else:
            commission = comm_map.get(title.lower(), "")
            if commission != "":
                print(f"       수수료: 채널명 정확매칭 → {commission}%")
            if commission == "" and title:
                t = title.lower()
                for key, val in comm_map.items():
                    if (len(t) >= 3 and t in key) or (len(key) >= 3 and key in t):
                        commission = val
                        print(f"       수수료: 채널명 부분매칭 '{title}' ↔ '{key}' → {commission}%")
                        break
            if commission == "":
                pno_url = extract_product_no(naver_url)
                campaign_ym = date_from[:7]
                if pno_url and pno_url in comm_by_product_no:
                    for c, ym in comm_by_product_no[pno_url]:
                        if ym == campaign_ym:
                            commission = c
                            print(f"       수수료: 상품번호+월 매칭 → {commission}%")
                            break
            if commission == "":
                print(f"       수수료: 매칭 실패 → 수동 확인 필요")

        # 원가 매칭 및 N 수식
        pp = sheets_mod._match_profit(product_name or title, profit_params)
        matched_name = str(pp.get("name", "")) if pp else ""
        if matched_name:
            print(f"       원가매칭: '{matched_name}'")
        else:
            print(f"       원가매칭: 실패")

        n_formula = ""
        revenue_int = revenue if isinstance(revenue, int) else 0
        if pp and revenue_int > 0 and commission != "":
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

        o_formula = f"=IFERROR(N{r_idx}/L{r_idx},\"\")" if n_formula else ""

        updates.append({
            "r_idx":        r_idx,
            "p_only":       False,
            "revenue":      revenue,
            "commission":   commission,
            "n_formula":    n_formula,
            "o_formula":    o_formula,
            "matched_name": matched_name,
        })
        rev_str = f"{revenue:,}원" if isinstance(revenue, int) else str(revenue)
        print(f"       → 매출={rev_str}, 수수료={commission}%, N={'있음' if n_formula else '없음'}")

    # ── 시트에 일괄 반영 ──────────────────────────────────────
    if not updates:
        print("\n채울 항목이 없습니다.")
        return

    full_cnt = sum(1 for u in updates if not u["p_only"])
    p_cnt    = sum(1 for u in updates if u["p_only"])
    print(f"\n총 {len(updates)}개 행 업데이트 중... (전체={full_cnt}, P만={p_cnt})")

    for u in updates:
        r_idx = u["r_idx"]
        if u["p_only"]:
            ws_camp.update(
                values=[[u["matched_name"]]],
                range_name=f"P{r_idx}",
                value_input_option="USER_ENTERED",
            )
        else:
            ws_camp.update(
                values=[[
                    u["revenue"]    if u["revenue"]    != "" else "",
                    u["commission"] if u["commission"] != "" else "",
                    u["n_formula"],
                    u["o_formula"],
                    u["matched_name"],
                ]],
                range_name=f"L{r_idx}:P{r_idx}",
                value_input_option="USER_ENTERED",
            )
        print(f"  행{r_idx} 완료")

    # ── 서식 적용 ─────────────────────────────────────────────
    try:
        requests_body = {"requests": []}
        sheet_id = ws_camp.id
        n_data = len(data_rows)

        requests_body["requests"].append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n_data + 1,
                      "startColumnIndex": 14, "endColumnIndex": 15},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENT", "pattern": "0.00%"}}},
            "fields": "userEnteredFormat(numberFormat)",
        }})
        for col_idx in [11, 13]:
            requests_body["requests"].append({"repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": n_data + 1,
                          "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
                "fields": "userEnteredFormat(numberFormat)",
            }})
        requests_body["requests"].append({"autoResizeDimensions": {
            "dimensions": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 15, "endIndex": 16}
        }})
        ss.batch_update(requests_body)
        print("서식 적용 완료")
    except Exception as e:
        print(f"서식 적용 실패: {e}")

    print("\n완료!")

if __name__ == "__main__":
    main()
