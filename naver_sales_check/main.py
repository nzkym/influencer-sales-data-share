"""
네이버 스마트스토어 행사 매출증감 확인 프로그램
이경하 담당 | 매일 오전 9시 자동 실행 (Google Cloud VM crontab)

컬럼 구조:
  A: 제목          B: 행사시작일(직원)  C: 행사종료일(직원)
  D: 집계기간      E: 행사기간매출     F: 행사일평균
  G: 비교일자      H: 비교매출         I: 비교일평균
  J: 일평균증감(F-I) ← 항상 표시, 행사 효과 즉시 확인
  K: 예상행사매출  L: 예상증감         ← 행사 진행 중만 표시
  M: 최종증감(E-H) ← 행사 종료 후만 표시 (인센티브 정산용)
  N: 채널(직원입력: nutone/jdhealth/nutpet)
  O: 업데이트시간 (헤더행에만)
"""

import re
import os
import sys
import time
from pathlib import Path

# 윈도우 콘솔(cp949)에서도 ✅ 같은 이모지가 깨지지 않도록 출력 인코딩을 UTF-8로 고정
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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

SHEET_URL          = os.getenv("SALES_CHECK_SHEET_URL")
GUGU_LOG_SHEET_URL = os.getenv("GUGU_LOG_SHEET_URL")

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

# 공구 활동 로그의 상품링크는 영문 스토어명(nutone/jdhealth/nutpet)을 사용
STORE_SLUG = {"뉴트원": "nutone", "제이디": "jdhealth", "넛펫": "nutpet"}


def _store_slug(store_raw: str) -> str:
    return STORE_SLUG.get(store_raw, store_raw)


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
    """숫자 열 콤마 서식 + O열 텍스트 서식(상품번호 보호) + 초록색 제거"""
    if data_rows < 2:
        return
    R = []
    # 숫자 서식: D E F G I J L M
    for col_idx in [3, 4, 5, 6, 8, 9, 11, 12]:
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
    # O열(14): 전체 열을 텍스트 서식으로 고정 (1000행까지)
    # → 상품번호를 콤마 구분으로 입력해도 숫자로 변환되지 않음
    R.append({"repeatCell": {
        "range": {
            "sheetId": ws.id,
            "startRowIndex": 0,
            "endRowIndex": 1000,
            "startColumnIndex": 14,
            "endColumnIndex": 15,
        },
        "cell": {"userEnteredFormat": {
            "numberFormat": {"type": "TEXT"},
            "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
        }},
        "fields": "userEnteredFormat(numberFormat,backgroundColor)",
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


def _query_one_day(headers: dict, date_dt: datetime,
                   product_no: str = None) -> tuple:
    """하루치 (매출합산, 결제건수) 반환. 네이버 API 24시간 제한 대응."""
    next_day = date_dt + timedelta(days=1)
    from_str = date_dt.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
    to_str   = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

    day_total = 0
    day_count = 0
    page = 1
    while True:
        url = (
            f"{NAVER_BASE}/external/v1/pay-order/seller/product-orders"
            f"?from={from_str}&to={to_str}"
            f"&rangeType=PAYED_DATETIME&pageSize=300&page={page}"
        )
        for attempt in range(3):
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"    [Rate Limit] {wait}초 후 재시도...")
                time.sleep(wait)
            else:
                break

        if resp.status_code != 200:
            print(f"    [API 오류] {resp.status_code}: {resp.text[:100]}")
            break

        data     = resp.json().get("data", {})
        contents = data.get("contents", [])
        for item in contents:
            po = item.get("content", {}).get("productOrder", {})
            if po.get("productOrderStatus", "") not in SALE_STATUSES:
                continue
            if product_no and str(po.get("productId", "")) != str(product_no):
                continue
            amount = po.get("totalPaymentAmount") or po.get("paymentAmount")
            if not amount:
                amount = int(po.get("quantity") or 1) * int(po.get("unitPrice") or 0)
            day_total += int(amount)
            day_count += 1

        if not data.get("pagination", {}).get("hasNext", False):
            break
        page += 1

    return day_total, day_count


def _query_full_period(headers: dict, date_from: str, date_to: str,
                       product_no: str) -> tuple:
    """특정 상품번호의 전체 기간 (매출합산, 결제건수) 한 번에 조회."""
    start_dt  = datetime.strptime(date_from, "%Y-%m-%d")
    end_dt    = datetime.strptime(date_to,   "%Y-%m-%d")
    yesterday = datetime.now(KST).replace(tzinfo=None) - timedelta(days=1)
    actual_end = min(end_dt, yesterday)

    from_str = start_dt.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
    to_str   = (actual_end + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

    total = 0
    count = 0
    page  = 1
    while True:
        url = (
            f"{NAVER_BASE}/external/v1/pay-order/seller/product-orders"
            f"?from={from_str}&to={to_str}"
            f"&rangeType=PAYED_DATETIME&pageSize=300&page={page}"
        )
        for attempt in range(3):
            resp = requests.get(url, headers=headers, timeout=60)
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
            else:
                break

        if resp.status_code == 400:
            raise ValueError("24시간 제한")
        if resp.status_code != 200:
            print(f"    [API 오류] {resp.status_code}")
            break

        data     = resp.json().get("data", {})
        contents = data.get("contents", [])
        for item in contents:
            po = item.get("content", {}).get("productOrder", {})
            if po.get("productOrderStatus", "") not in SALE_STATUSES:
                continue
            if str(po.get("productId", "")) != str(product_no):
                continue
            amount = po.get("totalPaymentAmount") or po.get("paymentAmount")
            if not amount:
                amount = int(po.get("quantity") or 1) * int(po.get("unitPrice") or 0)
            total += int(amount)
            count += 1

        if not data.get("pagination", {}).get("hasNext", False):
            break
        page += 1

    return total, count


def get_period_sales(headers: dict, date_from: str, date_to: str,
                     product_no: str = None) -> tuple:
    """
    기간 (매출합산, 결제건수) 반환.
    - 특정 상품: 전체 기간 한 번에 조회 (빠름)
    - 전체 스토어: 하루씩 순차 조회 (API 24시간 제한)
    """
    yesterday  = datetime.now(KST).replace(tzinfo=None) - timedelta(days=1)
    start_dt   = datetime.strptime(date_from, "%Y-%m-%d")
    actual_end = min(datetime.strptime(date_to, "%Y-%m-%d"), yesterday)

    if start_dt > actual_end:
        return 0, 0

    # 상품번호 지정 시: 전체 기간 한 번에 조회 시도
    if product_no:
        try:
            total, count = _query_full_period(headers, date_from, date_to, product_no)
            print(f"    합계: {total:,}원 / {count}건")
            return total, count
        except ValueError:
            pass   # 24시간 제한 → 일별 조회로 폴백

    # 전체 스토어 또는 폴백: 2개씩 병렬 조회 (Rate Limit 방지 위해 딜레이 포함)
    days = []
    d = start_dt
    while d <= actual_end:
        days.append(d)
        d += timedelta(days=1)

    def fetch_day(day):
        return day, *_query_one_day(headers, day, product_no)

    results = {}
    total = 0
    count = 0

    # 2개씩 묶어서 병렬 처리, 각 묶음 사이 1초 딜레이
    for i in range(0, len(days), 2):
        batch = days[i:i+2]
        with ThreadPoolExecutor(max_workers=2) as ex:
            for day, day_total, day_count in ex.map(fetch_day, batch):
                results[day] = (day_total, day_count)
        if i + 2 < len(days):
            time.sleep(1)

    for day in days:
        day_total, day_count = results[day]
        if day_total > 0:
            print(f"    {day.strftime('%m/%d')}: {day_total:,}원 ({day_count}건)")
        total += day_total
        count += day_count

    return total, count


# ── 공구 활동 로그 자동탐지 ───────────────────────────────
# 인플루언서 공구 활동 로그 시트(월별 탭, 삭제되지 않는 누적 기록)에서
# 해당 스토어/기간과 겹치는 공구 상품번호를 찾아 O열에 자동으로 채워준다.

_gugu_log_sheet = None
_gugu_log_tabs  = None
_gugu_log_cache: dict = {}


def _get_gugu_log_tabs() -> list:
    global _gugu_log_sheet, _gugu_log_tabs
    if _gugu_log_tabs is None:
        _gugu_log_tabs = []
        if GUGU_LOG_SHEET_URL:
            try:
                gs  = _get_gs_client()
                sid = _extract_sheet_id(GUGU_LOG_SHEET_URL)
                _gugu_log_sheet = gs.open_by_key(sid)
                _gugu_log_tabs  = _gugu_log_sheet.worksheets()
            except Exception as e:
                print(f"  [공구로그] 시트 열기 실패: {e}")
    return _gugu_log_tabs


def _parse_gugu_log_period(s: str, year: int):
    """'26.6.1~6.7', '2026.4/1~4/7' 등 → (시작일, 종료일)"""
    nums = re.findall(r"\d+", s)
    if len(nums) != 5:
        return None
    _, m1, d1, m2, d2 = (int(n) for n in nums)
    try:
        start = date(year, m1, d1)
        end   = date(year if m2 >= m1 else year + 1, m2, d2)
        return (start, end)
    except ValueError:
        return None


def _load_gugu_log_tab(ws, year: int) -> list:
    """공구 행(셀 중 '공구' 텍스트 + 기간 패턴)과 다음 행(네이버 링크)을 열 위치 무관하게 탐색"""
    campaigns = []
    try:
        vals = ws.get_values("A1:Z1000")
    except Exception as e:
        print(f"  [공구로그] '{ws.title}' 탭 읽기 실패: {e}")
        return campaigns

    _LINK_RE = re.compile(r"(?:brand|smartstore)\.naver\.com/([a-zA-Z0-9_]+)/products/(\d+)")

    for i in range(len(vals) - 1):
        row = vals[i]
        has_gugu = any(c.strip() == "공구" for c in row)
        if not has_gugu:
            continue
        period = None
        for c in row:
            period = _parse_gugu_log_period(c.strip(), year)
            if period:
                break
        if not period:
            continue
        nxt = vals[i + 1]
        for c in nxt:
            m = _LINK_RE.search(c)
            if m:
                campaigns.append({
                    "store":      m.group(1),
                    "product_no": m.group(2),
                    "start":      period[0],
                    "end":        period[1],
                    "name":       "",
                })
    return campaigns


def _get_gugu_log_campaigns(year: int, month: int) -> list:
    """해당 (연,월) 탭의 공구 캠페인 목록 (탭 단위 캐시)"""
    key = (year, month)
    if key not in _gugu_log_cache:
        campaigns = []
        for ws in _get_gugu_log_tabs():
            tm = re.match(r"(\d{2})\.(\d+)", ws.title)
            if tm and (2000 + int(tm.group(1)), int(tm.group(2))) == (year, month):
                campaigns = _load_gugu_log_tab(ws, year)
                break
        _gugu_log_cache[key] = campaigns
    return _gugu_log_cache[key]


def find_gugu_product_ids(store: str, period_start: date, period_end: date) -> set:
    """공구 활동 로그에서 기간과 겹치는 캠페인의 상품번호 목록 (해당 스토어만)"""
    ids = set()
    cur = date(period_start.year, period_start.month, 1)
    while (cur.year, cur.month) <= (period_end.year, period_end.month):
        for c in _get_gugu_log_campaigns(cur.year, cur.month):
            if (c["store"].lower() == store.lower()
                    and c["start"] <= period_end and c["end"] >= period_start):
                ids.add(c["product_no"])
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return ids


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


def parse_product_ids(cell_val) -> list:
    """
    O열 셀에서 상품번호 목록 추출.
    입력 예: "11774034951,13393725183,13088952477"
    반환 예: ["11774034951", "13393725183", "13088952477"]
    """
    s = str(cell_val).strip()
    if not s or s in ("0", ""):
        return []
    ids = []
    for part in re.split(r"[,\s，]+", s):
        part = part.strip().replace(",", "")
        if part.isdigit() and len(part) >= 8:   # 네이버 상품번호 최소 8자리
            ids.append(part)
    return ids


def calc_manual_deductions(headers: dict, product_ids: list,
                           date_from: str, date_to: str) -> list:
    """수동 지정 상품번호별 기간 매출 조회 후 금액 목록 반환"""
    amounts = []
    for pid in product_ids:
        print(f"    [수동공제] 상품 {pid} 조회 중...")
        amount, _ = get_period_sales(headers, date_from, date_to, product_no=pid)
        print(f"    [수동공제] 상품 {pid}: {amount:,}원")
        if amount > 0:
            amounts.append(amount)
    return amounts


def make_formula(raw: int, deductions: list) -> object:
    """공제 수식 생성. deductions=[] → int, [200,100] → '=raw-200-100'"""
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
    if not GUGU_LOG_SHEET_URL:
        print("  [안내] GUGU_LOG_SHEET_URL 미설정 → 공구 자동탐지 없이 진행")

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
    today     = datetime.now(KST).date()
    yesterday = today - timedelta(days=1)

    batch_updates = []
    verify_rows   = []   # (sheet_row, title, promo_raw, comp_raw) — 기재 후 재검증용

    for row_idx, row in enumerate(all_values):
        if row_idx == 0:
            continue
        if len(row) < 3:
            continue

        title     = str(row[0]).strip()   # A
        start_raw = str(row[1]).strip()   # B
        end_raw   = str(row[2]).strip()   # C

        # 채널: N열(index 13) 우선, 없으면 J열(index 9) 이전 위치에서 읽기
        store_raw = str(row[13]).strip() if len(row) > 13 else ""
        if not store_raw:
            store_raw = str(row[9]).strip() if len(row) > 9 else ""

        # O열(index 14): 수동 공구 상품번호 (콤마 구분)
        manual_ids = parse_product_ids(row[14] if len(row) > 14 else "")

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

        # 종료된 행사 중 G열(최종증감)이 이미 있으면 재계산 스킵
        existing_g = str(row[6]).strip() if len(row) > 6 else ""
        if promo_end < today and existing_g:
            print(f"\n[행 {row_idx+1}] {title} — 종료+계산완료, 스킵")
            continue

        promo_total_days = (promo_end - promo_start).days + 1
        promo_actual_end = min(promo_end, yesterday)
        elapsed_days     = (promo_actual_end - promo_start).days + 1

        if elapsed_days <= 0:
            continue

        # 비교기간: 전주 같은 요일 기준 (겹치면 더 전주로)
        weeks_back = -(-promo_total_days // 7)   # ceil(promo_total_days / 7)
        comp_start = promo_start - timedelta(weeks=weeks_back)
        comp_end   = comp_start + timedelta(days=promo_total_days - 1)

        # 집계기간 텍스트 (D열)
        period_text = (f"{promo_start.month}.{promo_start.day}"
                       f"~{promo_actual_end.month}.{promo_actual_end.day}"
                       f" ({elapsed_days}일)")

        # 비교일자 텍스트 (G열)
        comp_period_str = fmt_period(comp_start, comp_end)

        sheet_row = row_idx + 1

        print(f"\n[행 {sheet_row}] {title} ({store_raw})")
        print(f"  행사: {promo_start}~{promo_end} ({promo_total_days}일)")
        print(f"  집계: {promo_start}~{promo_actual_end} ({elapsed_days}일 경과)")
        print(f"  비교: {comp_start}~{comp_end} ({promo_total_days}일)")

        # 공구 활동 로그에서 행사/비교 기간과 겹치는 공구 상품번호 자동탐지 → O열 보강
        discovered = (find_gugu_product_ids(_store_slug(store_raw), promo_start, promo_actual_end)
                      | find_gugu_product_ids(_store_slug(store_raw), comp_start, comp_end))
        new_ids = [pid for pid in sorted(discovered) if pid not in manual_ids]
        if new_ids:
            print(f"  [공구 자동탐지] O열에 추가: {', '.join(new_ids)}")
            manual_ids = manual_ids + new_ids
            batch_updates.append({"range": f"O{sheet_row}", "values": [[",".join(manual_ids)]]})

        # 토큰 1회 발급 (행사/비교 기간 모두 재사용)
        try:
            token = _get_access_token(client_id, client_secret)
        except Exception as e:
            print(f"  [오류] 인증 실패: {e}")
            batch_updates.append({"range": f"D{sheet_row}", "values": [[period_text]]})
            continue

        headers_auth = {"Authorization": f"Bearer {token}"}

        # ── 행사기간 매출 조회 ────────────────────────────
        print(f"  ▷ 행사기간 매출 조회 중...")
        try:
            promo_raw, promo_count = get_period_sales(
                headers_auth,
                promo_start.strftime("%Y-%m-%d"),
                promo_actual_end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            print(f"  [오류] 행사매출 조회 실패: {e}")
            batch_updates.append({"range": f"D{sheet_row}", "values": [[period_text]]})
            continue

        # 행사기간 공구 공제 (O열 상품번호 기준) — 항상 현재 기간 기준으로 재계산
        print(f"  ▷ 행사기간 공구 공제 계산 중...")
        promo_deductions = []
        if manual_ids:
            promo_deductions = calc_manual_deductions(
                headers_auth, manual_ids,
                promo_start.strftime("%Y-%m-%d"), promo_actual_end.strftime("%Y-%m-%d"),
            )

        # ── 비교기간 매출 조회 (같은 토큰) ───────────────
        print(f"  ▷ 비교기간 매출 조회 중...")
        try:
            comp_raw, comp_count = get_period_sales(
                headers_auth,
                comp_start.strftime("%Y-%m-%d"),
                comp_end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            print(f"  [오류] 비교매출 조회 실패: {e}")
            batch_updates.append({"range": f"D{sheet_row}", "values": [[period_text]]})
            continue

        # 비교기간 공구 공제 (O열 상품번호 기준) — 항상 현재 기간 기준으로 재계산
        print(f"  ▷ 비교기간 공구 공제 계산 중...")
        comp_deductions = []
        if manual_ids:
            comp_deductions = calc_manual_deductions(
                headers_auth, manual_ids,
                comp_start.strftime("%Y-%m-%d"), comp_end.strftime("%Y-%m-%d"),
            )

        promo_net = promo_raw - sum(promo_deductions)
        comp_net  = comp_raw  - sum(comp_deductions)

        print(f"  행사: {promo_raw:,}원 ({promo_count}건) - 공구 {sum(promo_deductions):,}원 = {promo_net:,}원")
        print(f"  비교: {comp_raw:,}원 ({comp_count}건) - 공구 {sum(comp_deductions):,}원 = {comp_net:,}원")

        # ── 셀 수식 구성 ───────────────────────────────────
        # 새 컬럼 순서: D(일평균증감) E(예상매출) F(예상증감) G(최종증감)
        #              H(집계기간) I(행사매출) J(행사일평균) K(비교일자) L(비교매출) M(비교일평균)

        i_val = make_formula(promo_raw, promo_deductions)   # I: 행사기간매출
        j_val = f"=I{sheet_row}/{elapsed_days}"             # J: 행사일평균
        l_val = make_formula(comp_raw,  comp_deductions)    # L: 비교매출
        m_val = f"=L{sheet_row}/{promo_total_days}"         # M: 비교일평균
        d_val = f"=J{sheet_row}-M{sheet_row}"               # D: 일평균증감 (항상 표시)

        is_ended       = promo_end < today
        remaining_days = (promo_end - promo_actual_end).days

        if not is_ended:
            # 행사 진행 중: D(일평균증감) + E/F(예상) 표시, G(최종) 비워둠
            e_val = f"=J{sheet_row}*{promo_total_days}"   # E: 예상행사매출
            f_val = f"=E{sheet_row}-L{sheet_row}"          # F: 예상증감
            g_val = ""                                      # G: 최종증감 (종료후)
            print(f"  일평균증감: {(promo_net//elapsed_days - comp_net//promo_total_days):+,}원/일 | {remaining_days}일 남음")
        else:
            # 행사 종료: G(최종증감) 표시, E/F(예상) 비워둠
            e_val = ""
            f_val = ""
            g_val = f"=I{sheet_row}-L{sheet_row}"          # G: 최종증감 (인센티브 정산용)
            print(f"  최종증감: {promo_net - comp_net:+,}원")

        # A열 제목 끝에 업데이트 시간 기재
        now_kst = datetime.now(KST)
        row_update_time = f"{now_kst.month}.{now_kst.day} {now_kst.strftime('%H:%M')}"
        title_base = re.sub(r"\s*\[업데이트:\s*\d+\.\d+\s+\d+:\d+\]$", "", title)
        title_with_time = f"{title_base} [업데이트: {row_update_time}]"
        batch_updates.append({
            "range": f"A{sheet_row}",
            "values": [[title_with_time]],
        })

        verify_rows.append((sheet_row, title_base, promo_raw, comp_raw))

        # D~M 한 번에 기입 (10개 열)
        batch_updates.append({
            "range": f"D{sheet_row}:M{sheet_row}",
            "values": [[
                d_val,            # D: ★일평균증감 (항상)
                e_val,            # E: 예상행사매출 (진행중)
                f_val,            # F: 예상증감 (진행중)
                g_val,            # G: 최종증감 (종료후/인센티브)
                period_text,      # H: 집계기간
                i_val,            # I: 행사기간매출
                j_val,            # J: 행사일평균
                comp_period_str,  # K: 비교일자
                l_val,            # L: 비교매출
                m_val,            # M: 비교일평균
            ]],
        })

    # 헤더 + 업데이트 시간 기재
    # 헤더는 직원이 자유롭게 수정 가능 — 프로그램은 업데이트 시간(P1)만 기록
    update_time = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    batch_updates.append({"range": "P1", "values": [[f"업데이트: {update_time}"]]})

    if batch_updates:
        ws.batch_update(batch_updates, value_input_option="USER_ENTERED")
        _apply_number_format(spreadsheet, ws, len(all_values))
        print(f"\n✅ 업데이트 완료 ({update_time})")

        # ── 재검증: 시트에 기재된 I/L 수식의 원본 매출값이 조회값과 일치하는지 확인
        if verify_rows:
            print("\n── 재검증 ──────────────────────────────────")
            all_ok = True
            for vrow, vtitle, vpromo_raw, vcomp_raw in verify_rows:
                try:
                    wi = ws.get_values(f"I{vrow}", value_render_option="FORMULA")[0][0]
                    wl = ws.get_values(f"L{vrow}", value_render_option="FORMULA")[0][0]
                    i_ok = str(vpromo_raw) in str(wi)
                    l_ok = str(vcomp_raw)  in str(wl)
                    ok   = i_ok and l_ok
                    all_ok = all_ok and ok
                    mark = "✅" if ok else "❌"
                    print(f"  {mark} 행{vrow} {vtitle}")
                    if not i_ok:
                        print(f"      I열 불일치 — 기대:{vpromo_raw:,} / 시트:{wi}")
                    if not l_ok:
                        print(f"      L열 불일치 — 기대:{vcomp_raw:,} / 시트:{wl}")
                except Exception as e:
                    print(f"  ⚠️  행{vrow} 재검증 오류: {e}")
                    all_ok = False
            print(f"  {'전체 이상없음 ✅' if all_ok else '불일치 항목 있음 — 확인 필요 ❌'}")
            print("─" * 44)
    else:
        print(f"\n업데이트할 행이 없습니다. ({update_time})")

    print(f"{'='*55}\n")


if __name__ == "__main__":
    run_once()
