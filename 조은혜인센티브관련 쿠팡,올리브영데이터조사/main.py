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
    amount_by_key: dict[tuple[str, str], int] = defaultdict(int)

    for row in data:
        # 발주(입고) 건만 집계. 향후 반출/취소 등 다른 구분이 추가되면 별도 처리 필요
        if row[idx["구분"]].strip() != "발주":
            continue
        date_str = row[idx["입고/반출일자"]].strip()
        if not date_str:
            continue
        year_month = date_str[:7]  # "YYYY-MM"
        sku = row[idx["SKU 명"]].strip()

        key = (year_month, sku)
        qty_by_key[key] += to_int(row[idx["수량"]])
        amount_by_key[key] += to_int(row[idx["총 단가"]])

    return qty_by_key, amount_by_key


def write_aggregated_sheet(ws, header: list[str], data: dict, updated_at: str):
    rows = [
        [year_month, sku, value]
        for (year_month, sku), value in sorted(data.items())
    ]
    values = [
        ["마지막 업데이트:", updated_at],
        header,
        *rows,
    ]
    ws.clear()
    ws.update(range_name="A1", values=values, value_input_option="RAW")


def main():
    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    client = get_client()
    source_ws = get_worksheet(client, SOURCE_SHEET_ID, SOURCE_GID)
    rows = source_ws.get_all_values()

    qty_by_key, amount_by_key = aggregate(rows)

    inbound_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_INBOUND)
    write_aggregated_sheet(inbound_ws, ["년월", "SKU명", "입고수량"], qty_by_key, updated_at)

    amount_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_AMOUNT)
    write_aggregated_sheet(amount_ws, ["년월", "SKU명", "입고금액(총단가)"], amount_by_key, updated_at)

    print(f"완료: {len(qty_by_key)}개 (년월,SKU) 조합 업데이트 ({updated_at})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"⚠️ 쿠팡 로켓배송 데이터 업데이트 실패\n{e}")
        raise
