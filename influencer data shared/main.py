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
from datetime import datetime, date
from dotenv import load_dotenv
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

import naver_api
import sheets

# .env 로딩
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
MASTER_SHEET_URL    = os.getenv("MASTER_SHEET_URL")

CREDENTIALS_PATH = str(BASE_DIR / "credentials" / "google-credentials.json")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


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
        ws = client.open_by_key(sheet_id).sheet1
        rows = ws.get_all_records()
    except Exception as e:
        print(f"[오류] 캠페인 시트 읽기 실패: {e}")
        return []

    today = date.today()
    campaigns = []

    for row in rows:
        try:
            title     = str(row.get("제목") or "").strip()
            start_str = str(row.get("시작일자") or "").strip()
            end_str   = str(row.get("종료일자") or "").strip()
            url       = str(row.get("상품링크") or "").strip()
            sheet_url = str(row.get("데이터공유 구글스프레드_인플루언서전달링크") or "").strip()

            if not all([title, start_str, end_str, url, sheet_url]):
                continue

            start_date = parse_date(start_str)
            end_date   = parse_date(end_str)

            # 오늘이 캠페인 기간 내에 있는 행만 처리
            if not (start_date <= today <= end_date):
                continue

            campaigns.append({
                "title":      title,
                "product_no": extract_product_no(url),
                "date_from":  start_date.strftime("%Y-%m-%d"),
                "date_to":    end_date.strftime("%Y-%m-%d"),
                "sheet_url":  sheet_url,
            })
        except Exception as e:
            print(f"  [경고] 행 파싱 오류: {e}")

    return campaigns


# ── 메인 실행 ────────────────────────────────────────────
def run_once():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  실행 시작: {now_str}")
    print(f"{'='*55}")

    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        print("[오류] .env 파일에 네이버 API 키를 입력해주세요.")
        return

    campaigns = load_campaigns()

    if not campaigns:
        print(f"  오늘({date.today()}) 진행 중인 캠페인이 없습니다.")
        return

    print(f"  진행 중인 캠페인: {len(campaigns)}개\n")

    for i, campaign in enumerate(campaigns, 1):
        print(f"[{i}/{len(campaigns)}] {campaign['title'][:45]}")
        try:
            sales = naver_api.get_sales_data(
                client_id=NAVER_CLIENT_ID,
                client_secret=NAVER_CLIENT_SECRET,
                product_no=campaign["product_no"],
                date_from=campaign["date_from"],
                date_to=campaign["date_to"],
            )
            sheets.write_to_sheet(
                spreadsheet_url=campaign["sheet_url"],
                product_title=campaign["title"],
                sales_data=sales,
            )
            print(f"  ✓ 완료\n")
        except Exception as e:
            print(f"  [오류] {e}\n")

    print(f"{'='*55}\n")


def main():
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
