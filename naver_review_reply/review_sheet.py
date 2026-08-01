"""구글시트를 리뷰 답글 검수 대기열로 사용. 스토어별로 별도 탭을 쓴다.
직원이 시트에서 AI초안을 보고 필요시 '최종답변' 칸을 직접 수정한 뒤 '승인' 칸에 표시하면,
poster.py가 승인되고 아직 게시되지 않은 행만 골라 실제로 게시한다.
"""
import gspread
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER = ["review_id", "스토어", "상품명", "별점", "고객리뷰", "생성방식", "AI초안", "최종답변", "승인", "게시완료", "게시일자"]


def _col_letter(n: int) -> str:
    """1-indexed 열 번호를 시트 열 문자로 변환(1->A, 11->K, 27->AA). HEADER 길이가
    바뀌어도 범위 문자열이 깨지지 않도록 하드코딩 대신 이 함수로 계산한다
    (2026-07-22 - HEADER에 열 추가했는데 archive_posted의 범위 끝(J)을 안 고쳐서
    400 오류 났던 사고 이후 재발 방지용)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters

# '승인' 칸에 체크박스(TRUE) 또는 O/승인/1 등 직접 입력 어느 쪽이든 승인으로 인정
# "gpt"는 GPT 검수가 자동승인한 표시(사람이 체크한 "ㅇ"/"승인" 등과 구분하기 위함,
# 2026-07-22 요청) - 완료탭으로 이관돼도 이 값 그대로 남아 GPT/사람 승인 여부가 계속 구분됨.
_TRUE_VALUES = {"true", "o", "1", "승인", "y", "yes", "ㅇ", "완료", "gpt"}

_SPREADSHEET_CACHE = None
_WORKSHEET_CACHE: dict[str, "gspread.Worksheet"] = {}
_NEXT_ROW_CACHE: dict[str, int] = {}

_DONE_SHEET_NAME = "리뷰답글_완료"
_DONE_WORKSHEET_CACHE = None
_DONE_NEXT_ROW_CACHE: int | None = None


def _sheet_name(store: str) -> str:
    return f"리뷰답글_검수_{store}"


def _spreadsheet():
    """인증+스프레드시트 조회 결과를 프로세스 내에서 재사용한다 (매번 재인증하면
    Google Sheets API 호출량이 급증해 429(rate limit) 실패가 나기 쉬움)."""
    global _SPREADSHEET_CACHE
    if _SPREADSHEET_CACHE is None:
        creds = Credentials.from_service_account_file(config.CREDENTIALS_PATH, scopes=SCOPES)
        gc = gspread.authorize(creds)
        _SPREADSHEET_CACHE = gc.open_by_key(config.REVIEW_REPLY_SHEET_ID)
    return _SPREADSHEET_CACHE


def _get_worksheet(store: str):
    if store in _WORKSHEET_CACHE:
        return _WORKSHEET_CACHE[store]

    ss = _spreadsheet()
    name = _sheet_name(store)
    try:
        ws = ss.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=name, rows=500, cols=len(HEADER))
        ws.append_row(HEADER)

    _WORKSHEET_CACHE[store] = ws
    return ws


def sheet_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{config.REVIEW_REPLY_SHEET_ID}/edit"


def push_draft(review: dict, method: str = "AI"):
    """새로 생성된 초안을 해당 스토어 탭에 한 행 추가. method: '템플릿' 또는 'AI'.
    주의(2026-07-20 데이터 유실 사고 후 수정): gspread의 append_row()는 숨김 처리된 행
    (mark_posted가 완료된 행을 숨기므로 이 시트엔 항상 존재)이 있으면 "다음 빈 행"을
    잘못 계산해서 기존 행을 덮어쓰는 사고가 있었다. 그래서 append_row 대신 다음 행 번호를
    프로세스 내에서 직접 추적해서(최초 1번만 읽어옴) 그 위치에 명시적으로 쓴다 - 매번
    get_all_values()를 부르면 대량 등록 시 읽기 쿼터(429)에 금방 걸린다."""
    store = review["store"]
    ws = _get_worksheet(store)
    if store not in _NEXT_ROW_CACHE:
        _NEXT_ROW_CACHE[store] = len(ws.get_all_values()) + 1
    next_row = _NEXT_ROW_CACHE[store]
    last_col = _col_letter(len(HEADER))
    # archive_posted()가 게시완료된 행을 deleteDimension으로 지우면 그 탭의 "그리드 행수"
    # 자체가 같이 줄어든다. 그래서 백로그를 계속 소진해 나가다 보면 탭이 처음 만들 때의
    # 500행에서 헤더 1행까지 쪼그라들고, 그 뒤로는 초안을 한 건도 못 넣게 된다
    # (2026-08-01 JDHEALTH에서 실제 발생 - grid rows=1이라 A2:K2 쓰기가
    # "Range exceeds grid limits. Max rows: 1"로 전부 실패 → 초안 생성 연속 실패로
    # 회로차단기 발동). update()는 그리드를 자동으로 늘려주지 않으므로 쓰기 전에
    # 필요한 만큼 미리 확보한다(완료탭에서 이미 같은 방식으로 대응하고 있음).
    if next_row > ws.row_count:
        ws.add_rows(next_row - ws.row_count + 500)
    ws.update(f"A{next_row}:{last_col}{next_row}", [[
        review["review_id"], review["store"], review.get("product_name", ""),
        review.get("star", ""), review.get("content", ""), method,
        review["draft_text"], "", "", "",
        review.get("date", ""),
    ]])
    _NEXT_ROW_CACHE[store] = next_row + 1


def get_approved_unposted(stores: list[str] | None = None) -> list[dict]:
    """'승인' 표시되어 있고 아직 '게시완료'가 비어있는 행만, 스토어 탭 전체를 훑어서 반환."""
    stores = stores or config.STORES
    result = []
    for store in stores:
        ws = _get_worksheet(store)
        rows = ws.get_all_values()
        for idx, row in enumerate(rows[1:], start=2):
            if len(row) < 10:
                continue
            review_id, _store_col, product_name, _star, _content, _method, draft, final, approved, posted = row[:10]
            if posted.strip():
                continue
            if approved.strip().lower() not in _TRUE_VALUES:
                continue
            result.append({
                "row": idx,
                "store": store,
                "review_id": review_id,
                "product_name": product_name,
                "final_text": final.strip() or draft.strip(),
            })
    return result


def mark_needs_review(store: str, row: int):
    """GPT 검수에서 승인이 안 된(사람이 봐야 하는) 행을 주황색으로 표시한다
    (2026-07-22 요청 - 검수 대기열에서 한눈에 눈에 띄게 하기 위함)."""
    ws = _get_worksheet(store)
    try:
        ws.spreadsheet.batch_update({
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": ws.id,
                            "startRowIndex": row - 1,
                            "endRowIndex": row,
                            "startColumnIndex": 0,
                            "endColumnIndex": len(HEADER),
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.75, "blue": 0.3}}},
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            ]
        })
    except Exception:
        pass  # 표시 실패해도 치명적이지 않음(승인 자체는 그대로 비어있어 사람이 알아볼 수 있음)


def mark_posted(store: str, row: int, timestamp: str):
    """게시완료 시각을 기록한다.
    주의(2026-07-22): 예전엔 여기서 처리된 행을 회색+숨김 처리했었는데, 이후 추가된
    archive_posted()가 완료건을 아예 별도 탭으로 옮기고 원본 행을 삭제하므로 중간에
    숨김 처리할 필요가 없어졌다. 오히려 여러 번의 행 삭제(batch deleteDimension)가
    겹치면서 숨김 속성이 엉뚱한(승인도 안 된) 행에 잘못 옮겨붙는 사고가 있어서
    (사용자가 직접 발견/복구함) 숨김 처리 자체를 제거함 — 앞으로 이 탭의 행을
    다시 숨김 처리하지 말 것."""
    ws = _get_worksheet(store)
    ws.update_cell(row, HEADER.index("게시완료") + 1, timestamp)


def _get_done_worksheet():
    global _DONE_WORKSHEET_CACHE
    if _DONE_WORKSHEET_CACHE is not None:
        return _DONE_WORKSHEET_CACHE
    ss = _spreadsheet()
    try:
        ws = ss.worksheet(_DONE_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = ss.add_worksheet(title=_DONE_SHEET_NAME, rows=1000, cols=len(HEADER))
        ws.append_row(HEADER)
    _DONE_WORKSHEET_CACHE = ws
    return ws


def archive_posted(store: str):
    """검수 탭에서 '게시완료'가 채워진 행을 찾아 통합 '리뷰답글_완료' 탭으로 옮기고
    원래 탭에서는 삭제한다. 완료된 것들을 한 탭에 모아 나중에 몰아보기 쉽게 하기 위함
    (2026-07-21 요청). 스토어별 탭에는 처리 대기 중인 행만 남는다."""
    global _DONE_NEXT_ROW_CACHE
    ws = _get_worksheet(store)
    rows = ws.get_all_values()
    posted_idx = HEADER.index("게시완료")

    to_archive = []  # [(row_num, row_data)]
    for idx, row in enumerate(rows[1:], start=2):
        if len(row) > posted_idx and row[posted_idx].strip():
            padded = row[:len(HEADER)] + [""] * (len(HEADER) - len(row))
            to_archive.append((idx, padded))

    if not to_archive:
        return

    done_ws = _get_done_worksheet()
    if _DONE_NEXT_ROW_CACHE is None:
        _DONE_NEXT_ROW_CACHE = len(done_ws.get_all_values()) + 1

    # 행마다 update()를 따로 부르면(예전 방식) 백로그가 많을 때 시트 쓰기 API
    # 분당 한도(429)에 바로 걸린다 - 연속된 행 전체를 한 번의 범위 쓰기로 합친다
    # (2026-07-21 429 오류로 발견/수정).
    start_row = _DONE_NEXT_ROW_CACHE
    end_row = start_row + len(to_archive) - 1
    last_col = _col_letter(len(HEADER))
    values = [row_data for _, row_data in to_archive]
    # 완료탭이 계속 쌓이다 보니 시트 기본 행수(1000)를 넘어서는 경우가 생긴다
    # (2026-07-30 실사용 확인 - "Range exceeds grid limits" 오류로 게시는 성공했는데
    # 완료탭 이관만 실패했었음). update()는 그리드를 자동으로 늘려주지 않으므로
    # 필요하면 미리 행을 넉넉히 추가해둔다.
    if end_row > done_ws.row_count:
        done_ws.add_rows(end_row - done_ws.row_count + 500)
    done_ws.update(f"A{start_row}:{last_col}{end_row}", values)
    _DONE_NEXT_ROW_CACHE = end_row + 1

    # 완료탭은 항상 게시완료 시각 기준 최신순으로 보이게 정렬한다(2026-07-24 요청).
    # 정렬은 값 위치만 바꿀 뿐 총 행수는 그대로라 _DONE_NEXT_ROW_CACHE는 그대로 둬도 된다.
    sort_request = {
        "sortRange": {
            "range": {
                "sheetId": done_ws.id,
                "startRowIndex": 1,  # 헤더(0행) 제외
                "endRowIndex": end_row,
                "startColumnIndex": 0,
                "endColumnIndex": len(HEADER),
            },
            "sortSpecs": [{"dimensionIndex": HEADER.index("게시완료"), "sortOrder": "DESCENDING"}],
        }
    }

    # 아래(큰 행번호)부터 지워야 삭제 도중 앞서 계산해둔 행 번호가 밀리지 않는다.
    requests = [sort_request] + [
        {
            "deleteDimension": {
                "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": row_num - 1, "endIndex": row_num}
            }
        }
        for row_num, _ in sorted(to_archive, key=lambda x: x[0], reverse=True)
    ]
    ws.spreadsheet.batch_update({"requests": requests})
