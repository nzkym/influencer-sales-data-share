"""
구글 스프레드시트 기록 모듈
- 서비스 계정(credentials/google-credentials.json)으로 인증
- '링크 공유 편집 가능' 설정된 시트에도 접근 가능
- 판매 현황 테이블 + 막대 그래프 자동 생성
"""

import re
from datetime import datetime
from collections import defaultdict
import gspread
from google.oauth2.service_account import Credentials


from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDENTIALS_PATH = str(Path(__file__).parent / "credentials" / "google-credentials.json")

DISCLAIMER = "⚠️ 현재 수집된 데이터는 추후 취소 및 플랫폼반영 등의 이슈로 최종데이터는 달라질수있습니다."

# 색상 정의
COLOR_TITLE_BG   = {"red": 0.20, "green": 0.44, "blue": 0.78}
COLOR_TITLE_FG   = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
COLOR_HEADER_BG  = {"red": 0.23, "green": 0.53, "blue": 0.87}
COLOR_HEADER_FG  = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
COLOR_WARN_BG    = {"red": 1.0,  "green": 0.95, "blue": 0.80}
COLOR_WARN_FG    = {"red": 0.6,  "green": 0.3,  "blue": 0.0}
COLOR_ODD_BG     = {"red": 0.94, "green": 0.97, "blue": 1.0}
COLOR_EVEN_BG    = {"red": 1.0,  "green": 1.0,  "blue": 1.0}


def _get_client():
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def _extract_sheet_id(url: str) -> str:
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        raise ValueError(f"구글 스프레드시트 URL에서 ID를 찾을 수 없습니다: {url}")
    return match.group(1)


def _fmt_date(date_str: str) -> str:
    """'2026-04-07' → '2026.4.7일'"""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{d.year}.{d.month}.{d.day}일"
    except Exception:
        return date_str


def _extract_box_count(option: str) -> int:
    """옵션명에서 박스 수량 추출. 예: '선택: 12BOX(50%)' → 12"""
    match = re.search(r'(\d+)\s*BOX', option, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 1  # BOX 수량 없으면 1개로 간주


def _aggregate(sales_data: list) -> list:
    """날짜×옵션별 집계 (주문수 + 제품수)"""
    agg = defaultdict(lambda: defaultdict(int))
    for row in sales_data:
        agg[row["date"]][row["option"]] += row["quantity"]

    rows = []
    for date in sorted(agg.keys()):
        for option in sorted(agg[date].keys()):
            daily_orders = agg[date][option]
            box_count = _extract_box_count(option)
            daily_products = daily_orders * box_count
            rows.append({
                "date": date,
                "option": option,
                "daily_orders": daily_orders,
                "daily_products": daily_products,
            })
    return rows


def _daily_totals(aggregated: list) -> list:
    """날짜별 주문수/제품수 합계 (그래프용)"""
    order_totals = defaultdict(int)
    product_totals = defaultdict(int)
    for r in aggregated:
        order_totals[r["date"]] += r["daily_orders"]
        product_totals[r["date"]] += r["daily_products"]
    return [
        {"date": d, "orders": order_totals[d], "products": product_totals[d]}
        for d in sorted(order_totals.keys())
    ]


def write_to_sheet(
    spreadsheet_url: str,
    product_title: str,
    sales_data: list,
):
    client = _get_client()
    sheet_id = _extract_sheet_id(spreadsheet_url)
    spreadsheet = client.open_by_key(sheet_id)

    sheet_name = "판매현황"
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=300, cols=15)

    aggregated = _aggregate(sales_data)
    daily_totals = _daily_totals(aggregated)
    total_orders = sum(r["orders"] for r in daily_totals)
    total_products = sum(r["products"] for r in daily_totals)
    from datetime import timezone, timedelta
    KST = timezone(timedelta(hours=9))
    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    # ── 시트 데이터 구성 ──────────────────────────────
    values = []

    # 행1: 제목
    values.append([f"📊 {product_title}", "", "", ""])
    # 행2: 업데이트 시각
    values.append([f"마지막 업데이트: {updated_at}", "", "", ""])
    # 행3: 전체 누적
    values.append([f"총 주문수: {total_orders:,}건  |  총 제품수: {total_products:,}개", "", "", ""])
    # 행4: 주의사항
    values.append([DISCLAIMER, "", "", ""])
    # 행5: 빈 줄
    values.append(["", "", "", ""])
    # 행6: 헤더
    values.append(["날짜", "옵션", "주문수", "제품수"])

    DATA_START_ROW = 5  # 0-indexed: 행6(헤더)이 index 5

    # 행7~: 데이터
    if aggregated:
        for row in aggregated:
            values.append([
                _fmt_date(row["date"]),
                row["option"],
                row["daily_orders"],
                row["daily_products"],
            ])
    else:
        values.append(["", "아직 판매 데이터가 없습니다", "", ""])

    data_end_row = len(values)  # 0-indexed exclusive

    # 행 뒤에 그래프용 보조 데이터 (날짜별 주문수 + 제품수)
    values.append(["", "", "", ""])
    CHART_DATA_START = len(values)
    values.append(["날짜 (그래프용)", "주문수", "제품수"])
    for d in daily_totals:
        values.append([_fmt_date(d["date"]), d["orders"], d["products"]])
    CHART_DATA_END = len(values)

    # ── 시트 기록 ──────────────────────────────────────
    ws.clear()
    ws.update("A1", values)

    # ── 서식 적용 ──────────────────────────────────────
    requests_body = {"requests": []}
    R = requests_body["requests"]

    def cell_range(r1, c1, r2, c2):
        return {"sheetId": ws.id, "startRowIndex": r1, "endRowIndex": r2,
                "startColumnIndex": c1, "endColumnIndex": c2}

    # 제목 행 (행1)
    R.append({"repeatCell": {
        "range": cell_range(0, 0, 1, 4),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_TITLE_BG,
            "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": COLOR_TITLE_FG},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }})

    # 업데이트/누적 행 (행2~3)
    R.append({"repeatCell": {
        "range": cell_range(1, 0, 3, 4),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": False, "fontSize": 10},
        }},
        "fields": "userEnteredFormat(textFormat)",
    }})

    # 주의사항 행 (행4)
    R.append({"repeatCell": {
        "range": cell_range(3, 0, 4, 4),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_WARN_BG,
            "textFormat": {"italic": True, "fontSize": 9,
                           "foregroundColor": COLOR_WARN_FG},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }})

    # 헤더 행 (행6)
    R.append({"repeatCell": {
        "range": cell_range(DATA_START_ROW, 0, DATA_START_ROW + 1, 4),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_HEADER_BG,
            "textFormat": {"bold": True, "foregroundColor": COLOR_HEADER_FG},
            "horizontalAlignment": "CENTER",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
    }})

    # 데이터 행: 배경색 + 검은 텍스트 + 왼쪽 정렬 (일괄 적용)
    BLACK = {"red": 0.0, "green": 0.0, "blue": 0.0}
    for i, _ in enumerate(aggregated or [""]):
        row_idx = DATA_START_ROW + 1 + i
        bg = COLOR_ODD_BG if i % 2 == 0 else COLOR_EVEN_BG
        R.append({"repeatCell": {
            "range": cell_range(row_idx, 0, row_idx + 1, 4),
            "cell": {"userEnteredFormat": {
                "backgroundColor": bg,
                "textFormat": {"bold": False, "foregroundColor": BLACK},
                "horizontalAlignment": "LEFT",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
        }})

    # 주문수/제품수 컬럼(C, D)만 가운데 정렬
    R.append({"repeatCell": {
        "range": cell_range(DATA_START_ROW + 1, 2, data_end_row, 4),
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat(horizontalAlignment)",
    }})

    # 열 너비 설정 (A:자동, B:자동, C:고정80px, D:고정80px)
    R.append({"autoResizeDimensions": {"dimensions": {
        "sheetId": ws.id, "dimension": "COLUMNS",
        "startIndex": 0, "endIndex": 2,
    }}})
    for col_idx, width in [(2, 80), (3, 80)]:
        R.append({"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                      "startIndex": col_idx, "endIndex": col_idx + 1},
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }})

    # ── 차트 추가 ──────────────────────────────────────
    # 기존 차트 삭제
    existing = spreadsheet.fetch_sheet_metadata()
    for sheet in existing.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == ws.id:
            for chart in sheet.get("charts", []):
                R.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})

    if daily_totals and CHART_DATA_END > CHART_DATA_START + 1:
        chart_start = CHART_DATA_START
        chart_end = CHART_DATA_END

        R.append({"addChart": {"chart": {
            "spec": {
                "title": "날짜별 주문수 / 제품수",
                "titleTextFormat": {"bold": True, "fontSize": 12},
                "basicChart": {
                    "chartType": "COLUMN",
                    "legendPosition": "BOTTOM_LEGEND",
                    "axis": [
                        {"position": "BOTTOM_AXIS",
                         "title": "날짜",
                         "titleTextPosition": {"horizontalAlignment": "CENTER"}},
                        {"position": "LEFT_AXIS",
                         "title": "수량",
                         "titleTextPosition": {"horizontalAlignment": "CENTER"}},
                    ],
                    "domains": [{
                        "domain": {"sourceRange": {"sources": [{
                            "sheetId": ws.id,
                            "startRowIndex": chart_start,
                            "endRowIndex": chart_end,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1,
                        }]}}
                    }],
                    "series": [
                        {
                            "series": {"sourceRange": {"sources": [{
                                "sheetId": ws.id,
                                "startRowIndex": chart_start,
                                "endRowIndex": chart_end,
                                "startColumnIndex": 1,
                                "endColumnIndex": 2,
                            }]}},
                            "targetAxis": "LEFT_AXIS",
                            "color": {"red": 0.20, "green": 0.44, "blue": 0.78},
                        },
                        {
                            "series": {"sourceRange": {"sources": [{
                                "sheetId": ws.id,
                                "startRowIndex": chart_start,
                                "endRowIndex": chart_end,
                                "startColumnIndex": 2,
                                "endColumnIndex": 3,
                            }]}},
                            "targetAxis": "LEFT_AXIS",
                            "color": {"red": 0.91, "green": 0.49, "blue": 0.14},
                        },
                    ],
                    "headerCount": 1,
                },
            },
            "position": {
                "overlayPosition": {
                    "anchorCell": {
                        "sheetId": ws.id,
                        "rowIndex": DATA_START_ROW,
                        "columnIndex": 5,
                    },
                    "widthPixels": 480,
                    "heightPixels": 300,
                }
            },
        }}})

    spreadsheet.batch_update(requests_body)

    print(f"  → 구글 시트 업데이트 완료 ({len(aggregated)}행 + 그래프)")
    print(f"  → 시트 링크: {spreadsheet_url}")
