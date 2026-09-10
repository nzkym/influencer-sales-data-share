"""
쿠팡 서플라이허브 (supplier.coupang.com) 납품 출고 데이터 수집

실제 Chrome + CDP 방식 (포트 9234).
매 실행마다 새 headless 브라우저를 띄우는 대신, 상주 Chrome에 연결해
새 탭으로 작업하고 탭만 닫아 Chrome 창을 유지한다.
→ Akamai WAF 봇 차단 위험 최소화, 세션 장기 유지.
"""
import asyncio
import os
import sys
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

BASE_URL    = 'https://supplier.coupang.com'
INBOUND_URL = 'https://supplier.coupang.com/scm/receive/detail'

# coupang_browser.py는 modules의 상위 폴더에 있음
_ROOT = os.path.dirname(os.path.dirname(__file__))


def _get_browser_mod():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    import coupang_browser
    return coupang_browser


async def _login(page) -> bool:
    user_id  = os.getenv('COUPANG_ID', '')
    password = os.getenv('COUPANG_PASSWORD', '')

    if not user_id or not password:
        print('  [쿠팡] ❌ .env에 COUPANG_ID / COUPANG_PASSWORD 없음')
        return False

    await page.goto(BASE_URL, wait_until='domcontentloaded', timeout=20000)
    await page.wait_for_timeout(3000)

    for sel in ['input[name="username"]', 'input[name="email"]',
                'input[type="email"]', 'input[type="text"]']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.fill(user_id, timeout=3000)
                break
        except Exception:
            continue

    for sel in ['input[type="password"]', 'input[name="password"]']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.fill(password, timeout=3000)
                break
        except Exception:
            continue

    for sel in ['button[type="submit"]', 'button:has-text("로그인")',
                'button:has-text("Login")', 'input[type="submit"]']:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                await el.click(timeout=3000)
                break
        except Exception:
            continue

    print('  [쿠팡] 자동 로그인 대기... (최대 60초)')
    for _ in range(60):
        await page.wait_for_timeout(1000)
        url = page.url
        if ('supplier.coupang.com' in url and
                'login' not in url.lower() and 'signin' not in url.lower()):
            print('  [쿠팡] 자동 로그인 성공')
            return True

    # 자동 로그인 실패 → 수동 로그인 대기 (10분)
    print('  [쿠팡] 자동 로그인 실패 — 브라우저 창에서 수동 로그인 필요')
    print('  [쿠팡] 10분간 대기합니다...')
    try:
        import requests as _req
        token   = os.getenv('TELEGRAM_BOT_TOKEN', '')
        chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        if token and chat_id:
            _req.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                data={'chat_id': chat_id,
                      'text': '⚠️ 쿠팡 서플라이허브 자동 로그인 실패\n'
                              '브라우저 창에서 직접 로그인해주세요 (10분 이내).\n'
                              '로그인 안 하셔도 됩니다 — 1시간 후 자동 재시도됩니다.'},
                timeout=5,
            )
    except Exception:
        pass

    for _ in range(600):
        await page.wait_for_timeout(1000)
        url = page.url
        if ('supplier.coupang.com' in url and
                'login' not in url.lower() and 'signin' not in url.lower()):
            print('  [쿠팡] 수동 로그인 성공')
            return True

    print('  [쿠팡] ⏰ 로그인 시간 초과 — 오늘 쿠팡 수집 건너뜀')
    return False


async def _fetch(start_date: str, end_date: str) -> list[dict]:
    cb = _get_browser_mod()

    # 브라우저 기동 확인 (꺼져 있으면 자동 켬)
    if not cb.ensure_browser():
        print('  [쿠팡] ❌ 브라우저 기동 실패 — 수집 건너뜀')
        return []

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f'http://127.0.0.1:{cb.PORT}')
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        try:
            print(f'  [쿠팡] 세션 확인 중...')
            await page.goto(INBOUND_URL, wait_until='domcontentloaded', timeout=20000)
            await page.wait_for_timeout(3000)

            url = page.url
            if 'login' in url.lower() or 'signin' in url.lower():
                print('  [쿠팡] 세션 만료 - 재로그인')
                ok = await _login(page)
                if not ok:
                    return []
                # 로그인 후 목표 URL로 재이동
                await page.goto(INBOUND_URL, wait_until='domcontentloaded', timeout=20000)
                await page.wait_for_timeout(3000)

            print(f'  [쿠팡] 입고상세내역 조회: {start_date} ~ {end_date}')
            all_rows: list[dict] = []
            seen_keys: set = set()

            for page_num in range(1, 31):
                paged_url = (
                    f"{INBOUND_URL}?page={page_num}"
                    f"&totalOrderPrice=0&totalUnitPrice=0&totalVatPrice=0&totalCount=0"
                    f"&startDate={start_date}&endDate={end_date}"
                    f"&requestSeq=&vendorPaymentInfoSeq="
                )
                await page.goto(paged_url, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(3000)

                print(f'  [쿠팡] 페이지 {page_num} 파싱...')
                try:
                    await page.wait_for_selector('table tbody tr', timeout=10000)
                except Exception:
                    pass
                await page.wait_for_timeout(1000)
                page_rows = await _parse_table(page)
                print(f'  [쿠팡] 페이지 {page_num}: {len(page_rows)}건')

                if not page_rows:
                    break

                new_count = 0
                for r in page_rows:
                    key = f"{r['div']}|{r['no']}|{r['sku_id']}|{r['datetime']}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_rows.append(r)
                        new_count += 1

                if new_count == 0:
                    break

            print(f'  [쿠팡] 수집 완료: 총 {len(all_rows)}건')

            # 같은 브라우저 세션으로 RPD(기본 물류 지표) 수집
            try:
                from modules.coupang_rpd import collect_rpd_in_session
                await collect_rpd_in_session(page, context)
            except Exception as _rpd_e:
                print(f'  [RPD] 오류: {_rpd_e}')
                try:
                    from modules.telegram_alert import send_alert
                    send_alert(
                        f'⚠️ [RPD 수집 오류] 쿠팡 로켓 판매량 데이터 수집 실패\n'
                        f'{type(_rpd_e).__name__}: {str(_rpd_e)[:200]}\n'
                        f'(식약처 이력추적 등록은 정상 진행됨)'
                    )
                except Exception:
                    pass

            # 같은 브라우저 세션으로 정산-일반매입계정 수집
            try:
                from modules.coupang_settlement import collect_settlement_in_session
                await collect_settlement_in_session(page, context)
            except Exception as _settle_e:
                print(f'  [정산] 오류: {_settle_e}')
                try:
                    from modules.telegram_alert import send_alert
                    send_alert(
                        f'⚠️ [정산 수집 오류] 쿠팡 정산-일반매입계정 수집 실패\n'
                        f'{type(_settle_e).__name__}: {str(_settle_e)[:200]}\n'
                        f'(식약처 이력추적 등록은 정상 진행됨)'
                    )
                except Exception:
                    pass

            return all_rows

        except Exception as e:
            print(f'  [쿠팡] 오류: {e}')
            import traceback
            traceback.print_exc()
            return []
        finally:
            await page.close()  # 탭만 닫음 — Chrome 창 유지


async def _parse_table(page) -> list[dict]:
    """입고상세내역 테이블 파싱 (전체 컬럼, 구분 무관 — 발주/반출/조정 등 모든 행)"""
    data = await page.evaluate("""
        () => {
            const results = [];
            let rows = document.querySelectorAll('table tbody tr');
            if (!rows.length) rows = document.querySelectorAll('table tr');

            for (const row of rows) {
                const cells = row.querySelectorAll('td');
                if (cells.length < 8) continue;

                const get = (i) => cells[i] ? cells[i].textContent.trim() : '';

                // 컬럼: 구분(0) 번호(1) SKU번호(2) SKU명(3) 입고/반출일자(4) 물류센터(5)
                //       세금타입(6) 수량(7) 단가(8) 공급가액(9) 세액(10)
                //       총단가(11) 총공급가액(12) 총세액(13) 계산서번호(14) 지급일(15)
                const skuName = get(3);
                if (!skuName) continue;

                const dateRaw = get(4);
                const dateMatch = dateRaw.match(/\\d{4}-\\d{2}-\\d{2}/);
                const date = dateMatch ? dateMatch[0] : dateRaw;
                const qty  = parseInt(get(7).replace(/,/g, '')) || 0;

                results.push({
                    div: get(0), no: get(1), sku_id: get(2), sku_name: skuName, date: date,
                    datetime: dateRaw,
                    center: get(5), tax_type: get(6), qty: qty,
                    unit_price: get(8), supply_amt: get(9), tax_amt: get(10),
                    total_unit_price: get(11), total_supply_amt: get(12), total_tax_amt: get(13),
                    invoice_no: get(14), pay_date: get(15),
                });
            }
            return results;
        }
    """)
    return data


def _filter_orders(full_rows: list[dict]) -> list[dict]:
    """식약처 이력추적용 — '발주' 행 + 수량>0만 추출 (sku_id, sku_name, date, qty)"""
    return [
        {'sku_id': r['sku_id'], 'sku_name': r['sku_name'], 'date': r['date'], 'qty': r['qty']}
        for r in full_rows
        if r.get('div') == '발주' and r.get('qty', 0) > 0 and r.get('date')
    ]


def get_coupang_inbound(target_date: str) -> list[dict]:
    """식약처 이력추적용 — 발주 행만 (sku_id, sku_name, date, qty)"""
    return _filter_orders(asyncio.run(_fetch(target_date, target_date)))


def get_coupang_inbound_range(start_date: str, end_date: str) -> list[dict]:
    """식약처 이력추적용 — 발주 행만 (sku_id, sku_name, date, qty)"""
    return _filter_orders(asyncio.run(_fetch(start_date, end_date)))


def get_coupang_full(target_date: str) -> list[dict]:
    """전체 컬럼 원본 데이터 (구분 무관) — '쿠팡 로켓 입고상세내역' 탭 업로드용"""
    return asyncio.run(_fetch(target_date, target_date))


def get_coupang_full_range(start_date: str, end_date: str) -> list[dict]:
    """전체 컬럼 원본 데이터 (구분 무관) — '쿠팡 로켓 입고상세내역' 탭 업로드용"""
    return asyncio.run(_fetch(start_date, end_date))
