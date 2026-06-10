import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CREDENTIALS_PATH = str(
    BASE_DIR.parent / "influencer data shared" / "credentials" / "google-credentials.json"
)

# 원본: 쿠팡 로켓 입고상세내역
SOURCE_SHEET_ID = "1EekZ89wq_Dk4AY844QwROMUcQN2yL1jY_6Ja1YAO_mU"
SOURCE_GID = 617188056

# 가공 결과를 쓸 시트
TARGET_SHEET_ID = "1ab_Pha20ULYGh__gzzV59BRIoR4x_HSCEY9TiAMYWPw"
TARGET_GID_INBOUND = 2075262872  # 쿠팡 로켓 입고내역 (월별/SKU별 입고수량)
TARGET_GID_AMOUNT = 0            # 쿠팡 로켓배송 (월별/SKU별 입고금액)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KST = timezone(timedelta(hours=9))


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [안내] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 없음 — 전송 생략")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=15,
        )
    except Exception as e:
        print(f"  [텔레그램 오류] {e}")


def get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def get_worksheet(client: gspread.Client, sheet_id: str, gid: int):
    sh = client.open_by_key(sheet_id)
    return next(ws for ws in sh.worksheets() if ws.id == gid)


def to_int(value: str) -> int:
    value = (value or "").replace(",", "").strip()
    return int(value) if value else 0


def aggregate(rows: list[list[str]]):
    header, data = rows[0], rows[1:]
    idx = {name: i for i, name in enumerate(header)}

    qty_by_key: dict[tuple[str, str], int] = defaultdict(int)
    amount_by_month: dict[str, int] = defaultdict(int)

    for row in data:
        # 발주(입고) 건만 집계. 향후 반출/취소 등 다른 구분이 추가되면 별도 처리 필요
        if row[idx["구분"]].strip() != "발주":
            continue
        date_str = row[idx["입고/반출일자"]].strip()
        if not date_str:
            continue
        year_month = date_str[:7]  # "YYYY-MM"
        sku = row[idx["SKU 명"]].strip()

        qty_by_key[(year_month, sku)] += to_int(row[idx["수량"]])
        amount_by_month[year_month] += to_int(row[idx["총 단가"]])

    return qty_by_key, amount_by_month


NUMBER_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}


def write_inbound_sheet(ws, qty_by_key: dict, updated_at: str):
    # 년월 최신순, 동일 년월 내에서는 SKU명 오름차순
    rows = sorted(qty_by_key.items(), key=lambda kv: kv[0][1])
    rows.sort(key=lambda kv: kv[0][0], reverse=True)

    values = [["마지막 업데이트:", updated_at], ["년월", "SKU명", "입고수량"]]
    values += [[year_month, sku, qty] for (year_month, sku), qty in rows]

    ws.clear()
    ws.update(range_name="A1", values=values, value_input_option="RAW")
    if rows:
        ws.format(f"C3:C{2 + len(rows)}", NUMBER_FORMAT)


def write_amount_sheet(ws, amount_by_month: dict, updated_at: str):
    # 년월 최신순
    rows = sorted(amount_by_month.items(), key=lambda kv: kv[0], reverse=True)

    values = [["마지막 업데이트:", updated_at], ["년월", "입고금액(총단가,단위:원)"]]
    values += [[year_month, amount] for year_month, amount in rows]

    ws.clear()
    ws.update(range_name="A1", values=values, value_input_option="RAW")
    if rows:
        ws.format(f"B3:B{2 + len(rows)}", NUMBER_FORMAT)


def main():
    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    client = get_client()
    source_ws = get_worksheet(client, SOURCE_SHEET_ID, SOURCE_GID)
    rows = source_ws.get_all_values()

    qty_by_key, amount_by_month = aggregate(rows)

    inbound_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_INBOUND)
    write_inbound_sheet(inbound_ws, qty_by_key, updated_at)

    amount_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_AMOUNT)
    write_amount_sheet(amount_ws, amount_by_month, updated_at)

    print(f"완료: {len(qty_by_key)}개 (년월,SKU) 조합, {len(amount_by_month)}개월 업데이트 ({updated_at})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"⚠️ 쿠팡 로켓배송 데이터 업데이트 실패\n{e}")
        raise
