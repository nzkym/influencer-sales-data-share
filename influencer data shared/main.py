"""
인플루언서 판매 데이터 공유 프로그램
- 캠페인 목록: 구글 시트(MASTER_SHEET_URL)에서 읽기
- 네이버 스마트스토어 API로 판매 데이터 조회
- 각 인플루언서 구글 시트에 결과 기록

실행 방법:
  python main.py          → 로컬 PC: 시작 즉시 1회 + 3시간마다 반복
  python main.py --once   → GitHub Actions: 1회 실행 후 종료
"""

import re
import os
import sys
import time
import schedule
import requests as _requests
from datetime import datetime, date, timezone, timedelta
from dotenv import load_dotenv
from pathlib import Path

import json
import gspread
from google.oauth2.service_account import Credentials

import naver_api
import sheets
import pharmabros

# .env 로딩
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

MASTER_SHEET_URL = os.getenv("MASTER_SHEET_URL")

# 스토어명 → API 키 매핑
STORE_CREDENTIALS = {
    "nutone":   (os.getenv("NUTONE_CLIENT_ID"),   os.getenv("NUTONE_CLIENT_SECRET")),
    "jdhealth": (os.getenv("JDHEALTH_CLIENT_ID"), os.getenv("JDHEALTH_CLIENT_SECRET")),
    "nutpet":   (os.getenv("NUTPET_CLIENT_ID"),   os.getenv("NUTPET_CLIENT_SECRET")),
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# 공구수수료 별도 시트 (fill_lmno.py와 동일)
COMM_SHEET_URL = os.getenv(
    "COMM_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/1tNYLgKB-rXcwF5ZdjxT3xti4ygvNl6SEJ-x0w8Gmjt0/edit",
)

CREDENTIALS_PATH = str(BASE_DIR / "credentials" / "google-credentials.json")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SOLDOUT_NOTIFIED_FILE = BASE_DIR / "soldout_notified.json"

# 파마브로스 파일공유 — 사장님 구글 계정 OAuth2 + Drive 폴더
PHARMABROS_OAUTH_CLIENT_ID     = os.getenv("OAUTH_CLIENT_ID", "")
PHARMABROS_OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
PHARMABROS_OAUTH_REFRESH_TOKEN = os.getenv("OAUTH_REFRESH_TOKEN", "")
PHARMABROS_DRIVE_FOLDER_ID     = os.getenv("PHARMABROS_DRIVE_FOLDER_ID", "")

# 파마브로스파일공유 판별 함수 (띄어쓰기 무관)
def _is_pharmabros(value: str) -> bool:
    """K열 값이 '파마브로스파일공유'이면 True (공백 무시)."""
    return re.sub(r"\s+", "", str(value)) == "파마브로스파일공유"


def _load_soldout_notified() -> set:
    """품절 알림을 이미 보낸 상품번호 목록 로드."""
    try:
        if SOLDOUT_NOTIFIED_FILE.exists():
            return set(json.loads(SOLDOUT_NOTIFIED_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def _save_soldout_notified(notified: set):
    SOLDOUT_NOTIFIED_FILE.write_text(json.dumps(list(notified)), encoding="utf-8")




# ── 텔레그램 알림 ────────────────────────────────────────
def send_telegram(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        _requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10,
        )
    except Exception:
        pass


# ── 날짜 파싱 ────────────────────────────────────────────
def parse_date(date_str: str) -> date:
    date_str = date_str.strip()
    parts = re.split(r"[.\-/]", date_str)
    if len(parts) == 3:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    raise ValueError(f"날짜 형식을 인식할 수 없습니다: {date_str}")


def extract_product_no(url: str) -> str:
    match = re.search(r"/products/(\d+)", url)
    if not match:
        raise ValueError(f"상품번호를 URL에서 찾을 수 없습니다: {url}")
    return match.group(1)


def _parse_incentive_date(date_str: str):
    """'26.6.1~6.7' → (date(2026,6,1), date(2026,6,7)), (None,None) on failure."""
    s = date_str.strip()
    if "~" not in s:
        return None, None
    left, right = s.split("~", 1)
    left_parts = left.strip().split(".")
    right_parts = right.strip().split(".")
    if len(left_parts) != 3:
        return None, None
    try:
        year = 2000 + int(left_parts[0])
        sm, sd = int(left_parts[1]), int(left_parts[2])
        if len(right_parts) == 2:
            em, ed = int(right_parts[0]), int(right_parts[1])
        elif len(right_parts) == 1:
            em, ed = sm, int(right_parts[0])
        else:
            return None, None
        ey = year + 1 if em < sm else year
        return date(year, sm, sd), date(ey, em, ed)
    except (ValueError, IndexError):
        return None, None


# ── 캠페인 목록 읽기 ─────────────────────────────────────
def load_campaigns() -> list:
    """
    구글 시트(MASTER_SHEET_URL)에서 캠페인 목록을 읽어옵니다.
    오늘이 시작일~종료일 사이인 캠페인만 반환합니다.

    시트 열 구조:
      A: No | B: 제목 | C: 시작일자 | D: 종료일자 | E: 링크 | F: 공유 구글스프레드
    """
    if not MASTER_SHEET_URL:
        print("[오류] .env 파일에 MASTER_SHEET_URL을 입력해주세요.")
        return []

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
        spreadsheet = client.open_by_key(sheet_id)
        gid_match = re.search(r"gid=(\d+)", MASTER_SHEET_URL)
        if gid_match:
            gid = int(gid_match.group(1))
            ws = next(s for s in spreadsheet.worksheets() if s.id == gid)
        else:
            ws = spreadsheet.sheet1
        rows = ws.get_all_records()
    except Exception as e:
        print(f"[오류] 캠페인 시트 읽기 실패: {e}")
        return []

    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    campaigns = []

    for row in rows:
        try:
            title     = str(row.get("제목") or "").strip()
            start_str = str(row.get("시작일자") or "").strip()
            end_str   = str(row.get("종료일자") or "").strip()
            url       = str(row.get("상품링크") or "").strip()
            sheet_url = str(row.get("데이터공유 구글스프레드_인플루언서전달링크") or "").strip()
            store     = str(row.get("스토어") or "").strip().lower()

            if not all([title, start_str, end_str, url, sheet_url, store]):
                continue

            # 재고 읽기 — 헤더에 "재고"가 포함된 열을 유연하게 탐색
            inventory_key = next((k for k in row.keys() if "재고" in k), None)
            inventory_raw = str(row.get(inventory_key) or "").strip() if inventory_key else ""
            if inventory_raw == "0" or inventory_raw.lower() in ("품절", "소진", "종료"):
                print(f"  [스킵] '{title}': 재고 소진으로 캠페인 제외")
                continue
            inventory = int(inventory_raw.replace(",", "")) if inventory_raw.replace(",", "").isdigit() else None

            if store not in STORE_CREDENTIALS:
                print(f"  [경고] 알 수 없는 스토어명: '{store}' (nutone/jdhealth/nutpet 중 하나여야 합니다)")
                continue

            api_id, api_secret = STORE_CREDENTIALS[store]
            if not api_id or not api_secret:
                print(f"  [경고] '{store}' API 키가 .env에 없습니다.")
                continue

            start_date = parse_date(start_str)
            end_date   = parse_date(end_str)

            if not (start_date <= today <= end_date):
                continue

            campaigns.append({
                "title":      title,
                "product_no": extract_product_no(url),
                "url":        url,
                "date_from":  start_date.strftime("%Y-%m-%d"),
                "date_to":    end_date.strftime("%Y-%m-%d"),
                "sheet_url":  sheet_url,
                "api_id":     api_id,
                "api_secret": api_secret,
                "inventory":  inventory,
            })
        except Exception as e:
            print(f"  [경고] 행 파싱 오류: {e}")

    return campaigns


# ── 파마브로스 파일공유 캠페인 읽기 ──────────────────────
def load_pharmabros_campaigns() -> list:
    """
    K열(파마브로스파일공유여부)이 '파마브로스파일공유'인 캠페인 반환.
    - 진행 중인 캠페인 (start <= today <= end)
    - 종료일 다음날 (end + 1일 == today) — 최종 업로드용

    반환 캠페인에 is_final 키 추가:
      is_final=True  → 종료 다음날 최종 업로드
      is_final=False → 진행 중 중간 업로드
    """
    if not MASTER_SHEET_URL:
        return []

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
        spreadsheet = client.open_by_key(sheet_id)
        gid_match = re.search(r"gid=(\d+)", MASTER_SHEET_URL)
        if gid_match:
            gid = int(gid_match.group(1))
            ws = next(s for s in spreadsheet.worksheets() if s.id == gid)
        else:
            ws = spreadsheet.sheet1
        rows = ws.get_all_records()
    except Exception as e:
        print(f"[오류] 파마브로스 캠페인 시트 읽기 실패: {e}")
        return []

    KST_zone = timezone(timedelta(hours=9))
    today    = datetime.now(KST_zone).date()
    campaigns = []

    for row in rows:
        try:
            # K열: 파마브로스파일공유여부
            pharmabros_flag = str(row.get("파마브로스파일공유여부") or "").strip()
            if not _is_pharmabros(pharmabros_flag):
                continue

            title     = str(row.get("제목") or "").strip()
            start_str = str(row.get("시작일자") or "").strip()
            end_str   = str(row.get("종료일자") or "").strip()
            url       = str(row.get("상품링크") or "").strip()
            store     = str(row.get("스토어") or "").strip().lower()

            if not all([title, start_str, end_str, url, store]):
                continue
            if store not in STORE_CREDENTIALS:
                continue

            api_id, api_secret = STORE_CREDENTIALS[store]
            if not api_id or not api_secret:
                continue

            start_date = parse_date(start_str)
            end_date   = parse_date(end_str)

            is_active    = start_date <= today <= end_date
            is_final_day = (today == end_date + timedelta(days=1))
            is_delete_day = (today == pharmabros.get_delete_date(end_str))

            if not (is_active or is_final_day or is_delete_day):
                continue

            campaigns.append({
                "title":         title,
                "product_no":    extract_product_no(url),
                "url":           url,
                "date_from":     start_date.strftime("%Y-%m-%d"),
                "date_to":       end_date.strftime("%Y-%m-%d"),
                "api_id":        api_id,
                "api_secret":    api_secret,
                "is_final":      is_final_day,
                "is_delete_day": is_delete_day,
            })
        except Exception as e:
            print(f"  [경고] 파마브로스 행 파싱 오류: {e}")

    return campaigns


# ── 캠페인 전체 목록 읽기 (종료된 것 포함) ───────────────
def load_all_campaigns() -> list:
    """시작일이 오늘 이전인 모든 캠페인 반환 (종료 포함)."""
    if not MASTER_SHEET_URL:
        return []

    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
        spreadsheet = client.open_by_key(sheet_id)
        gid_match = re.search(r"gid=(\d+)", MASTER_SHEET_URL)
        if gid_match:
            gid = int(gid_match.group(1))
            ws = next(s for s in spreadsheet.worksheets() if s.id == gid)
        else:
            ws = spreadsheet.sheet1
        rows = ws.get_all_records()
    except Exception as e:
        print(f"[오류] 캠페인 시트 읽기 실패: {e}")
        return []

    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    campaigns = []

    for row in rows:
        try:
            title     = str(row.get("제목") or "").strip()
            start_str = str(row.get("시작일자") or "").strip()
            end_str   = str(row.get("종료일자") or "").strip()
            url       = str(row.get("상품링크") or "").strip()
            store     = str(row.get("스토어") or "").strip().lower()

            if not all([title, start_str, end_str, url, store]):
                continue
            if store not in STORE_CREDENTIALS:
                continue

            api_id, api_secret = STORE_CREDENTIALS[store]
            if not api_id or not api_secret:
                continue

            start_date = parse_date(start_str)
            end_date   = parse_date(end_str)

            if start_date > today:
                continue

            sheet_url = str(row.get("데이터공유 구글스프레드_인플루언서전달링크") or "").strip()
            campaigns.append({
                "title":      title,
                "product_no": extract_product_no(url),
                "url":        url,
                "sheet_url":  sheet_url,
                "date_from":  start_date.strftime("%Y-%m-%d"),
                "date_to":    end_date.strftime("%Y-%m-%d"),
                "api_id":     api_id,
                "api_secret": api_secret,
                "store":      store,
                "is_ended":   end_date < today,
            })
        except Exception as e:
            print(f"  [경고] 행 파싱 오류: {e}")

    return campaigns


# ── 인센티브 시트에서 캠페인 읽기 ─────────────────────────
def load_incentive_campaigns(min_year_month: str = "2026-05") -> list:
    """인센티브정산 시트(COMM_SHEET_URL)에서 공구 캠페인 목록 읽기.
    2행 1세트(공구+공구링크) 형식을 파싱한다.
    """
    if not COMM_SHEET_URL:
        return []
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", COMM_SHEET_URL).group(1)
        ss = client.open_by_key(sid)
    except Exception as e:
        print(f"  [인센티브시트 읽기 실패] {e}")
        return []

    KST = timezone(timedelta(hours=9))
    today = datetime.now(KST).date()
    raw = []

    for ws_tab in ss.worksheets():
        ym = _tab_year_month(ws_tab.title)
        if not ym or ym < min_year_month:
            continue
        try:
            rows = ws_tab.get_all_values()
        except Exception:
            continue

        i = 0
        while i < len(rows):
            row = rows[i]
            a = row[0].strip() if len(row) > 0 else ""
            b = row[1].strip() if len(row) > 1 else ""
            d = row[3].strip() if len(row) > 3 else ""

            if b == "공구" and "~" in a:
                start_d, end_d = _parse_incentive_date(a)
                channel = d
                j = i + 1
                while j < len(rows):
                    nr = rows[j]
                    nb = nr[1].strip() if len(nr) > 1 else ""
                    na = nr[0].strip() if len(nr) > 0 else ""
                    if nb == "공구" or (na and "~" in na):
                        break
                    if nb == "공구링크":
                        pname = nr[4].strip() if len(nr) > 4 else ""
                        purl  = nr[5].strip() if len(nr) > 5 else ""
                        # M열(index 12) 수수료 추출
                        comm_raw = nr[12].strip() if len(nr) > 12 else ""
                        comm_m = re.search(r'(\d+(?:\.\d+)?)', comm_raw)
                        comm_val = float(comm_m.group(1)) if comm_m else ""
                        if start_d and end_d and purl:
                            sm = re.search(r'brand\.naver\.com/(\w+)', purl)
                            store = sm.group(1) if sm else ""
                            pm = re.search(r'/products/(\d+)', purl)
                            pno = pm.group(1) if pm else ""
                            if store in STORE_CREDENTIALS and pno:
                                aid, asec = STORE_CREDENTIALS[store]
                                if aid and asec:
                                    raw.append({
                                        "channel_name": channel,
                                        "product_name": pname,
                                        "incentive_comm": comm_val,
                                        "product_no":   pno,
                                        "url":          purl.split("?")[0],
                                        "date_from":    start_d.strftime("%Y-%m-%d"),
                                        "date_to":      end_d.strftime("%Y-%m-%d"),
                                        "api_id":       aid,
                                        "api_secret":   asec,
                                        "store":        store,
                                        "sheet_url":    "",
                                        "is_ended":     end_d < today,
                                    })
                    j += 1
                i = j
            else:
                i += 1

    from collections import Counter
    ch_counts = Counter(c["channel_name"] for c in raw)
    for c in raw:
        if ch_counts[c["channel_name"]] > 1:
            c["title"] = f"{c['channel_name']},{c['product_name']}"
        else:
            c["title"] = c["channel_name"]

    return raw


def _calc_totals(sales_data: list) -> tuple:
    """판매 데이터에서 총 주문수, 총 제품수 계산 (개별 시트와 동일 방식)."""
    aggregated = sheets._aggregate(sales_data)
    total_orders = sum(r["daily_orders"] for r in aggregated)
    total_products = sum(r["daily_products"] for r in aggregated)
    return total_orders, total_products


def _read_master_sheet1_lm() -> dict:
    """시트1의 I열(매출), J열(공구수수료) 읽기.
    반환: {제목: {"revenue": int|"", "commission": float|""}}
    """
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
        ss = client.open_by_key(sheet_id)
        ws = ss.sheet1
        rows = ws.get_all_records()
        result = {}
        for row in rows:
            title = str(row.get("제목", "")).strip()
            if not title:
                continue
            # I열 매출 (숫자, 쉼표 제거)
            raw_rev = str(row.get("매출", "")).replace(",", "").strip()
            revenue = int(raw_rev) if raw_rev.lstrip("-").isdigit() else ""
            # J열 공구수수료 — 숫자 추출 ("40", "40%", "공구수수료40%" 모두 처리)
            raw_comm = str(row.get("공구수수료(%,vat포함)", "")).strip()
            m = re.search(r"(\d+(?:\.\d+)?)", raw_comm)
            commission = float(m.group(1)) if m else ""
            result[title] = {"revenue": revenue, "commission": commission}
        return result
    except Exception as e:
        print(f"  [시트1 읽기 실패] {e}")
        return {}


def _extract_commission_raw(raw: str):
    """수수료 문자열에서 숫자 추출. '공구수수료40%', '40%', '40' 모두 처리."""
    s = str(raw).strip()
    m = re.search(r'공구수수료\s*(\d+(?:\.\d+)?)\s*%', s)
    if m:
        return float(m.group(1))
    if '컨텐츠' not in s:
        m = re.search(r'(\d+(?:\.\d+)?)', s)
        if m:
            return float(m.group(1))
    return ""


def _tab_year_month(tab_title: str) -> str:
    """탭 제목에서 'YYYY-MM' 추출. '26.4', '4월' 등 처리."""
    t = tab_title.strip()
    m = re.match(r'(\d{2})\.(\d{1,2})', t)
    if m:
        return f"20{m.group(1)}-{int(m.group(2)):02d}"
    m2 = re.match(r'(\d{1,2})\s*월', t)
    if m2:
        return f"2026-{int(m2.group(1)):02d}"
    return ""


def _read_comm_map() -> tuple:
    """공구수수료 시트에서 채널명→수수료 매핑 읽기.
    반환: (comm_map, comm_by_product_no)
      comm_map             = {채널명(소문자): 수수료율(float)}
      comm_by_product_no   = {상품번호: [(수수료율, "YYYY-MM"), ...]}
    """
    comm_map = {}
    comm_by_product_no = {}
    if not COMM_SHEET_URL:
        return comm_map, comm_by_product_no
    try:
        creds  = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
        client = gspread.authorize(creds)
        comm_sid = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", COMM_SHEET_URL).group(1)
        ss_comm  = client.open_by_key(comm_sid)

        def _pno(url):
            m = re.search(r"/products/(\d+)", str(url))
            return m.group(1) if m else ""

        for ws_tab in ss_comm.worksheets():
            title_lower = ws_tab.title.lower()
            if not any(x in title_lower for x in ["월", ".1", ".2", ".3", ".4", ".5", ".6", ".7", ".8", ".9"]):
                continue
            ym = _tab_year_month(ws_tab.title)
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
                            comm  = _extract_commission_raw(m_val)
                            if comm != "":
                                if channel:
                                    comm_map[channel.lower()] = comm
                                for cell in nrow:
                                    pno = _pno(str(cell))
                                    if pno and ym:
                                        comm_by_product_no.setdefault(pno, []).append((comm, ym))
                        j += 1
                    i = j
                else:
                    i += 1
    except Exception as e:
        print(f"  [공구수수료 시트 읽기 실패] {e}")
    return comm_map, comm_by_product_no


def update_summary_tab():
    """마스터 시트의 '캠페인 실적' 탭 업데이트."""
    KST = timezone(timedelta(hours=9))
    all_campaigns = load_all_campaigns()

    # 인센티브 시트에서 추가 캠페인 병합 (시트1에 없는 것만)
    incentive = load_incentive_campaigns()
    if incentive:
        seen = {(c["product_no"], c["date_from"], c["date_to"]) for c in all_campaigns}
        # 시트1 sheet_url 매핑 (인센티브 캠페인에 연결용)
        s1_sheet_by_pno = {}
        for c in all_campaigns:
            if c.get("sheet_url"):
                s1_sheet_by_pno.setdefault(c["product_no"], []).append(c)
        added = 0
        for ic in incentive:
            key = (ic["product_no"], ic["date_from"], ic["date_to"])
            if key not in seen:
                # 시트1에서 같은 상품번호의 sheet_url 연결
                for s1c in s1_sheet_by_pno.get(ic["product_no"], []):
                    if s1c["date_from"] == ic["date_from"] or s1c["date_to"] == ic["date_to"]:
                        ic["sheet_url"] = s1c["sheet_url"]
                        break
                all_campaigns.append(ic)
                seen.add(key)
                added += 1
        if added:
            print(f"  [인센티브 시트] {added}개 캠페인 추가 로드")

    if not all_campaigns:
        return

    existing = sheets.read_summary_tab(MASTER_SHEET_URL)
    today_str = datetime.now(KST).date().strftime("%Y-%m-%d")
    changed = False

    # 1) 기존 행: 상태 빈칸 채우기 + URL 정리
    for r in existing.values():
        if not r.get("status"):
            r["status"] = "완료" if str(r.get("date_to", "")) < today_str else "진행중"
            changed = True
        if r.get("url") and "?" in r["url"]:
            r["url"] = r["url"].split("?")[0]
            changed = True

    # 2) 새로 처리할 캠페인:
    #    - 종료됐고 탭에 없는 신규
    #    - 제품명이 없거나 캠페인 제목과 동일한 경우 (잘못된 짧은 이름)
    fetch_campaigns = [
        c for c in all_campaigns
        if (c["is_ended"] and c["title"] not in existing)
        or (c["title"] in existing
            and (not existing[c["title"]].get("product_name")
                 or existing[c["title"]].get("product_name") == c["title"]))
    ]

    new_rows = []
    if fetch_campaigns:
        print(f"\n  [실적 집계] 캠페인 {len(fetch_campaigns)}개 처리 중...")
        for campaign in fetch_campaigns:
            clean_url = campaign["url"].split("?")[0]
            clean_url = campaign["url"].split("?")[0]
            # 주문 상세에서 product_name 추출
            try:
                sales, product_name = naver_api.get_sales_data(
                    client_id=campaign["api_id"],
                    client_secret=campaign["api_secret"],
                    product_no=campaign["product_no"],
                    date_from=campaign["date_from"],
                    date_to=campaign["date_to"],
                )
            except Exception:
                sales, product_name = [], ""
            # 주문 0건이면 상품 API로 폴백
            if not product_name:
                product_name = naver_api.get_product_name(
                    campaign["api_id"], campaign["api_secret"], campaign["product_no"]
                )
            # API에서도 못 가져오면 인센티브 시트 제품명 사용
            if not product_name:
                product_name = campaign.get("product_name", "")
            # 합계는 개별 시트에서 읽기 (정확도 우선)
            try:
                total_orders, total_products, _ = sheets.read_totals_from_sheet(campaign["sheet_url"])
            except Exception:
                total_orders, total_products = _calc_totals(sales)

            new_rows.append({
                "title":          campaign["title"],
                "product_name":   product_name,
                "url":            clean_url,
                "store":          campaign["store"],
                "date_from":      campaign["date_from"],
                "date_to":        campaign["date_to"],
                "total_orders":   total_orders,
                "total_products": total_products,
                "status":         "완료" if campaign["is_ended"] else "진행중",
                "updated_at":     datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
            })
        changed = True

    if not changed and not new_rows:
        # 매출은 있는데 수수료가 없는 기존 행이 있으면 채우기 위해 계속 진행
        needs_fill = any(
            isinstance(row.get("revenue"), int) and row["revenue"] > 0
            and row.get("commission", "") == ""
            for row in existing.values()
        )
        if not needs_fill:
            return

    new_titles = {r["title"] for r in new_rows}
    kept_rows = [r for r in existing.values() if r["title"] not in new_titles]
    all_rows = new_rows + kept_rows
    all_rows.sort(key=lambda r: str(r.get("date_to", "")), reverse=True)

    # ── L/M/N/O열 계산 ────────────────────────────────────────
    # 이익계산참고사항 로드 (시트 읽기, 1회)
    profit_params = []
    try:
        profit_params = sheets.read_profit_params(MASTER_SHEET_URL)
    except Exception as e:
        print(f"  [이익참고] 읽기 실패: {e}")

    # 시트1 J열(공구수수료) 로드 (시트 읽기, 1회)
    master1_lm = _read_master_sheet1_lm()

    # 공구수수료 별도 시트 로드 (시트1에 수수료 없는 행 대비 fallback)
    comm_map, comm_by_product_no = _read_comm_map()

    # 새로 집계되는 캠페인 제목 집합 (이 타이밍에만 매출 API 호출)
    fetch_titles   = {r["title"] for r in new_rows}
    title_to_campaign = {c["title"]: c for c in all_campaigns}

    row_extras = {}
    for row in all_rows:
        title        = row["title"]
        product_name = row.get("product_name", "") or title
        pp           = sheets._match_profit(product_name, profit_params)

        # ── 매출(L): 이미 계산된 캠페인은 캐시 그대로 ──────────
        revenue_refetched = False
        if title not in fetch_titles:
            # kept_rows: 기존 L열 값 재사용 (API 호출 없음)
            revenue = row.get("revenue", "")
            # 매출 누락 + 종료된 캠페인 → API 재조회
            if revenue == "" and str(row.get("date_to", "")) < today_str:
                campaign = title_to_campaign.get(title, {})
                if campaign and campaign.get("api_id"):
                    try:
                        revenue = naver_api.get_campaign_revenue(
                            campaign["api_id"], campaign["api_secret"],
                            campaign["product_no"],
                            campaign.get("date_from", str(row.get("date_from", ""))),
                            campaign.get("date_to", str(row.get("date_to", ""))),
                        )
                        if isinstance(revenue, int):
                            print(f"  [매출 재조회] {title}: {revenue:,}원")
                            revenue_refetched = True
                    except Exception as e:
                        print(f"  [매출 재조회 실패] {title}: {e}")
        else:
            # fetch_campaigns: 종료 시 1회만 API 조회
            s1_rev = master1_lm.get(title, {}).get("revenue", "")
            if isinstance(s1_rev, int) and s1_rev > 0:
                revenue = s1_rev          # 시트1에 수기 입력값 우선
            else:
                campaign = title_to_campaign.get(title, {})
                if campaign:
                    try:
                        revenue = naver_api.get_campaign_revenue(
                            campaign["api_id"], campaign["api_secret"],
                            campaign["product_no"],
                            campaign["date_from"], campaign["date_to"],
                        )
                    except Exception as e:
                        print(f"  [매출조회 실패] {title}: {e}")
                        revenue = ""
                else:
                    revenue = ""

        # ── 공구수수료(M): 시트1 J열 우선, 없으면 캐시, 그래도 없으면 공구수수료 시트 fallback ──
        s1_comm    = master1_lm.get(title, {}).get("commission", "")
        commission = s1_comm if s1_comm != "" else row.get("commission", "")

        if commission == "" and comm_map:
            # 1순위: 채널명 정확 매칭
            commission = comm_map.get(title.lower(), "")
            # 2순위: 채널명 부분 매칭
            if commission == "":
                t_lower = title.lower()
                for key, val in comm_map.items():
                    if (len(t_lower) >= 3 and t_lower in key) or (len(key) >= 3 and key in t_lower):
                        commission = val
                        print(f"  [수수료 부분매칭] '{title}' ↔ '{key}' → {val}%")
                        break
            # 3순위: 상품번호 + 캠페인 월 매칭
            if commission == "":
                row_url = row.get("url", "") or ""
                pno_m = re.search(r"/products/(\d+)", str(row_url))
                if pno_m:
                    pno        = pno_m.group(1)
                    campaign_ym = str(row.get("date_from", ""))[:7]
                    for c, ym in comm_by_product_no.get(pno, []):
                        if ym == campaign_ym:
                            commission = c
                            print(f"  [수수료 상품번호+월 매칭] '{title}' → {c}%")
                            break

        # 4순위: 인센티브 시트에서 직접 가져온 수수료
        if commission == "":
            campaign_info_comm = title_to_campaign.get(title, {})
            ic_comm = campaign_info_comm.get("incentive_comm", "")
            if ic_comm != "":
                commission = ic_comm
                print(f"  [수수료 인센티브시트] '{title}' → {ic_comm}%")

        # ── 옵션별 원가 계산 (멀티제품 캠페인) ──────────────────
        # 새로 종료된 캠페인(fetch_titles)에만 개별 시트 읽기 실행
        # kept_rows는 이미 N 수식이 있으므로 매 시간 재조회 불필요
        option_cost_total = 0
        option_delivery   = 0
        option_ch_comm    = 0.0
        opt_matched_names = []
        campaign_info = title_to_campaign.get(title, {})
        c_sheet_url   = campaign_info.get("sheet_url", "")
        if (title in fetch_titles or revenue_refetched) and c_sheet_url and isinstance(revenue, int) and revenue > 0:
            try:
                option_totals = sheets.read_option_totals_from_sheet(c_sheet_url)
                if len(option_totals) >= 2:
                    opt_res = sheets.calc_option_cost(option_totals, profit_params)
                    if opt_res["total_cost"] > 0:
                        option_cost_total = opt_res["total_cost"]
                        option_delivery   = opt_res["delivery"]
                        option_ch_comm    = opt_res["ch_comm"]
                        opt_matched_names = opt_res["matched_names"]
                        print(f"  [멀티원가] {title[:30]}: {' + '.join(opt_matched_names)} = {option_cost_total:,}원")
            except Exception as e:
                print(f"  [옵션별 원가 실패] {title}: {e}")

        # P열 매칭 제품명 (멀티제품이면 "+", 단일이면 단독)
        if opt_matched_names:
            matched_name = " + ".join(opt_matched_names)
        else:
            matched_name = str(pp.get("name", "")) if pp else ""

        row_extras[title] = {
            "revenue":           revenue,
            "commission":        commission,
            "profit_params":     pp,
            "matched_name":      matched_name,
            "option_cost_total": option_cost_total,
            "option_delivery":   option_delivery,
            "option_ch_comm":    option_ch_comm,
        }

    sheets.write_summary_tab(MASTER_SHEET_URL, all_rows, row_extras=row_extras)
    print(f"  → 캠페인 실적 탭 업데이트 완료\n")


# ── 메인 실행 ────────────────────────────────────────────
def run_once():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  실행 시작: {now_str}")
    print(f"{'='*55}")

    if not MASTER_SHEET_URL:
        print("[오류] .env 파일에 MASTER_SHEET_URL을 입력해주세요.")
        return

    campaigns = load_campaigns()

    if not campaigns:
        print(f"  오늘({date.today()}) 진행 중인 캠페인이 없습니다.")
        return

    print(f"  진행 중인 캠페인: {len(campaigns)}개\n")

    soldout_notified = _load_soldout_notified()

    for i, campaign in enumerate(campaigns, 1):
        print(f"[{i}/{len(campaigns)}] {campaign['title'][:45]}")
        try:
            sales, product_name = naver_api.get_sales_data(
                client_id=campaign["api_id"],
                client_secret=campaign["api_secret"],
                product_no=campaign["product_no"],
                date_from=campaign["date_from"],
                date_to=campaign["date_to"],
            )
            remaining = sheets.write_to_sheet(
                spreadsheet_url=campaign["sheet_url"],
                product_title=campaign["title"],
                sales_data=sales,
                date_from=campaign["date_from"],
                date_to=campaign["date_to"],
                product_url=campaign.get("url", ""),
                product_name=product_name,
                inventory=campaign.get("inventory"),
            )
            product_no = campaign["product_no"]
            if remaining is not None:
                if remaining <= 0:
                    # 남은재고 0 → 품절 알림 (아직 알림 안 보낸 경우만)
                    if product_no not in soldout_notified:
                        inv = campaign.get("inventory") or 0
                        send_telegram(
                            f"🔴 [품절 알림]\n\n"
                            f"📦 상품명: {campaign['title'][:40]}\n"
                            f"📊 초기재고: {inv:,}개\n"
                            f"✅ 판매 완료: 재고 소진\n"
                            f"🏪 스토어: {next((k for k, v in STORE_CREDENTIALS.items() if v[0] == campaign.get('api_id')), '')}\n\n"
                            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                        )
                        soldout_notified.add(product_no)
                        _save_soldout_notified(soldout_notified)
                        print(f"  [품절 알림] 텔레그램 발송 완료")
                else:
                    # 재고 보충됨 → 알림 기록 초기화 (다음 품절 시 재알림 가능)
                    if product_no in soldout_notified:
                        soldout_notified.discard(product_no)
                        _save_soldout_notified(soldout_notified)
                        print(f"  [재고 보충 감지] 품절 알림 기록 초기화")
            print(f"  완료\n")
        except Exception as e:
            print(f"  [오류] {e}\n")
            err_str = str(e)
            err_lower = err_str.lower()
            # 품절·판매중지·404 오류는 Telegram 알림 없이 조용히 넘어감
            if ("404" in err_str or "not found" in err_lower
                    or "품절" in err_str or "sold_out" in err_lower
                    or "no such product" in err_lower):
                print(f"  [스킵] 품절 또는 판매중지 상품으로 판단 — 오류 알림 생략\n")
                continue
            if "403" in err_str or "Forbidden" in err_str:
                cause = "네이버 API 인증 오류 (IP 차단 또는 API 키 만료)"
                action = "→ 네이버 커머스 API에서 IP 및 API 키를 확인해주세요"
            elif "401" in err_str or "Unauthorized" in err_str:
                cause = "네이버 API 키 오류 (ID 또는 Secret 불일치)"
                action = "→ .env 파일의 API 키를 확인해주세요"
            elif "spreadsheet" in err_str.lower() or "gspread" in err_str.lower():
                cause = "구글 시트 접근 오류 (권한 또는 URL 문제)"
                action = "→ 인플루언서 구글 시트가 서비스 계정과 공유되어 있는지 확인해주세요"
            elif "products" in err_str or "상품번호" in err_str:
                cause = "상품 URL에서 상품번호를 찾을 수 없음"
                action = "→ 캠페인 시트의 상품링크 URL을 확인해주세요"
            else:
                cause = f"알 수 없는 오류: {err_str[:100]}"
                action = "→ 개발자에게 문의해주세요"

            send_telegram(
                f"⚠️ [인플루언서 프로그램 오류]\n\n"
                f"📦 상품명: {campaign['title'][:40]}\n"
                f"🔗 인플루언서 시트: {campaign.get('sheet_url', '')}\n"
                f"🏪 스토어: {next((k for k, v in STORE_CREDENTIALS.items() if v[0] == campaign.get('api_id')), campaign.get('api_id', ''))}\n\n"
                f"❌ 원인: {cause}\n"
                f"{action}\n\n"
                f"🕐 발생시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

    update_summary_tab()

    # ── 파마브로스 파일공유 ───────────────────────────────
    _run_pharmabros_if_needed()

    print(f"{'='*55}\n")


def _run_pharmabros_if_needed(force: bool = False):
    """
    파마브로스파일공유여부=파마브로스파일공유 캠페인을 찾아,
    현재 시각이 업로드 시간대(KST 10시/14시/16시)이면 실행.
    force=True → 시간 체크 없이 즉시 실행 (테스트용)
    """
    global PHARMABROS_DRIVE_FOLDER_ID

    pb_campaigns = load_pharmabros_campaigns()
    if not pb_campaigns:
        print("  [파마브로스] 대상 캠페인 없음 (K열 확인 필요)")
        return

    print(f"\n  [파마브로스] 대상 캠페인 {len(pb_campaigns)}개 확인")

    for campaign in pb_campaigns:
        title = campaign["title"]

        # ── 자동 삭제 대상일 처리 ────────────────────────────
        if campaign.get("is_delete_day"):
            now_h = datetime.now(timezone(timedelta(hours=9))).hour
            if not force and now_h != 10:
                print(f"  [파마브로스] '{title[:30]}' — 삭제 예정일이나 10시 아님, 스킵")
                continue
            try:
                deleted = pharmabros.delete_campaign_files(
                    client_id=PHARMABROS_OAUTH_CLIENT_ID,
                    client_secret=PHARMABROS_OAUTH_CLIENT_SECRET,
                    refresh_token=PHARMABROS_OAUTH_REFRESH_TOKEN,
                    folder_id=PHARMABROS_DRIVE_FOLDER_ID,
                    title=title,
                )
                if deleted:
                    print(f"  [파마브로스] 🗑️ '{title[:30]}' 최종 파일 자동 삭제 완료: {deleted}")
                else:
                    print(f"  [파마브로스] '{title[:30]}' 삭제할 파일 없음 (이미 삭제됨)")
            except Exception as e:
                print(f"  [파마브로스 삭제 오류] {e}")
            continue  # 업로드 로직은 실행하지 않음
        start_date = campaign["date_from"]
        end_date   = campaign["date_to"]
        is_final   = campaign["is_final"]

        if not force:
            run_flag, _ = pharmabros.should_run(start_date, end_date)
            if not run_flag:
                print(f"  [파마브로스] '{title[:30]}' — 현재 시각은 업로드 시간대 아님, 스킵")
                continue
        else:
            print(f"  [파마브로스] '{title[:30]}' — 강제 실행 모드 (시간 체크 무시)")

        try:
            # OAuth2 토큰 + 폴더 ID 확인
            if not all([PHARMABROS_OAUTH_CLIENT_ID,
                        PHARMABROS_OAUTH_CLIENT_SECRET,
                        PHARMABROS_OAUTH_REFRESH_TOKEN,
                        PHARMABROS_DRIVE_FOLDER_ID]):
                print(
                    "  [파마브로스] ⚠️  .env에 OAuth2 설정이 없습니다.\n"
                    "  → get_drive_token.py 를 실행해서 토큰을 발급받으세요."
                )
                continue  # 기존 기능에 영향 없이 스킵

            pharmabros.run_pharmabros(
                campaign=campaign,
                credentials_path=CREDENTIALS_PATH,
                oauth_client_id=PHARMABROS_OAUTH_CLIENT_ID,
                oauth_client_secret=PHARMABROS_OAUTH_CLIENT_SECRET,
                oauth_refresh_token=PHARMABROS_OAUTH_REFRESH_TOKEN,
                drive_folder_id=PHARMABROS_DRIVE_FOLDER_ID,
            )
        except Exception as e:
            print(f"  [파마브로스 오류] {e}")
            err_str = str(e)
            if "invalid_grant" in err_str:
                cause  = "구글 인증 토큰 만료 또는 취소됨"
                action = (
                    "조치 방법:\n"
                    "1. 사장님 PC에서 python get_drive_token.py 실행\n"
                    "2. 새 OAUTH_REFRESH_TOKEN 복사\n"
                    "3. SSH 서버에서 아래 명령어 실행:\n"
                    "sed -i \"s|OAUTH_REFRESH_TOKEN=.*|OAUTH_REFRESH_TOKEN=새토큰값|\" "
                    "~/influencer-sales-data-share/'influencer data shared'/.env"
                )
            elif "503" in err_str or "Transient" in err_str or "transient" in err_str:
                cause  = "구글 서버 일시적 오류 (503) — 자동 재시도 예정"
                action = "별도 조치 불필요. 5~10분 후 자동으로 재시도됩니다."
            elif "403" in err_str or "Forbidden" in err_str:
                cause  = "구글 드라이브 접근 권한 오류"
                action = "드라이브 폴더 공유 설정 또는 OAuth 앱 권한을 확인해주세요."
            elif "quota" in err_str.lower() or "429" in err_str:
                cause  = "구글 API 할당량 초과"
                action = "잠시 후 자동 재시도됩니다. 반복 시 문의해주세요."
            else:
                cause  = f"알 수 없는 오류: {err_str[:120]}"
                action = "개발자에게 문의해주세요."
            send_telegram(
                f"⚠️ [파마브로스 파일공유 오류]\n\n"
                f"📦 캠페인: {title[:40]}\n"
                f"❌ 원인: {cause}\n\n"
                f"🔧 {action}\n\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )


def main():
    # 파마브로스 테스트 모드: 시간 체크 없이 파마브로스만 즉시 실행
    if "--test-pharmabros" in sys.argv:
        print("\n[테스트 모드] 파마브로스 파일공유 강제 실행\n")
        _run_pharmabros_if_needed(force=True)
        return

    # GitHub Actions 또는 --once 플래그: 1회 실행 후 종료
    if "--once" in sys.argv or os.getenv("GITHUB_ACTIONS"):
        run_once()
        return

    # 로컬 PC: 시작 즉시 1회 + 3시간마다 반복
    print("인플루언서 판매 데이터 공유 프로그램 시작")
    print("3시간마다 자동 업데이트  |  종료: Ctrl+C\n")
    run_once()
    schedule.every(3).hours.do(run_once)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
