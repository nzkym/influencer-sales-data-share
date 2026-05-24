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


def _extract_box_count(option: str, unit_keyword: str = "") -> int:
    """옵션명에서 수량 단위 자동 추출.
    unit_keyword 지정 시 해당 키워드를 우선 사용.
    미지정 시 한국 이커머스에서 쓰이는 단위를 자동 인식.
    """
    if unit_keyword:
        match = re.search(r'(\d+)\s*' + re.escape(unit_keyword), option, re.IGNORECASE)
        if match:
            return int(match.group(1))
    match = re.search(
        r'(\d+)\s*(BOX|박스|bx|개입|세트|set|팩|pack|구|EA|캔|병|통|봉)',
        option, re.IGNORECASE
    )
    if match:
        return int(match.group(1))
    return 1


def _aggregate(sales_data: list, unit_keyword: str = "") -> list:
    """날짜×옵션별 집계 (주문수 + 제품수)"""
    agg = defaultdict(lambda: defaultdict(int))
    for row in sales_data:
        agg[row["date"]][row["option"]] += row["quantity"]

    rows = []
    for date in sorted(agg.keys()):
        for option in sorted(agg[date].keys()):
            daily_orders = agg[date][option]
            box_count = _extract_box_count(option, unit_keyword)
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


def read_totals_from_sheet(sheet_url: str) -> tuple:
    """인플루언서 개별 시트에서 총 주문수·제품수·제품명 읽기.
    반환: (total_orders, total_products, product_name)
    """
    if not sheet_url:
        raise ValueError("sheet_url 없음")
    client = _get_client()
    sheet_id = _extract_sheet_id(sheet_url)
    spreadsheet = client.open_by_key(sheet_id)
    ws = spreadsheet.sheet1

    # 행1: 제품명 (A1)
    product_name = str(ws.acell("A1").value or "").replace("📊", "").strip()

    # A열에서 "총 주문수:" 포함 셀 탐색 (행 위치 무관하게)
    a_col = ws.col_values(1)
    summary_cell = next((v for v in a_col if "총 주문수:" in str(v)), "")
    orders_match = re.search(r"총 주문수:\s*([\d,]+)건", summary_cell)
    products_match = re.search(r"총 제품수:\s*([\d,]+)개", summary_cell)

    total_orders = int(orders_match.group(1).replace(",", "")) if orders_match else 0
    total_products = int(products_match.group(1).replace(",", "")) if products_match else 0

    return total_orders, total_products, product_name


def read_profit_params(spreadsheet_url: str) -> list:
    """이익계산참고사항 탭 읽기.
    반환: [{"name": "뉴트키즈타민", "cost": 4890, "channel_comm": 0.0385, "delivery": 3000}, ...]
    """
    try:
        client = _get_client()
        sheet_id = _extract_sheet_id(spreadsheet_url)
        ss = client.open_by_key(sheet_id)
        ws = ss.worksheet("이익계산참고사항")
        rows = ws.get_all_values()

        def parse_num(s):
            try:
                return float(str(s).replace(",", "").replace("%", "").strip() or 0)
            except Exception:
                return 0.0

        def parse_pct(s):
            s = str(s).strip()
            try:
                if s.endswith("%"):
                    return float(s[:-1]) / 100
                v = float(s)
                return v / 100 if v > 1 else v
            except Exception:
                return 0.0

        result = []
        for row in rows[1:]:  # 헤더 스킵
            if not row or not row[0].strip():
                continue
            result.append({
                "name":         row[0].strip(),
                "cost":         parse_num(row[3]) if len(row) > 3 else 0,
                "channel_comm": parse_pct(row[7]) if len(row) > 7 else 0,
                "delivery":     parse_num(row[8]) if len(row) > 8 else 3000,
            })
        return result
    except Exception as e:
        print(f"  [이익계산참고사항] 읽기 실패: {e}")
        return []


def _match_profit(product_name: str, profit_params: list) -> dict:
    """이익계산참고사항 제품명 퍼지 매칭 (짧은 이름이 긴 이름에 포함되는지 확인)."""
    if not product_name or not profit_params:
        return {}
    norm = lambda s: re.sub(r'[\s\[\]()（）,./·×X]', '', str(s)).lower()
    name_n = norm(product_name)
    for row in profit_params:
        short = norm(row.get("name", ""))
        if short and len(short) >= 4 and short in name_n:
            return row
    return {}


def read_summary_tab(spreadsheet_url: str) -> dict:
    """'캠페인 실적' 탭 읽기. 반환: {제목: row_dict}"""
    try:
        client = _get_client()
        sheet_id = _extract_sheet_id(spreadsheet_url)
        spreadsheet = client.open_by_key(sheet_id)
        ws = spreadsheet.worksheet("캠페인 실적")
        records = ws.get_all_records()
        result = {}
        for row in records:
            title = str(row.get("제목", "")).strip()
            if title:
                # 캐시된 매출 값 파싱
                raw_rev = str(row.get("매출", "")).replace(",", "").strip()
                cached_revenue = int(raw_rev) if raw_rev.lstrip("-").isdigit() else ""
                raw_comm = str(row.get("공구수수료(vat포함)", "")).replace(",", "").strip()
                try:
                    cached_commission = float(raw_comm) if raw_comm else ""
                except Exception:
                    cached_commission = ""
                result[title] = {
                    "title":          title,
                    "product_name":   str(row.get("제품명", "")),
                    "url":            str(row.get("진행링크", "")),
                    "store":          str(row.get("스토어", "")),
                    "date_from":      str(row.get("시작일", "")),
                    "date_to":        str(row.get("종료일", "")),
                    "total_orders":   row.get("주문수", 0),
                    "total_products": row.get("제품수", 0),
                    "status":         str(row.get("상태", "")),
                    "updated_at":     str(row.get("업데이트", "")),
                    "revenue":        cached_revenue,
                    "commission":     cached_commission,
                }
        return result
    except Exception:
        return {}


def write_summary_tab(
    spreadsheet_url: str,
    summary_rows: list,
    row_extras: dict = None,
):
    """
    마스터 시트의 '캠페인 실적' 탭 전체 업데이트.
    row_extras = {제목: {"revenue": int, "commission": float, "profit_params": dict}}
    → L(매출), M(공구수수료), N(이익 수식), O(이익률 수식) 포함하여 일괄 기록.
    기존 값이 날아가지 않도록 A-O를 한 번에 씁니다.
    """
    client = _get_client()
    sheet_id = _extract_sheet_id(spreadsheet_url)
    spreadsheet = client.open_by_key(sheet_id)

    try:
        ws = spreadsheet.worksheet("캠페인 실적")
        if ws.col_count < 30:
            ws.resize(rows=ws.row_count, cols=30)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="캠페인 실적", rows=200, cols=30)

    header = [
        "No", "제목", "제품명", "스토어", "시작일", "종료일",
        "주문수", "제품수", "상태", "진행링크", "업데이트",
        "매출", "공구수수료(vat포함)", "이익", "이익률",
    ]
    values = [header]

    for i, row in enumerate(summary_rows, 1):
        row_sheet = i + 1  # 시트 행 번호 (헤더=1, 데이터 시작=2)
        extras    = (row_extras or {}).get(row["title"], {})

        # ── L열 매출 ──────────────────────────────
        revenue = extras.get("revenue")
        if not isinstance(revenue, int):
            rev_cached = row.get("revenue", "")
            revenue = rev_cached if isinstance(rev_cached, int) else ""

        # ── M열 공구수수료 (숫자, 예: 40) ─────────
        commission = extras.get("commission")
        if not isinstance(commission, (int, float)):
            commission = row.get("commission", "")
            if not isinstance(commission, (int, float)):
                commission = ""

        # ── N열 이익 수식 ─────────────────────────
        pp = extras.get("profit_params", {})
        n_formula = ""
        if pp and isinstance(revenue, int) and revenue > 0 and commission != "":
            cost     = int(pp.get("cost", 0) or 0)
            ch_comm  = float(pp.get("channel_comm", 0) or 0)
            delivery = int(pp.get("delivery", 3000) or 3000)
            if cost > 0:
                n_formula = (
                    f"=L{row_sheet}"
                    f"-({cost}*H{row_sheet})"
                    f"-(L{row_sheet}*(M{row_sheet}/100))"
                    f"-(L{row_sheet}*{ch_comm})"
                    f"-({delivery}*G{row_sheet})"
                )

        # ── O열 이익률 수식 ───────────────────────
        o_formula = (
            f"=IFERROR(N{row_sheet}/L{row_sheet},\"\")"
            if n_formula else ""
        )

        values.append([
            i,
            row["title"],
            row.get("product_name", ""),
            row["store"],
            row["date_from"],
            row["date_to"],
            row["total_orders"],
            row["total_products"],
            row.get("status", ""),
            row.get("url", ""),
            row["updated_at"],
            revenue if revenue != "" else "",
            commission if commission != "" else "",
            n_formula,
            o_formula,
        ])

    # ── 시트 전체를 A-O 한 번에 쓰기 (clear 사용하지 않음) ──
    # 이전 데이터 행이 남지 않도록: 기존 행 수를 파악해 초과분 공백으로 덮어씀
    old_row_count = ws.row_count
    ws.update("A1", values, value_input_option="USER_ENTERED")
    if old_row_count > len(values):
        blank = [[""] * len(header)] * (old_row_count - len(values))
        ws.update(f"A{len(values)+1}", blank, value_input_option="USER_ENTERED")

    # ── 서식 적용 ─────────────────────────────────
    requests_body = {"requests": []}
    R = requests_body["requests"]

    def cell_range(r1, c1, r2, c2):
        return {"sheetId": ws.id, "startRowIndex": r1, "endRowIndex": r2,
                "startColumnIndex": c1, "endColumnIndex": c2}

    BLACK = {"red": 0, "green": 0, "blue": 0}

    # 헤더 서식 (A-O)
    R.append({"repeatCell": {
        "range": cell_range(0, 0, 1, len(header)),
        "cell": {"userEnteredFormat": {
            "backgroundColor": COLOR_TITLE_BG,
            "textFormat": {"bold": True, "foregroundColor": COLOR_TITLE_FG},
            "horizontalAlignment": "CENTER",
        }},
        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
    }})

    # 데이터 행 교대 색상 (A-K)
    for i in range(len(summary_rows)):
        row_idx = i + 1
        bg = COLOR_ODD_BG if i % 2 == 0 else COLOR_EVEN_BG
        R.append({"repeatCell": {
            "range": cell_range(row_idx, 0, row_idx + 1, 11),  # A-K
            "cell": {"userEnteredFormat": {"backgroundColor": bg}},
            "fields": "userEnteredFormat(backgroundColor)",
        }})
        # L-O: 연한 초록 배경으로 구분
        COLOR_LMNO_BG = {"red": 0.90, "green": 0.97, "blue": 0.90}
        R.append({"repeatCell": {
            "range": cell_range(row_idx, 11, row_idx + 1, 15),  # L-O
            "cell": {"userEnteredFormat": {
                "backgroundColor": COLOR_LMNO_BG,
                "textFormat": {"bold": False, "foregroundColor": BLACK},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }})

    # L열(매출), N열(이익) 숫자 서식 (#,##0)
    if len(summary_rows) > 0:
        for col_idx in [11, 13]:  # L=11, N=13
            R.append({"repeatCell": {
                "range": cell_range(1, col_idx, len(summary_rows) + 1, col_idx + 1),
                "cell": {"userEnteredFormat": {
                    "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                }},
                "fields": "userEnteredFormat(numberFormat)",
            }})
        # O열(이익률) 백분율 서식
        R.append({"repeatCell": {
            "range": cell_range(1, 14, len(summary_rows) + 1, 15),
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "PERCENT", "pattern": "0.00%"},
            }},
            "fields": "userEnteredFormat(numberFormat)",
        }})

    # 열 너비 (A-O)
    col_widths = [40, 100, 260, 65, 85, 85, 55, 55, 55, 200, 110, 100, 70, 100, 70]
    for idx, width in enumerate(col_widths):
        R.append({"updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                      "startIndex": idx, "endIndex": idx + 1},
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        }})

    if R:
        spreadsheet.batch_update(requests_body)


def _aggregate_hourly(sales_data: list) -> tuple:
    """시간대별 집계. 반환: (orders[date][hour], products[date][hour])"""
    orders   = defaultdict(lambda: defaultdict(int))
    products = defaultdict(lambda: defaultdict(int))
    for row in sales_data:
        date = row["date"]
        hour = row.get("hour", 0)
        qty  = row["quantity"]
        box  = _extract_box_count(row.get("option", ""))
        orders[date][hour]   += qty
        products[date][hour] += qty * box
    return orders, products


def write_to_sheet(
    spreadsheet_url: str,
    product_title: str,
    sales_data: list,
    date_from: str = "",
    date_to: str = "",
    product_url: str = "",
    product_name: str = "",
    unit_keyword: str = "",
    inventory: int = None,
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

    aggregated = _aggregate(sales_data, unit_keyword)
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
    # 행3: 진행기간
    if date_from and date_to:
        period = f"📅 진행기간: {_fmt_date(date_from)} ~ {_fmt_date(date_to)}"
    else:
        period = ""
    values.append([period, "", "", "", "", ""])
    # 행4: 상품링크 (하이퍼링크)
    clean_url = product_url.split("?")[0] if product_url else ""
    link_formula = f'=HYPERLINK("{clean_url}","🔗 상품링크: {clean_url}")' if clean_url else ""
    values.append([link_formula, "", "", "", "", ""])
    # 행5: 상품명
    values.append([f"📦 상품명: {product_name}" if product_name else "", "", "", "", "", ""])
    # 행6: 총계 (+ 재고 정보 — 입력된 경우만)
    remaining = None
    if inventory is not None:
        remaining = max(0, inventory - total_products)
        values.append([
            f"총 주문수: {total_orders:,}건  |  총 제품수: {total_products:,}개",
            "", "", "", "",
            f"📦 초기재고: {inventory:,}개  |  남은재고: {remaining:,}개",
            "",
        ])
    else:
        values.append([f"총 주문수: {total_orders:,}건  |  총 제품수: {total_products:,}개", "", "", "", "", ""])
    # 행4: 주의사항
    values.append([DISCLAIMER, "", "", "", "", ""])
    # 행5: 빈 줄
    values.append(["", "", "", "", "", ""])
    # 행6: 헤더
    values.append(["날짜", "옵션", "주문수", "제품수", "", "🏆 옵션별 순위", "총 주문수"])

    DATA_START_ROW = 8  # 0-indexed (행1~8: 정보행, 행9: 헤더)

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

    # 차트 공간 확보 (차트 높이 ~16행 + 여유 2행)
    for _ in range(18):
        values.append(["", "", "", "", "", "", ""])

    # 그래프용 보조 데이터 (차트 아래)
    CHART_DATA_START = len(values)
    values.append(["날짜 (그래프용)", "주문수", "제품수"])
    for d in daily_totals:
        values.append([_fmt_date(d["date"]), d["orders"], d["products"]])
    CHART_DATA_END = len(values)

    # ── 시간대별 섹션 ────────────────────────────────────
    hourly_orders, hourly_products = _aggregate_hourly(sales_data)
    dates_sorted = sorted(hourly_orders.keys())

    HOURLY_SECTION_TITLE_ROW = None
    HOURLY_CHART_DATA_START   = None
    HOURLY_CHART_DATA_END     = None
    HOURLY_CHART_ANCHOR       = None
    SIMPLE_TITLE_ROW          = None
    SIMPLE_HEADER_ROW         = None

    if dates_sorted:
        has_hourly = any(hourly_orders[d] for d in dates_sorted)
        if has_hourly:
            values.append(["", "", "", "", "", "", ""])
            HOURLY_SECTION_TITLE_ROW = len(values)
            values.append(["⏰ 시간대별 판매 현황", "", "", "", "", "", ""])

            # 차트 공간 (차트 여기에 배치)
            HOURLY_CHART_ANCHOR = len(values)
            for _ in range(18):
                values.append(["", "", "", "", "", "", ""])

            # 차트 소스 겸 데이터 테이블 (단일, 중복 없음)
            HOURLY_CHART_DATA_START = len(values)
            values.append(["날짜+시간", "주문수", "제품수"])
            for date in dates_sorted:
                d_obj = datetime.strptime(date, "%Y-%m-%d")
                short_date = f"{d_obj.month}.{d_obj.day}일"
                for hour in sorted(hourly_orders[date].keys()):
                    o = hourly_orders[date][hour]
                    p = hourly_products[date].get(hour, 0)
                    if o > 0:
                        values.append([f"{short_date} {hour}시", o, p])
            HOURLY_CHART_DATA_END = len(values)

        SIMPLE_TITLE_ROW  = None
        SIMPLE_HEADER_ROW = None

    # ── 시트 기록 ──────────────────────────────────────
    ws.clear()
    ws.update("A1", values, value_input_option="USER_ENTERED")

    # ── 서식 적용 ──────────────────────────────────────
    requests_body = {"requests": []}
    R = requests_body["requests"]

    # 기존 서식 전체 초기화 (이전 실행 잔존 서식 제거)
    R.append({"updateCells": {
        "range": {"sheetId": ws.id},
        "fields": "userEnteredFormat",
    }})
    # 숨겨진 행/열 전체 해제 (이전 실행 잔존 숨김 제거)
    R.append({"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "ROWS",
                  "startIndex": 0, "endIndex": 500},
        "properties": {"hiddenByUser": False},
        "fields": "hiddenByUser",
    }})
    R.append({"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "COLUMNS",
                  "startIndex": 0, "endIndex": 20},
        "properties": {"hiddenByUser": False},
        "fields": "hiddenByUser",
    }})

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

    # 정보 행 (행2~6: 업데이트·진행기간·상품링크·상품명·총계)
    R.append({"repeatCell": {
        "range": cell_range(1, 0, 6, 7),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": False, "fontSize": 10},
        }},
        "fields": "userEnteredFormat(textFormat)",
    }})

    # 주의사항 행 (행7)
    R.append({"repeatCell": {
        "range": cell_range(6, 0, 7, 7),
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
                        "rowIndex": data_end_row + 1,
                        "columnIndex": 0,
                    },
                    "widthPixels": 520,
                    "heightPixels": 320,
                }
            },
        }}})

    # ── 시간대별 섹션 서식 (정확한 인덱스 기반) ──────────
    COLOR_SECTION_BG = {"red": 0.18, "green": 0.18, "blue": 0.28}
    COLOR_HDR        = {"red": 0.25, "green": 0.47, "blue": 0.78}
    WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}

    for title_row in [HOURLY_SECTION_TITLE_ROW, SIMPLE_TITLE_ROW]:
        if title_row is not None:
            R.append({"repeatCell": {
                "range": cell_range(title_row, 0, title_row + 1, 8),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": COLOR_SECTION_BG,
                    "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": WHITE},
                }},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }})

    # 시간대별 차트 데이터 헤더
    if HOURLY_CHART_DATA_START is not None:
        R.append({"repeatCell": {
            "range": cell_range(HOURLY_CHART_DATA_START, 0, HOURLY_CHART_DATA_START + 1, 3),
            "cell": {"userEnteredFormat": {
                "backgroundColor": COLOR_HDR,
                "textFormat": {"bold": True, "foregroundColor": WHITE},
                "horizontalAlignment": "CENTER",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
        }})

    # 단순 테이블 헤더
    if SIMPLE_HEADER_ROW is not None:
        R.append({"repeatCell": {
            "range": cell_range(SIMPLE_HEADER_ROW, 0, SIMPLE_HEADER_ROW + 1, 4),
            "cell": {"userEnteredFormat": {
                "backgroundColor": COLOR_HDR,
                "textFormat": {"bold": True, "foregroundColor": WHITE},
                "horizontalAlignment": "CENTER",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
        }})

    # ── 시간대별 차트 추가 ─────────────────────────────
    if HOURLY_CHART_DATA_START is not None and HOURLY_CHART_ANCHOR is not None:
        R.append({"addChart": {"chart": {
            "spec": {
                "title": "시간대별 주문수 / 제품수",
                "titleTextFormat": {"bold": True, "fontSize": 12},
                "basicChart": {
                    "chartType": "COLUMN",
                    "legendPosition": "BOTTOM_LEGEND",
                    "axis": [
                        {"position": "BOTTOM_AXIS", "title": "시간대"},
                        {"position": "LEFT_AXIS",   "title": "수량"},
                    ],
                    "domains": [{"domain": {"sourceRange": {"sources": [{
                        "sheetId": ws.id,
                        "startRowIndex": HOURLY_CHART_DATA_START,
                        "endRowIndex":   HOURLY_CHART_DATA_END,
                        "startColumnIndex": 0, "endColumnIndex": 1,
                    }]}}}],
                    "series": [
                        {
                            "series": {"sourceRange": {"sources": [{
                                "sheetId": ws.id,
                                "startRowIndex": HOURLY_CHART_DATA_START,
                                "endRowIndex":   HOURLY_CHART_DATA_END,
                                "startColumnIndex": 1, "endColumnIndex": 2,
                            }]}},
                            "targetAxis": "LEFT_AXIS",
                            "color": {"red": 0.20, "green": 0.44, "blue": 0.78},
                        },
                        {
                            "series": {"sourceRange": {"sources": [{
                                "sheetId": ws.id,
                                "startRowIndex": HOURLY_CHART_DATA_START,
                                "endRowIndex":   HOURLY_CHART_DATA_END,
                                "startColumnIndex": 2, "endColumnIndex": 3,
                            }]}},
                            "targetAxis": "LEFT_AXIS",
                            "color": {"red": 0.91, "green": 0.49, "blue": 0.14},
                        },
                    ],
                    "headerCount": 1,
                },
            },
            "position": {"overlayPosition": {
                "anchorCell": {"sheetId": ws.id, "rowIndex": HOURLY_CHART_ANCHOR, "columnIndex": 0},
                "widthPixels": 900, "heightPixels": 320,
            }},
        }}})


    spreadsheet.batch_update(requests_body)

    print(f"  → 구글 시트 업데이트 완료 ({len(aggregated)}행 + 그래프)")
    print(f"  → 시트 링크: {spreadsheet_url}")
    return remaining  # None이면 재고 미설정, 0 이하면 품절
