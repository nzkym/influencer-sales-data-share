"""구글시트 읽기/쓰기 — 제품 매핑, 입고 탭 이력번호, 개인/쿠팡 탭 저장"""
import os
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

SHEET_ID   = os.getenv('TFOOD_SHEET_ID', '1EekZ89wq_Dk4AY844QwROMUcQN2yL1jY_6Ja1YAO_mU')
CREDS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

_client: gspread.Client | None = None


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds_path = os.path.join(os.path.dirname(__file__), '..', CREDS_PATH)
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        _client = gspread.authorize(creds)
        # 타임아웃 설정 (gspread 버전에 따라 속성명이 다름)
        try:
            _client.session.requests_session.timeout = 30
        except AttributeError:
            try:
                _client.auth.timeout = 30
            except Exception:
                pass
    return _client


def _get_sheet(tab_name: str) -> gspread.Worksheet:
    gc = _get_client()
    wb = gc.open_by_key(SHEET_ID)
    return wb.worksheet(tab_name)


# ── 제품 매핑 ─────────────────────────────────────────────────────────────

def read_product_mapping() -> list[dict]:
    """
    시트1에서 이지어드민 → 식약처 매핑 읽기.
    E열 불필요 — 이력번호는 입고 탭에서 별도 조회.

    반환: [{
        'code':       str,   # 이지어드민 상품코드 (A열)
        'ezadmin_name': str, # 이지어드민 상품명 (B열)
        'tfood_name': str,   # 식약처 등록명 (C열)
        'multiplier': int,   # 배수 (D열 비고: '3배수' 포함 시 3, 없으면 1)
    }]
    C열이 비어있으면 식약처 미등록 → 제외
    """
    sheet = _get_sheet('시트1')
    rows = sheet.get_all_values()
    products = []

    for row in rows[1:]:  # 헤더 제외
        if not row or not row[0]:
            continue
        code       = row[0].strip() if len(row) > 0 else ''
        ez_name    = row[1].strip() if len(row) > 1 else ''
        tfood_name = row[2].strip() if len(row) > 2 else ''
        remark     = row[3].strip() if len(row) > 3 else ''

        if not tfood_name:
            continue  # 식약처 미등록 제품 스킵

        multiplier   = 3 if '3배수' in remark else 1
        coupang_skuid = row[4].strip() if len(row) > 4 else ''
        coupang_name  = row[5].strip() if len(row) > 5 else ''

        products.append({
            'code':          code,
            'ezadmin_name':  ez_name,
            'tfood_name':    tfood_name,
            'multiplier':    multiplier,
            'coupang_skuid': coupang_skuid,
            'coupang_name':  coupang_name,
        })

    print(f"  [시트1] 매핑 {len(products)}건 로드")
    return products


_canonical_names_cache: list[str] | None = None


def _get_canonical_names() -> list[str]:
    """시트1 C열의 식약처 등록명(정식명) 목록 반환. 모듈 내 캐시 사용."""
    global _canonical_names_cache
    if _canonical_names_cache is None:
        try:
            rows = _get_sheet('시트1').get_all_values()
            _canonical_names_cache = [
                row[2].strip() for row in rows[1:]
                if len(row) > 2 and row[2].strip()
            ]
        except Exception:
            _canonical_names_cache = []
    return _canonical_names_cache


def _kr_bigram_similarity(a: str, b: str) -> float:
    """한글 문자만 사용한 bigram Jaccard 유사도. 0.0~1.0"""
    def kr_bigrams(s: str) -> set:
        kr = ''.join(c for c in s if '가' <= c <= '힣')
        return {kr[i:i+2] for i in range(len(kr) - 1)}
    ba, bb = kr_bigrams(a), kr_bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def _remap_to_canonical(result: dict) -> dict:
    """
    입고 탭 제품명이 시트1 정식명과 다를 때(리뉴얼·오타 등) 자동 감지해서 병합.
    한글 bigram 유사도 0.70 이상이면 같은 제품으로 판단.
    """
    canonical_names = _get_canonical_names()
    if not canonical_names:
        return result

    canonical_set = set(canonical_names)
    remapped: dict = {}

    for key, lots in result.items():
        if key in canonical_set:
            target = key
        else:
            best_sim, best_canon = 0.0, None
            for canon in canonical_names:
                sim = _kr_bigram_similarity(key, canon)
                if sim > best_sim:
                    best_sim, best_canon = sim, canon
            if best_sim >= 0.70 and best_canon:
                print(f'  [입고] 제품명 변경 감지: "{key}" → "{best_canon}" (유사도 {best_sim:.2f})')
                target = best_canon
            else:
                target = key

        for lot in lots:
            existing = next((x for x in remapped.get(target, []) if x['histrace_num'] == lot['histrace_num']), None)
            if existing:
                existing['qty'] += lot['qty']
            else:
                remapped.setdefault(target, []).append(lot)

    # 병합 후 각 제품 내 입고일 오름차순 재정렬
    for key in remapped:
        remapped[key].sort(key=lambda x: x['received_date'])

    return remapped


def read_lots_by_product() -> dict[str, list[dict]]:
    """
    입고 탭에서 제품별 이력추적관리번호 목록 읽기 (FIFO 순).
    입고 탭의 구 제품명은 시트1 정식명으로 자동 변환(리뉴얼 대응).

    반환: {
        '식약처 등록명(품목명)': [
            {'histrace_num': str, 'received_date': str, 'qty': int},
            ...  # 입고일 오름차순 (오래된 로트 먼저 = FIFO)
        ]
    }
    """
    try:
        sheet = _get_sheet('입고 및 재고')
    except Exception:
        sheet = _get_sheet('입고')
    all_rows = sheet.get_all_values()
    if len(all_rows) < 2:
        return {}

    # 헤더에서 컬럼 인덱스 찾기
    header = [h.strip() for h in all_rows[0]]
    def col(name_kw):
        for i, h in enumerate(header):
            if name_kw in h:
                return i
        return -1

    hist_col  = col('이력추적관리번호')
    name_col  = col('품목명')
    date_col  = col('입고일')
    qty_col   = col('입고량')
    stock_col = col('총재고수량')

    if hist_col < 0 or name_col < 0:
        print(f"  [입고] 필수 컬럼 없음 — 헤더: {header}")
        return {}

    raw: list[dict] = []
    for row in all_rows[1:]:
        if not row or len(row) <= max(hist_col, name_col):
            continue
        hist = row[hist_col].strip()
        name = row[name_col].strip()
        date = row[date_col].strip() if date_col >= 0 and len(row) > date_col else ''
        try:
            qty = int(str(row[qty_col]).replace(',', '').strip()) if qty_col >= 0 and len(row) > qty_col else 0
        except ValueError:
            qty = 0
        try:
            stock = int(str(row[stock_col]).replace(',', '').strip()) if stock_col >= 0 and len(row) > stock_col and row[stock_col].strip() else qty
        except ValueError:
            stock = qty

        if not hist or not name:
            continue
        # 총재고수량 0 이하 로트는 선택 대상에서 제외 (재고 마이너스 방지)
        if stock <= 0:
            continue
        raw.append({'histrace_num': hist, 'product_name': name, 'received_date': date, 'qty': qty, 'stock': stock})

    # 입고일 오름차순 정렬 (FIFO: 오래된 로트 먼저 소모)
    raw.sort(key=lambda x: x['received_date'])

    # 제품명별 그룹화 (동일 이력번호 = 동일 로트 복수 입고 → 수량 합산)
    result: dict[str, list[dict]] = {}
    for r in raw:
        key = r['product_name']
        if key not in result:
            result[key] = []
        existing = next((x for x in result[key] if x['histrace_num'] == r['histrace_num']), None)
        if existing:
            existing['qty'] += r['qty']
            existing['stock'] = existing.get('stock', 0) + r.get('stock', 0)
        else:
            result[key].append({'histrace_num': r['histrace_num'],
                                'received_date': r['received_date'],
                                'qty': r['qty'],
                                'stock': r.get('stock', 0)})

    # 구 제품명 → 정식명 자동 변환 (리뉴얼·이름 변경 대응)
    result = _remap_to_canonical(result)

    print(f"  [입고] {len(result)}개 제품, {sum(len(v) for v in result.values())}개 로트 로드 (FIFO)")
    return result


def check_new_inbound_products() -> list[dict]:
    """
    '입고 및 재고' 탭의 제품 중 시트1 C열에 매칭 안 되는 신규 제품 감지.
    감지 시 이지어드민_일별출고에서 유사 이름 자동 매칭 → 시트1에 즉시 자동 추가.
    반환: [{'name': str, 'hist': str, 'qty': str, 'recv_dt': str,
             'ez_name': str, 'backfill_dates': list[str]}, ...]
    """
    global _canonical_names_cache
    _canonical_names_cache = None  # 캐시 초기화 (최신 시트1 반영)

    try:
        sheet = _get_sheet('입고 및 재고')
        all_rows = sheet.get_all_values()
    except Exception as e:
        print(f'  [신규제품감지] 입고 탭 읽기 실패: {e}')
        return []

    if len(all_rows) < 2:
        return []

    header = [h.strip() for h in all_rows[0]]
    def col(kw):
        for i, h in enumerate(header):
            if kw in h:
                return i
        return -1

    name_col = col('품목명')
    hist_col = col('이력추적관리번호')
    qty_col  = col('입고량')
    date_col = col('입고일')

    if name_col < 0 or hist_col < 0:
        return []

    # 입고관리 제품 목록 (중복 제거)
    seen: dict[str, dict] = {}  # name → {hist, qty, recv_dt}
    for row in all_rows[1:]:
        if len(row) <= max(name_col, hist_col):
            continue
        name = row[name_col].strip()
        hist = row[hist_col].strip()
        qty  = row[qty_col].strip()  if qty_col  >= 0 and len(row) > qty_col  else ''
        dt   = row[date_col].strip() if date_col >= 0 and len(row) > date_col else ''
        if name and name not in seen:
            seen[name] = {'hist': hist, 'qty': qty, 'recv_dt': dt}

    canonical = _get_canonical_names()
    if not canonical:
        return []

    # config.json의 inbound_ignore_products 목록 로드
    try:
        import json as _json, os as _os
        _cfg_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'config.json')
        with open(_cfg_path, encoding='utf-8') as _f:
            _cfg = _json.load(_f)
        _ignore_list = [s.strip() for s in _cfg.get('inbound_ignore_products', [])]
    except Exception:
        _ignore_list = []

    # 시트1에 없는 제품 찾기 (bigram 유사도 0.70 미만 → 신규 제품)
    new_products = []
    for name, info in seen.items():
        # 무시 목록에 있는 제품은 건너뜀 (기존 재고 소모 중 등 사유로 이력추적 미등록)
        if any(_kr_bigram_similarity(name, ig) >= 0.80 or ig in name or name in ig
               for ig in _ignore_list):
            print(f'  [신규제품감지] 무시 목록으로 건너뜀: {name}')
            continue
        best_sim = max((_kr_bigram_similarity(name, c) for c in canonical), default=0.0)
        if best_sim < 0.70:
            new_products.append({'name': name, **info, 'ez_name': '', 'backfill_dates': []})
            print(f'  [신규제품감지] 미등록 제품: {name} (최고유사도 {best_sim:.2f})')

    if not new_products:
        return []

    # ── 1단계: 감지 즉시 텔레그램 알림 (자동추가 전에 먼저 전송) ────────────
    # 이후 어떤 오류가 나더라도 법적 기한 내 수동 대응이 가능하도록 감지 사실을 먼저 알림
    _detect_lines = '\n'.join(
        f'  · {p["name"]}\n    이력번호: {p["hist"]} | 입고일: {p["recv_dt"]}'
        for p in new_products
    )
    try:
        from modules.telegram_alert import send_alert
        send_alert(
            '🆕 [식약처 신규제품 감지] 시트1 자동 추가 시도 중\n'
            '아래 제품이 식약처 입고관리에서 처음 발견되었습니다.\n'
            '식약처 출고 등록 누락 방지를 위해 즉시 확인해주세요.\n\n'
            + _detect_lines
        )
    except Exception as e:
        print(f'  [신규제품감지] 감지 텔레그램 알림 실패: {e}')

    # ── 2단계: 이지어드민_일별출고에서 미매핑 상품명 수집 ───────────────────
    try:
        sheet1 = _get_sheet('시트1')
        sheet1_rows = sheet1.get_all_values()
    except Exception as e:
        print(f'  [신규제품감지] 시트1 읽기 실패: {e}')
        return new_products  # 감지 알림은 이미 전송됨

    mapped_ez_names = {
        row[1].strip() for row in sheet1_rows[1:]
        if len(row) > 1 and row[1].strip()
    }

    try:
        ez_ws = _get_sheet(IZIADMIN_DAILY_SHEET)
        ez_rows = ez_ws.get_all_values()
    except Exception as e:
        print(f'  [신규제품감지] 이지어드민_일별출고 읽기 실패: {e}')
        ez_rows = []

    # ez_name → set of sale dates (not yet mapped in 시트1)
    ez_sales: dict[str, set] = {}
    for row in ez_rows[1:]:
        if len(row) < 3:
            continue
        ez_name  = row[2].strip()
        date_str = row[1].strip() if len(row) > 1 else ''
        if ez_name and ez_name != '(0건)' and ez_name not in mapped_ez_names:
            ez_sales.setdefault(ez_name, set()).add(date_str)

    unmapped_ez = list(ez_sales.keys())

    # ── 5영업일 판정 헬퍼 ────────────────────────────────────────────────
    from datetime import date as _date, datetime as _datetime, timezone, timedelta
    try:
        import holidays as _hl
        _kr_hol = _hl.KR(years=_date.today().year)
    except Exception:
        _kr_hol = set()

    _today = _datetime.now(timezone(timedelta(hours=9))).date()

    def _biz_elapsed(d_str: str) -> int:
        try:
            d = _date.fromisoformat(d_str)
        except Exception:
            return 99
        cnt = 0
        cur = d
        while cur <= _today:
            if cur.weekday() < 5 and cur not in _kr_hol:
                cnt += 1
            cur += timedelta(days=1)
        return cnt

    # ── 3단계: 각 신규 제품 시트1 자동 추가 ────────────────────────────────
    added_results = []  # (식약처명, ez_name, backfill_dates, success, err_msg)

    for p in new_products:
        식약처명 = p['name']
        add_ok   = False
        err_msg  = ''

        # 이지어드민명 자동 매칭 (미매핑 이름 중 가장 유사한 것)
        best_ez, best_ez_sim = '', 0.0
        for ez_name in unmapped_ez:
            sim2 = _kr_bigram_similarity(식약처명, ez_name)
            if sim2 > best_ez_sim:
                best_ez_sim = sim2
                best_ez = ez_name

        p['ez_name'] = best_ez
        p['ez_sim']  = best_ez_sim

        try:
            new_row = ['', best_ez, 식약처명, '', '']
            sheet1.append_row(new_row, value_input_option='USER_ENTERED')
            add_ok = True
            print(f'  [신규제품감지] 시트1 자동 추가: {식약처명} ← {best_ez!r} (유사도 {best_ez_sim:.2f})')
        except Exception as e:
            err_msg = str(e)
            print(f'  [신규제품감지] 시트1 자동 추가 실패 ({식약처명}): {e}')

        # mapped_ez_names 업데이트 (다음 제품 매칭 시 중복 방지)
        if best_ez and add_ok:
            mapped_ez_names.add(best_ez)
            unmapped_ez = [n for n in unmapped_ez if n != best_ez]

        # 5영업일 이내 소급 처리 대상 날짜 수집
        backfill = []
        if best_ez and best_ez in ez_sales:
            for d_str in sorted(ez_sales[best_ez]):
                elapsed = _biz_elapsed(d_str)
                if 1 <= elapsed <= 5:
                    backfill.append(d_str)
        p['backfill_dates'] = backfill if add_ok else []

        added_results.append((식약처명, best_ez, best_ez_sim, backfill, add_ok, err_msg))

    # 캐시 무효화 — 이 실행의 이후 호출에서 업데이트된 시트1 반영
    _canonical_names_cache = None

    # ── 4단계: 자동추가 결과 텔레그램 알림 ──────────────────────────────────
    result_lines = []
    for 식약처명, best_ez, best_ez_sim, backfill, add_ok, err_msg in added_results:
        if add_ok:
            line = f'✅ {식약처명}'
            if best_ez:
                line += f'\n   이지어드민명: {best_ez} ({best_ez_sim:.0%})'
            else:
                line += '\n   ⚠️ 이지어드민명 자동 매칭 실패 → B열 직접 입력 필요'
            if backfill:
                line += f'\n   소급 출고 처리 예정: {", ".join(backfill)}'
        else:
            line = (
                f'❌ {식약처명} — 시트1 자동 추가 실패!\n'
                f'   → 수동으로 시트1에 직접 추가해주세요\n'
                f'   오류: {err_msg[:80]}'
            )
        result_lines.append(line)

    try:
        from modules.telegram_alert import send_alert
        send_alert(
            '🆕 [식약처 신규제품] 시트1 자동 추가 결과\n'
            '잘못됐으면 시트1에서 직접 수정해주세요.\n\n'
            + '\n\n'.join(result_lines)
        )
    except Exception as e:
        print(f'  [신규제품감지] 결과 텔레그램 알림 실패: {e}')

    return new_products


def find_histrace_num(tfood_name: str, lots_by_product: dict[str, list[dict]]) -> str:
    """
    식약처 등록명으로 FIFO 첫 번째 이력추적관리번호 반환.
    정확 매칭 → 공백 제거 부분 매칭 순.
    """
    # 1. 정확 매칭
    if tfood_name in lots_by_product:
        return lots_by_product[tfood_name][0]['histrace_num']

    # 2. 공백·괄호 제거 부분 매칭
    name_clean = tfood_name.replace(' ', '').replace('(', '').replace(')', '').lower()
    for key, lots in lots_by_product.items():
        key_clean = key.replace(' ', '').replace('(', '').replace(')', '').lower()
        if name_clean in key_clean or key_clean in name_clean:
            return lots[0]['histrace_num']

    return ''


# ── 구글시트 저장 (개인/쿠팡 탭) ─────────────────────────────────────────

def write_personal_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    sheet = _get_sheet('개인')
    _append_rows(sheet, rows, outlet_name='개인', sabn='999-99-99902', addr='')
    return len(rows)


def write_coupang_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    sheet = _get_sheet('쿠팡')
    _append_rows(sheet, rows,
                 outlet_name='쿠팡',
                 sabn='120-88-00767',
                 addr='서울특별시 송파구 송파대로 570 (신천동)')
    return len(rows)


def _append_rows(sheet: gspread.Worksheet, rows: list[dict],
                 outlet_name: str, sabn: str, addr: str):
    """
    출고일자 | 식품이력추적관리번호 | 제품명 | 출고지명
    출고지사업자등록번호 | 출고지주소 | 출고수량 | 인허가번호 | 비고
    """
    values = []
    for r in rows:
        values.append([
            r['date'],
            r['histrace_num'],
            r['product_name'],
            outlet_name,
            sabn,
            addr,
            r['qty'],
            '',
            '',
        ])
    sheet.append_rows(values, value_input_option='USER_ENTERED')
    print(f"  [시트] {outlet_name} 탭 {len(values)}건 저장 완료")


def read_personal_rows() -> list[dict]:
    return _read_rows('개인')


def read_coupang_rows() -> list[dict]:
    return _read_rows('쿠팡')


def _read_rows(tab_name: str) -> list[dict]:
    sheet = _get_sheet(tab_name)
    all_vals = sheet.get_all_values()
    if len(all_vals) < 2:
        return []
    results = []
    for row in all_vals[1:]:
        if not row or not row[0]:
            continue
        try:
            results.append({
                'date':         row[0] if len(row) > 0 else '',
                'histrace_num': row[1] if len(row) > 1 else '',
                'product_name': row[2] if len(row) > 2 else '',
                'outlet_name':  row[3] if len(row) > 3 else '',
                'sabn':         row[4] if len(row) > 4 else '',
                'addr':         row[5] if len(row) > 5 else '',
                'qty':          int(str(row[6]).replace(',', '')) if len(row) > 6 and row[6] else 0,
                'permit_no':    row[7] if len(row) > 7 else '',
                'remark':       row[8] if len(row) > 8 else '',
            })
        except Exception as e:
            print(f"  [시트] 행 파싱 오류: {e}")
    return results


def log_transmission(rows: list[dict], channel: str = '', method: str = '',
                     result: str = '', error: str = '') -> None:
    """
    전송내역 탭에 전송 기록 추가 (모든 행을 한 번의 API 호출로 일괄 기록).

    rows    : 전송한 행 목록. 각 행에 'channel'/'method'/'result'/'error'를
              개별 지정하면 그 값이 우선 적용되고, 없으면 아래 함수 인자값 사용.
    channel : '개인' | '쿠팡' (행별 지정이 없을 때 기본값)
    method  : 'SOAP' | '웹UI' (행별 지정이 없을 때 기본값)
    result  : '성공' | '실패' | 'DRY-RUN' (행별 지정이 없을 때 기본값)
    error   : 오류 내용 (없으면 '')
    """
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST).strftime('%Y-%m-%d %H:%M')

    import os
    tfood_id = os.getenv('TFOOD_ID', '')
    key_type = '테스트' if tfood_id in ('tfwebservice20', '') else '실제'

    try:
        sheet = _get_sheet('전송내역')

        # 헤더가 없으면 추가 (1행만 읽어서 quota 절약)
        header = sheet.row_values(1)
        if not header:
            sheet.append_row([
                '전송일시', '채널', '전송방법', 'API종류',
                '출고일자', '이력추적관리번호', '제품명', '수량',
                '결과', '오류내용'
            ])

        # 각 행 기록 (행별 channel/method/result/error 우선 적용)
        new_rows = []
        for r in rows:
            row_error = r.get('error', error) or ''
            new_rows.append([
                now,
                r.get('channel', channel),
                r.get('method', method),
                key_type,
                r.get('date', r.get('tgow_dt', '')),
                r.get('histrace_num', r.get('food_histrace_num', '')),
                r.get('product_name', ''),
                r.get('qty', r.get('prod_qty', '')),
                r.get('result', result),
                row_error[:200],
            ])

        if new_rows:
            # 최신 순 정렬: 헤더(row1) 바로 아래에 삽입해 최신이 상단에 오도록
            sheet.insert_rows(new_rows, row=2, value_input_option='USER_ENTERED')
            from collections import Counter
            counts = Counter(row[8] for row in new_rows)
            summary = ', '.join(f'{k} {v}건' for k, v in counts.items())
            print(f'  [전송내역] {len(new_rows)}건 기록 완료 ({summary})')
    except Exception as e:
        print(f'  [전송내역] 기록 실패 (무시): {e}')


def get_active_histrace_nums() -> dict:
    """
    제품별로 현재 출고에 사용해야 할 이력추적관리번호(현재 활성 로트)를 계산.

    입고 및 재고 탭의 총재고수량 기준으로 재고가 남아있는 로트 중
    가장 오래된(입고일 오름차순) 로트를 선택 (FIFO).

    전송내역 누적량 기반 계산은 사용하지 않는다 — 창고에서 실제로 어떤 로트를
    먼저 출고했는지와 무관하게, 시스템이 입고일 순서대로 독자적으로 FIFO를
    유지하기 위해 재고 잔량만을 기준으로 삼는다.

    Returns: {product_name: histrace_num}
    """
    try:
        result: dict[str, str] = {}
        for product, lot_list in read_lots_by_product().items():
            if not lot_list:
                continue
            # 재고(총재고수량) > 0인 로트 중 입고일 가장 오래된 것 선택
            # lot_list는 이미 입고일 오름차순 정렬 상태
            active = lot_list[-1]['histrace_num']  # 모든 로트 소진 시 최신 로트
            for lot in lot_list:
                if lot.get('stock', 0) > 0:
                    active = lot['histrace_num']
                    break
            result[product] = active
        return result
    except Exception:
        return {}


def get_lot_queue() -> dict[str, list[dict]]:
    """
    제품별 출고 가능 로트 대기열 (FIFO 순). 각 로트의 남은 출고 가능 수량 포함.
    로트 경계를 초과하는 출고(로트 분할)에 사용.

    remaining 기준: 이지어드민 '총재고수량' 직접 사용.
    (전송내역 누적으로 계산하면 시스템 외부 식약처 등록분을 반영 못해 오차 발생)

    Returns: {product_name: [{'histrace_num': str, 'remaining': int}, ...]}
    """
    try:
        result: dict[str, list[dict]] = {}
        for product, lot_list in read_lots_by_product().items():
            if not lot_list:
                continue
            queue = [
                {'histrace_num': lot['histrace_num'], 'remaining': lot['stock']}
                for lot in lot_list
                if lot.get('stock', 0) > 0
            ]
            result[product] = queue
        return result
    except Exception:
        return {}


def get_already_sent_keys(target_date: str) -> dict:
    """
    전송내역 탭에서 특정 날짜에 이미 성공 전송된 (이력번호)별 건수 반환.
    같은 이력번호+날짜로 여러 건(별도 입고건)이 있을 수 있으므로
    건수 단위로 체크 — 일부만 성공한 경우 나머지만 재전송 가능.
    """
    try:
        sheet = _get_sheet('전송내역')
        rows  = sheet.get_all_values()
        date_clean = target_date.replace('-', '')
        sent_counts: dict = {}
        for row in rows[1:]:
            if len(row) < 9:
                continue
            if len(row) > 3 and row[3].strip() == '테스트':
                continue  # 테스트 API 전송 건 제외
            row_date   = str(row[4]).strip().replace('-', '')
            histrace   = str(row[5]).strip()
            row_result = str(row[8]).strip()
            if row_date == date_clean and row_result == '성공' and histrace:
                sent_counts[histrace] = sent_counts.get(histrace, 0) + 1
        return sent_counts
    except Exception:
        return {}


def get_already_sent_products(target_date: str) -> dict:
    """
    전송내역 탭에서 특정 날짜에 채널별로 이미 '성공' 전송된 제품명 집합 반환.
    로트(이력번호)가 바뀌어도 같은 제품+채널+날짜는 중복 전송 방지하기 위함.

    Returns: {'개인': {product_name, ...}, '쿠팡': {product_name, ...}}
    """
    try:
        sheet = _get_sheet('전송내역')
        rows  = sheet.get_all_values()
        date_clean = target_date.replace('-', '')
        result: dict = {'개인': set(), '쿠팡': set()}
        for row in rows[1:]:
            if len(row) < 9:
                continue
            if len(row) > 3 and row[3].strip() == '테스트':
                continue  # 테스트 API 전송 건 제외
            row_date = str(row[4]).strip().replace('-', '')
            if row_date != date_clean or row[8].strip() != '성공':
                continue
            channel = row[1].strip()
            product = row[6].strip()
            if channel in result and product:
                result[channel].add(product)
        return result
    except Exception:
        return {'개인': set(), '쿠팡': set()}


def check_coupang_missing_alert(new_product_start_row: int = 18) -> None:
    """
    시트1 전체(행18+는 신규 제품 포함)에서 쿠팡로켓 E/F열 중 하나라도 비어있는
    제품을 감지해 텔레그램으로 알림 전송 (주 1회 호출 대상).
    C열(식약처 등록명)이 있는 행만 대상. 번들 제품(C열 없는 행)은 자동 제외.
    """
    try:
        sheet = _get_sheet('시트1')
        rows = sheet.get_all_values()
        missing_skuid = []   # E열(쿠팡 skuid) 빈칸
        missing_name  = []   # F열(쿠팡 상품명) 빈칸
        for i, row in enumerate(rows[1:], start=2):  # 헤더 제외, 행번호는 2부터
            if len(row) < 3:
                continue
            prod_code  = row[0].strip()
            name_b     = row[1].strip()
            sik_name   = row[2].strip()           # C열: 식약처 등록명
            if not sik_name:
                continue  # 식약처 미등록 제품 제외
            coupang_id   = row[4].strip() if len(row) > 4 else ''  # E열
            coupang_name = row[5].strip() if len(row) > 5 else ''  # F열
            if not coupang_id:
                missing_skuid.append(f'  - 행{i} [{prod_code}] {name_b} — E열(skuid) 없음')
            if not coupang_name:
                missing_name.append(f'  - 행{i} [{prod_code}] {name_b} — F열(상품명) 없음')

        issues = []
        if missing_skuid:
            issues.append('【E열(쿠팡 skuid) 미입력】\n' + '\n'.join(missing_skuid))
        if missing_name:
            issues.append('【F열(쿠팡 상품명) 미입력】\n' + '\n'.join(missing_name))

        if not issues:
            print(f'  [쿠팡알림] 시트1 E/F열 모두 정상')
            return

        msg = (
            '[쿠팡로켓 E/F열 미입력 알림]\n'
            '아래 제품의 쿠팡 E열(skuid) 또는 F열(상품명)이 비어있습니다.\n'
            'F열(상품명)이 없으면 쿠팡 입고 시 자동 매칭이 실패합니다:\n\n'
            + '\n\n'.join(issues)
        )
        from modules.telegram_alert import send_alert
        send_alert(msg)
        print(f'  [쿠팡알림] E/F열 미입력 {len(missing_skuid)+len(missing_name)}건 알림 전송')
    except Exception as e:
        print(f'  [쿠팡알림] 건너뜀: {e}')


READONLY_TABS = {'기타메모'}  # 코드에서 절대 수정 금지 (식약처 소명 메모, 사람이 직접 작성)


def clear_tab(tab_name: str):
    if tab_name in READONLY_TABS:
        raise ValueError(f"[보호됨] '{tab_name}' 탭은 읽기 전용입니다. 코드로 수정 불가.")
    sheet = _get_sheet(tab_name)
    all_vals = sheet.get_all_values()
    if len(all_vals) <= 1:
        return
    sheet.delete_rows(2, len(all_vals))
    print(f"  [시트] {tab_name} 탭 초기화 완료 ({len(all_vals)-1}행 삭제)")


def write_coupang_inbound_tab(inbound_rows: list[dict]) -> int:
    """
    쿠팡 서플라이허브 입고상세내역(전체 컬럼)을 '쿠팡 로켓 입고상세내역' 탭에 기록.
    inbound_rows: get_coupang_full()/get_coupang_full_range() 반환값 (구분 무관, 전체 컬럼)
    헤더가 없으면 자동 생성. 최신 수집분이 상단에 오도록 row=2에 삽입.
    """
    if not inbound_rows:
        return 0

    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    collected_at = datetime.now(KST).strftime('%Y-%m-%d %H:%M')

    HEADER = ['구분', '번호', 'SKU 번호', 'SKU 명', '입고/반출일자', '물류센터',
              '세금타입', '수량', '단가', '공급가액', '세액', '총 단가', '총 공급가액',
              '총 세액', '계산서번호', '지급일', '업데이트시간']
    TAB = '쿠팡 로켓 입고상세내역'

    try:
        sheet = _get_sheet(TAB)
        existing = sheet.get_all_values()

        # 헤더가 없으면 추가 (첫 실행 시)
        if not existing or not existing[0] or existing[0][0] != '구분':
            sheet.clear()
            sheet.append_row(HEADER)
            existing = [HEADER]

        # 기존 행과 중복(구분+번호+SKU번호+입고/반출일시) 제외 — 재시도/재실행 시 중복 방지
        # 입고/반출일시는 분 단위까지 포함 — 같은 날 같은 SKU의 별도 입고건을 구분
        existing_keys = {
            (row[0], row[1], row[2], row[4])
            for row in existing[1:] if len(row) >= 5
        }

        new_rows = [
            [r.get('div', ''), r.get('no', ''), r.get('sku_id', ''),
             r.get('sku_name', ''), r.get('datetime') or r.get('date', ''), r.get('center', ''),
             r.get('tax_type', ''), r.get('qty', 0), r.get('unit_price', ''),
             r.get('supply_amt', ''), r.get('tax_amt', ''), r.get('total_unit_price', ''),
             r.get('total_supply_amt', ''), r.get('total_tax_amt', ''),
             r.get('invoice_no', ''), r.get('pay_date', ''), '']
            for r in inbound_rows
            if (r.get('div', ''), r.get('no', ''), r.get('sku_id', ''), r.get('datetime') or r.get('date', '')) not in existing_keys
        ]

        if not new_rows:
            print(f'  [쿠팡 입고탭] 신규 데이터 없음 (중복 {len(inbound_rows)}건 제외) → {TAB}')
            return 0

        # 업데이트시간은 새로 추가되는 행 중 맨 위(최신) 한 행에만 기재
        new_rows[0][-1] = collected_at

        # 최신 수집분이 상단(row 2)에 오도록 삽입
        sheet.insert_rows(new_rows, row=2, value_input_option='USER_ENTERED')
        print(f'  [쿠팡 입고탭] {len(new_rows)}건 기록 완료 → {TAB}')
        return len(new_rows)
    except Exception as e:
        print(f'  [쿠팡 입고탭] 기록 실패: {e}')
        return 0


# ── 식약처 실제출고 탭 (출고관리 화면 크롤링 결과 저장 + 교차검증용) ──────────


ACTUAL_OUTGO_SHEET = '식약처_실제출고'
LINK_STATUS_SHEET  = '식약처_연계현황'

_ACTUAL_OUTGO_HEADER = [
    '수집일시', '출고일자', '이력번호', '제품명', '채널', '출고수량',
]

_LINK_STATUS_HEADER = [
    '수집일시', '구분', '이력번호', '제품명', '수량', '거래처',
    '일자', '지연여부', '정보연계일자',
]


def update_actual_outgo_sheet(records: list[dict]) -> None:
    """
    식약처 출고관리 화면에서 크롤링한 실제 출고 기록을 '식약처_실제출고' 탭에 저장.
    기존 내용 전체 교체.
    records: [{histrace_num, product_name, outgo_date, channel, qty}]
    """
    import re
    from datetime import datetime, timezone, timedelta
    now_str = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M')

    rows = []
    for r in records:
        date_raw = str(r.get('outgo_date', '')).strip()
        # YYYY.MM.DD → YYYY-MM-DD 정규화
        date_norm = re.sub(r'(\d{4})[./](\d{2})[./](\d{2})', r'\1-\2-\3', date_raw)
        rows.append([
            now_str,
            date_norm,
            r.get('histrace_num', ''),
            r.get('product_name', ''),
            r.get('channel', ''),
            r.get('qty', 0),
        ])

    # 출고일자 오름차순 정렬
    rows.sort(key=lambda x: str(x[1]))

    try:
        wb = _get_client().open_by_key(SHEET_ID)
        try:
            sheet = wb.worksheet(ACTUAL_OUTGO_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            sheet = wb.add_worksheet(title=ACTUAL_OUTGO_SHEET, rows=1000, cols=8)
            print(f'  [실제출고탭] "{ACTUAL_OUTGO_SHEET}" 탭 신규 생성')

        sheet.clear()
        data = [_ACTUAL_OUTGO_HEADER] + rows if rows else [_ACTUAL_OUTGO_HEADER]
        sheet.update(data, 'A1', value_input_option='USER_ENTERED')
        print(f'  [실제출고탭] {len(rows)}건 업데이트 완료')
    except Exception as e:
        print(f'  [실제출고탭] 업데이트 실패: {e}')


def read_actual_outgo_by_date() -> dict[str, dict[str, int]]:
    """
    '식약처_실제출고' 탭에서 날짜별·채널별 출고수량 합계 반환.
    Returns: {'2026-07-16': {'개인': 141, '쿠팡': 0}, ...}
    탭이 없거나 읽기 실패 시 빈 dict 반환.
    """
    import re
    try:
        sheet = _get_sheet(ACTUAL_OUTGO_SHEET)
        rows = sheet.get_all_values()
    except Exception:
        return {}

    result: dict[str, dict[str, int]] = {}
    for row in rows[1:]:
        if len(row) < 6:
            continue
        date = str(row[1]).strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            continue
        channel = str(row[4]).strip()
        try:
            qty = int(str(row[5]).replace(',', '').strip() or '0')
        except ValueError:
            qty = 0
        if date not in result:
            result[date] = {'개인': 0, '쿠팡': 0}
        if '개인' in channel:
            result[date]['개인'] += qty
        elif '쿠팡' in channel:
            result[date]['쿠팡'] += qty
    return result


def update_link_status_sheet(records: list[dict]) -> None:
    """
    이력정보 연계현황 수집 결과를 '식약처_연계현황' 탭에 저장.
    records: fetch_link_status() 반환값 [{type, histrace_num, product_name,
             qty, channel, outgo_date, delayed, link_date}]
    """
    import re
    from datetime import datetime, timezone, timedelta
    now_str = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M')

    def _norm(d: str) -> str:
        return re.sub(r'(\d{4})[./](\d{2})[./](\d{2})', r'\1-\2-\3', str(d).strip())

    rows = [
        [
            now_str,
            r.get('type', ''),
            r.get('histrace_num', ''),
            r.get('product_name', ''),
            r.get('qty', 0),
            r.get('channel', ''),
            _norm(r.get('outgo_date', '')),
            r.get('delayed', ''),
            _norm(r.get('link_date', '')),
        ]
        for r in records
    ]
    rows.sort(key=lambda x: (x[6], x[1]), reverse=True)   # 최신 일자 → 구분 순

    try:
        wb = _get_client().open_by_key(SHEET_ID)
        try:
            sheet = wb.worksheet(LINK_STATUS_SHEET)
        except gspread.exceptions.WorksheetNotFound:
            sheet = wb.add_worksheet(title=LINK_STATUS_SHEET, rows=2000, cols=10)
            print(f'  [연계현황탭] "{LINK_STATUS_SHEET}" 탭 신규 생성')

        sheet.clear()
        data = [_LINK_STATUS_HEADER] + rows if rows else [_LINK_STATUS_HEADER]
        sheet.update(data, 'A1', value_input_option='USER_ENTERED')
        inbound  = sum(1 for r in records if r.get('type') == '입고')
        outbound = sum(1 for r in records if r.get('type') == '출고')
        delayed  = sum(1 for r in records if r.get('delayed') == '지연')
        print(f'  [연계현황탭] {len(records)}건 업데이트 (입고 {inbound} / 출고 {outbound} / 지연 {delayed})')
    except Exception as e:
        print(f'  [연계현황탭] 업데이트 실패: {e}')


def read_outgo_by_date_from_link_status() -> dict[str, dict[str, int]]:
    """
    '식약처_연계현황' 탭에서 출고 기록만 필터해 날짜별·채널별 수량 합계 반환.
    Returns: {'2026-07-16': {'개인': 141, '쿠팡': 60}, ...}
    탭 없거나 실패 시 빈 dict 반환.
    채널 판단: '쿠팡' 포함 → 쿠팡, 그 외 → 개인
    """
    import re
    try:
        sheet = _get_sheet(LINK_STATUS_SHEET)
        rows  = sheet.get_all_values()
    except Exception:
        return {}

    result: dict[str, dict[str, int]] = {}
    for row in rows[1:]:
        if len(row) < 7:
            continue
        if str(row[1]).strip() != '출고':   # 출고만
            continue
        date = str(row[6]).strip()
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            continue
        channel = str(row[5]).strip()
        try:
            qty = int(str(row[4]).replace(',', '').strip() or '0')
        except ValueError:
            qty = 0
        result.setdefault(date, {'개인': 0, '쿠팡': 0})
        if '쿠팡' in channel:
            result[date]['쿠팡'] += qty
        else:
            result[date]['개인'] += qty
    return result


# ── 이지어드민 일별 출고 영구 저장 ──────────────────────────────────────────

IZIADMIN_DAILY_SHEET = '이지어드민_일별출고'
_IZIADMIN_DAILY_HEADER = ['수집일시', '출고날짜', '이지어드민상품명', '수량']


def append_iziadmin_daily(date: str, sales: dict[str, int], now_ts: str = '') -> int:
    """
    이지어드민 개인판매 raw 데이터를 '이지어드민_일별출고' 탭에 누적 저장.

    - sales: get_personal_sales() 반환값 {이지어드민상품명: 수량} (이미 __poomgo_found__ 제거된 것)
    - 개인 채널만 저장. 쿠팡은 '쿠팡 로켓 입고상세내역' 탭 사용.
    - 같은 출고날짜가 이미 있으면 스킵 (재실행 idempotent).
    - 탭 없으면 자동 생성.
    - 반환: 저장된 행 수 (0이면 스킵됨)
    """
    from datetime import datetime, timezone, timedelta
    if not now_ts:
        now_ts = datetime.now(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M')

    wb = _get_client().open_by_key(SHEET_ID)
    try:
        ws = wb.worksheet(IZIADMIN_DAILY_SHEET)
    except gspread.exceptions.WorksheetNotFound:
        ws = wb.add_worksheet(title=IZIADMIN_DAILY_SHEET, rows=5000, cols=4)
        ws.append_row(_IZIADMIN_DAILY_HEADER, value_input_option='USER_ENTERED')
        print(f'  [일별출고탭] "{IZIADMIN_DAILY_SHEET}" 탭 신규 생성')

    all_vals = ws.get_all_values()

    # 같은 날짜 이미 있으면 스킵
    for row in all_vals[1:]:
        if len(row) >= 2 and row[1].strip() == date:
            print(f'  [일별출고탭] {date} 이미 저장됨, 스킵')
            return 0

    # qty > 0 인 품목만 저장 (이지어드민 원본명 그대로)
    new_rows = [
        [now_ts, date, name, qty]
        for name, qty in sorted(sales.items())
        if qty > 0
    ]

    if not new_rows:
        ws.insert_rows([[now_ts, date, '(0건)', 0]], row=2, value_input_option='USER_ENTERED')
        print(f'  [일별출고탭] {date} 0건 마커 저장')
        return 0

    # 헤더(1행) 바로 아래에 삽입 → 최신 날짜가 항상 맨 위에 표시
    ws.insert_rows(new_rows, row=2, value_input_option='USER_ENTERED')
    print(f'  [일별출고탭] {date} {len(new_rows)}개 품목 저장')
    return len(new_rows)


def read_iziadmin_daily_by_date(date: str) -> dict[str, int]:
    """
    '이지어드민_일별출고' 탭에서 특정 날짜의 개인판매 raw 데이터 반환.
    Returns: {이지어드민상품명: 수량} — get_personal_sales() 반환과 동일 형식
    날짜 없거나 실패 시 빈 dict 반환.
    """
    try:
        ws = _get_sheet(IZIADMIN_DAILY_SHEET)
        all_vals = ws.get_all_values()
    except Exception:
        return {}

    result: dict[str, int] = {}
    for row in all_vals[1:]:
        if len(row) < 4 or row[1].strip() != date:
            continue
        name = row[2].strip()
        if not name or name == '(0건)':
            continue
        try:
            qty = int(str(row[3]).replace(',', '').strip() or '0')
        except ValueError:
            qty = 0
        if qty > 0:
            result[name] = result.get(name, 0) + qty
    return result


def get_saved_daily_dates() -> set[str]:
    """'이지어드민_일별출고' 탭에 저장된 날짜 집합 반환."""
    try:
        ws = _get_sheet(IZIADMIN_DAILY_SHEET)
        return {row[1].strip() for row in ws.get_all_values()[1:] if len(row) > 1 and row[1].strip()}
    except Exception:
        return set()
