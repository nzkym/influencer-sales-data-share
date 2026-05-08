"""
네이버 스마트스토어 행사 매출증감 확인 프로그램
이경하 담당 | 매일 오전 9시 자동 실행

시트 열 구조:
  A: 제목  B: 행사시작일  C: 행사종료일
  D: 행사기간매출(전일까지)  E: 행사일자 평균매출
  F: 비교일자(자동계산)  G: 비교일자매출  H: 비교일자 평균매출
  I: 행사기간매출-비교일자매출
  J: 채널(nutone/jdhealth/nutpet)
"""

import re
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
import requests
import bcrypt
import base64

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

SHEET_URL        = os.getenv("SALES_CHECK_SHEET_URL")
MASTER_SHEET_URL = os.getenv("MASTER_SHEET_URL")   # 공구 캠페인 마스터 시트

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDENTIALS_PATH = str(
    BASE_DIR.parent / "influencer data shared" / "credentials" / "google-credentials.json"
)

NAVER_BASE = "https://api.commerce.naver.com"

STORE_MAP = {
    "nutone":   (os.getenv("NUTONE_CLIENT_ID"),   os.getenv("NUTONE_CLIENT_SECRET")),
    "뉴트원":   (os.getenv("NUTONE_CLIENT_ID"),   os.getenv("NUTONE_CLIENT_SECRET")),
    "jdhealth": (os.getenv("JDHEALTH_CLIENT_ID"), os.getenv("JDHEALTH_CLIENT_SECRET")),
    "제이디":   (os.getenv("JDHEALTH_CLIENT_ID"), os.getenv("JDHEALTH_CLIENT_SECRET")),
    "nutpet":   (os.getenv("NUTPET_CLIENT_ID"),   os.getenv("NUTPET_CLIENT_SECRET")),
    "넛펫":     (os.getenv("NUTPET_CLIENT_ID"),   os.getenv("NUTPET_CLIENT_SECRET")),
}

SALE_STATUSES = {"PAYED", "DELIVERING", "DELIVERED", "PURCHASE_DECIDED"}
KST = timezone(timedelta(hours=9))


# ── 구글 시트 ─────────────────────────────────────────────

def _get_gs_client():
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def _extract_sheet_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError(f"스프레드시트 URL에서 ID 없음: {url}")
    return m.group(1)


def _apply_number_format(spreadsheet, ws, data_rows: int):
    """D, E, G, H, I 열에 #,##0 콤마 서식 적용"""
    if data_rows < 2:
        return
    R = []
    for col_idx in [3, 4, 6, 7, 8]:   # D=3, E=4, G=6, H=7, I=8 (0-indexed)
        R.append({"repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": 1,
                "endRowIndex": data_rows,
                "startColumnIndex": col_idx,
                "endColumnIndex": col_idx + 1,
            },
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}
            }},
            "fields": "userEnteredFormat.numberFormat",
        }})
    if R:
        spreadsheet.batch_update({"requests": R})


# ── 네이버 커머스 API ─────────────────────────────────────

def _get_access_token(client_id: str, client_secret: str) -> str:
    """토큰 발급. 스토어당 1회만 호출하고 재사용."""
    timestamp = str(int(time.time() * 1000))
    password  = f"{client_id}_{timestamp}"
    hashed    = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
    sign      = base64.standard_b64encode(hashed).decode("utf-8")
    resp = requests.post(
        f"{NAVER_BASE}/external/v1/oauth2/token",
        data={
            "client_id": client_id,
            "timestamp": timestamp,
            "client_secret_sign": sign,
            "grant_type": "client_credentials",
            "type": "SELF",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _query_orders_one_day(headers: dict, from_str: str, to_str: str) -> list:
    all_items = []
    page = 1
    while True:
        url = (
            f"{NAVER_BASE}/external/v1/pay-order/seller/product-orders"
            f"?from={from_str}&to={to_str}"
            f"&rangeType=PAYED_DATETIME&pageSize=100&page={page}"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"    [API 오류] {resp.status_code}: {resp.text[:100]}")
            break
        data = resp.json().get("data", {})
        all_items.extend(data.get("contents", []))
        if not data.get("pagination", {}).get("hasNext", False):
            break
        page += 1
    return all_items


def get_period_sales(headers: dict, date_from: str, date_to: str,
                     product_no: str = None, workers: int = 5) -> int:
    """
    기간 매출 합산. 병렬 조회로 속도 개선.
    product_no 지정 시 해당 상품만 합산 (공구 공제용).
    """
    current   = datetime.strptime(date_from, "%Y-%m-%d")
    end_dt    = datetime.strptime(date_to,   "%Y-%m-%d")
    yesterday = datetime.now(KST).replace(tzinfo=None) - timedelta(days=1)
    actual_end = min(end_dt, yesterday)

    days = []
    d = current
    while d <= actual_end:
        days.append(d)
        d += timedelta(days=1)

    if not days:
        return 0

    def fetch_one(day):
        next_day = day + timedelta(days=1)
        from_str = day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        to_str   = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        items = _query_orders_one_day(headers, from_str, to_str)

        day_total = 0
        for item in items:
            po = item.get("content", {}).get("productOrder", {})
            if po.get("productOrderStatus", "") not in SALE_STATUSES:
                continue
            if product_no and str(po.get("productId", "")) != str(product_no):
                continue
            amount = po.get("totalPaymentAmount") or po.get("paymentAmount")
            if not amount:
                amount = int(po.get("quantity") or 1) * int(po.get("unitPrice") or 0)
            day_total += int(amount)
        return day, day_total, len(items)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(fetch_one, days))

    total = 0
    for day, day_total, count in results:
        print(f"    {day.strftime('%m/%d')}: {day_total:,}원 ({count}건)")
        total += day_total
    return total


# ── 공구 캠페인 공제 ──────────────────────────────────────

def load_gugu_campaigns(store: str) -> list:
    """인플루언서 마스터 시트에서 해당 스토어 공구 캠페인 읽기"""
    if not MASTER_SHEET_URL:
        return []
    try:
        gs = _get_gs_client()
        sid = _extract_sheet_id(MASTER_SHEET_URL)
        sp  = gs.open_by_key(sid)
        gid_m = re.search(r"gid=(\d+)", MASTER_SHEET_URL)
        ws = (next(s for s in sp.worksheets() if s.id == int(gid_m.group(1)))
              if gid_m else sp.sheet1)
        rows = ws.get_all_records()
    except Exception as e:
        print(f"  [공구] 마스터 시트 읽기 실패: {e}")
        return []

    campaigns = []
    for row in rows:
        if str(row.get("스토어") or "").strip().lower() != store.lower():
            continue
        url       = str(row.get("상품링크") or "").strip()
        start_str = str(row.get("시작일자") or "").strip()
        end_str   = str(row.get("종료일자") or "").strip()
        if not url or not start_str or not end_str:
            continue
        try:
            m = re.search(r"/products/(\d+)", url)
            if not m:
                continue
            campaigns.append({
                "product_no": m.group(1),
                "start":      parse_date(start_str),
                "end":        parse_date(end_str),
                "title":      str(row.get("제목") or ""),
            })
        except Exception:
            continue
    return campaigns


def calc_gugu_deduction(headers: dict, store: str,
                        period_start: date, period_end: date) -> tuple:
    """
    기간 내 공구(인플루언서) 매출 합산.
    반환: (총공제액int, 상품별금액list)
    """
    campaigns  = load_gugu_campaigns(store)
    yesterday  = datetime.now(KST).date() - timedelta(days=1)
    eff_end    = min(period_end, yesterday)

    total   = 0
    details = []   # 각 캠페인별 공제 금액 (수식 구성용)

    for c in campaigns:
        ol_start = max(period_start, c["start"])
        ol_end   = min(eff_end,      c["end"])
        if ol_start > ol_end:
            continue

        print(f"    [공구] '{c['title']}' 겹침: {ol_start}~{ol_end}")
        amount = get_period_sales(
            headers,
            ol_start.strftime("%Y-%m-%d"),
            ol_end.strftime("%Y-%m-%d"),
            product_no=c["product_no"],
            workers=2,
        )
        print(f"    [공구] 공제액: {amount:,}원")
        if amount > 0:
            total += amount
            details.append(amount)

    return total, details


# ── 수식 / 날짜 유틸 ─────────────────────────────────────

def parse_date(s: str) -> date:
    s = s.strip().replace(" ", "")
    nums = re.split(r"[.\-/]", s)
    if len(nums) == 3:
        return date(int(nums[0]), int(nums[1]), int(nums[2]))
    raise ValueError(f"날짜 파싱 실패: {s}")


def fmt_period(d1: date, d2: date) -> str:
    if d1.year == d2.year:
        if d1.month == d2.month:
            return f"{d1.year}.{d1.month}.{d1.day}~{d2.day}"
        return f"{d1.year}.{d1.month}.{d1.day}~{d2.month}.{d2.day}"
    return f"{d1.year}.{d1.month}.{d1.day}~{d2.year}.{d2.month}.{d2.day}"


def make_formula(raw: int, deductions: list) -> object:
    """
    공제 수식 생성.
    공제 없음 → 숫자 그대로 (int)
    공제 있음 → "=89517630-5000000" 수식 문자열
    """
    if not deductions:
        return raw
    return "=" + str(raw) + "".join(f"-{d}" for d in deductions)


# ── 메인 실행 ─────────────────────────────────────────────

def run_once():
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  행사 매출증감 확인 | {now_str}")
    print(f"{'='*55}")

    if not SHEET_URL:
        print("[오류] .env에 SALES_CHECK_SHEET_URL이 없습니다.")
        return

    if not MASTER_SHEET_URL:
        print("  [안내] MASTER_SHEET_URL 미설정 → 공구 공제 없이 진행")

    gs = _get_gs_client()
    sheet_id = _extract_sheet_id(SHEET_URL)
    spreadsheet = gs.open_by_key(sheet_id)

    gid_match = re.search(r"gid=(\d+)", SHEET_URL)
    if gid_match:
        gid = int(gid_match.group(1))
        ws = next(s for s in spreadsheet.worksheets() if s.id == gid)
    else:
        ws = spreadsheet.sheet1

    all_values = ws.get_all_values()
    today      = datetime.now(KST).date()
    yesterday  = today - timedelta(days=1)

    batch_updates = []

    for row_idx, row in enumerate(all_values):
        if row_idx == 0:
            continue
        if len(row) < 10:
            continue

        title     = str(row[0]).strip()
        start_raw = str(row[1]).strip()
        end_raw   = str(row[2]).strip()
        store_raw = str(row[9]).strip()

        if not title or not start_raw or not end_raw or not store_raw:
            continue

        creds_pair = STORE_MAP.get(store_raw.lower()) or STORE_MAP.get(store_raw)
        if not creds_pair or not creds_pair[0]:
            print(f"\n[행 {row_idx+1}] '{store_raw}' — API 키 없음, 스킵")
            continue

        client_id, client_secret = creds_pair

        try:
            promo_start = parse_date(start_raw)
            promo_end   = parse_date(end_raw)
        except Exception as e:
            print(f"\n[행 {row_idx+1}] 날짜 파싱 오류: {e}")
            continue

        if promo_start > today:
            continue

        promo_total_days = (promo_end - promo_start).days + 1
        promo_actual_end = min(promo_end, yesterday)
        elapsed_days     = (promo_actual_end - promo_start).days + 1

        if elapsed_days <= 0:
            continue

        comp_end   = promo_start - timedelta(days=1)
        comp_start = comp_end - timedelta(days=promo_total_days - 1)

        comp_period_str = fmt_period(comp_start, comp_end)
        sheet_row = row_idx + 1

        print(f"\n[행 {sheet_row}] {title} ({store_raw})")
        print(f"  행사: {promo_start}~{promo_end} ({promo_total_days}일)")
        print(f"  집계: {promo_start}~{promo_actual_end} ({elapsed_days}일 경과)")
        print(f"  비교: {comp_start}~{comp_end} ({promo_total_days}일)")

        # 토큰 1회 발급
        try:
            token = _get_access_token(client_id, client_secret)
        except Exception as e:
            print(f"  [오류] 인증 실패: {e}")
            batch_updates.append({"range": f"F{sheet_row}", "values": [[comp_period_str]]})
            continue

        headers = {"Authorization": f"Bearer {token}"}

        # ── 행사기간 매출 (병렬 조회) ────────────────────
        print(f"  ▷ 행사기간 매출 조회 중...")
        try:
            promo_raw = get_period_sales(
                headers,
                promo_start.strftime("%Y-%m-%d"),
                promo_actual_end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            print(f"  [오류] 행사매출 조회 실패: {e}")
            batch_updates.append({"range": f"F{sheet_row}", "values": [[comp_period_str]]})
            continue

        # 행사기간 공구 공제
        print(f"  ▷ 행사기간 공구 공제 계산 중...")
        promo_gugu, promo_deductions = calc_gugu_deduction(
            headers, store_raw, promo_start, promo_actual_end
        )

        # ── 비교기간 매출 (병렬 조회, 같은 토큰) ─────────
        print(f"  ▷ 비교기간 매출 조회 중...")
        try:
            comp_raw = get_period_sales(
                headers,
                comp_start.strftime("%Y-%m-%d"),
                comp_end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            print(f"  [오류] 비교매출 조회 실패: {e}")
            batch_updates.append({"range": f"F{sheet_row}", "values": [[comp_period_str]]})
            continue

        # 비교기간 공구 공제
        print(f"  ▷ 비교기간 공구 공제 계산 중...")
        comp_gugu, comp_deductions = calc_gugu_deduction(
            headers, store_raw, comp_start, comp_end
        )

        promo_net = promo_raw - promo_gugu
        comp_net  = comp_raw  - comp_gugu

        print(f"  행사: {promo_raw:,}원 - 공구 {promo_gugu:,}원 = {promo_net:,}원")
        print(f"  비교: {comp_raw:,}원 - 공구 {comp_gugu:,}원 = {comp_net:,}원")
        print(f"  증감: {promo_net - comp_net:+,}원")

        # ── 셀 수식 구성 ───────────────────────────────────
        # D: 공제 내역 보이는 수식 (없으면 숫자)
        d_val = make_formula(promo_raw, promo_deductions)
        g_val = make_formula(comp_raw,  comp_deductions)

        # E, H, I: D·G 셀 참조 수식 → D·G 변경 시 자동 반영
        e_val = f"=D{sheet_row}/{elapsed_days}"
        h_val = f"=G{sheet_row}/{promo_total_days}"
        i_val = f"=D{sheet_row}-G{sheet_row}"

        batch_updates.append({
            "range": f"D{sheet_row}:I{sheet_row}",
            "values": [[d_val, e_val, comp_period_str, g_val, h_val, i_val]],
        })

    if batch_updates:
        # USER_ENTERED: = 시작 값을 수식으로 해석
        ws.batch_update(batch_updates, value_input_option="USER_ENTERED")
        # 콤마 서식 적용
        _apply_number_format(spreadsheet, ws, len(all_values))
        print(f"\n✅ {len(batch_updates)}개 행 업데이트 완료")
    else:
        print("\n업데이트할 행이 없습니다.")

    print(f"{'='*55}\n")


if __name__ == "__main__":
    run_once()
