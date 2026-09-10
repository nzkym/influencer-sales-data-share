# -*- coding: utf-8 -*-
"""
쿠팡 서플라이허브 브라우저 자동 기동 헬퍼 (포트 9234)

실제 Chrome + CDP 방식 — Playwright headless 대신 상주 Chrome을 재사용해
Akamai WAF 봇 차단 위험을 최소화한다. 작업 후 브라우저를 닫지 않고 유지해
다음 날 같은 창을 재사용하므로 세션이 장기간 유지된다.

사용 예:
    from coupang_browser import ensure_browser, PORT

    ensure_browser()
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        context = browser.contexts[0]
        page = await context.new_page()
        # ... 작업 ...
        await page.close()   # 탭만 닫음
        # browser.close() 호출 금지 — Chrome 창이 유지되어야 세션이 살아있음
"""
import ctypes
import subprocess
import sys
import time
import urllib.request

if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT    = 9234
PROFILE = r"C:\클로드프로그램_C드라이브\쿠팡서플라이허브\chrome_profile"
URL     = "https://supplier.coupang.com"
PYTHON  = r"C:\Users\Admin\AppData\Local\Python\bin\python.exe"
SAFE_LAUNCHER = (
    r"D:\드롭박스\TeamK Dropbox\DS Anderson\프로그램\클로드 프로그램"
    r"\크롬_탭_자동정리\크롬_안전실행.py"
)


def is_alive(timeout: int = 3) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=timeout)
        return True
    except Exception:
        return False


def ensure_browser(wait_sec: int = 40, verbose: bool = True) -> bool:
    """
    서플라이허브 Chrome이 떠 있도록 보장한다. 이미 떠 있으면 아무것도 안 함(멱등).
    Returns: 기동 성공 여부
    """
    if is_alive():
        if verbose:
            print(f"  [쿠팡브라우저] 포트 {PORT} 이미 실행 중")
        return True

    if verbose:
        print(f"  [쿠팡브라우저] 포트 {PORT} 꺼져 있음 — 자동 기동 중...")

    subprocess.Popen(
        [PYTHON, SAFE_LAUNCHER, str(PORT), PROFILE, URL],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    for _ in range(wait_sec):
        time.sleep(1)
        if is_alive():
            time.sleep(4)
            if verbose:
                print("  [쿠팡브라우저] 기동 완료")
            return True

    if verbose:
        print(f"  [쿠팡브라우저] ❌ {wait_sec}초 기다렸으나 기동 실패")
    return False


def close_browser(verbose: bool = True) -> bool:
    """작업 끝난 뒤 Chrome을 정상 종료한다(로그인 쿠키는 프로필에 보존됨)."""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        ).stdout
        pid = None
        for line in out.splitlines():
            if f":{PORT}" in line and "LISTENING" in line:
                pid = int(line.split()[-1])
                break
        if not pid:
            if verbose:
                print("  [쿠팡브라우저] 이미 꺼져 있음")
            return True

        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        hwnds: list = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.POINTER(ctypes.c_int))
        def cb(hwnd, lparam):
            p = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(hwnd):
                hwnds.append(hwnd)
            return True

        user32.EnumWindows(cb, None)
        for h in hwnds:
            user32.PostMessageW(h, WM_CLOSE, 0, 0)

        for _ in range(10):
            time.sleep(1)
            if not is_alive():
                if verbose:
                    print("  [쿠팡브라우저] 정상 종료 완료 (쿠키/세션 유지됨)")
                return True
        if verbose:
            print("  [쿠팡브라우저] 종료 요청했으나 아직 살아있음")
        return False
    except Exception as e:
        if verbose:
            print(f"  [쿠팡브라우저] 종료 중 오류: {e}")
        return False


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "open"
    if action == "close":
        close_browser()
    else:
        ensure_browser()
