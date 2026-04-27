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
    """옵션명에서 박스 수량 추출. 예: '선택: 12BOX(50%)' → 12, '12박스' → 12"""
    match = re.search(r'(\d+)\s*(BOX|박스)', option, re.IGNORECASE)
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
    date_from: str = "",   # "2026-04-06" 형식 (D+일 계산용)
):
    from datetime import timezone, timedelta, date as date_type
    KST = timezone(timedelta(hours=9))

    client = _get_client()
    sheet_id = _extract_sheet_id(spreadsheet_url)
    spreadsheet = client.open_by_key(sheet_id)

    # 기존 "판매현황" 탭이 첫 번째 시트가 아니면 삭제
    try:
        old_ws = spreadsheet.worksheet("판매현황")
        if old_ws.id != spreadsheet.sheet1.id:
            spreadsheet.del_worksheet(old_ws)
    except gspread.WorksheetNotFound:
        pass

    # 첫 번째 시트 사용 + 이름을 "판매현황"으로 변경
    ws = spreadsheet.sheet1
    if ws.title != "판매현황":
        ws.update_title("판매현황")

    aggregated = _aggregate(sales_data)
    daily_totals = _daily_totals(aggregated)
    total_orders = sum(r["orders"] for r in daily_totals)
    total_products = sum(r["products"] for r in daily_totals)
    updated_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    # D+일 계산
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        today = datetime.now(KST).date()
        d_day = (today - start).days + 1
        d_day_str = f"D+{d_day}일째"
    except Exception:
        d_day_str = ""

    # 옵션별 총 주문수 순위
    option_totals = defaultdict(int)
    for r in aggregated:
        option_totals[r["option"]] += r["daily_orders"]
    ranked = sorted(option_totals.items(), key=lambda x: x[1], reverse=True)

    # ── 시트 데이터 구성 ──────────────────────────────
    values = []

    # 행1: 제목 + D+일
    title_row = [f"📊 {product_title}", "", "", "", "", d_day_str]
    values.append(title_row)
    # 행2: 업데이트 시각
    values.append([f"마지막 업데이트: {updated_at}", "", "", "", "", ""])
    # 행3: 총계
    values.append([f"총 주문수: {total_orders:,}건  |  총 제품수: {total_products:,}개", "", "", "", "", ""])
    # 행4: 주의사항
    values.append([DISCLAIMER, "", "", "", "", ""])
    # 행5: 빈 줄
    values.append(["", "", "", "", "", ""])
    # 행6: 헤더
    values.append(["날짜", "옵션", "주문수", "제품수", "", "🏆 옵션별 순위", "총 주문수"])

    DATA_START_ROW = 5  # 0-indexed

    # 주문수가 모두 같은지 여부
    all_equal = len(set(v for _, v in ranked)) == 1

    # 행7~: 데이터 + 순위 병렬 표시
    if aggregated:
        for i, row in enumerate(aggregated):
            if len(ranked) >= 2 and i < len(ranked):
                if all_equal:
                    # 주문수 모두 같으면 순위 번호 없이 옵션명만
                    rank_label = ranked[i][0]
                else:
                    rank_label = f"{i+1}위  {ranked[i][0]}"
                rank_orders = ranked[i][1]
            else:
                rank_label = ""
                rank_orders = ""
            values.append([
                _fmt_date(row["date"]),
                row["option"],
                row["daily_orders"],
                row["daily_products"],
                "",
                rank_label,
                rank_orders if rank_label else "",
            ])
    else:
        values.append(["", "아직 판매 데이터가 없습니다", "", "", "", "", ""])

    data_end_row = len(values)

    # 그래프용 보조 데이터
    values.append(["", "", "", "", "", "", ""])
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

    BLACK = {"red": 0.0, "green": 0.0, "blue": 0.0}
    COLOR_RANK_BG = {"red": 1.0, "green": 0.97, "blue": 0.88}  # 순위 열 배경 (연한 노랑)

    # 제목 행 (행1) - A~D + F(D+일)
    R.append({"repeatCell": {
        "range": cell_range(0, 0, 1, 7),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_TITLE_BG,
            "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": COLOR_TITLE_FG},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }})
    # D+일 오른쪽 정렬
    R.append({"repeatCell": {
        "range": cell_range(0, 5, 1, 7),
        "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT"}},
        "fields": "userEnteredFormat(horizontalAlignment)",
    }})

    # 업데이트/누적 행 (행2~3)
    R.append({"repeatCell": {
        "range": cell_range(1, 0, 3, 7),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": False, "fontSize": 10},
        }},
        "fields": "userEnteredFormat(textFormat)",
    }})

    # 주의사항 행 (행4)
    R.append({"repeatCell": {
        "range": cell_range(3, 0, 4, 7),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_WARN_BG,
            "textFormat": {"italic": True, "fontSize": 9,
                           "foregroundColor": COLOR_WARN_FG},
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat)",
    }})

    # 헤더 행 (행6) - A~D
    R.append({"repeatCell": {
        "range": cell_range(DATA_START_ROW, 0, DATA_START_ROW + 1, 4),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_HEADER_BG,
            "textFormat": {"bold": True, "foregroundColor": COLOR_HEADER_FG},
            "horizontalAlignment": "CENTER",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
    }})

    # 순위 헤더 (행6 F~G)
    COLOR_RANK_HEADER = {"red": 0.95, "green": 0.76, "blue": 0.20}
    R.append({"repeatCell": {
        "range": cell_range(DATA_START_ROW, 5, DATA_START_ROW + 1, 7),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_RANK_HEADER,
            "textFormat": {"bold": True, "foregroundColor": BLACK},
            "horizontalAlignment": "CENTER",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
    }})

    # 데이터 행 (A~D)
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
        # 순위 열 (F~G)
        R.append({"repeatCell": {
            "range": cell_range(row_idx, 5, row_idx + 1, 7),
            "cell": {"userEnteredFormat": {
                "backgroundColor": COLOR_RANK_BG,
                "textFormat": {"bold": i < 3, "foregroundColor": BLACK},
                "horizontalAlignment": "LEFT",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
        }})

    # C, D열 가운데 정렬
    R.append({"repeatCell": {
        "range": cell_range(DATA_START_ROW + 1, 2, data_end_row, 4),
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat(horizontalAlignment)",
    }})
    # G열(순위 주문수) 가운데 정렬
    R.append({"repeatCell": {
        "range": cell_range(DATA_START_ROW + 1, 6, data_end_row, 7),
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat(horizontalAlignment)",
    }})

    # 열 너비 설정
    R.append({"autoResizeDimensions": {"dimensions": {
        "sheetId": ws.id, "dimension": "COLUMNS",
        "startIndex": 0, "endIndex": 2,
    }}})
    for col_idx, width in [(2, 80), (3, 80), (4, 20), (5, 220), (6, 80)]:
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
                        "rowIndex": CHART_DATA_END + 2,
                        "columnIndex": 0,
                    },
                    "widthPixels": 520,
                    "heightPixels": 320,
                }
            },
        }}})

    spreadsheet.batch_update(requests_body)

    print(f"  → 구글 시트 업데이트 완료 ({len(aggregated)}행 + 그래프)")
    print(f"  → 시트 링크: {spreadsheet_url}")
