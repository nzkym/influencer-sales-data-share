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

# 원본: 올리브영 매출 (연도별 탭. 연도가 바뀌면 시트ID/GID도 함께 갱신 필요)
OLIVE_SOURCE_SHEET_ID = "14dbKT1mHZcEMkg183tmxv3aP-pBenYctJUStKyOQaOU"
OLIVE_SOURCE_GID = 295784298  # *26년 매출
OLIVE_YEAR = 2026

# 원본: 업체 전체 매출 (판매처별/연도별 월매출 "합계" 행)
TOTAL_SALES_SHEET_ID = "17fFgSgzxaS72rwM003XbpCOlrWE2eAny_7zRlZj2AR8"
TOTAL_SALES_GID = 647519508  # 매출

# 가공 결과를 쓸 시트
TARGET_SHEET_ID = "1ab_Pha20ULYGh__gzzV59BRIoR4x_HSCEY9TiAMYWPw"
TARGET_GID_INBOUND = 2075262872    # 쿠팡 로켓 입고내역 (월별/SKU별 입고수량/입고금액)
TARGET_GID_INCENTIVE = 0           # 인센티브 계산 (쿠팡+올리브영 월별 인센티브)
TARGET_GID_OLIVE_SALES = 669309880  # 올리브영 매출 (월별/품명별 매출액)

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

        amount = to_int(row[idx["총 단가"]])
        qty_by_key[(year_month, sku)] += to_int(row[idx["수량"]])
        amount_by_key[(year_month, sku)] += amount
        amount_by_month[year_month] += amount

    return qty_by_key, amount_by_key, amount_by_month


# 올리브영 '*26년 매출' 탭: 월별 블록(합계수량/합계매출/일평균수량/일평균매출/일별...)이
# 가로로 나열되어 있음. 각 블록의 시작 컬럼(0-base) 목록 (1월~12월)
OLIVE_MONTH_BLOCK_STARTS = [4, 70, 130, 196, 260, 326, 390, 456, 522, 586, 652, 716]


def aggregate_olive(rows: list[list[str]]):
    sales_by_key: dict[tuple[str, str], int] = defaultdict(int)
    sales_by_month: dict[str, int] = defaultdict(int)

    for row in rows[3:]:
        if row[0].strip() != "전체":
            continue
        item = row[3].strip()
        if not item:
            continue
        for i, start in enumerate(OLIVE_MONTH_BLOCK_STARTS):
            year_month = f"{OLIVE_YEAR}-{i + 1:02d}"
            amount = to_int(row[start + 1])  # 합계 매출
            sales_by_month[year_month] += amount
            if amount:
                sales_by_key[(year_month, item)] = amount

    # 매출이 전혀 없는 (아직 시작 안 된) 달은 제외
    sales_by_month = {ym: v for ym, v in sales_by_month.items() if v > 0}
    return sales_by_key, sales_by_month


def aggregate_total_sales(rows: list[list[str]]) -> dict[str, int]:
    """'매출' 탭의 "합계" 행(현재 연도 + 직전 연도, 1~12월 컬럼)에서 (년월 -> 업체 총 매출)을 추출."""
    sales_by_month: dict[str, int] = {}
    for i, row in enumerate(rows[1:], start=1):
        if row[0].strip() != "합계":
            continue
        for year_row in (row, rows[i + 1] if i + 1 < len(rows) else None):
            if not year_row:
                continue
            year_label = year_row[1].strip()  # 예: "2026년"
            if not year_label.endswith("년"):
                continue
            year = year_label[:-1]
            for month in range(1, 13):
                col = 1 + month  # 1월=인덱스2, 2월=인덱스3, ..., 12월=인덱스13
                if col < len(year_row):
                    sales_by_month[f"{year}-{month:02d}"] = to_int(year_row[col])
        break
    return sales_by_month


NUMBER_FORMAT = {"numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}

# 이 달부터 쿠팡 인센티브(C~E열) 자동계산 시작. 그 이전 달은 기존 입력값을 그대로 보존
INCENTIVE_START_MONTH = "2026-06"

INCENTIVE_HEADERS = [
    "년월",
    "인센티브 총합",
    "쿠팡 로켓 입고금액(총단가,단위:원)",
    "직전3개월평균대비 증감금액",
    "직전 3개월평균",
    "쿠팡 인센티브",
    "올리브영 매출",
    "올리브영인센티브 & 내용",
    "업체 총 매출",
    "직전3개월평균대비증감",
]

# 올리브영 월 매출 구간별 인센티브율 (월매출 하한(원), 인센티브율)
OLIVE_TIERS = [
    (30_000_000, 0.02),
    (20_000_000, 0.015),
    (10_000_000, 0.012),
    (5_000_000, 0.01),
    (0, 0.005),
]

# 최초 구간 돌파 1회성 보너스 (월매출 달성 기준(원), 보너스(원), 표시 라벨)
OLIVE_BONUS_TIERS = [
    (3_000_000, 50_000, "300만"),
    (5_000_000, 100_000, "500만"),
    (10_000_000, 200_000, "1,000만"),
    (20_000_000, 300_000, "2,000만"),
    (30_000_000, 500_000, "3,000만"),
]


def olive_tier_rate(sales: int) -> float:
    for threshold, rate in OLIVE_TIERS:
        if sales >= threshold:
            return rate
    return 0.0


def calc_olive_incentive(sales_by_month: dict, latest_month: str) -> dict:
    """년월 오름차순으로 등급별 인센티브 + 최초 구간돌파 1회성 보너스를 계산.
    INCENTIVE_START_MONTH 이전 달과 당월(latest_month, 취합 진행중)은 결과에서 제외한다.
    단, "최초 달성" 판정은 INCENTIVE_START_MONTH 이전 달의 매출도 포함해 누적 추적한다."""
    months_asc = sorted(ym for ym in sales_by_month if ym < latest_month)
    achieved = set()
    result = {}
    for ym in months_asc:
        sales = sales_by_month[ym]
        rate = olive_tier_rate(sales)
        incentive = round(sales * rate)
        labels = [f"{rate * 100:g}% 구간"]

        bonuses = []
        for threshold, bonus, label in OLIVE_BONUS_TIERS:
            if threshold not in achieved and sales >= threshold:
                achieved.add(threshold)
                bonuses.append((bonus, label))

        if ym < INCENTIVE_START_MONTH:
            continue

        for bonus, label in bonuses:
            incentive += bonus
            labels.append(f"최초 {label} 돌파 보너스 {bonus:,}원")

        result[ym] = (incentive, f"{incentive:,}원 (" + " + ".join(labels) + ")")
    return result


def shift_month(year_month: str, delta: int) -> str:
    year, month = map(int, year_month.split("-"))
    total = year * 12 + (month - 1) + delta
    year, month = divmod(total, 12)
    return f"{year:04d}-{month + 1:02d}"


def write_inbound_sheet(ws, qty_by_key: dict, amount_by_key: dict, updated_at: str):
    # 년월 최신순, 동일 년월 내에서는 SKU명 오름차순
    rows = sorted(qty_by_key.items(), key=lambda kv: kv[0][1])
    rows.sort(key=lambda kv: kv[0][0], reverse=True)

    values = [["마지막 업데이트:", updated_at], ["년월", "SKU명", "입고수량", "입고금액(총단가,단위:원)"]]
    values += [[year_month, sku, qty, amount_by_key[(year_month, sku)]] for (year_month, sku), qty in rows]

    ws.clear()
    ws.update(range_name="A1", values=values, value_input_option="RAW")
    if rows:
        ws.format(f"C3:D{2 + len(rows)}", NUMBER_FORMAT)


def write_olive_sales_sheet(ws, sales_by_key: dict, updated_at: str):
    # 년월 최신순, 동일 년월 내에서는 품명 오름차순
    rows = sorted(sales_by_key.items(), key=lambda kv: kv[0][1])
    rows.sort(key=lambda kv: kv[0][0], reverse=True)

    values = [["마지막 업데이트:", updated_at], ["년월", "품명", "매출액"]]
    values += [[year_month, item, amount] for (year_month, item), amount in rows]

    ws.clear()
    ws.update(range_name="A1", values=values, value_input_option="RAW")
    if rows:
        ws.format(f"C3:C{2 + len(rows)}", NUMBER_FORMAT)


def write_incentive_sheet(
    ws,
    amount_by_month: dict,
    olive_sales_by_month: dict,
    total_sales_by_month: dict,
    updated_at: str,
):
    # INCENTIVE_START_MONTH 이전 달의 인센티브 관련 컬럼(B,D,E,F,H)은 기존 입력값을 그대로 보존
    # (C=쿠팡 입고금액, G=올리브영 매출, I=업체 총 매출은 실제 매출 데이터이므로 항상 재계산)
    existing = ws.get_values("A3:H1000", value_render_option="UNFORMATTED_VALUE")
    preserved = {
        row[0]: (row + [""] * 8)[:8]
        for row in existing
        if row and row[0] < INCENTIVE_START_MONTH
    }

    months_desc = sorted(amount_by_month, reverse=True)
    latest_month = months_desc[0]
    last_row = 2 + len(months_desc)

    olive_incentive = calc_olive_incentive(olive_sales_by_month, latest_month)

    values = [["마지막 업데이트:", updated_at], INCENTIVE_HEADERS]
    # 셀 클릭 시 계산방식이 보이도록 D/E/F/J는 가능하면 시트 수식으로 작성
    formula_cells: list[tuple[str, str]] = []

    for k, year_month in enumerate(months_desc):
        r = 3 + k
        has_prev3 = r + 3 <= last_row  # 직전 3개월 행이 시트에 존재하는지
        amount = amount_by_month[year_month]
        olive_sales = olive_sales_by_month.get(year_month, "")
        total_sales = total_sales_by_month.get(year_month, "")

        if year_month < INCENTIVE_START_MONTH:
            prow = preserved.get(year_month, [""] * 8)
            total_incentive = prow[1]
            change, prev_avg, coupang_incentive = prow[3], prow[4], prow[5]
            olive_label = prow[7]
        elif year_month == latest_month:
            # 당월(데이터 취합 진행중)은 다음달에 계산
            change, prev_avg, coupang_incentive = "", "", ""
            olive_label, total_incentive = "", ""
        else:
            prev_avg = round(sum(amount_by_month.get(shift_month(year_month, -i), 0) for i in (1, 2, 3)) / 3)
            change = amount - prev_avg
            coupang_incentive = round(change * 0.01) if change >= 5_000_000 else 0

            if has_prev3:
                # D/E/F는 셀 클릭 시 계산방식이 보이도록 수식으로 작성 (B 계산용 값은 위에서 그대로 사용)
                formula_cells.append((f"E{r}", f"=ROUND(AVERAGE(C{r + 1}:C{r + 3}),0)"))
                formula_cells.append((f"D{r}", f"=C{r}-E{r}"))
                formula_cells.append((f"F{r}", f"=IF(D{r}>=5000000,ROUND(D{r}*0.01,0),0)"))
                change, prev_avg = "", ""

            olive_total, olive_label = olive_incentive.get(year_month, (0, ""))
            total_incentive = coupang_incentive + olive_total

        # J(직전3개월평균대비증감): 당월은 비워두고 다음달 실행 시 계산
        if year_month == latest_month:
            total_change = ""
        elif has_prev3:
            formula_cells.append((f"J{r}", f"=I{r}-ROUND(AVERAGE(I{r + 1}:I{r + 3}),0)"))
            total_change = ""
        elif total_sales == "":
            total_change = ""
        else:
            prev_sales = [total_sales_by_month.get(shift_month(year_month, -i)) for i in (1, 2, 3)]
            total_change = "" if any(v is None for v in prev_sales) else round(total_sales - sum(prev_sales) / 3)

        values.append([
            year_month, total_incentive, amount, change, prev_avg,
            coupang_incentive, olive_sales, olive_label, total_sales, total_change,
        ])

    ws.batch_clear(["A1:J1000"])
    ws.update(range_name="A1", values=values, value_input_option="RAW")
    if formula_cells:
        ws.batch_update(
            [{"range": cell, "values": [[formula]]} for cell, formula in formula_cells],
            value_input_option="USER_ENTERED",
        )
    ws.format(f"B3:J{2 + len(months_desc)}", NUMBER_FORMAT)


def main():
    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M")

    client = get_client()

    source_ws = get_worksheet(client, SOURCE_SHEET_ID, SOURCE_GID)
    rows = source_ws.get_all_values()
    qty_by_key, amount_by_key, amount_by_month = aggregate(rows)

    inbound_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_INBOUND)
    write_inbound_sheet(inbound_ws, qty_by_key, amount_by_key, updated_at)

    olive_source_ws = get_worksheet(client, OLIVE_SOURCE_SHEET_ID, OLIVE_SOURCE_GID)
    olive_rows = olive_source_ws.get_all_values()
    olive_sales_by_key, olive_sales_by_month = aggregate_olive(olive_rows)

    olive_sales_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_OLIVE_SALES)
    write_olive_sales_sheet(olive_sales_ws, olive_sales_by_key, updated_at)

    total_sales_ws = get_worksheet(client, TOTAL_SALES_SHEET_ID, TOTAL_SALES_GID)
    total_sales_rows = total_sales_ws.get_all_values()
    total_sales_by_month = aggregate_total_sales(total_sales_rows)

    incentive_ws = get_worksheet(client, TARGET_SHEET_ID, TARGET_GID_INCENTIVE)
    write_incentive_sheet(incentive_ws, amount_by_month, olive_sales_by_month, total_sales_by_month, updated_at)

    print(
        f"완료: 쿠팡 {len(qty_by_key)}개 (년월,SKU), "
        f"올리브영 {len(olive_sales_by_key)}개 (년월,품명), "
        f"{len(amount_by_month)}개월 업데이트 ({updated_at})"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"⚠️ 쿠팡 로켓배송 데이터 업데이트 실패\n{e}")
        raise
