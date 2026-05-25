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

# ── 성분명 기반 매칭용 상수 ──────────────────────────────────────────
# 브랜드명: 핵심 성분명 추출 시 제거
_BRAND_WORDS = frozenset({
    '뉴트원', 'nutone', 'jdhealth', 'jd헬스', '제이디헬스', 'nutpet', '뉴트펫',
})
# 노이즈 단어: 마케팅·수식어 — 핵심 성분명과 무관
_NOISE_WORDS = frozenset({
    '부스터', '프리미엄', '고함량', '강화', '스페셜', '플러스', '고급',
    '단독', '특가', '할인', '기획', '이벤트', '한정', '세일',
    '신제품', '리뉴얼', '초특가', '최저가', '공구',
})

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


def _extract_product_key(option: str) -> str:
    """옵션명에서 제품 식별 키 추출 (멀티제품 캠페인 전용).
    '뉴트원 유산균 / 2BOX'            → '뉴트원 유산균'
    '뉴트원알티지오메가3 / 4BOX'       → '뉴트원알티지오메가3'
    '선택: 12BOX(50%)'                 → ''  (단일제품 옵션)
    """
    s = str(option).strip()
    if "/" in s:
        left = s.split("/", 1)[0].strip()
        # 왼쪽에 BOX 패턴 없으면 제품명으로 판단
        if not re.search(r'\d+\s*(?:BOX|박스|bx)', left, re.IGNORECASE):
            return left
    # / 없거나 왼쪽에 BOX가 있는 경우 → 단일제품 형태
    stripped = re.sub(r'선택\s*[:：]\s*', '', s)
    stripped = re.sub(r'\d+\s*(?:BOX|박스|bx|개입|구|정|캡슐|세트|팩)', '', stripped, flags=re.IGNORECASE)
    stripped = re.sub(r'[\(\[\（].*?[\)\]\）]', '', stripped)
    stripped = re.sub(r'\d+', '', stripped).strip()
    return stripped if len(stripped) >= 2 else ''


def _short_product_label(key: str) -> str:
    """제품 키에서 브랜드명 제거 후 짧은 표시 이름 반환.
    '뉴트원 유산균'       → '유산균'
    '뉴트원알티지오메가3'  → '알티지오메가3'
    """
    s = str(key).strip()
    for brand in sorted(_BRAND_WORDS, key=len, reverse=True):
        pat = re.compile(re.escape(brand), re.IGNORECASE)
        s_new = pat.sub('', s).strip()
        if s_new and s_new != s:
            return s_new
    return s


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
        ws = ss.worksheet("이익계산참고사항(자사확인용)")
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


def _core_ingredient(name: str) -> str:
    """제품명에서 브랜드명·포장단위·마케팅 단어를 제거하고 핵심 성분명만 반환.

    예) '뉴트원초임계알티지오메가3부스터'           → '초임계알티지오메가3'
        '[단5일]뉴트원 초임계알티지 오메가3 30캡슐' → '초임계알티지오메가3'
        '뉴트원 코큐엔자임Q10 코큐텐 30캡슐'        → '코큐엔자임q10코큐텐'
    """
    s = re.sub(r'\[.*?\]', '', str(name)).lower()   # 브래킷 제거
    # 공백·특수문자 기준 단어 분리
    words = re.split(r'[\s,./·×()\[\]（）]+', s)
    filtered = []
    for w in words:
        w = re.sub(r'[×X\[\]()（）]', '', w).strip()
        if not w or len(w) < 2:
            continue
        if w in _BRAND_WORDS or w in _NOISE_WORDS:
            continue
        # 숫자+단위 패턴 제거 (예: 30캡슐, 60정, 500mg, 3box)
        if re.fullmatch(
            r'\d+\s*(?:캡슐|정|포|박스|box|bx|세트|set|팩|pack|ml|mg|g|kg|ea|개입|구)',
            w, re.IGNORECASE,
        ):
            continue
        if re.fullmatch(r'\d+', w):   # 단순 숫자
            continue
        filtered.append(w)

    # 합쳐서 특수문자 제거
    joined = re.sub(r'[^\w가-힣a-z0-9]', '', ''.join(filtered))
    # 공백 없는 복합어에서도 브랜드·노이즈 단어 제거
    # (예: '뉴트원초임계알티지오메가3부스터' → 분리 없이 통째로 들어온 경우 처리)
    for word in sorted(_BRAND_WORDS | _NOISE_WORDS, key=len, reverse=True):
        joined = joined.replace(word, '')
    return joined


def _match_profit(product_name: str, profit_params: list) -> dict:
    """이익계산참고사항 제품명 퍼지 매칭.

    1단계  : 특수문자·공백 제거 후 포함 매칭 (완전 일치)
    1.5단계: 핵심 성분명 기반 매칭 — 브랜드·포장·마케팅 단어 제거 후 포함 비교
             → 공백 없는 복합어('뉴트원초임계알티지오메가3부스터')나
                '30캡슐' vs '부스터' 같은 포장 차이를 무시하고 성분으로만 매칭
    2단계  : 키워드 분해 매칭 — 매칭 수(count) 우선, 동점 시 비율(ratio) 결정
    """
    if not product_name or not profit_params:
        return {}
    norm = lambda s: re.sub(r'[\s\[\]()（）,./·×X]', '', str(s)).lower()
    name_n = norm(product_name)

    # ── 1단계: 정규화 후 완전 포함 매칭 ──────────────────────────────
    for row in profit_params:
        short = norm(row.get("name", ""))
        if short and len(short) >= 4 and short in name_n:
            return row

    # ── 1.5단계: 핵심 성분명 기반 매칭 ──────────────────────────────
    # 브랜드명·포장단위·마케팅 단어 제거 후 성분명만 비교
    core_name = _core_ingredient(product_name)
    if len(core_name) >= 3:
        for row in profit_params:
            core_pp = _core_ingredient(row.get("name", ""))
            if core_pp and len(core_pp) >= 3:
                # 어느 쪽이든 상대방에 포함되면 매칭
                if core_pp in core_name or core_name in core_pp:
                    return row

    # ── 2단계: 키워드 분해 매칭 ──────────────────────────────────────
    clean_raw = re.sub(r'\[.*?\]', '', product_name)
    clean_n   = norm(clean_raw)

    # 단어 경계 매칭용 공백 보존 버전 (숫자 교차 연결 artifact 방지)
    norm_wb  = lambda s: re.sub(r'[\[\]()（）,./·×X]', '', str(s)).lower()
    clean_wb = re.sub(r'\s+', ' ', norm_wb(clean_raw)).strip()
    name_wb  = re.sub(r'\s+', ' ', norm_wb(product_name)).strip()

    def _kw_match(kw: str) -> bool:
        if not kw:
            return False
        kw_n = norm(kw)
        # 단어 경계 매칭 (공백 기준)
        wb_ok = ((' ' + kw + ' ') in (' ' + clean_wb + ' ')
                 or (' ' + kw + ' ') in (' ' + name_wb + ' ')
                 or clean_wb.startswith(kw + ' ') or clean_wb.endswith(' ' + kw)
                 or name_wb.startswith(kw + ' ')  or name_wb.endswith(' ' + kw))
        # 공백 제거 버전 (복합어 보조) — 숫자 시작 키워드는 교차 오매칭 가능성 제외
        rm_ok = kw_n in clean_n or kw_n in name_n
        return wb_ok or (rm_ok and not (kw_n[:1].isdigit() if kw_n else False))

    best_matches = 0
    best_score   = 0.0
    best_row     = None

    for row in profit_params:
        raw = str(row.get("name", "")).lower()
        keywords = [w for w in re.split(r'[\s,./·×()]+', raw) if len(w) >= 2]
        if not keywords:
            continue
        matches = sum(1 for kw in keywords if _kw_match(kw))
        if matches < 2:
            continue
        score = matches / len(keywords)
        # 매칭 수 우선, 동점 시 비율로 결정
        if (matches, score) > (best_matches, best_score):
            best_matches = matches
            best_score   = score
            best_row     = row

    return best_row or {}


def read_option_totals_from_sheet(sheet_url: str) -> dict:
    """개별 캠페인 시트(판매현황 탭)에서 옵션별 총 제품수 반환.
    반환: {option_name: total_product_count}
    옵션이 1개뿐이거나 읽기 실패 시 {} 반환.
    """
    if not sheet_url:
        return {}
    try:
        client = _get_client()
        sheet_id = _extract_sheet_id(sheet_url)
        spreadsheet = client.open_by_key(sheet_id)
        ws = spreadsheet.sheet1
        all_vals = ws.get_all_values()

        # 헤더 행 탐색 (날짜/옵션 컬럼)
        header_idx = None
        for i, row in enumerate(all_vals):
            if len(row) >= 2 and row[0].strip() == "날짜" and row[1].strip() == "옵션":
                header_idx = i
                break
        if header_idx is None:
            return {}

        # 옵션별 제품수 합산
        option_totals = {}
        for row in all_vals[header_idx + 1:]:
            if not row or not row[0].strip():
                continue
            # 그래프용 보조 데이터 시작 시 종료
            if row[0].strip() == "날짜 (그래프용)":
                break
            option = row[1].strip() if len(row) > 1 else ""
            try:
                products = int(str(row[3]).replace(",", "").strip() or 0) if len(row) > 3 else 0
            except (ValueError, TypeError):
                products = 0
            if option and products > 0:
                option_totals[option] = option_totals.get(option, 0) + products

        return option_totals
    except Exception as e:
        print(f"  [옵션별 시트 읽기 실패] {e}")
        return {}


def calc_option_cost(option_totals: dict, profit_params: list) -> dict:
    """옵션별 제품수 × 원가를 합산하여 총 원가 계산.
    반환: {
        "total_cost":    int,        # 총 원가 합계 (cost_i × product_count_i 합산)
        "delivery":      int,        # 대표 배송비 (첫 번째 매칭 기준)
        "ch_comm":       float,      # 대표 채널수수료율 (첫 번째 매칭 기준)
        "matched_names": list[str],  # 매칭된 제품명 목록
        "matched_count": int,        # 매칭 성공한 옵션 수
    }
    """
    result = {
        "total_cost":    0,
        "delivery":      3000,
        "ch_comm":       0.0,
        "matched_names": [],
        "matched_count": 0,
    }
    if not option_totals or not profit_params:
        return result

    delivery_set = False
    ch_comm_set  = False

    for option, product_count in option_totals.items():
        pp = _match_profit(option, profit_params)
        if not pp:
            continue
        result["matched_count"] += 1
        cost = int(pp.get("cost", 0) or 0)
        result["total_cost"] += cost * product_count
        name = str(pp.get("name", ""))
        if name and name not in result["matched_names"]:
            result["matched_names"].append(name)
        if not ch_comm_set:
            result["ch_comm"] = float(pp.get("channel_comm", 0) or 0)
            ch_comm_set = True
        if not delivery_set:
            result["delivery"] = int(pp.get("delivery", 3000) or 3000)
            delivery_set = True

    return result


def read_summary_tab(spreadsheet_url: str) -> dict:
    """'캠페인 실적' 탭 읽기. 반환: {제목: row_dict}"""
    try:
        client = _get_client()
        sheet_id = _extract_sheet_id(spreadsheet_url)
        spreadsheet = client.open_by_key(sheet_id)
        ws = spreadsheet.worksheet("캠페인 실적(자사확인용)")
        records = ws.get_all_records()
        result = {}
        for row in records:
            title = str(row.get("제목", "")).strip()
            if title:
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
        ws = spreadsheet.worksheet("캠페인 실적(자사확인용)")
        if ws.col_count < 30:
            ws.resize(rows=ws.row_count, cols=30)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="캠페인 실적(자사확인용)", rows=200, cols=30)

    header = [
        "No", "제목", "제품명", "스토어", "시작일", "종료일",
        "주문수", "제품수", "상태", "진행링크", "업데이트",
        "매출", "공구수수료(vat포함)", "이익", "이익률", "매칭원가제품명",
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

        # ── P열 매칭원가제품명 ────────────────────
        matched_name = extras.get("matched_name", "")

        # ── N열 이익 수식 ─────────────────────────
        pp                = extras.get("profit_params", {})
        option_cost_total = extras.get("option_cost_total", 0)
        option_delivery   = extras.get("option_delivery", 0)
        option_ch_comm    = extras.get("option_ch_comm", 0.0)

        n_formula = ""
        if isinstance(revenue, int) and revenue > 0 and commission != "":
            if option_cost_total > 0:
                # 멀티제품: 옵션별 원가×제품수 합산값 하드코딩
                del_v = option_delivery or 3000
                ch_v  = option_ch_comm
                n_formula = (
                    f"=L{row_sheet}"
                    f"-{option_cost_total}"
                    f"-(L{row_sheet}*(M{row_sheet}/100))"
                    f"-(L{row_sheet}*{ch_v})"
                    f"-({del_v}*G{row_sheet})"
                )
            elif pp:
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
            matched_name,
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
        # L-P: 연한 초록 배경으로 구분
        COLOR_LMNO_BG = {"red": 0.90, "green": 0.97, "blue": 0.90}
        R.append({"repeatCell": {
            "range": cell_range(row_idx, 11, row_idx + 1, 16),  # L-P
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

    # 열 너비 (A-P)
    col_widths = [40, 100, 260, 65, 85, 85, 55, 55, 55, 200, 110, 100, 70, 100, 70, 180]
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

    # ── 멀티제품 감지 ─────────────────────────────────────────────────
    # 옵션명에서 제품 키를 추출해 고유 제품 목록을 파악 (순서 보존)
    _seen_pks: set = set()
    unique_products: list = []
    for _r in aggregated:
        _pk = _extract_product_key(_r["option"])
        if _pk and _pk not in _seen_pks:
            _seen_pks.add(_pk)
            unique_products.append(_pk)

    is_multi_product = len(unique_products) >= 2

    # 제품별 총 제품수 / 날짜별 제품수 집계 (멀티제품 시만 계산)
    prod_product_totals: dict = defaultdict(int)          # {product_key: total_products}
    date_prod_products: dict = defaultdict(lambda: defaultdict(int))  # {date: {pk: products}}
    if is_multi_product:
        for _r in aggregated:
            _pk = _extract_product_key(_r["option"])
            if _pk:
                prod_product_totals[_pk] += _r["daily_products"]
                date_prod_products[_r["date"]][_pk] += _r["daily_products"]

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
    _summary_line = f"총 주문수: {total_orders:,}건  |  총 제품수: {total_products:,}개"
    if is_multi_product:
        _prod_breakdown = " / ".join(
            f"{_short_product_label(pk)}: {prod_product_totals[pk]:,}개"
            for pk in unique_products
        )
        _summary_line += f"\n({_prod_breakdown})"
    if inventory is not None:
        remaining = max(0, inventory - total_products)
        values.append([
            _summary_line,
            "", "", "", "",
            f"📦 초기재고: {inventory:,}개  |  남은재고: {remaining:,}개",
            "",
        ])
    else:
        values.append([_summary_line, "", "", "", "", ""])
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
    if is_multi_product:
        _prod_labels = [_short_product_label(pk) for pk in unique_products]
        values.append(["날짜 (그래프용)", "주문수", "제품수"] + _prod_labels)
        for d in daily_totals:
            _pc = [date_prod_products[d["date"]].get(pk, 0) for pk in unique_products]
            values.append([_fmt_date(d["date"]), d["orders"], d["products"]] + _pc)
    else:
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
            # 시간대별 제품별 집계 (멀티제품 전용)
            _hour_prod_p: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
            if is_multi_product:
                for _r in sales_data:
                    _pk2 = _extract_product_key(_r.get("option", ""))
                    if _pk2:
                        _d2, _h2 = _r["date"], _r.get("hour", 0)
                        _bx2 = _extract_box_count(_r.get("option", ""), unit_keyword)
                        _hour_prod_p[_d2][_h2][_pk2] += _r["quantity"] * _bx2

            HOURLY_CHART_DATA_START = len(values)
            if is_multi_product:
                _prod_labels_h = [_short_product_label(pk) for pk in unique_products]
                values.append(["날짜+시간", "주문수", "제품수"] + _prod_labels_h)
                for date in dates_sorted:
                    d_obj = datetime.strptime(date, "%Y-%m-%d")
                    short_date = f"{d_obj.month}.{d_obj.day}일"
                    for hour in sorted(hourly_orders[date].keys()):
                        o = hourly_orders[date][hour]
                        p = hourly_products[date].get(hour, 0)
                        if o > 0:
                            _pc_h = [_hour_prod_p[date][hour].get(pk, 0) for pk in unique_products]
                            values.append([f"{short_date} {hour}시", o, p] + _pc_h)
            else:
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

    # 행6(총계) 강조 + 멀티제품 시 줄바꿈
    if is_multi_product:
        R.append({"repeatCell": {
            "range": cell_range(5, 0, 6, 7),
            "cell": {"userEnteredFormat": {
                "wrapStrategy": "WRAP",
                "textFormat": {"bold": True, "fontSize": 10},
            }},
            "fields": "userEnteredFormat(wrapStrategy,textFormat)",
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
