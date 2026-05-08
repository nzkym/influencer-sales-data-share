"""
네이버 스마트스토어 행사 매출증감 확인 프로그램
이경하 담당 | 매일 오전 9시 자동 실행

실제 시트 열 구조:
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

from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
import requests
import bcrypt
import base64

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

SHEET_URL = os.getenv("SALES_CHECK_SHEET_URL")
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
        raise ValueError(f"스프레드시트 URL에서 ID를 찾을 수 없습니다: {url}")
    return m.group(1)


# ── 네이버 커머스 API ─────────────────────────────────────

def _get_access_token(client_id: str, client_secret: str) -> str:
    """토큰 발급. 스토어당 1회만 호출하고 재사용."""
    timestamp = str(int(time.time() * 1000))
    password = f"{client_id}_{timestamp}"
    hashed = bcrypt.hashpw(password.encode("utf-8"), client_secret.encode("utf-8"))
    sign = base64.standard_b64encode(hashed).decode("utf-8")
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
            print(f"    [API 오류] {resp.status_code}: {resp.text[:150]}")
            break
        data = resp.json().get("data", {})
        all_items.extend(data.get("contents", []))
        if not data.get("pagination", {}).get("hasNext", False):
            break
        page += 1
    return all_items


def get_period_sales(headers: dict, date_from: str, date_to: str,
                     debug_first: bool = False) -> int:
    """
    이미 발급된 headers로 기간 매출 합산.
    date_from~date_to가 어제 이후면 어제까지만 집계.
    """
    current = datetime.strptime(date_from, "%Y-%m-%d")
    end_dt  = datetime.strptime(date_to,   "%Y-%m-%d")
    yesterday = datetime.now(KST).replace(tzinfo=None) - timedelta(days=1)
    actual_end = min(end_dt, yesterday)

    total = 0
    first_logged = False

    while current <= actual_end:
        next_day = current + timedelta(days=1)
        from_str = current.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        to_str   = next_day.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"

        items = _query_orders_one_day(headers, from_str, to_str)

        day_total = 0
        for item in items:
            po = item.get("content", {}).get("productOrder", {})
            if po.get("productOrderStatus", "") not in SALE_STATUSES:
                continue

            if debug_first and not first_logged:
                print(f"    [디버그] productOrder 필드: {list(po.keys())}")
                print(f"    [디버그] totalPaymentAmount={po.get('totalPaymentAmount')}, "
                      f"unitPrice={po.get('unitPrice')}, quantity={po.get('quantity')}")
                first_logged = True

            amount = po.get("totalPaymentAmount") or po.get("paymentAmount")
            if not amount:
                amount = int(po.get("quantity") or 1) * int(po.get("unitPrice") or 0)
            day_total += int(amount)

        total += day_total
        print(f"    {current.strftime('%m/%d')}: {day_total:,}원 ({len(items)}건)")
        current = next_day

    return total


# ── 날짜 파싱 ─────────────────────────────────────────────

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


# ── 메인 실행 ─────────────────────────────────────────────

def run_once():
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  행사 매출증감 확인 | {now_str}")
    print(f"{'='*55}")

    if not SHEET_URL:
        print("[오류] .env에 SALES_CHECK_SHEET_URL이 없습니다.")
        return

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
    first_data_row = True

    for row_idx, row in enumerate(all_values):
        if row_idx == 0:
            continue
        if len(row) < 10:
            continue

        title     = str(row[0]).strip()  # A
        start_raw = str(row[1]).strip()  # B: 행사시작일
        end_raw   = str(row[2]).strip()  # C: 행사종료일
        store_raw = str(row[9]).strip()  # J: 채널

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
            print(f"\n[행 {row_idx+1}] 행사 아직 시작 전 ({promo_start}) — 스킵")
            continue

        promo_total_days  = (promo_end - promo_start).days + 1
        promo_actual_end  = min(promo_end, yesterday)
        elapsed_days      = (promo_actual_end - promo_start).days + 1

        comp_end   = promo_start - timedelta(days=1)
        comp_start = comp_end - timedelta(days=promo_total_days - 1)

        # F열: 비교일자는 API 없이 계산 가능 → 먼저 기록해둠
        comp_period_str = fmt_period(comp_start, comp_end)
        sheet_row = row_idx + 1

        print(f"\n[행 {sheet_row}] {title} ({store_raw})")
        print(f"  행사: {promo_start}~{promo_end} ({promo_total_days}일)")
        print(f"  집계: {promo_start}~{promo_actual_end} ({elapsed_days}일 경과)")
        print(f"  비교: {comp_start}~{comp_end} ({promo_total_days}일)")
        print(f"  비교일자(F열): {comp_period_str}")

        # ── 토큰 1회 발급 → 행사/비교 기간 모두 재사용 ──────
        try:
            token = _get_access_token(client_id, client_secret)
        except Exception as e:
            print(f"  [오류] 인증 실패: {e}")
            # F열만이라도 기입
            batch_updates.append({"range": f"F{sheet_row}", "values": [[comp_period_str]]})
            continue

        headers = {"Authorization": f"Bearer {token}"}

        # 행사기간 매출
        print(f"  ▷ 행사기간 매출 조회 중...")
        try:
            promo_sales = get_period_sales(
                headers,
                promo_start.strftime("%Y-%m-%d"),
                promo_actual_end.strftime("%Y-%m-%d"),
                debug_first=first_data_row,
            )
            first_data_row = False
        except Exception as e:
            print(f"  [오류] 행사매출 조회 실패: {e}")
            batch_updates.append({"range": f"F{sheet_row}", "values": [[comp_period_str]]})
            continue

        promo_avg = round(promo_sales / elapsed_days) if elapsed_days > 0 else 0

        # 비교기간 매출 (같은 토큰 재사용 — 토큰 이중 발급 없음)
        print(f"  ▷ 비교기간 매출 조회 중...")
        try:
            comp_sales = get_period_sales(
                headers,
                comp_start.strftime("%Y-%m-%d"),
                comp_end.strftime("%Y-%m-%d"),
            )
        except Exception as e:
            print(f"  [오류] 비교매출 조회 실패: {e}")
            batch_updates.append({"range": f"F{sheet_row}", "values": [[comp_period_str]]})
            continue

        comp_avg = round(comp_sales / promo_total_days) if promo_total_days > 0 else 0
        diff     = promo_sales - comp_sales

        print(f"  행사매출: {promo_sales:,}원  일평균: {promo_avg:,}원")
        print(f"  비교매출: {comp_sales:,}원   일평균: {comp_avg:,}원")
        print(f"  증감:     {diff:+,}원")

        # D~I 한 번에 기입
        batch_updates.append({
            "range": f"D{sheet_row}:I{sheet_row}",
            "values": [[promo_sales, promo_avg, comp_period_str,
                        comp_sales, comp_avg, diff]],
        })

    if batch_updates:
        ws.batch_update(batch_updates)
        print(f"\n✅ {len(batch_updates)}개 항목 시트 업데이트 완료")
    else:
        print("\n업데이트할 행이 없습니다.")

    print(f"{'='*55}\n")


if __name__ == "__main__":
    run_once()
