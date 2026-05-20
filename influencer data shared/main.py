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

CREDENTIALS_PATH = str(BASE_DIR / "credentials" / "google-credentials.json")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SOLDOUT_NOTIFIED_FILE = BASE_DIR / "soldout_notified.json"

# 파마브로스 파일공유 드라이브 폴더 ID (.env에 저장, 없으면 자동 생성)
PHARMABROS_DRIVE_FOLDER_ID = os.getenv("PHARMABROS_DRIVE_FOLDER_ID", "")
# 폴더 최초 생성 시 편집자 권한을 줄 사장님 이메일 (.env 또는 기본값)
PHARMABROS_OWNER_EMAIL = os.getenv("PHARMABROS_OWNER_EMAIL", "hsbchong7@gmail.com")

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

            is_active = start_date <= today <= end_date
            is_final_day = (today == end_date + timedelta(days=1))

            if not (is_active or is_final_day):
                continue

            campaigns.append({
                "title":      title,
                "product_no": extract_product_no(url),
                "url":        url,
                "date_from":  start_date.strftime("%Y-%m-%d"),
                "date_to":    end_date.strftime("%Y-%m-%d"),
                "api_id":     api_id,
                "api_secret": api_secret,
                "is_final":   is_final_day,
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


def _calc_totals(sales_data: list) -> tuple:
    """판매 데이터에서 총 주문수, 총 제품수 계산 (개별 시트와 동일 방식)."""
    aggregated = sheets._aggregate(sales_data)
    total_orders = sum(r["daily_orders"] for r in aggregated)
    total_products = sum(r["daily_products"] for r in aggregated)
    return total_orders, total_products


def update_summary_tab():
    """마스터 시트의 '캠페인 실적' 탭 업데이트."""
    KST = timezone(timedelta(hours=9))
    all_campaigns = load_all_campaigns()
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
        return

    new_titles = {r["title"] for r in new_rows}
    kept_rows = [r for r in existing.values() if r["title"] not in new_titles]
    all_rows = new_rows + kept_rows
    all_rows.sort(key=lambda r: str(r.get("date_to", "")), reverse=True)

    sheets.write_summary_tab(MASTER_SHEET_URL, all_rows)
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
        title      = campaign["title"]
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
            # 드라이브 폴더 확인/생성 (최초 1회만 생성됨)
            if not PHARMABROS_DRIVE_FOLDER_ID:
                PHARMABROS_DRIVE_FOLDER_ID = pharmabros.ensure_folder(
                    CREDENTIALS_PATH,
                    owner_email=PHARMABROS_OWNER_EMAIL,
                )
                # .env에 저장하여 다음 실행 시 재사용
                _save_pharmabros_folder_id(PHARMABROS_DRIVE_FOLDER_ID)

            folder_url, uploaded_urls = pharmabros.run_pharmabros(
                campaign=campaign,
                credentials_path=CREDENTIALS_PATH,
                folder_id=PHARMABROS_DRIVE_FOLDER_ID,
            )
            # 업로드된 파일 목록 텍스트
            files_text = "\n".join(
                f"  📄 {name}" for name, _url in uploaded_urls
            )
            label = "최종" if is_final else "중간"
            send_telegram(
                f"✅ [파마브로스 {label} 업로드 완료]\n\n"
                f"📦 캠페인: {title[:40]}\n"
                f"📅 기간: {start_date} ~ {end_date}\n"
                f"📁 공유 폴더: {folder_url}\n\n"
                f"업로드된 파일:\n{files_text}\n\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )
        except Exception as e:
            print(f"  [파마브로스 오류] {e}")
            send_telegram(
                f"⚠️ [파마브로스 파일공유 오류]\n\n"
                f"📦 캠페인: {title[:40]}\n"
                f"❌ 오류: {str(e)[:200]}\n"
                f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )


def _save_pharmabros_folder_id(folder_id: str):
    """생성된 드라이브 폴더 ID를 .env 파일에 저장."""
    env_path = BASE_DIR / ".env"
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
        else:
            content = ""

        if "PHARMABROS_DRIVE_FOLDER_ID" in content:
            # 기존 값 교체
            import re as _re
            content = _re.sub(
                r"PHARMABROS_DRIVE_FOLDER_ID=.*",
                f"PHARMABROS_DRIVE_FOLDER_ID={folder_id}",
                content,
            )
        else:
            content = content.rstrip("\n") + f"\nPHARMABROS_DRIVE_FOLDER_ID={folder_id}\n"

        env_path.write_text(content, encoding="utf-8")
        print(f"  [파마브로스] 드라이브 폴더 ID 저장 완료: {folder_id}")
    except Exception as e:
        print(f"  [파마브로스] .env 저장 실패 (무시): {e}")


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
