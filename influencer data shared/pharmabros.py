"""
파마브로스 판매현황 파일공유 모듈
─────────────────────────────────────────
- 네이버 API로 개별 주문 데이터 수집
- 엑셀(.xlsx) 파일 생성
- 구글 드라이브 공유 폴더에 업로드

실행 스케줄 (한국시간 기준):
  시작일        : 14시, 16시  → 당일 데이터
  D+1 ~ 종료일  : 매일 10시   → 시작일~당일 누적 데이터
  종료일 다음날  : 10시        → 시작일~종료일 최종 전체 데이터
"""

import os
import io
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from google.cloud import storage as gcs_storage
from google.oauth2 import service_account

# ── 한국시간 ──────────────────────────────────────────────
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


# ── 실행 여부 판단 ────────────────────────────────────────
def should_run(start_date_str: str, end_date_str: str) -> tuple[bool, bool]:
    """
    현재 KST 시각 기준으로 파마브로스 업로드를 실행해야 하는지 판단.

    반환: (should_run: bool, is_final: bool)
      is_final=True  → 종료일 다음날 최종 업로드
      is_final=False → 중간 업로드
    """
    from datetime import date as _date
    now   = now_kst()
    today = now.date()
    hour  = now.hour

    start = _parse_date(start_date_str)
    end   = _parse_date(end_date_str)

    # 종료일 다음날 10시: 최종 업로드
    if today == end + timedelta(days=1) and hour == 10:
        return True, True

    # 시작일 14시 또는 16시
    if today == start and hour in (14, 16):
        return True, False

    # D+1 이후 종료일까지 매일 10시
    if start < today <= end and hour == 10:
        return True, False

    return False, False


def _parse_date(s: str):
    from datetime import date
    parts = re.split(r"[.\-/]", s.strip())
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


# ── 엑셀 파일 생성 ────────────────────────────────────────
def _col_width(ws, col: int, width: float):
    ws.column_dimensions[get_column_letter(col)].width = width


def create_excel(
    orders: list,
    title: str,
    date_from: str,
    date_to: str,
    is_final: bool,
) -> bytes:
    """
    주문 데이터를 받아 엑셀 바이트를 반환합니다.

    orders: [{"주문번호", "주문일시", "주문상태", "옵션", "주문수량"}, ...]
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "판매현황"

    # ── 색상 정의 ─────────────────────────────────────────
    HEADER_FILL  = PatternFill("solid", fgColor="1A2744")   # 진남색
    TITLE_FILL   = PatternFill("solid", fgColor="2D3F6B")   # 중간남색
    TOTAL_FILL   = PatternFill("solid", fgColor="EEF1FA")   # 연보라
    WHITE_FONT   = Font(name="맑은 고딕", color="FFFFFF", bold=True, size=11)
    BODY_FONT    = Font(name="맑은 고딕", size=10)
    BOLD_FONT    = Font(name="맑은 고딕", bold=True, size=10)
    TOTAL_FONT   = Font(name="맑은 고딕", bold=True, size=10, color="1A2744")
    CENTER       = Alignment(horizontal="center", vertical="center")
    LEFT         = Alignment(horizontal="left",   vertical="center")
    thin         = Side(style="thin", color="CCCCCC")
    BORDER       = Border(left=thin, right=thin, top=thin, bottom=thin)

    # ── 1행: 제목 ─────────────────────────────────────────
    label = f"{'[최종]' if is_final else '[중간]'} {title} 판매현황   ({date_from} ~ {date_to})"
    ws.merge_cells("A1:E1")
    cell = ws["A1"]
    cell.value = label
    cell.font  = Font(name="맑은 고딕", color="FFFFFF", bold=True, size=13)
    cell.fill  = TITLE_FILL
    cell.alignment = CENTER
    ws.row_dimensions[1].height = 32

    # ── 2행: 발행 시각 ────────────────────────────────────
    ws.merge_cells("A2:E2")
    ts_cell = ws["A2"]
    ts_cell.value     = f"생성: {now_kst().strftime('%Y-%m-%d %H:%M')} KST    총 {len(orders)}건"
    ts_cell.font      = Font(name="맑은 고딕", size=9, color="888888")
    ts_cell.alignment = RIGHT = Alignment(horizontal="right", vertical="center")
    ws.row_dimensions[2].height = 18

    # ── 3행: 헤더 ─────────────────────────────────────────
    headers = ["주문번호", "주문일시", "주문상태", "옵션", "주문수량"]
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=3, column=col, value=h)
        c.font      = WHITE_FONT
        c.fill      = HEADER_FILL
        c.alignment = CENTER
        c.border    = BORDER
    ws.row_dimensions[3].height = 22

    # ── 데이터 행 ─────────────────────────────────────────
    EVEN_FILL = PatternFill("solid", fgColor="F8F9FB")
    for idx, order in enumerate(orders, 1):
        row = idx + 3
        row_fill = EVEN_FILL if idx % 2 == 0 else None
        values = [
            order.get("주문번호", ""),
            order.get("주문일시", ""),
            order.get("주문상태", ""),
            order.get("옵션", ""),
            order.get("주문수량", 0),
        ]
        aligns = [CENTER, CENTER, CENTER, LEFT, CENTER]
        for col, (val, aln) in enumerate(zip(values, aligns), 1):
            c = ws.cell(row=row, column=col, value=val)
            c.font      = BODY_FONT
            c.alignment = aln
            c.border    = BORDER
            if row_fill:
                c.fill = row_fill
        ws.row_dimensions[row].height = 18

    # ── 합계 행 ───────────────────────────────────────────
    total_row = len(orders) + 4
    total_qty = sum(o.get("주문수량", 0) for o in orders)
    ws.merge_cells(f"A{total_row}:D{total_row}")
    tc1 = ws[f"A{total_row}"]
    tc1.value     = "총 주문수량"
    tc1.font      = TOTAL_FONT
    tc1.fill      = TOTAL_FILL
    tc1.alignment = CENTER
    tc1.border    = BORDER
    tc2 = ws.cell(row=total_row, column=5, value=total_qty)
    tc2.font      = TOTAL_FONT
    tc2.fill      = TOTAL_FILL
    tc2.alignment = CENTER
    tc2.border    = BORDER
    ws.row_dimensions[total_row].height = 22

    # ── 열 너비 ───────────────────────────────────────────
    _col_width(ws, 1, 22)   # 주문번호
    _col_width(ws, 2, 20)   # 주문일시
    _col_width(ws, 3, 14)   # 주문상태
    _col_width(ws, 4, 36)   # 옵션
    _col_width(ws, 5, 10)   # 주문수량

    # ── 틀 고정 (3행 이후) ────────────────────────────────
    ws.freeze_panes = "A4"

    # 바이트로 반환
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Google Cloud Storage 업로드 ───────────────────────────
def upload_to_gcs(
    credentials_path: str,
    bucket_name: str,
    file_bytes: bytes,
    file_name: str,
) -> str:
    """
    GCS 버킷에 엑셀 파일 업로드 후 공개 다운로드 URL 반환.
    같은 이름 파일은 자동 덮어쓰기.
    """
    creds = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = gcs_storage.Client(credentials=creds)
    bucket = client.bucket(bucket_name)
    blob   = bucket.blob(file_name)

    blob.upload_from_string(
        file_bytes,
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )
    blob.make_public()

    print(f"  [GCS] 업로드 완료: {file_name}")
    return blob.public_url


# ── 파일명 생성 ───────────────────────────────────────────
def make_filenames(is_final: bool, date_from: str, date_to: str) -> list[str]:
    """
    업로드할 파일명 목록 반환 (항상 1개).

    정기 업로드: 파마브로스_판매현황_20260514~20260520_1400.xlsx
    최종 업로드: 파마브로스_판매현황_최종_20260514~20260520.xlsx
    """
    d_from = date_from.replace("-", "")
    d_to   = date_to.replace("-", "")

    if is_final:
        return [f"파마브로스_판매현황_최종_{d_from}~{d_to}.xlsx"]
    else:
        hhmm = now_kst().strftime("%H%M")
        return [f"파마브로스_판매현황_{d_from}~{d_to}_{hhmm}.xlsx"]


# ── 메인 실행 함수 ────────────────────────────────────────
def run_pharmabros(campaign: dict, credentials_path: str, bucket_name: str):
    """
    파마브로스 판매현황 GCS 업로드 실행.
    campaign 키: title, product_no, date_from, date_to, api_id, api_secret
    """
    import naver_api

    title     = campaign["title"]
    date_from = campaign["date_from"]
    date_to   = campaign["date_to"]
    is_final  = campaign.get("is_final", False)

    # 실제 조회 기간
    if is_final:
        query_to = date_to
    else:
        today_str = now_kst().strftime("%Y-%m-%d")
        query_to  = min(date_to, today_str)

    print(f"\n  [파마브로스] '{title}' 데이터 수집 시작")
    print(f"  {'[최종]' if is_final else '[중간]'} 기간: {date_from} ~ {query_to}")

    orders = naver_api.get_pharmabros_orders(
        client_id=campaign["api_id"],
        client_secret=campaign["api_secret"],
        product_no=campaign["product_no"],
        date_from=date_from,
        date_to=query_to,
    )

    # 엑셀 생성
    excel_bytes = create_excel(
        orders=orders,
        title=title,
        date_from=date_from,
        date_to=query_to,
        is_final=is_final,
    )

    # 파일명
    file_name = make_filenames(is_final, date_from, query_to)[0]

    # GCS 업로드
    file_url = upload_to_gcs(
        credentials_path=credentials_path,
        bucket_name=bucket_name,
        file_bytes=excel_bytes,
        file_name=file_name,
    )

    print(f"  [파마브로스] ✅ 업로드 완료: {file_name}")
    print(f"  [파마브로스] 다운로드 URL: {file_url}")
    return file_url, [(file_name, file_url)]
