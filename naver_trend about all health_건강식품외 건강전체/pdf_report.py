"""
PDF 리포트 생성 모듈.
ReportLab을 사용하여 건강식품 트렌드 분석 결과를 PDF로 출력합니다.
한글 폰트: Windows 맑은 고딕 (malgun.ttf)
"""

import re
from pathlib import Path
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------

# Windows 및 Linux(Google Cloud) 한글 폰트 경로 후보
FONT_CANDIDATES = [
    # Windows
    ("C:/Windows/Fonts/malgun.ttf",   "C:/Windows/Fonts/malgunbd.ttf"),
    # Linux - NanumGothic (apt: fonts-nanum)
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
     "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    ("/usr/share/fonts/nanum/NanumGothic.ttf",
     "/usr/share/fonts/nanum/NanumGothicBold.ttf"),
]

def _register_fonts():
    """
    Register Korean fonts and font family.
    - Must call registerFontFamily so that <b> and <i> XML tags in Paragraph
      use Korean-capable fonts instead of falling back to Helvetica.
    - Falls back to Helvetica if fonts not found.
    """
    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    for regular_path, bold_path in FONT_CANDIDATES:
        if not Path(regular_path).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("Korean", regular_path))
            try:
                pdfmetrics.registerFont(TTFont("KoreanBold", bold_path))
            except Exception:
                pdfmetrics.registerFont(TTFont("KoreanBold", regular_path))

            registerFontFamily(
                "Korean",
                normal="Korean",
                bold="KoreanBold",
                italic="Korean",
                boldItalic="KoreanBold",
            )
            print(f"[PDF] 한글 폰트 등록 완료: {regular_path}")
            return "Korean", "KoreanBold"
        except Exception as e:
            print(f"[PDF] 폰트 등록 실패 ({regular_path}): {e}")
            continue

    print("[PDF] 한글 폰트를 찾지 못했습니다. Helvetica 사용 (한글 깨짐 가능)")
    return "Helvetica", "Helvetica-Bold"


FONT_NORMAL, FONT_BOLD = _register_fonts()

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

C_PRIMARY   = colors.HexColor("#1A237E")   # deep blue — header
C_ACCENT    = colors.HexColor("#E53935")   # red      — early_rising
C_ORANGE    = colors.HexColor("#FB8C00")   # orange   — growing
C_GREEN     = colors.HexColor("#43A047")   # green    — stable
C_GRAY      = colors.HexColor("#9E9E9E")   # gray     — declining
C_LIGHTBLUE = colors.HexColor("#E3F2FD")   # light bg — table header
C_LIGHTYELLOW = colors.HexColor("#FFFDE7") # light bg — warning section
C_WHITE     = colors.white
C_BLACK     = colors.HexColor("#212121")

PHASE_COLORS = {
    "early_rising": C_ACCENT,
    "growing":      C_ORANGE,
    "stable":       C_GREEN,
    "peak":         colors.HexColor("#1565C0"),
    "declining":    C_GRAY,
    "unknown":      C_GRAY,
}

# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

def _style(name, **kwargs):
    base = dict(fontName=FONT_NORMAL, fontSize=10, leading=14,
                textColor=C_BLACK, spaceAfter=2)
    base.update(kwargs)
    return ParagraphStyle(name, **base)


S_TITLE    = _style("title",    fontName=FONT_BOLD, fontSize=20, leading=26,
                    textColor=C_WHITE, spaceAfter=6)
S_H1       = _style("h1",       fontName=FONT_BOLD, fontSize=13, leading=17,
                    textColor=C_PRIMARY, spaceBefore=8, spaceAfter=3)
S_H2       = _style("h2",       fontName=FONT_BOLD, fontSize=10, leading=14,
                    textColor=C_PRIMARY, spaceBefore=5, spaceAfter=2)
S_BODY     = _style("body",     fontSize=9,  leading=13)
S_SMALL    = _style("small",    fontSize=8,  leading=11, textColor=C_GRAY)
S_WARNING  = _style("warning",  fontSize=8,  leading=12,
                    textColor=colors.HexColor("#BF360C"))
S_KEYWORD  = _style("keyword",  fontName=FONT_BOLD, fontSize=9)

# Table cell styles — always use Paragraph for cells so text wraps properly
_TC_BASE  = dict(fontName=FONT_NORMAL, fontSize=7.5, leading=10,
                 textColor=C_BLACK, spaceAfter=0, spaceBefore=0,
                 wordWrap="CJK", splitLongWords=True)
TC_HDR    = ParagraphStyle("tc_hdr",   fontName=FONT_BOLD,  fontSize=8,
                            leading=11, textColor=C_WHITE,
                            alignment=1, spaceAfter=0, spaceBefore=0)
TC_RANK   = ParagraphStyle("tc_rank",  alignment=1, **_TC_BASE)
TC_KW     = ParagraphStyle("tc_kw",    fontName=FONT_BOLD, fontSize=7.5,
                            leading=10, textColor=C_BLACK, alignment=0,
                            spaceAfter=0, spaceBefore=0,
                            wordWrap="CJK", splitLongWords=True)
TC_NUM    = ParagraphStyle("tc_num",   alignment=2, **_TC_BASE)
TC_CTR    = ParagraphStyle("tc_ctr",   alignment=1, **_TC_BASE)
TC_PHASE  = ParagraphStyle("tc_phase", fontName=FONT_BOLD, fontSize=7.5,
                            leading=10, textColor=C_BLACK, alignment=1,
                            spaceAfter=0, spaceBefore=0,
                            wordWrap="CJK", splitLongWords=True)

# Usable page width = A4(210mm) - left(15mm) - right(15mm) = 180mm
PAGE_W = 180


def _p(text, style=None):
    return Paragraph(text or " ", style or S_BODY)


def _cell(text, style=TC_NUM):
    """Create a Paragraph cell for use inside a Table."""
    return Paragraph(str(text) if text is not None else " ", style)


def _phase_label(phase: str) -> str:
    return {
        "early_rising": "얼리라이징",
        "growing":      "성장중",
        "stable":       "안정",
        "peak":         "최고점",
        "declining":    "하락",
        "unknown":      "미상",
    }.get(phase, phase)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _cover_table(today: str, total_kw: int) -> list:
    """Blue header banner."""
    S_TITLE_SMALL = _style("title_small", fontName=FONT_NORMAL, fontSize=9,
                           leading=13, textColor=C_WHITE, spaceAfter=0)
    data = [[
        _p(f"네이버 데이터랩<br/>건강식품 트렌드 분석", S_TITLE),
        _p(
            f"생성일시: {today}<br/>"
            f"분석 카테고리: 식품 &gt; 건강식품<br/>"
            f"데이터 출처: 쇼핑인사이트 + 검색어트렌드 API<br/>"
            f"분석 키워드: {total_kw}개",
            S_TITLE_SMALL,
        ),
    ]]
    t = Table(data, colWidths=[110*mm, 70*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), C_PRIMARY),
        ("TEXTCOLOR",   (0, 0), (-1, -1), C_WHITE),
        ("ALIGN",       (1, 0), (1, 0),   "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0),   6*mm),
        ("RIGHTPADDING",(1, 0), (1, 0),   4*mm),
        ("TOPPADDING",  (0, 0), (-1, -1), 5*mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5*mm),
    ]))
    return [t, Spacer(1, 6*mm)]


def _keyword_table(
    kws: list[dict],
    columns: list[tuple],   # [(header_text, cell_fn, style, width_mm), ...]
    header_bg: object,
    alt_row_color: object,
    grid_color: str,
    phase_col_idx: int = -1,  # column index of trend phase (-1 = no coloring)
) -> "Table":
    """
    범용 키워드 테이블 빌더.
    모든 셀을 Paragraph로 감싸 한글 자동 줄바꿈을 보장합니다.
    """
    col_w = [c[3] * mm for c in columns]

    # Header row
    header_row = [_cell(c[0], TC_HDR) for c in columns]

    # Data rows
    data_rows = []
    for kw in kws:
        row = []
        for _, cell_fn, style, _ in columns:
            val = cell_fn(kw)
            row.append(_cell(val, style))
        data_rows.append(row)

    rows = [header_row] + data_rows
    t = Table(rows, colWidths=col_w, repeatRows=1)

    ts_cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0),  header_bg),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, alt_row_color]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor(grid_color)),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
    ]

    # Color phase column cells individually
    if phase_col_idx >= 0:
        for r, kw in enumerate(kws, start=1):
            c = PHASE_COLORS.get(kw["trend_phase"], C_GRAY)
            ts_cmds.append(("TEXTCOLOR", (phase_col_idx, r), (phase_col_idx, r), c))

    t.setStyle(TableStyle(ts_cmds))
    return t


def _early_rising_section(early_kws: list[dict]) -> list:
    """얼리라이징 키워드 테이블."""
    from keyword_volume import format_volume

    story = [_p("지금 당장 주목할 얼리라이징 키워드", S_H1)]

    if not early_kws:
        story.append(_p("현재 분석 기간 기준 얼리라이징 단계 키워드가 없습니다.", S_SMALL))
        story.append(Spacer(1, 4*mm))
        return story

    story.append(_p(
        "아직 경쟁이 적지만 검색량이 빠르게 오르고 있는 성분 · 제품 - 생산 준비 우선 검토 대상",
        S_SMALL
    ))
    story.append(Spacer(1, 2*mm))

    has_volume = any(kw.get("monthly_total_search", 0) > 0 for kw in early_kws)

    # PC+모바일 두 컬럼 표시. 총 폭 180mm
    if has_volume:
        columns = [
            ("순위",        lambda k: str(early_kws.index(k)+1),                      TC_RANK,  8),
            ("키워드",      lambda k: k["keyword"],                                    TC_KW,   32),
            ("기회점수",    lambda k: f"{k['opportunity_score']:.1f}",                 TC_NUM,  17),
            ("성장률",      lambda k: f"{k['recent_growth_rate']:+.1f}%",              TC_NUM,  17),
            ("얼리무버",    lambda k: f"{k['early_mover_score']:.1f}",                 TC_NUM,  17),
            ("3개월평균",   lambda k: f"{k['avg_ratio_3mo']:.1f}",                     TC_NUM,  17),
            ("PC검색량",    lambda k: format_volume(k.get("monthly_pc_search", 0)),    TC_NUM,  26),
            ("모바일검색량",lambda k: format_volume(k.get("monthly_mobile_search", 0)),TC_NUM,  26),
        ]  # 8+32+17+17+17+17+26+26 = 160mm
    else:
        columns = [
            ("순위",      lambda k: str(early_kws.index(k)+1),             TC_RANK, 10),
            ("키워드",    lambda k: k["keyword"],                           TC_KW,   42),
            ("기회점수",  lambda k: f"{k['opportunity_score']:.1f}",        TC_NUM,  22),
            ("성장률",    lambda k: f"{k['recent_growth_rate']:+.1f}%",     TC_NUM,  22),
            ("얼리무버",  lambda k: f"{k['early_mover_score']:.1f}",        TC_NUM,  22),
            ("3개월평균", lambda k: f"{k['avg_ratio_3mo']:.1f}",            TC_NUM,  22),
        ]  # 10+42+22+22+22+22 = 140mm

    t = _keyword_table(
        early_kws, columns,
        header_bg=C_ACCENT,
        alt_row_color=colors.HexColor("#FFEBEE"),
        grid_color="#FFCDD2",
    )
    story.append(t)
    story.append(Spacer(1, 5*mm))
    return story


def _growing_section(growing_kws: list[dict]) -> list:
    """성장 중인 키워드 테이블."""
    from keyword_volume import format_volume

    if not growing_kws:
        return []

    story = [_p("성장 중인 키워드", S_H1)]
    story.append(_p("이미 성장 궤도에 올라섰으나 아직 경쟁이 과열되지 않은 키워드", S_SMALL))
    story.append(Spacer(1, 2*mm))

    kws = growing_kws[:25]
    has_volume = any(kw.get("monthly_total_search", 0) > 0 for kw in kws)

    if has_volume:
        columns = [
            ("순위",        lambda k: str(kws.index(k)+1),                            TC_RANK,  8),
            ("키워드",      lambda k: k["keyword"],                                    TC_KW,   32),
            ("기회점수",    lambda k: f"{k['opportunity_score']:.1f}",                 TC_NUM,  17),
            ("성장률",      lambda k: f"{k['recent_growth_rate']:+.1f}%",              TC_NUM,  17),
            ("얼리무버",    lambda k: f"{k['early_mover_score']:.1f}",                 TC_NUM,  17),
            ("일관성",      lambda k: f"{k['consistency_score']:.1f}",                 TC_NUM,  17),
            ("PC검색량",    lambda k: format_volume(k.get("monthly_pc_search", 0)),    TC_NUM,  26),
            ("모바일검색량",lambda k: format_volume(k.get("monthly_mobile_search", 0)),TC_NUM,  26),
        ]  # 8+32+17+17+17+17+26+26 = 160mm
    else:
        columns = [
            ("순위",     lambda k: str(kws.index(k)+1),                    TC_RANK, 10),
            ("키워드",   lambda k: k["keyword"],                            TC_KW,   42),
            ("기회점수", lambda k: f"{k['opportunity_score']:.1f}",         TC_NUM,  22),
            ("성장률",   lambda k: f"{k['recent_growth_rate']:+.1f}%",      TC_NUM,  22),
            ("얼리무버", lambda k: f"{k['early_mover_score']:.1f}",         TC_NUM,  22),
            ("일관성",   lambda k: f"{k['consistency_score']:.1f}",         TC_NUM,  22),
        ]  # 10+42+22+22+22+22 = 140mm

    t = _keyword_table(
        kws, columns,
        header_bg=C_ORANGE,
        alt_row_color=colors.HexColor("#FFF3E0"),
        grid_color="#FFE0B2",
    )
    story.append(t)
    story.append(Spacer(1, 5*mm))
    return story


def _full_ranking_section(analyzed: list[dict]) -> list:
    """전체 기회점수 랭킹."""
    from keyword_volume import format_volume

    kws = analyzed[:50]
    story = [_p(f"전체 키워드 기회점수 랭킹 (총 {len(analyzed)}개)", S_H1)]
    story.append(Spacer(1, 2*mm))

    has_volume = any(kw.get("monthly_total_search", 0) > 0 for kw in kws)

    if has_volume:
        # PC·모바일 둘 다 표시. 총 180mm
        columns = [
            ("순위",        lambda k: str(kws.index(k)+1),                            TC_RANK,  7),
            ("키워드",      lambda k: k["keyword"],                                    TC_KW,   28),
            ("기회점수",    lambda k: f"{k['opportunity_score']:.1f}",                 TC_NUM,  16),
            ("성장률",      lambda k: f"{k['recent_growth_rate']:+.1f}%",              TC_NUM,  16),
            ("단계",        lambda k: _phase_label(k["trend_phase"]),                  TC_PHASE,20),
            ("얼리무버",    lambda k: f"{k['early_mover_score']:.1f}",                 TC_NUM,  15),
            ("일관성",      lambda k: f"{k['consistency_score']:.1f}",                 TC_NUM,  14),
            ("PC검색량",    lambda k: format_volume(k.get("monthly_pc_search", 0)),    TC_NUM,  25),
            ("모바일검색량",lambda k: format_volume(k.get("monthly_mobile_search", 0)),TC_NUM,  25),
        ]  # 7+28+16+16+20+15+14+25+25 = 166mm
    else:
        columns = [
            ("순위",     lambda k: str(kws.index(k)+1),                    TC_RANK,  9),
            ("키워드",   lambda k: k["keyword"],                            TC_KW,   38),
            ("기회점수", lambda k: f"{k['opportunity_score']:.1f}",         TC_NUM,  20),
            ("성장률",   lambda k: f"{k['recent_growth_rate']:+.1f}%",      TC_NUM,  21),
            ("단계",     lambda k: _phase_label(k["trend_phase"]),          TC_PHASE,27),
            ("얼리무버", lambda k: f"{k['early_mover_score']:.1f}",         TC_NUM,  20),
            ("일관성",   lambda k: f"{k['consistency_score']:.1f}",         TC_NUM,  20),
        ]  # 9+38+20+21+27+20+20 = 155mm

    t = _keyword_table(
        kws, columns,
        header_bg=C_PRIMARY,
        alt_row_color=C_LIGHTBLUE,
        grid_color="#BBDEFB",
        phase_col_idx=4,
    )
    story.append(t)
    story.append(Spacer(1, 5*mm))
    return story


def _md_to_rl(text: str) -> str:
    """
    Convert inline markdown to ReportLab XML markup.
    Handles: **bold**, *italic*, `code`, and XML-unsafe chars.
    """
    import re
    # Escape XML special chars first (except we'll re-add tags below)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # **bold**
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    # *italic* (single, not double)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    # `code` — use same font to avoid Korean glyph fallback
    text = re.sub(r"`(.+?)`", r"<b>\1</b>", text)
    return text


def _parse_md_table(lines: list[str]) -> Table | None:
    """Parse a block of markdown table lines into a ReportLab Table."""
    rows = []
    for line in lines:
        if re.match(r"^\|[-:| ]+\|$", line.strip()):
            continue  # separator row
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)

    if len(rows) < 1:
        return None

    n_cols = max(len(r) for r in rows)
    # Pad rows to equal length
    rows = [r + [""] * (n_cols - len(r)) for r in rows]

    # Convert cells to Paragraphs
    p_rows = []
    for ri, row in enumerate(rows):
        p_row = []
        for cell in row:
            style = S_SMALL
            cell_text = _md_to_rl(cell)
            p_row.append(Paragraph(cell_text or " ", style))
        p_rows.append(p_row)

    page_width = 180 * mm
    col_w = [page_width / n_cols] * n_cols

    t = Table(p_rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0),  C_LIGHTBLUE),
        ("FONTNAME",       (0, 0), (-1, 0),  FONT_BOLD),
        ("FONTSIZE",       (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, colors.HexColor("#F5F5F5")]),
        ("GRID",           (0, 0), (-1, -1), 0.3, C_GRAY),
        ("TOPPADDING",     (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 3),
        ("LEFTPADDING",    (0, 0), (-1, -1), 4),
    ]))
    return t


S_QUOTE = _style("quote", fontSize=9, leading=13,
                 textColor=colors.HexColor("#5D4037"),
                 leftIndent=8*mm, borderPadding=(3, 0, 3, 6))
S_CODE  = _style("code",  fontName=FONT_NORMAL, fontSize=8, leading=12,
                 leftIndent=6*mm, textColor=colors.HexColor("#37474F"),
                 backColor=colors.HexColor("#F5F5F5"))


def _glossary_section() -> list:
    """용어 정의 섹션."""
    story = [
        _p("📖 주요 용어 정의", S_H1),
        HRFlowable(width="100%", thickness=0.5, color=C_PRIMARY),
        Spacer(1, 3*mm),
    ]

    terms = [
        (
            "기회점수 (Opportunity Score, 0~100점)",
            "시장 진입 기회의 종합 점수. 얼리무버점수(40%) + 성장률(25%) + 일관성점수(15%) + "
            "성장여력(10%) + 장기트렌드 보정(10%)을 가중 합산합니다. "
            "점수가 높을수록 지금이 진입 적기이며 선점 가능성이 높습니다."
        ),
        (
            "성장률 (Growth Rate, %)",
            "최근 3개월 평균 검색량 vs 직전 3개월 평균 검색량의 변화율(%)입니다. "
            "예) +50%면 최근 3개월이 직전 3개월보다 검색량이 50% 더 많음을 의미합니다. "
            "주간 데이터(3개월 주별)를 기준으로 계산하며, 데이터 부족 시 1년 주별 데이터의 후반/전반을 비교합니다."
        ),
        (
            "얼리무버점수 (Early Mover Score, 0~100점)",
            "경쟁자가 아직 진입하기 전 선점 가능성을 나타내는 점수입니다. "
            "① 최근 성장률이 높고 ② 과거(1~2년 전) 검색량이 낮은 저점에서 출발했으며 "
            "③ 역대 최고점 대비 현재 검색량이 낮아 성장 여력이 있을수록 높게 산출됩니다."
        ),
        (
            "일관성점수 (Consistency Score, 0~100점)",
            "장기간(2016년~현재)에 걸쳐 검색량이 얼마나 꾸준히 우상향했는지를 나타냅니다. "
            "점수가 높을수록 일시적 유행이 아닌 지속 성장 트렌드이며 안정적 수요 기반을 의미합니다."
        ),
        (
            "3개월 평균 검색지수",
            "최근 3개월간의 평균 검색량 지수 (네이버 기준 0~100 상대 지수). "
            "100이 해당 기간 최고 검색량 기준이며, 키워드 간 시장 규모를 간접 비교할 때 활용합니다."
        ),
        (
            "월간검색량 (Monthly Search Volume)",
            "네이버 검색광고 키워드도구 기준 해당 키워드의 최근 월간 PC + 모바일 검색 횟수. "
            "트렌드 지수(0~100 상대값)와 달리 실제 절대 검색량이므로 시장 규모를 직접 파악할 수 있습니다. "
            "광고 API 키가 .env에 설정된 경우에만 표시됩니다. PC와 모바일 검색량을 각각 표시합니다."
        ),
        (
            "트렌드 단계 (Trend Phase)",
            "early_rising(초기 급성장): 얼리무버 기회, 경쟁 진입 전 단계  |  "
            "growing(성장 지속): 검색량 꾸준히 증가 중  |  "
            "peak(정점 도달): 최고점 근접, 이미 경쟁 치열  |  "
            "stable(안정적 유지): 큰 변동 없이 유지  |  "
            "declining(하락 중): 검색량 감소 추세"
        ),
    ]

    s_term = _style("gloss_term", fontName=FONT_BOLD, fontSize=8, leading=12)
    s_desc = _style("gloss_desc", fontName=FONT_NORMAL, fontSize=8, leading=12)
    s_hdr  = _style("gloss_hdr",  fontName=FONT_BOLD, fontSize=9, leading=13,
                    textColor=C_WHITE, alignment=1)

    rows = [[Paragraph("용어", s_hdr), Paragraph("설명", s_hdr)]]
    for term, desc in terms:
        rows.append([Paragraph(term, s_term), Paragraph(desc, s_desc)])

    col_w = [52*mm, 128*mm]
    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  C_PRIMARY),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, C_LIGHTBLUE]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#BBDEFB")),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 6*mm))
    return story


def _briefing_section(briefing: str) -> list:
    """AI 브리핑 텍스트 섹션 — 마크다운을 ReportLab으로 변환."""
    import re

    story = [PageBreak(), _p("AI 시장 분석 브리핑", S_H1),
             HRFlowable(width="100%", thickness=1, color=C_PRIMARY),
             Spacer(1, 3*mm)]

    lines = briefing.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # ── Empty line ──────────────────────────────────────────────────
        if not stripped:
            story.append(Spacer(1, 2*mm))
            i += 1
            continue

        # ── Headings ────────────────────────────────────────────────────
        if stripped.startswith("# "):
            story.append(_p(_md_to_rl(stripped[2:]), S_H1))
            i += 1
            continue
        if stripped.startswith("## "):
            story.append(_p(_md_to_rl(stripped[3:]), S_H2))
            i += 1
            continue
        if stripped.startswith("### ") or stripped.startswith("#### "):
            text = stripped.lstrip("#").strip()
            story.append(_p(f"<b>{_md_to_rl(text)}</b>", S_BODY))
            i += 1
            continue

        # ── Horizontal rule ─────────────────────────────────────────────
        if re.match(r"^-{3,}$", stripped):
            story.append(HRFlowable(width="100%", thickness=0.5,
                                     color=C_GRAY, spaceAfter=3))
            i += 1
            continue

        # ── Code block (``` ... ```) ─────────────────────────────────────
        if stripped.startswith("```"):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            for cl in code_lines:
                story.append(_p(cl.replace(" ", "&nbsp;") or "&nbsp;", S_CODE))
            story.append(Spacer(1, 2*mm))
            continue

        # ── Markdown table block ─────────────────────────────────────────
        if stripped.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            tbl = _parse_md_table(table_lines)
            if tbl:
                story.append(tbl)
                story.append(Spacer(1, 2*mm))
            continue

        # ── Blockquote (> ...) ──────────────────────────────────────────
        if stripped.startswith("> "):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip(">").strip())
                i += 1
            quote_text = " ".join(quote_lines)
            story.append(_p(_md_to_rl(quote_text), S_QUOTE))
            story.append(Spacer(1, 1*mm))
            continue

        # ── Bullet list (- item) ─────────────────────────────────────────
        if re.match(r"^[-*]\s+", stripped):
            text = re.sub(r"^[-*]\s+", "", stripped)
            bullet_style = _style("bullet", fontSize=9, leading=13, leftIndent=6*mm,
                                   bulletIndent=3*mm)
            story.append(Paragraph(f"• {_md_to_rl(text)}", bullet_style))
            i += 1
            continue

        # ── Numbered list (1. item) ──────────────────────────────────────
        if re.match(r"^\d+\.\s+", stripped):
            num, text = re.match(r"^(\d+)\.\s+(.+)$", stripped).groups()
            num_style = _style("num", fontSize=9, leading=13, leftIndent=6*mm)
            story.append(Paragraph(f"{num}. {_md_to_rl(text)}", num_style))
            i += 1
            continue

        # ── Normal paragraph ─────────────────────────────────────────────
        story.append(_p(_md_to_rl(stripped), S_BODY))
        i += 1

    story.append(Spacer(1, 4*mm))
    return story


def _make_mention_table(ranked: list, header_color, light_color, grid_color,
                         label_color, name_col_header: str) -> "Table":
    """TV/홈쇼핑 공통 언급 성분 테이블."""
    TC_HDR = ParagraphStyle(f"tc_hdr_{id(header_color)}", fontName=FONT_BOLD, fontSize=8,
                             leading=11, textColor=C_WHITE, alignment=1,
                             spaceAfter=0, spaceBefore=0)
    TC_ING = ParagraphStyle(f"tc_ing_{id(label_color)}", fontName=FONT_BOLD, fontSize=8,
                             leading=11, textColor=label_color, alignment=0,
                             spaceAfter=0, spaceBefore=0, wordWrap="CJK")
    TC_TXT = ParagraphStyle(f"tc_txt_{id(header_color)}", fontName=FONT_NORMAL, fontSize=7.5,
                             leading=10, textColor=C_BLACK, alignment=0,
                             spaceAfter=0, spaceBefore=0, wordWrap="CJK")
    TC_NUM = ParagraphStyle(f"tc_num_{id(label_color)}", fontName=FONT_BOLD, fontSize=8,
                             leading=11, textColor=label_color, alignment=1,
                             spaceAfter=0, spaceBefore=0)

    header_row = [
        Paragraph("순위", TC_HDR),
        Paragraph("성분명", TC_HDR),
        Paragraph("언급", TC_HDR),
        Paragraph(name_col_header, TC_HDR),
        Paragraph("채널", TC_HDR),
    ]
    rows = [header_row]
    for i, (ing, mentions) in enumerate(ranked):
        names = list({m["program"] for m in mentions})
        channels = list({m["channel"] for m in mentions})
        rows.append([
            Paragraph(str(i + 1), TC_NUM),
            Paragraph(ing, TC_ING),
            Paragraph(str(len(mentions)), TC_NUM),
            Paragraph(", ".join(names[:4]), TC_TXT),
            Paragraph(", ".join(channels[:3]), TC_TXT),
        ])

    col_w = [12*mm, 35*mm, 14*mm, 75*mm, 44*mm]
    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0),  header_color),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [C_WHITE, light_color]),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor(grid_color)),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
    ]))
    return t


def _rank_agg(aggregated: dict, top_n: int = 10) -> list:
    ranked = sorted(
        [(ing, m) for ing, m in aggregated.items() if len(m) >= 5],
        key=lambda x: len(x[1]), reverse=True
    )[:top_n]
    if not ranked:
        ranked = sorted(aggregated.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    return ranked


def _tv_section(tv_data: dict) -> list:
    """📺 TV 방송 + 홈쇼핑 언급 성분 순위 섹션."""
    if not tv_data:
        return []

    all_agg = tv_data.get("aggregated_ingredients", {})
    if not all_agg:
        return []

    ranked = _rank_agg(all_agg)
    if not ranked:
        return []

    C_TV = colors.HexColor("#4A148C")
    C_TV_LIGHT = colors.HexColor("#F3E5F5")

    generated_at = tv_data.get("generated_at", "")
    days = tv_data.get("days", 7)
    collected = tv_data.get("collected_sources", 0)
    note = f"최근 {days}일 기준 | 수집 소스 {collected}개"
    if generated_at:
        try:
            dt = datetime.fromisoformat(generated_at)
            note += f" | 수집일: {dt.strftime('%Y-%m-%d %H:%M')}"
        except Exception:
            pass

    story = [
        PageBreak(),
        _p("📺 TV/홈쇼핑 건강 성분 언급 순위", S_H1),
        HRFlowable(width="100%", thickness=1, color=C_TV),
        Spacer(1, 2*mm),
        _p(note, S_SMALL),
        _p("※ 네이버 뉴스·블로그 기반 수집 / 5회 이상 언급된 성분만 표시 / 참고용", S_WARNING),
        Spacer(1, 3*mm),
        _make_mention_table(ranked, C_TV, C_TV_LIGHT, "#CE93D8", C_TV, "출처 (TV/홈쇼핑)"),
        Spacer(1, 5*mm),
    ]
    return story


def _failures_section(api_failures: dict) -> list:
    """⚠️ API 누락 키워드 섹션."""
    if not api_failures:
        return []

    story = [
        HRFlowable(width="100%", thickness=1, color=C_ACCENT),
        Spacer(1, 2*mm),
        _p(f"⚠️ API 오류로 데이터 누락된 키워드 ({len(api_failures)}개)", S_H1),
        _p(
            "아래 키워드는 API 할당량 초과 또는 네트워크 오류로 트렌드 데이터를 수집하지 못했습니다. "
            "--no-scrape 옵션으로 재실행하면 이 키워드만 다시 수집됩니다.",
            S_WARNING,
        ),
        Spacer(1, 2*mm),
    ]

    header = ["키워드", "누락된 기간"]
    rows = [header] + [
        [kw, ", ".join(periods)]
        for kw, periods in sorted(api_failures.items())
    ]

    col_w = [50*mm, 130*mm]
    t = Table(rows, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0), colors.HexColor("#BF360C")),
        ("TEXTCOLOR",   (0, 0), (-1, 0), C_WHITE),
        ("FONTNAME",    (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ALIGN",       (0, 0), (-1, -1), "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [C_WHITE, C_LIGHTYELLOW]),
        ("GRID",        (0, 0), (-1, -1), 0.3, colors.HexColor("#FFCCBC")),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 4*mm))
    return story


def _charts_section(chart_files: list[str]) -> list:
    """차트 이미지 삽입."""
    if not chart_files:
        return []

    from reportlab.platypus import Image

    story = [PageBreak(), _p("트렌드 차트", S_H1),
             HRFlowable(width="100%", thickness=1, color=C_PRIMARY),
             Spacer(1, 3*mm)]

    chart_titles = {
        "chart_opportunity_scores": "기회점수 Top 15",
        "chart_longterm_trends":    "장기 트렌드 (2016~현재)",
        "chart_positioning_map":    "포지셔닝 맵: 성장률 vs 얼리무버점수",
    }

    for path in chart_files:
        p = Path(path)
        if not p.exists():
            continue
        title = chart_titles.get(p.stem, p.stem)
        story.append(_p(title, S_H2))
        img = Image(str(p), width=170*mm, height=90*mm, kind="proportional")
        story.append(img)
        story.append(Spacer(1, 5*mm))

    return story


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_pdf(
    output_path: Path,
    analyzed_keywords: list[dict],
    briefing: str,
    scrape_results: dict | None = None,
    trend_data: dict | None = None,
    chart_files: list[str] | None = None,
    tv_data: dict | None = None,
) -> Path:
    """
    Build and save a PDF report.

    Args:
        output_path: Full path for the output .pdf file
        analyzed_keywords: Sorted list of analyzed keyword dicts
        briefing: AI-generated briefing text
        scrape_results: Raw scraping results (optional)
        trend_data: Full trend data dict (for _api_failures, optional)
        chart_files: List of chart image paths (optional)

    Returns:
        Path to the generated PDF file
    """
    output_path = Path(output_path).with_suffix(".pdf")
    today = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        leftMargin=15*mm,
        rightMargin=15*mm,
        topMargin=15*mm,
        bottomMargin=15*mm,
        title="건강식품 트렌드 분석 리포트",
        author="Naver Health Trend Analyzer",
    )

    story = []

    # Cover banner
    story += _cover_table(today, len(analyzed_keywords))

    # Scrape summary
    if scrape_results:
        lines = []
        for period in ["1년", "3개월", "1개월"]:
            kw_list = scrape_results.get(period, [])
            if kw_list:
                top5 = ", ".join(k["keyword"] for k in kw_list[:5])
                lines.append(f"<b>{period}</b> TOP5: {top5}")
        if lines:
            story.append(_p("수집된 키워드 현황", S_H2))
            for line in lines:
                story.append(_p(line, S_SMALL))
            story.append(Spacer(1, 3*mm))

    # Phase summary bar
    phase_counts: dict[str, int] = {}
    for kw in analyzed_keywords:
        phase_counts[kw["trend_phase"]] = phase_counts.get(kw["trend_phase"], 0) + 1

    summary_parts = [
        f"얼리라이징 <b>{phase_counts.get('early_rising', 0)}</b>개  |  "
        f"성장중 <b>{phase_counts.get('growing', 0)}</b>개  |  "
        f"안정 <b>{phase_counts.get('stable', 0)}</b>개  |  "
        f"최고점 <b>{phase_counts.get('peak', 0)}</b>개  |  "
        f"하락 <b>{phase_counts.get('declining', 0)}</b>개"
    ]
    story.append(_p(summary_parts[0], S_BODY))
    story.append(Spacer(1, 4*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY))
    story.append(Spacer(1, 4*mm))

    # Glossary (term definitions)
    story += _glossary_section()
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_GRAY))
    story.append(Spacer(1, 4*mm))

    # Key sections
    early_rising_kws = [k for k in analyzed_keywords if k["trend_phase"] == "early_rising"]
    growing_kws      = [k for k in analyzed_keywords if k["trend_phase"] == "growing"]

    story += _early_rising_section(early_rising_kws)
    story += _growing_section(growing_kws)
    story += _full_ranking_section(analyzed_keywords)

    # AI briefing
    story += _briefing_section(briefing)

    # Charts
    story += _charts_section(chart_files or [])

    # TV monitoring section
    story += _tv_section(tv_data or {})

    # API failures
    api_failures = (trend_data or {}).get("_api_failures", {})
    story += _failures_section(api_failures)

    doc.build(story)
    print(f"[PDF] 저장 완료: {output_path}")
    return output_path
