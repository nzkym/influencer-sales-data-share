"""네이버 로그인 자동화 (Selenium). 정담건강/뉴트원/뉴트펫은 같은 계정(위임관리)이라
독립된 크롬 프로세스("지속 브라우저") 하나에 로그인을 1회만 해두고, 이후로는 그 창에
접속해 스토어 전환(work.channel-select)만 하며 재사용한다 (2026-07-20 아키텍처 확정 —
매번 새 프로필에 쿠키를 이식하던 방식은 네이버가 "새 기기 로그인"으로 의심해 강제
로그아웃시키는 문제가 있었음)."""
import json
import time
import logging
import subprocess
import urllib.request
from pathlib import Path

import psutil

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from webdriver_manager.chrome import ChromeDriverManager

import config
import telegram_notify
import alert_throttle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_DIR / "naver_login.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

SELLER_CENTER_URL = "https://sell.smartstore.naver.com/"

# 정담건강/뉴트원/뉴트펫은 전부 같은 네이버 계정(위임관리)이라 로그인은 1번만 하면 되고,
# 판매자센터 우측 상단 "스토어 이동" 메뉴로 스토어만 전환하면 된다 (2026-07-20 실사용자 DOM 캡처로 확인).
STORE_DISPLAY_NAMES = {
    "NUTONE": "뉴트원",
    "JDHEALTH": "정담건강",
    "NUTPET": "뉴트펫",
}
LOGIN_URL = (
    "https://accounts.commerce.naver.com/login"
    "?url=https%3A%2F%2Fsell.smartstore.naver.com%2F%23%2Flogin-callback"
)
STORE_SWITCH_TRIGGER = 'a[data-action-location-id="selectStore"]'
STORE_SWITCH_ROW_XPATH_TMPL = (
    "//div[@class='seller-input']"
    "[.//p[contains(@class,'store-text-area')][contains(normalize-space(.), '{name}')]]"
    "//label"
)


# ── 사람이 직접 로그인하는 동안 창을 절대 자동으로 닫지 않는 "지속 브라우저" 모드 ──
# 일반 create_driver()는 Selenium이 프로세스를 직접 띄우고 관리해서, 스크립트가 끝나면
# (타임아웃 포함) 창도 같이 닫힌다. 로그인은 사람 속도로 진행되니, 여기서는 크롬을
# 완전히 독립된 프로세스로 띄워놓고, 이후 여러 번에 걸쳐 그 창에 "접속"만 해서
# 상태를 확인/조작한다 — 접속한 driver를 quit()해도 크롬 자체는 안 닫힌다.
_DEBUG_PORT = 9333
_PROFILE_DIR = None  # 아래에서 config.DATA_DIR 기준으로 지연 설정
_CHROMEDRIVER_PATH = None  # 실행 중 1회만 해석해서 재사용(_chromedriver_path 참고)


def _profile_dir() -> Path:
    global _PROFILE_DIR
    if _PROFILE_DIR is None:
        # 2026-08-14: Dropbox 동기화 부하 문제(카카오/플로우/업체전반정보수집과 동일 패턴,
        # feedback_dropbox_ram_bloat 참고)로 크롬 프로필을 C드라이브로 이동함(4.3GB, 파일 1398개
        # - 캐시/서비스워커 등이 계속 바뀌어서 Dropbox가 끊임없이 재동기화하던 게 원인).
        # 다른 컴퓨터에는 이 폴더가 없음 - 로그인 세션 캐시일 뿐이라 원래도 동기화 불필요.
        _PROFILE_DIR = Path(r"C:\클로드프로그램_C드라이브\naver_review_reply\chrome_profile")
        _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return _PROFILE_DIR


def _find_chrome_exe() -> str | None:
    # 시스템 크롬은 계속 자동 업데이트되는데 chromedriver(자동화용 드라이버)는 항상
    # 최신 크롬 버전을 바로 못 따라가서(2026-07-27 확인 - 크롬 150.0.7871.182로
    # 업데이트됐는데 구글이 그 버전에 맞는 chromedriver를 아직 안 냄), 이 버전 차이 때문에
    # 클릭/타이핑이 에러 없이 조용히 안 먹는 현상이 반복됐다(로그인 버튼, 리뷰 검색창
    # 둘 다에서 확인됨 - 여러 우회 방법 다 실패했었음).
    # 그래서 자동화 전용으로 "Chrome for Testing"(구글이 chromedriver와 정확히 버전을
    # 맞춰서 배포하는 전용 바이너리)을 별도로 받아뒀다 - 시스템 크롬이 알아서
    # 업데이트되든 말든 이 바이너리는 안 바뀌므로 앞으로 이 문제가 재발하지 않는다.
    dedicated = config.DATA_DIR / "chrome_for_testing" / "chrome-win64" / "chrome.exe"
    if dedicated.exists():
        return str(dedicated)
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).exists():
            return p
    return None


def _debug_port_alive() -> bool:
    try:
        # timeout=1은 브라우저가 순간적으로 바쁠 때(페이지 로딩 중 등) 오탐(false
        # negative)을 낼 수 있어(2026-08-14 실측 확인 - 이게 원인으로 의심되는 정황:
        # 같은 프로필 디렉터리에 크롬 프로세스 3개가 동시에 떠 있던 사고 발견) 3초로
        # 넉넉히 늘림.
        urllib.request.urlopen(f"http://127.0.0.1:{_DEBUG_PORT}/json/version", timeout=3)
        return True
    except Exception:
        return False


def _kill_stale_chrome_instances():
    """이 프로필(_profile_dir())을 쓰는 chrome.exe 프로세스가 남아있으면 전부 정리한다.
    (2026-08-14 도입) - `_debug_port_alive()`가 False라고 판단해서 새로 띄우려는
    시점에, 예전에 뜬 인스턴스가 완전히 죽지는 않고 디버그포트만 응답을 멈춘 채로
    떠 있는 경우가 있었다(정확한 재현 조건은 못 밝혔지만, 실제로 같은 프로필을 쓰는
    chrome.exe가 3개나 동시에 존재하는 사고가 있었음 - 서로 자원을 다투며 자동화가
    아무 진행 없이 멈추는 원인이 됨). 새 인스턴스를 띄우기 직전에 이 프로필을 쓰는
    기존 프로세스를 전부 강제 종료해서, 항상 정확히 하나만 존재하도록 보장한다."""
    profile_dir_str = str(_profile_dir())
    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info["name"] or "").lower() != "chrome.exe":
                continue
            cmdline = proc.info.get("cmdline") or []
            if any(profile_dir_str in arg for arg in cmdline):
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed:
        log.info(f"[cleanup] 이 프로필을 쓰던 잔여 chrome.exe {killed}개 정리함")
        time.sleep(1)


def ensure_persistent_browser_open(start_url: str = SELLER_CENTER_URL):
    """독립 프로세스로 크롬을 띄운다(이미 떠있으면 그대로 재사용). 이 함수가 띄운 창은
    attach_persistent_driver()로 여러 번 접속/조작해도 절대 자동으로 닫히지 않는다."""
    if _debug_port_alive():
        return
    _kill_stale_chrome_instances()
    chrome_exe = _find_chrome_exe()
    if not chrome_exe:
        raise RuntimeError("크롬 실행파일을 찾을 수 없습니다")
    subprocess.Popen(
        [
            chrome_exe,
            f"--remote-debugging-port={_DEBUG_PORT}",
            f"--user-data-dir={_profile_dir()}",
            "--no-first-run",
            "--no-default-browser-check",
            # 프로세스 재시작(taskkill)이 크롬 입장에선 "비정상 종료"라 매번 켤 때마다
            # "페이지를 복원하시겠습니까?" 팝업이 뜬다. 자동화를 막진 않지만(네이버
            # 페이지 요소가 아니라 크롬 자체 UI) 재발 방지용으로 비활성화한다(2026-07-22).
            "--disable-session-crashed-bubble",
            "--disable-infobars",
            start_url,
        ],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    for _ in range(20):
        if _debug_port_alive():
            break
        time.sleep(0.5)


def _running_browser_version() -> str | None:
    """지금 디버그포트에 실제로 떠 있는 크롬의 버전을 읽는다("150.0.7871.124" 형식)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{_DEBUG_PORT}/json/version", timeout=3) as resp:
            browser = json.loads(resp.read().decode("utf-8")).get("Browser", "")
        # 예: "Chrome/150.0.7871.124"
        if "/" in browser:
            return browser.split("/", 1)[1].strip() or None
    except Exception:
        pass
    return None


def _dedicated_chrome_version() -> str | None:
    """Chrome for Testing 폴더에 같이 풀리는 '<버전>.manifest' 파일명에서 버전을 읽는다
    (브라우저가 아직 안 떠 있어서 디버그포트를 못 읽을 때의 대비책)."""
    folder = config.DATA_DIR / "chrome_for_testing" / "chrome-win64"
    for manifest in folder.glob("*.manifest"):
        stem = manifest.stem
        if stem and stem[0].isdigit():
            return stem
    return None


def _chromedriver_path() -> str:
    """**반드시 지금 붙으려는 그 브라우저의 버전에 맞는** chromedriver 경로를 반환한다.

    2026-09-02 장애 원인이 여기였다: 자동화용 브라우저는 버전이 고정된
    "Chrome for Testing"(150.0.7871.124)인데, chromedriver는 `ChromeDriverManager()`가
    **시스템에 설치된 크롬**(자동 업데이트로 151.0.7922.174가 됨)을 기준으로 고르고
    있었다. 그래서 151용 chromedriver로 150 브라우저에 붙으려다
    "session not created: cannot connect to chrome at 127.0.0.1:9333 / This version of
    ChromeDriver only supports Chrome version 151"로 매일 밤 전 스토어 수집이 실패했다
    (08-31까진 시스템 크롬도 150이라 우연히 맞아서 정상 동작했던 것).
    브라우저 버전을 고정해둔 의미가 살도록 드라이버 버전도 그 브라우저에 맞춰 고정한다."""
    global _CHROMEDRIVER_PATH
    if _CHROMEDRIVER_PATH:
        return _CHROMEDRIVER_PATH
    version = _running_browser_version() or _dedicated_chrome_version()
    if version:
        try:
            _CHROMEDRIVER_PATH = ChromeDriverManager(driver_version=version).install()
            return _CHROMEDRIVER_PATH
        except Exception as e:
            # 구글이 그 버전 드라이버를 아직/더는 안 내주는 등 예외 상황 - 자동 선택으로
            # 폴백하되, 버전이 어긋나면 붙기 자체가 실패하므로 로그에 명확히 남긴다.
            log.error(f"[chromedriver] 브라우저 버전({version})에 맞는 드라이버 확보 실패 - 자동 선택으로 폴백: {e}")
    else:
        log.error("[chromedriver] 브라우저 버전을 확인하지 못함 - 자동 선택으로 폴백")
    _CHROMEDRIVER_PATH = ChromeDriverManager().install()
    return _CHROMEDRIVER_PATH


def attach_persistent_driver() -> webdriver.Chrome:
    """이미 열려있는(또는 방금 새로 연) 지속 브라우저에 접속만 한다.
    이 driver를 quit()해도 크롬 창 자체는 닫히지 않는다(접속만 끊길 뿐)."""
    ensure_persistent_browser_open()
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{_DEBUG_PORT}")
    service = Service(_chromedriver_path())
    return webdriver.Chrome(service=service, options=options)


def check_logged_in(driver) -> bool:
    driver.get(SELLER_CENTER_URL)
    time.sleep(3)
    if "nidlogin" in driver.current_url or "nid.naver.com" in driver.current_url:
        return False
    try:
        # 로그아웃 상태의 첫 화면(로그인하기/가입하기 버튼)도 "smartstore.naver.com"
        # 도메인이라 그것만으론 구분 안 됨 — 로그인 후에만 "#/"로 시작하는 SPA 라우트로
        # 이동하므로 이걸로 판정한다 (2026-07-20 오탐 발견 후 수정).
        WebDriverWait(driver, 10).until(lambda d: "#/" in d.current_url)
        return True
    except TimeoutException:
        return False


def _click(driver, element):
    """항상 JS로 클릭 이벤트를 직접 발생시킨다(2026-07-27 재작성).
    네이티브(좌표 기반) 클릭이 겹친 모달/네비바에 가로막히는 경우(element click
    intercepted)뿐 아니라, **에러 없이 "성공"한 것처럼 보이는데 실제로는 아무 반응이
    없는 경우**가 오늘 여러 곳(로그인 버튼, 스토어 전환 트리거)에서 반복 확인됐다
    (크롬이 chromedriver보다 앞선 패치버전으로 업데이트되면서 생긴 것으로 추정,
    2026-07-27). 후자는 예외가 안 나서 기존의 "예외 시에만 JS 폴백" 방식으론 못
    잡았다 - 그래서 아예 항상 JS 클릭을 우선 사용하도록 바꿈. 네이티브 클릭이 필요한
    특수한 경우(예: 실제 마우스 좌표에 의존하는 드래그 등)는 이 프로젝트에 없다."""
    driver.execute_script("arguments[0].click();", element)


def dismiss_notice_popup(driver):
    """.seller-layer-modal(스토어 선택 모달이든 "스마트스토어센터 공지" 팝업이든 같은
    부트스트랩 모달 틀을 씀)이 열려있으면 닫는다.
    2026-07-20 확인: "공지" 팝업은 페이지를 새로고침할 때마다 계속 다시 뜨는데, 그때마다
    스토어 전환 트리거를 가로막아서 스토어 전환이 계속 실패하는 근본 원인이었다. 그냥
    닫기만 하면 다음 새로고침 때 또 뜨므로, "하루동안 보지 않기" 체크박스를 먼저 눌러서
    아예 다시 안 뜨게 만든다."""
    # 한 번 뜬 모달은 닫혀도 DOM에서 실제로 제거되지 않고 그냥 안 보이게(aria-hidden)만
    # 되는 경우가 많다(2026-07-27 확인 - 하루 세션 동안 안 보이는 잔여 모달이 10개 넘게
    # 쌓여있었음). find_element(첫 매치)가 이런 숨겨진 잔여 모달을 집으면, 실제로는
    # 화면을 막는 게 아무것도 없는데도 element_to_be_clickable이 타임아웃돼 "팝업 닫기
    # 실패" 오탐이 계속 나는 원인이었다 - 반드시 실제로 보이는 것만 대상으로 한다.
    visible_modals = [m for m in driver.find_elements(By.CSS_SELECTOR, ".seller-layer-modal") if m.is_displayed()]
    if not visible_modals:
        return
    modal = visible_modals[0]
    try:
        # 체크박스 자체는 커스텀 스타일 때문에 안 보이는 요소라(라디오 버튼과 같은 패턴),
        # 감싸고 있는 label을 클릭해야 한다. 이것도 반드시 이 모달 안에서만 찾는다 -
        # 문서 전체에서 찾으면 위와 같은 이유로 다른(숨겨진) 모달의 것을 집을 수 있다.
        again_label = modal.find_element(By.XPATH, './/input[@name="again"]/ancestor::label[1]')
        _click(driver, again_label)
    except NoSuchElementException:
        pass
    try:
        close_btn = modal.find_element(By.CSS_SELECTOR, "button.close")
        _click(driver, close_btn)
        time.sleep(1)
    except Exception as e:
        log.error(f"팝업 닫기 실패: {e}")

    # 주의(2026-07-20): 여기서 안 닫히면 페이지를 새로고침하는 방식도 시도했었는데,
    # "판매자센터 루트 URL로 새로고침하면 항상 기본 스토어(정담건강)로 초기화된다"는 걸
    # 나중에 알게 되어 오히려 스토어 전환을 계속 되돌리는 원인이 됐다. 그래서 이 새로고침
    # 폴백은 완전히 제거함 - 안 닫히면 그냥 다음 단계에서 실패하고 재시도하도록 둔다.


def current_store_name(driver) -> str | None:
    """`span.shop`(ng-bind="::vm.loginInfo.channelName")은 Angular가 현재 로그인된
    채널명에 직접 바인딩해둔 요소라 이걸로 현재 스토어를 정확히 판정한다.
    **주의(2026-07-20 심각한 버그 발견 후 수정)**: 이전엔 "화면 아무 곳에나 그 스토어명
    텍스트가 있으면 그 스토어"로 판정했는데, 스토어 전환 팝업이 열려있으면 그 안에
    3개 스토어명이 전부 나열돼 있어서 실제 활성 스토어와 무관하게 항상 오탐이 났었다 —
    이 때문에 실제로는 정담건강 화면인데 뉴트원으로 전환됐다고 오판하고 정담건강 리뷰를
    뉴트원 것으로 잘못 라벨링해 시트에 등록한 사고가 있었음."""
    for _ in range(6):
        els = driver.find_elements(By.CSS_SELECTOR, "span.shop")
        if els:
            text = els[0].text.strip()
            for store, name in STORE_DISPLAY_NAMES.items():
                if text == name:
                    return store
        time.sleep(0.5)
    return None


def switch_store(driver, store: str) -> bool:
    """판매자센터 우측 상단 "스토어 이동" 메뉴로 store로 전환한다.
    이미 그 스토어가 활성 상태면 모달을 열지 않고 바로 성공 처리한다."""
    display_name = STORE_DISPLAY_NAMES.get(store)
    if not display_name:
        log.error(f"알 수 없는 스토어: {store}")
        return False
    if current_store_name(driver) == store:
        return True
    dismiss_notice_popup(driver)
    time.sleep(2)  # 팝업 닫기 애니메이션(fade-out)이 끝날 때까지 대기 - 안 하면 트리거 클릭이 가로막힘
    try:
        trigger = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, STORE_SWITCH_TRIGGER))
        )
        _click(driver, trigger)
        # 모달이 열리는 애니메이션/렌더링 시간을 넉넉히 준다 - 너무 빨리 다음 단계로
        # 넘어가는 게 계속된 실패의 실제 원인이었다(2026-07-20 확인, 여유시간을 늘려서 해결).
        time.sleep(3)

        # 스토어 선택 모달의 페이드인 애니메이션이 이 자동화 환경에서는 끝까지
        # 완료되지 않아 opacity가 계속 0으로 남아있는 경우가 있었다(2026-07-30 실측
        # 확인 - display:block인데 getComputedStyle().opacity가 0으로 고정됨).
        # element_to_be_clickable은 가시성(opacity 포함)을 요구해서 이 상태에서
        # 영원히 타임아웃났다 - opacity 0인 채로 JS 강제클릭하면 실제로는 정상
        # 작동함을 확인했으므로, 존재 여부만 기다리고 클릭은 _click()의 JS클릭에 맡긴다.
        xpath = STORE_SWITCH_ROW_XPATH_TMPL.format(name=display_name)
        radio = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
        time.sleep(3)
        _click(driver, radio)
        time.sleep(3)
        dismiss_notice_popup(driver)
        return True
    except Exception as e:
        # ElementClickInterceptedException 등 (TimeoutException, NoSuchElementException) 외의
        # 예외도 있을 수 있어 전부 잡는다. 실패해도 모달을 정리해야 하는데, 안 그러면 남은
        # 모달이 다음 시도의 트리거 클릭을 가로막아 연쇄로 계속 실패한다(2026-07-20 확인 -
        # 이게 오늘 밤 스토어 전환이 계속 안 됐던 진짜 원인이었음).
        log.error(f"[{store}] 스토어 전환 실패: {e}")
        dismiss_notice_popup(driver)
        return False


def get_cookie_string(store: str) -> str | None:
    """지속 브라우저를 해당 store로 전환한 뒤 실시간 쿠키를 HTTP 요청용 문자열로 변환한다.
    예전엔 파일에 저장해두고 재사용했는데, 로그인 방식이 지속 브라우저로 바뀌면서
    (2026-07-20) 그 파일이 더 이상 자동 갱신되지 않아 폐기함 - 매번 실시간으로 가져온다.

    2026-08-12 수정: 예전엔 여기서 직접 switch_store()를 3번만 재시도하고 끝나는
    약한 로직을 썼는데, get_authenticated_driver()가 이미 가진 "재시도→진짜 로그아웃
    확인→필요시 재로그인→alert_throttle 실패 기록"까지 포함한 강한 로직을 안 쓰고
    있었다. 그 결과 poster.py 쪽(get_authenticated_driver 사용)은 스토어 전환
    실패에서도 잘 복구되는데, run_draft.py의 수집 단계(이 함수 경유)만 3번 실패하면
    조용히 포기하고 알림도 안 남기는 사각지대가 있었다(실측: NUTONE이 08-11~08-12
    이틀간 이 경로로 수집 자체가 안 됐는데 "정상 완료"로 잘못 보고됨). 이제 같은
    강한 로직을 재사용한다."""
    driver = get_authenticated_driver(store, headless=False)
    if driver is None:
        return None
    try:
        cookies = driver.get_cookies()
        return "; ".join(f"{c['name']}={c['value']}" for c in cookies if "name" in c and "value" in c)
    except Exception as e:
        log.error(f"[{store}] 쿠키 문자열 변환 실패: {e}")
        return None


def refresh_cookies(store: str) -> bool:
    """세션이 살아있는지 재확인한다."""
    return get_authenticated_driver(store, headless=False) is not None


def _id_pw_login(driver, store: str) -> bool:
    """저장된 계정으로 자동 로그인 시도(2026-07-22 추가, 2026-07-27 재작성).
    예전엔 세션이 완전히 끊기면 무조건 텔레그램으로 사람 개입을 요청했는데, 사장님이
    "쿠키/세션으로 실패하면 아이디/비번으로 직접 접속하라"고 요청해서 추가함 —
    이 컴퓨터는 매번 로그인해오던 신뢰된 기기라 캡차/2차인증 없이 될 가능성이 높음.

    2026-07-23~27에 "저장된 계정 카드" 클릭 방식(Naver 자체 UI)을 우선 시도했었는데,
    가끔 클릭이 그냥 무시되고(에러도 없이 URL이 안 바뀜) 실패하는 게 반복됐다.
    사용자가 직접 로그인할 때는 그냥 (브라우저 자체 비밀번호 관리자가 이미 채워둔)
    아이디/비번 입력폼에서 "로그인" 버튼만 눌러서 바로 됐음 - 이 방식이 훨씬 단순하고
    검증됐으므로, 저장된 계정 카드 클릭은 완전히 제거하고 이 방식만 쓴다."""
    login_id = config.store_env(store, "NAVER_LOGIN_ID")
    login_pw = config.store_env(store, "NAVER_LOGIN_PW")
    try:
        driver.get(LOGIN_URL)
        time.sleep(3)
        id_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='text']"))
        )
        pw_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")

        # 브라우저 자체 비밀번호 관리자가 이미 채워둔 경우가 많다(2026-07-27 확인 -
        # 사용자가 직접 로그인할 때도 아무것도 입력 안 하고 로그인 버튼만 눌러서 됐음).
        # 이미 채워져 있으면 건드리지 않는다 - 지우고 다시 입력하려다 이중입력 등
        # 여러 사고가 있었다(2026-07-22, 07-23).
        if not id_field.get_attribute("value") or not pw_field.get_attribute("value"):
            if not login_id or not login_pw:
                log.error(f"[{store}] .env에 NAVER_LOGIN_ID/PW가 없어 자동 로그인 불가")
                return False
            _click(driver, id_field)
            id_field.send_keys(Keys.CONTROL, "a")
            id_field.send_keys(Keys.DELETE)
            id_field.send_keys(login_id)
            _click(driver, pw_field)
            pw_field.send_keys(Keys.CONTROL, "a")
            pw_field.send_keys(Keys.DELETE)
            pw_field.send_keys(login_pw)
            # 이 폼이 React 등 프레임워크의 controlled input이면 DOM value는 바뀌어도
            # 프레임워크 내부 상태(그래서 "로그인" 버튼이 활성화되는지 여부)는 실제
            # input 이벤트를 받아야 갱신될 수 있다 - send_keys가 키 이벤트를 보내긴
            # 하지만 혹시 놓쳤을 경우를 대비해 명시적으로 한 번 더 발생시킨다
            # (2026-07-27 - 클릭은 되는데 로그인이 안 되는 원인 후보로 추가).
            driver.execute_script(
                "for (const el of arguments) {"
                "  el.dispatchEvent(new Event('input', {bubbles: true}));"
                "  el.dispatchEvent(new Event('change', {bubbles: true}));"
                "}",
                id_field, pw_field,
            )
            time.sleep(1)
            # 무인 서버 환경에서는 크롬 창이 OS상 실제 포커스가 없어 native send_keys가
            # 예외 없이 그냥 아무 효과 없이 조용히 실패하는 경우가 확인됐다(2026-07-27,
            # poster.py 검색창에서 동일 증상 확인 - JS로 값을 강제로 넣으면 항상 성공함).
            # 그래서 실제 값이 반영됐는지 확인하고, 안 됐으면 JS로 강제 입력한다.
            if id_field.get_attribute("value") != login_id or pw_field.get_attribute("value") != login_pw:
                log.info(f"[{store}] 아이디/비번 native 입력이 반영 안 됨 - JS로 강제 입력")
                driver.execute_script(
                    """
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                    const [idEl, pwEl, idVal, pwVal] = arguments;
                    setter.call(idEl, idVal);
                    idEl.dispatchEvent(new Event('input', {bubbles: true}));
                    idEl.dispatchEvent(new Event('change', {bubbles: true}));
                    setter.call(pwEl, pwVal);
                    pwEl.dispatchEvent(new Event('input', {bubbles: true}));
                    pwEl.dispatchEvent(new Event('change', {bubbles: true}));
                    """,
                    id_field, pw_field, login_id, login_pw,
                )
                time.sleep(1)

        # Enter키 제출 대신 "로그인" 버튼을 직접 찾아서 클릭한다 - Enter 제출이 이 폼에서
        # 가끔 안 먹는 경우가 있었다(2026-07-27 확인). 이 폼은 <form> 태그로 감싸져
        # 있지 않아서(2026-07-27 확인 - ancestor::form 검색이 못 찾음) 그냥 페이지
        # 전체에서 정확히 "로그인"인 버튼을 찾는다 - "네이버 아이디로 로그인" 같은 탭
        # 전환 버튼은 텍스트가 두 줄이라 normalize-space가 정확히 "로그인"과 다름.
        # 주의(2026-07-27 확인): 버튼 텍스트가 자식 <span>에 들어있어서 XPath의 text()는
        # 못 잡는다(직계 텍스트 노드만 봄) - normalize-space(.)로 전체 문자열을 봐야 한다.
        login_btn = driver.find_element(By.XPATH, "//button[normalize-space(.)='로그인']")
        # 버튼이 비활성 상태면 클릭 자체는 에러 없이 "성공"하지만 아무 일도 안 일어난다 -
        # 다음에 또 원인 불명으로 실패하면 이 로그로 "비활성 버튼을 눌렀다" 가능성을
        # 구분할 수 있게 남겨둔다(2026-07-27).
        log.info(f"[{store}] 로그인 버튼 상태 - enabled: {login_btn.is_enabled()}, disabled속성: {login_btn.get_attribute('disabled')}")
        _click(driver, login_btn)
        time.sleep(4)
        if "#/" in driver.current_url:
            log.info(f"[{store}] 아이디/비번 자동 로그인 성공")
            return True
        log.error(f"[{store}] 아이디/비번 로그인 후에도 대시보드로 이동 안 됨(캡차/2차인증 가능성) - url={driver.current_url}")
        return False
    except Exception as e:
        log.error(f"[{store}] 아이디/비번 자동 로그인 실패: {e}")
        return False


def get_authenticated_driver(store: str, headless: bool = True):
    """스토어별 인증된 driver 반환. 실패 시 None + 텔레그램 알림.

    쿠키를 매번 새 브라우저 프로필(임시 프로필)에 이식하는 방식은 네이버 쪽에서
    "새 기기 로그인"으로 의심받아 강제 로그아웃/보안알림이 뜨는 문제가 있었다
    (2026-07-20 실사용 중 확인). 그래서 항상 하나의 지속 프로필(_profile_dir())을
    재사용해 매번 같은 "기기"로 보이도록 한다. 이 프로필은 항상 화면이 보인다
    (headless 강제 안 됨 — 지속 프로필은 headless 모드와 궁합이 안 좋음)."""
    driver = attach_persistent_driver()
    try:
        # 스토어 전환 UI(트리거/모달)는 대시보드에서 가장 안정적으로 동작한다 - 다른 페이지에서
        # 호출하면 그 페이지의 다른 요소가 클릭을 가로막는 경우가 있었다(2026-07-20 확인).
        driver.get(SELLER_CENTER_URL)
        time.sleep(4)
        # 주의(2026-07-20 근본 원인 확정): 여기서 switch_store() 성공 직후 check_logged_in()을
        # 불렀었는데, check_logged_in()이 내부적으로 판매자센터 루트 URL을 다시 불러온다 -
        # 그런데 그 루트 URL은 새로고침할 때마다 항상 기본 스토어(정담건강)로 초기화되므로,
        # 방금 성공한 전환을 바로 그 자리에서 되돌려버리고 있었다. switch_store()가 이미
        # 로그인 여부까지 내포해서 확인하므로(트리거 요소 자체가 로그인 안 하면 없음) 따로
        # 재확인할 필요 없다.
        # 스토어 전환 실패의 상당수는 순간적인 팝업/토스트에 막힌 것뿐이라(2026-07-24
        # 확인 - 같은 코드로 방금 실패했다가 바로 재시도하면 성공하는 경우가 많았음),
        # 로그인 재시도로 넘어가기 전에 먼저 단순 재시도를 몇 번 해본다.
        for attempt in range(3):
            if switch_store(driver, store):
                log.info(f"[{store}] 로그인/스토어 전환 확인")
                alert_throttle.report_success(f"login_{store}")
                return driver
            time.sleep(3)

        # 스토어 전환이 계속 실패해도 그게 꼭 로그아웃 때문은 아니다 - 순간적인 팝업/UI
        # 문제일 수 있는데, 그런데도 로그인 페이지로 이동해서 재로그인을 시도해버리면
        # 오히려 멀쩡했던 세션을 끊어버릴 위험이 있다(2026-07-27 사용자 지적: "왜 자꾸
        # 로그아웃을 하냐" - 실제로 로그아웃 상태가 아니었는데 재로그인 시도 자체가
        # 문제였을 가능성). 그래서 진짜 로그아웃됐는지 먼저 확인하고, 로그아웃이 아니면
        # 로그인 페이지에는 아예 가지 않고 스토어 전환만 몇 번 더 재시도한다.
        if check_logged_in(driver):
            log.info(f"[{store}] 스토어 전환 {attempt + 1}회 실패했지만 로그인은 유지된 상태 - 전환만 더 재시도")
            for attempt in range(3):
                if switch_store(driver, store):
                    log.info(f"[{store}] 재시도 후 스토어 전환 확인")
                    alert_throttle.report_success(f"login_{store}")
                    return driver
                time.sleep(3)
        else:
            log.info(f"[{store}] 세션이 실제로 끊긴 것으로 확인 - 아이디/비번 자동 로그인 시도")
            if _id_pw_login(driver, store):
                driver.get(SELLER_CENTER_URL)
                time.sleep(4)
                for attempt in range(2):
                    if switch_store(driver, store):
                        log.info(f"[{store}] 자동 로그인 후 스토어 전환 확인")
                        alert_throttle.report_success(f"login_{store}")
                        return driver
                    time.sleep(3)

        # 로그인은 매일 자동으로 재시도되고, 하루 이틀 정도의 일시적 문제는 저절로
        # 풀리는 경우가 많았다 - 이 문제가 연속으로 반복될 때만 실제로 텔레그램을
        # 보낸다(2026-07-27 요청, THRESHOLD=2일).
        alert_throttle.report_failure(
            f"login_{store}",
            f"[{store}] 로그인이 며칠째 안 되고 있습니다(자동 로그인도 실패) - 브라우저에서 직접 로그인해주세요.",
        )
        return None
    except Exception as e:
        alert_throttle.report_failure(f"login_{store}", f"[{store}] 로그인 중 오류가 며칠째 반복되고 있습니다: {str(e)[:200]}")
        return None
