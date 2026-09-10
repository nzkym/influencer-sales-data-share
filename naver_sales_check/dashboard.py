"""
진행행사 한눈그래프 — 행사 기간 제품별 판매 현황을 시각화하는 탭 생성

시트1의 행사 목록 중 '가장 최근에 시작된 행사' 하나를 골라
그 기간의 제품별/날짜별 판매 실적을 그래프와 함께 보여준다.

- O열(공구 상품ID)에 적힌 상품은 전부 제외한다 (시트1의 매출 계산과 동일 기준)
- 새 행사가 시작되면 탭 내용을 전부 지우고 새 행사 기준으로 다시 그린다
- 행사 진행 중에는 매일 갱신된다
"""

import re
import time
from datetime import datetime, timedelta, date
from collections import defaultdict

import requests

TAB_TITLE = "진행행사 한눈그래프"

# 담당자 요청으로 '이 탭에서만' 빼는 제품 (행사 제목 → 짧은 제품명 목록).
# 시트1의 매출/증감/최종증감 계산에는 전혀 영향을 주지 않는다.
# 새 행사가 시작되면 제목이 달라지므로 자동으로 적용이 끝난다.
DASHBOARD_ONLY_EXCLUDE = {
    "2026추석세일(뉴트원 브스)": ["액상 마그네슘 액티브"],   # 2026-09-10 요청, 이번 행사만
}


def apply_manual_exclude(rows: list, promo_title: str) -> tuple:
    """DASHBOARD_ONLY_EXCLUDE 에 걸린 제품을 걸러낸다. (남은행, 빠진제품명 목록)"""
    targets = DASHBOARD_ONLY_EXCLUDE.get(promo_title.strip())
    if not targets:
        return rows, []
    tset = {t.strip() for t in targets}
    kept, dropped = [], set()
    for r in rows:
        name = short_name(r["name"])
        if name in tset:
            dropped.add(name)
            continue
        kept.append(r)
    return kept, sorted(dropped)


# 표시용 짧은 제품명을 만들 때 지워낼 브랜드명
_BRANDS = ["뉴트원", "뉴트키즈", "넛펫", "정담건강", "뉴트메디"]

# '350mg' '30정' '19종' 처럼 용량/규격을 나타내는 토큰 — 제품명은 여기서 끊는다
_SPEC = re.compile(
    r'^\d[\d,]*(?:\.\d+)?\s*'
    r'(?:mg|mcg|ug|g|iu|ml|억|만|종|정|캡슐|포|구|달톤|kcal|일분|개월분|매|ea|알)',
    re.IGNORECASE,
)

# 그래프 색상
_C_SALES = {"red": 0.20, "green": 0.44, "blue": 0.78}   # 파랑 — 매출
_C_ORDER = {"red": 0.91, "green": 0.49, "blue": 0.14}   # 주황 — 주문수
_C_QTY   = {"red": 0.24, "green": 0.70, "blue": 0.44}   # 초록 — 상품수량
_C_TOP5 = [
    {"red": 0.20, "green": 0.44, "blue": 0.78},
    {"red": 0.91, "green": 0.49, "blue": 0.14},
    {"red": 0.24, "green": 0.70, "blue": 0.44},
    {"red": 0.62, "green": 0.32, "blue": 0.79},
    {"red": 0.90, "green": 0.29, "blue": 0.24},
]

_HDR_BG   = {"red": 0.22, "green": 0.29, "blue": 0.37}
_TITLE_BG = {"red": 0.95, "green": 0.96, "blue": 0.98}
_KPI_BG   = {"red": 0.93, "green": 0.96, "blue": 1.00}
_WHITE    = {"red": 1.0, "green": 1.0, "blue": 1.0}


# ── 제품명 정리 ────────────────────────────────────────────

def pack_label(product_name: str) -> str:
    """상품명 끝의 '（, 4개）' 같은 구성 표기 추출. 없으면 '기본'"""
    m = re.search(r',\s*(\d+개)\s*$', product_name)
    return m.group(1) if m else "기본"


def short_name(product_name: str) -> str:
    """긴 검색용 상품명 → 담당자가 알아볼 짧은 제품명

    '뉴트원 마그네슘 액티브 350mg 약사개발 ... 30정, 1개' → '마그네슘 액티브'
    """
    s = re.sub(r'^\[[^\]]*\]\s*', '', product_name)   # [4개월분] 같은 머리말 제거
    s = re.sub(r',\s*\d+개\s*$', '', s)               # 끝의 ', 4개' 제거
    for b in _BRANDS:
        s = re.sub(re.escape(b), '', s, flags=re.IGNORECASE)
    s = re.sub(r'(?<!\d),(?!\d)', ' ', s)   # 숫자 사이 콤마(5,000)는 남긴다
    out = []
    for tok in s.split():
        if _SPEC.match(tok):
            break
        out.append(tok)
        if len(out) >= 3:
            break
    label = " ".join(out).strip()
    if len(label) >= 2:
        return label
    return re.sub(r'^\[[^\]]*\]\s*', '', product_name)[:20]


# ── 네이버 API: 기간 내 주문을 제품 단위로 수집 ─────────────

def fetch_orders(headers: dict, base_url: str, sale_statuses: set,
                 date_from: date, date_to: date, exclude_ids: set) -> list:
    """행사기간 주문을 하루씩 조회해 제품 단위로 반환.

    반환: [{"date": date, "pid": str, "name": str, "qty": int, "amount": int}, ...]
    """
    rows = []
    d = date_from
    while d <= date_to:
        nxt = d + timedelta(days=1)
        f_str = d.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        t_str = nxt.strftime("%Y-%m-%dT00:00:00.000") + "%2B09:00"
        page = 1
        day_cnt = 0
        while True:
            url = (
                f"{base_url}/external/v1/pay-order/seller/product-orders"
                f"?from={f_str}&to={t_str}"
                f"&rangeType=PAYED_DATETIME&pageSize=300&page={page}"
            )
            resp = None
            for attempt in range(3):
                resp = requests.get(url, headers=headers, timeout=60)
                if resp.status_code == 429:
                    time.sleep(5 * (attempt + 1))
                else:
                    break
            if resp is None or resp.status_code != 200:
                print(f"    [한눈그래프] {d} 조회 실패 "
                      f"({resp.status_code if resp else 'no-response'})")
                break

            data = resp.json().get("data", {})
            for item in data.get("contents", []):
                po = item.get("content", {}).get("productOrder", {})
                if po.get("productOrderStatus", "") not in sale_statuses:
                    continue
                pid = str(po.get("productId", ""))
                if pid in exclude_ids:          # 공구 상품 제외
                    continue
                amount = po.get("totalPaymentAmount") or po.get("paymentAmount")
                if not amount:
                    amount = int(po.get("quantity") or 1) * int(po.get("unitPrice") or 0)
                rows.append({
                    "date": d,
                    "pid": pid,
                    "name": po.get("productName") or "",
                    "qty": int(po.get("quantity") or 1),
                    "amount": int(amount),
                })
                day_cnt += 1

            if not data.get("pagination", {}).get("hasNext", False):
                break
            page += 1

        print(f"    [한눈그래프] {d.strftime('%m/%d')}: {day_cnt}건")
        d = nxt
    return rows


# ── 집계 ───────────────────────────────────────────────────

def summarize(rows: list) -> dict:
    """제품별 / 날짜별 / 제품×날짜 집계"""
    by_prod = defaultdict(lambda: {
        "orders": 0, "qty": 0, "amount": 0, "packs": defaultdict(int), "pids": set(),
    })
    by_date = defaultdict(lambda: {"orders": 0, "qty": 0, "amount": 0})
    by_prod_date = defaultdict(lambda: defaultdict(int))

    for r in rows:
        key = short_name(r["name"])
        p = by_prod[key]
        p["orders"] += 1
        p["qty"] += r["qty"]
        p["amount"] += r["amount"]
        p["packs"][pack_label(r["name"])] += r["qty"]
        p["pids"].add(r["pid"])

        d = by_date[r["date"]]
        d["orders"] += 1
        d["qty"] += r["qty"]
        d["amount"] += r["amount"]

        by_prod_date[key][r["date"]] += r["amount"]

    products = []
    for name, v in by_prod.items():
        best_pack = max(v["packs"].items(), key=lambda x: x[1])[0] if v["packs"] else "기본"
        products.append({
            "name": name,
            "orders": v["orders"],
            "qty": v["qty"],
            "amount": v["amount"],
            "best_pack": best_pack,
            "pid_count": len(v["pids"]),
        })
    products.sort(key=lambda x: -x["amount"])

    dates = sorted(by_date.keys())
    daily = [{"date": d, **by_date[d]} for d in dates]

    return {"products": products, "daily": daily, "by_prod_date": by_prod_date}


# ── 시트 쓰기 ──────────────────────────────────────────────

def _fmt_day(d: date) -> str:
    return f"{d.month}.{d.day}"


def _get_or_create_tab(spreadsheet):
    """탭이 없으면 만들고, 있으면 내용·차트를 전부 비운다.
    (행사가 바뀌면 이전 행사 내용을 지우고 새로 그리기 위함)"""
    ws = None
    for w in spreadsheet.worksheets():
        if w.title == TAB_TITLE:
            ws = w
            break
    if ws is None:
        return spreadsheet.add_worksheet(title=TAB_TITLE, rows=400, cols=20), []

    drop = []
    meta = spreadsheet.fetch_sheet_metadata()
    for sheet in meta.get("sheets", []):
        if sheet.get("properties", {}).get("sheetId") == ws.id:
            for chart in sheet.get("charts", []):
                drop.append({"deleteEmbeddedObject": {"objectId": chart["chartId"]}})
    ws.clear()
    return ws, drop


def _rng(sheet_id, r1, c1, r2, c2):
    """1-indexed(끝 포함) → GridRange"""
    return {"sheetId": sheet_id, "startRowIndex": r1 - 1, "endRowIndex": r2,
            "startColumnIndex": c1 - 1, "endColumnIndex": c2}


def _basic_chart(sheet_id, title, chart_type, dom, series, anchor_row, anchor_col,
                 w=560, h=340, stacked=None, x_title="", y_title=""):
    """dom / series 항목은 (시작행, 끝행, 시작열, 끝열) — 1-indexed, 끝 포함"""
    def src(t):
        r1, r2, c1, c2 = t
        return {"sourceRange": {"sources": [_rng(sheet_id, r1, c1, r2, c2)]}}

    # 가로막대(BAR)는 값 축이 아래쪽이라 시리즈를 BOTTOM_AXIS에 붙여야 한다
    val_axis = "BOTTOM_AXIS" if chart_type == "BAR" else "LEFT_AXIS"

    spec = {
        "chartType": chart_type,
        "legendPosition": "BOTTOM_LEGEND",
        "headerCount": 1,
        "axis": [
            {"position": "BOTTOM_AXIS", "title": x_title,
             "titleTextPosition": {"horizontalAlignment": "CENTER"}},
            {"position": "LEFT_AXIS", "title": y_title,
             "titleTextPosition": {"horizontalAlignment": "CENTER"}},
        ],
        "domains": [{"domain": src(dom)}],
        "series": [
            {"series": src(t), "targetAxis": val_axis, "color": c}
            for t, c in series
        ],
    }
    if stacked:
        spec["stackedType"] = stacked
    return {"addChart": {"chart": {
        "spec": {
            "title": title,
            "titleTextFormat": {"bold": True, "fontSize": 12},
            "basicChart": spec,
        },
        "position": {"overlayPosition": {
            "anchorCell": {"sheetId": sheet_id,
                           "rowIndex": anchor_row - 1,
                           "columnIndex": anchor_col - 1},
            "widthPixels": w, "heightPixels": h,
        }},
    }}}


def write_dashboard(spreadsheet, promo: dict, agg: dict, excluded_ids: list) -> str:
    """탭을 통째로 다시 그린다."""
    products = agg["products"]
    daily = agg["daily"]
    by_prod_date = agg["by_prod_date"]

    ws, R = _get_or_create_tab(spreadsheet)
    sid = ws.id

    tot_amount = sum(p["amount"] for p in products)
    tot_orders = sum(p["orders"] for p in products)
    tot_qty = sum(p["qty"] for p in products)
    n_days = max(len(daily), 1)

    period_txt = (f"{promo['start'].year}.{promo['start'].month}.{promo['start'].day}"
                  f" ~ {promo['end'].month}.{promo['end'].day}"
                  f"  (총 {promo['total_days']}일 · 집계 {n_days}일)")
    if excluded_ids:
        head = ", ".join(excluded_ids[:3])
        more = f" 외 {len(excluded_ids) - 3}개" if len(excluded_ids) > 3 else ""
        excl_txt = (f"⚠️ 공구 상품 {len(excluded_ids)}개를 뺀 순수 행사 실적입니다 "
                    f"({head}{more})")
    else:
        excl_txt = "⚠️ 이 기간에 겹치는 공구 상품은 없습니다 (전체 판매 실적)"
    if promo.get("manual_excluded"):
        excl_txt += ("   ·   담당자 요청으로 제외: "
                     + ", ".join(promo["manual_excluded"]))

    rows = [
        [f"📊 {promo['title']}", "", "", "", "", promo["status_text"]],
        [f"마지막 업데이트: {promo['updated_at']}"],
        [f"📅 행사기간: {period_txt}"],
        [f"🏪 판매처: {promo['store']}"
         + (f"      ⚖️ 비교기간: {promo['comp_text']} "
            f"(같은 {n_days}일 · 매출 {promo['comp_total']:,}원)"
            if promo.get("comp_text") else "")],
        [excl_txt],
        [""],
        # A+B 병합해서 총매출을 넓게 — 나머지는 C~G
        ["총 매출", "", "총 주문수", "총 상품수량", "하루 평균 매출",
         "판매 제품종류", "비교기간 대비"],
        [tot_amount, "", tot_orders, tot_qty, tot_amount // n_days,
         len(products), promo["diff_text"]],
        [""],
        ["🏆 제품별 실적 — 어떤 제품이 얼마나 팔렸는지 (매출 높은 순)"],
        ["순위", "제품", "주문수", "상품수량", "매출", "매출비중", "가장 많이 나간 구성"],
    ]
    for i, p in enumerate(products, 1):
        rows.append([
            i, p["name"], p["orders"], p["qty"], p["amount"],
            (p["amount"] / tot_amount) if tot_amount else 0,
            p["best_pack"],
        ])

    PROD_HDR = 11
    PROD_1 = 12
    PROD_N = PROD_HDR + len(products)

    CHART_TOP = PROD_N + 2      # 차트 밴드 시작 행
    SRC_TOP = CHART_TOP + 37    # 차트 아래 — 그래프 원본 데이터 안내문 행

    top10 = products[:10]
    top5 = products[:5]

    # 블록1(A~B) 제품TOP10 / 블록2(D~G) 날짜별 / 블록3(I~N) TOP5 제품×날짜
    n_block = max(len(top10), len(daily)) + 1
    src_rows = []
    for i in range(n_block):
        line = [""] * 14
        if i == 0:
            line[0], line[1] = "제품", "매출"
            line[3], line[4], line[5], line[6] = "날짜", "매출", "주문수", "상품수량"
            line[8] = "날짜"
            for j, p in enumerate(top5):
                line[9 + j] = p["name"]
        else:
            k = i - 1
            if k < len(top10):
                line[0], line[1] = top10[k]["name"], top10[k]["amount"]
            if k < len(daily):
                d = daily[k]
                line[3] = _fmt_day(d["date"])
                line[4], line[5], line[6] = d["amount"], d["orders"], d["qty"]
                line[8] = _fmt_day(d["date"])
                for j, p in enumerate(top5):
                    line[9 + j] = by_prod_date[p["name"]].get(d["date"], 0)
        src_rows.append(line)

    while len(rows) < SRC_TOP - 1:
        rows.append([""])
    rows.append(["※ 아래는 위 그래프를 그리기 위한 원본 데이터입니다 (수정하지 마세요)"])
    rows.extend(src_rows)

    ws.update(values=rows, range_name="A1", value_input_option="USER_ENTERED")

    SRC_HDR = SRC_TOP + 1
    SRC_END = SRC_HDR + n_block - 1

    # ── 서식 ──
    R.append({"repeatCell": {
        "range": _rng(sid, 1, 1, 1, 7),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True, "fontSize": 14},
            "backgroundColor": _TITLE_BG, "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(textFormat,backgroundColor,verticalAlignment)",
    }})
    R.append({"repeatCell": {
        "range": _rng(sid, 2, 1, 5, 7),
        "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 10},
                                       "backgroundColor": _TITLE_BG}},
        "fields": "userEnteredFormat(textFormat,backgroundColor)",
    }})
    for r0, c0 in ((7, 1), (8, 1)):
        R.append({"mergeCells": {"range": _rng(sid, r0, c0, r0, 2),
                                 "mergeType": "MERGE_ALL"}})
    R.append({"repeatCell": {
        "range": _rng(sid, 7, 1, 7, 7),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": _WHITE},
            "backgroundColor": _HDR_BG, "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"}},
        "fields": ("userEnteredFormat(textFormat,backgroundColor,"
                   "horizontalAlignment,verticalAlignment,wrapStrategy)"),
    }})
    R.append({"repeatCell": {
        "range": _rng(sid, 8, 1, 8, 7),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True, "fontSize": 13},
            "backgroundColor": _KPI_BG, "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
            "numberFormat": {"type": "NUMBER", "pattern": "#,##0"}}},
        "fields": ("userEnteredFormat(textFormat,backgroundColor,horizontalAlignment,"
                   "verticalAlignment,wrapStrategy,numberFormat)"),
    }})
    # 비교기간 대비(G8)는 글자라서 숫자서식을 풀어준다
    R.append({"repeatCell": {
        "range": _rng(sid, 8, 7, 8, 7),
        "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"},
                                       "textFormat": {"bold": True, "fontSize": 11}}},
        "fields": "userEnteredFormat(numberFormat,textFormat)",
    }})
    # KPI 두 줄은 넉넉한 높이로
    for r0, px in ((7, 34), (8, 40)):
        R.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "ROWS",
                      "startIndex": r0 - 1, "endIndex": r0},
            "properties": {"pixelSize": px}, "fields": "pixelSize",
        }})
    R.append({"repeatCell": {
        "range": _rng(sid, 10, 1, 10, 7),
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 12}}},
        "fields": "userEnteredFormat.textFormat",
    }})
    R.append({"repeatCell": {
        "range": _rng(sid, PROD_HDR, 1, PROD_HDR, 7),
        "cell": {"userEnteredFormat": {
            "textFormat": {"bold": True, "foregroundColor": _WHITE},
            "backgroundColor": _HDR_BG, "horizontalAlignment": "CENTER"}},
        "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)",
    }})

    if products:
        R.append({"repeatCell": {
            "range": _rng(sid, PROD_1, 3, PROD_N, 5),
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                "horizontalAlignment": "RIGHT"}},
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)",
        }})
        R.append({"repeatCell": {
            "range": _rng(sid, PROD_1, 6, PROD_N, 6),
            "cell": {"userEnteredFormat": {
                "numberFormat": {"type": "PERCENT", "pattern": "0.0%"},
                "horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat(numberFormat,horizontalAlignment)",
        }})
        for col in (1, 7):
            R.append({"repeatCell": {
                "range": _rng(sid, PROD_1, col, PROD_N, col),
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }})
        for i in range(min(3, len(products))):
            R.append({"repeatCell": {
                "range": _rng(sid, PROD_1 + i, 1, PROD_1 + i, 7),
                "cell": {"userEnteredFormat": {
                    "backgroundColor": {"red": 1.0, "green": 0.97, "blue": 0.88},
                    "textFormat": {"bold": True}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }})
        R.append({"addConditionalFormatRule": {"rule": {
            "ranges": [_rng(sid, PROD_1, 5, PROD_N, 5)],
            "gradientRule": {
                "minpoint": {"color": _WHITE, "type": "MIN"},
                "maxpoint": {"color": {"red": 0.62, "green": 0.77, "blue": 0.95},
                             "type": "MAX"},
            }}, "index": 0}})

    R.append({"repeatCell": {
        "range": _rng(sid, SRC_TOP, 1, SRC_END, 14),
        "cell": {"userEnteredFormat": {"textFormat": {
            "fontSize": 9,
            "foregroundColor": {"red": 0.62, "green": 0.62, "blue": 0.62}}}},
        "fields": "userEnteredFormat.textFormat",
    }})

    # 제품표 헤더도 줄바꿈 허용 (좁은 열에서 글자가 잘리지 않도록)
    R.append({"repeatCell": {
        "range": _rng(sid, PROD_HDR, 1, PROD_HDR, 7),
        "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP",
                                       "verticalAlignment": "MIDDLE"}},
        "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
    }})
    if products:
        # 긴 제품명은 잘리지 말고 줄바꿈되게
        R.append({"repeatCell": {
            "range": _rng(sid, PROD_1, 2, PROD_N, 2),
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP",
                                           "verticalAlignment": "MIDDLE"}},
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
        }})

    for col, width in [(1, 60), (2, 265), (3, 90), (4, 100), (5, 130),
                       (6, 100), (7, 170)]:
        R.append({"updateDimensionProperties": {
            "range": {"sheetId": sid, "dimension": "COLUMNS",
                      "startIndex": col - 1, "endIndex": col},
            "properties": {"pixelSize": width}, "fields": "pixelSize",
        }})
    R.append({"updateSheetProperties": {
        "properties": {"sheetId": sid,
                       "gridProperties": {"frozenRowCount": PROD_HDR}},
        "fields": "gridProperties.frozenRowCount",
    }})

    # ── 차트 ──
    if daily and products:
        n1, nd = len(top10), len(daily)
        R.append(_basic_chart(
            sid, "제품별 매출 TOP 10 — 어떤 제품이 잘 팔렸나", "BAR",
            (SRC_HDR, SRC_HDR + n1, 1, 1),
            [((SRC_HDR, SRC_HDR + n1, 2, 2), _C_SALES)],
            CHART_TOP, 1, x_title="매출(원)", y_title="제품"))

        R.append(_basic_chart(
            sid, "날짜별 매출 추이 — 언제 잘 팔렸나", "COLUMN",
            (SRC_HDR, SRC_HDR + nd, 4, 4),
            [((SRC_HDR, SRC_HDR + nd, 5, 5), _C_SALES)],
            CHART_TOP, 8, x_title="날짜", y_title="매출(원)"))

        R.append(_basic_chart(
            sid, "날짜별 주문수 / 상품수량", "COLUMN",
            (SRC_HDR, SRC_HDR + nd, 4, 4),
            [((SRC_HDR, SRC_HDR + nd, 6, 6), _C_ORDER),
             ((SRC_HDR, SRC_HDR + nd, 7, 7), _C_QTY)],
            CHART_TOP + 18, 1, x_title="날짜", y_title="건 / 개"))

        if len(top5) >= 2:
            series = [((SRC_HDR, SRC_HDR + nd, 10 + j, 10 + j), _C_TOP5[j])
                      for j in range(len(top5))]
            R.append(_basic_chart(
                sid, "인기 제품 TOP5 — 날짜별 매출 쌓기", "COLUMN",
                (SRC_HDR, SRC_HDR + nd, 9, 9), series,
                CHART_TOP + 18, 8, stacked="STACKED",
                x_title="날짜", y_title="매출(원)"))

    if R:
        spreadsheet.batch_update({"requests": R})
    return TAB_TITLE
