"""
Naver DataLab Search Trend API wrapper.
Official API: POST https://openapi.naver.com/v1/datalab/search
"""

import os
import time
import json
import subprocess
import sys
import requests
from datetime import datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

try:
    from dateutil.relativedelta import relativedelta
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dateutil"])
    from dateutil.relativedelta import relativedelta

load_dotenv()

NAVER_SEARCH_TREND_URL = "https://openapi.naver.com/v1/datalab/search"
MAX_KEYWORDS_PER_REQUEST = 5  # API limit: max 5 keyword groups per request
MAX_KEYWORDS_PER_GROUP = 20   # API limit: max 20 keywords per group
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10   # seconds (일반 오류용)
BATCH_DELAY = 10        # seconds — 배치 간 대기 (속도 제한 방지)
PERIOD_DELAY = 20       # seconds — 수집 기간 전환 시 대기
RATE_LIMIT_WAIT = 60    # seconds — 429 발생 후 재시도 전 대기


def _load_api_keys() -> list[tuple[str, str]]:
    """
    .env에서 네이버 API 키 목록을 불러옵니다.
    NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (기본)
    NAVER_CLIENT_ID_2 / NAVER_CLIENT_SECRET_2 (추가 키)
    NAVER_CLIENT_ID_3 / NAVER_CLIENT_SECRET_3 ...

    Returns:
        [(client_id, client_secret), ...] — 유효한 키 쌍 목록
    """
    keys = []

    # 기본 키
    cid = os.getenv("NAVER_CLIENT_ID", "").strip()
    secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if cid and secret:
        keys.append((cid, secret))

    # 추가 키: _2, _3, _4, ... 최대 10개까지 탐색
    for i in range(2, 11):
        cid = os.getenv(f"NAVER_CLIENT_ID_{i}", "").strip()
        secret = os.getenv(f"NAVER_CLIENT_SECRET_{i}", "").strip()
        if cid and secret:
            keys.append((cid, secret))
        else:
            break  # 번호가 연속되지 않으면 중단

    if not keys:
        raise ValueError(
            "NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET 환경 변수가 설정되지 않았습니다. "
            ".env 파일을 확인하세요."
        )

    return keys


# 모듈 로드 시 키 목록 초기화 (런타임 중 소진 상태 추적)
_api_keys: list[tuple[str, str]] = []
_exhausted_keys: set[int] = set()  # 일일 한도 소진된 키 인덱스


def _get_active_key_index() -> int:
    """소진되지 않은 첫 번째 키 인덱스를 반환. 없으면 -1."""
    for i in range(len(_api_keys)):
        if i not in _exhausted_keys:
            return i
    return -1


def _get_headers(key_index: int = 0) -> dict:
    """지정된 인덱스의 API 키로 인증 헤더를 생성합니다."""
    global _api_keys
    if not _api_keys:
        _api_keys = _load_api_keys()
    client_id, client_secret = _api_keys[key_index]
    return {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
        "Content-Type": "application/json",
    }


def _call_api_with_retry(payload: dict) -> Optional[dict]:
    """
    Call Naver DataLab API with retry logic + 자동 API 키 전환.
    429 발생 시 → 다음 키로 즉시 전환 → 모든 키 소진 시 대기 후 재시도.

    Returns:
        API response dict, or None on failure.
    """
    global _api_keys, _exhausted_keys

    if not _api_keys:
        _api_keys = _load_api_keys()

    total_keys = len(_api_keys)
    if total_keys > 1:
        print(f"[API] 등록된 API 키: {total_keys}개")

    for attempt in range(1, MAX_RETRIES + 1):
        key_idx = _get_active_key_index()
        if key_idx == -1:
            print(f"[API] 모든 API 키({total_keys}개)의 일일 한도가 소진되었습니다. 내일 다시 시도하세요.")
            return None

        try:
            headers = _get_headers(key_idx)
            response = requests.post(
                NAVER_SEARCH_TREND_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                key_label = f"키 #{key_idx + 1}" if total_keys > 1 else "기본 API 키"
                _exhausted_keys.add(key_idx)

                next_idx = _get_active_key_index()
                if next_idx != -1:
                    print("\n" + "!" * 55)
                    print(f"! [네이버 데이터랩 API] {key_label} 한도 초과 (429)")
                    print(f"! {RATE_LIMIT_WAIT}초 대기 후 키 #{next_idx + 1}로 전환합니다.")
                    print("!" * 55)
                    time.sleep(RATE_LIMIT_WAIT)
                    continue
                else:
                    print("\n" + "=" * 55)
                    print("!!! [네이버 데이터랩 API] 호출 실패 원인 안내 !!!")
                    print("=" * 55)
                    print(f"  오류 코드 : HTTP 429 (Too Many Requests)")
                    print(f"  실패한 API: 네이버 데이터랩 검색어트렌드 API")
                    print(f"              openapi.naver.com/v1/datalab/search")
                    print(f"  원인      : 오늘 하루 API 호출 횟수를 모두 사용했습니다.")
                    print(f"              (무료 한도: 1,000회/일, 등록 키 {total_keys}개 모두 소진)")
                    print(f"  ※ 이 오류는 Anthropic/Claude API와 무관합니다.")
                    print(f"    Claude AI 브리핑은 정상 생성됩니다.")
                    print("-" * 55)
                    print(f"  해결 방법:")
                    print(f"  1) 내일 자정 이후 다시 실행 (한도 자동 초기화)")
                    print(f"  2) .env에 NAVER_CLIENT_ID_2 / NAVER_CLIENT_SECRET_2")
                    print(f"     추가 후 재실행 (developers.naver.com에서 앱 추가 등록)")
                    print(f"  ▶ 현재 실행은 트렌드 데이터 없이 계속 진행됩니다.")
                    print("=" * 55 + "\n")
                    return None
            elif response.status_code == 401:
                print("\n" + "=" * 55)
                print("!!! [네이버 데이터랩 API] 인증 오류 !!!")
                print("=" * 55)
                print(f"  오류 코드 : HTTP 401 (Unauthorized)")
                print(f"  실패한 키  : 키 #{key_idx + 1}")
                print(f"  원인      : Client ID 또는 Client Secret이 올바르지 않습니다.")
                print(f"  확인 방법  : developers.naver.com → 내 애플리케이션에서 키 확인")
                print("=" * 55 + "\n")
                _exhausted_keys.add(key_idx)
                continue
            elif response.status_code == 400:
                print(f"[API] 잘못된 요청 (400): {response.text}")
                return None
            else:
                print(f"[API] HTTP {response.status_code}: {response.text[:200]}")
                if attempt < MAX_RETRIES:
                    wait_time = RETRY_BASE_DELAY * attempt
                    print(f"[API] {wait_time}초 후 재시도 ({attempt}/{MAX_RETRIES})...")
                    time.sleep(wait_time)

        except requests.exceptions.Timeout:
            print(f"[API] 요청 타임아웃. 재시도 ({attempt}/{MAX_RETRIES})...")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)
        except requests.exceptions.ConnectionError as e:
            print(f"[API] 연결 오류: {e}. 재시도 ({attempt}/{MAX_RETRIES})...")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)
        except Exception as e:
            print(f"[API] 예기치 않은 오류: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)

    print(f"[API] {MAX_RETRIES}번 재시도 후 실패.")
    return None


def _batch_keywords(keywords: list[str], batch_size: int = MAX_KEYWORDS_PER_REQUEST) -> list[list[str]]:
    """Split keywords into batches of batch_size."""
    return [keywords[i:i + batch_size] for i in range(0, len(keywords), batch_size)]


def _parse_trend_results(api_response: dict) -> dict[str, list[dict]]:
    """
    Parse API response into a dict of {keyword: [{period, ratio}]}.

    Each entry in the returned data:
        period: "YYYY-MM-DD" or "YYYY-MM" depending on timeUnit
        ratio: float 0-100 (relative search volume)
    """
    results = {}

    if not api_response or "results" not in api_response:
        return results

    for item in api_response["results"]:
        title = item.get("title", "")
        data = item.get("data", [])
        results[title] = [{"period": d["period"], "ratio": float(d["ratio"])} for d in data]

    return results


def get_search_trend(
    keywords: list[str],
    start_date: str,
    end_date: str,
    time_unit: str = "week",
) -> tuple[dict[str, list[dict]], set[str]]:
    """
    Get search trend data for a list of keywords.

    Args:
        keywords: List of keyword strings (each becomes its own group)
        start_date: "YYYY-MM-DD" format
        end_date: "YYYY-MM-DD" format
        time_unit: "date", "week", or "month"

    Returns:
        Tuple of:
          - Dict of {keyword: [{"period": str, "ratio": float}]}
          - Set of keywords that failed due to API errors (not genuine no-data)
    """
    if not keywords:
        return {}, set()

    all_results = {}
    api_failed = set()  # keywords that failed due to API error (not genuine no-data)
    batches = _batch_keywords(keywords, MAX_KEYWORDS_PER_REQUEST)

    print(f"[API] 검색트렌드 조회: {len(keywords)}개 키워드, {len(batches)}개 배치")
    print(f"[API] 기간: {start_date} ~ {end_date} ({time_unit})")

    for batch_idx, batch in enumerate(batches):
        keyword_groups = [
            {"groupName": kw, "keywords": [kw]}
            for kw in batch
        ]

        payload = {
            "startDate": start_date,
            "endDate": end_date,
            "timeUnit": time_unit,
            "keywordGroups": keyword_groups,
        }

        print(f"[API] 배치 {batch_idx + 1}/{len(batches)}: {batch}")
        response = _call_api_with_retry(payload)

        if response:
            batch_results = _parse_trend_results(response)
            all_results.update(batch_results)
            print(f"[API] 배치 {batch_idx + 1} 완료: {len(batch_results)}개 결과")
        else:
            print(f"[API] 배치 {batch_idx + 1} 실패. 해당 키워드 누락 처리.")
            for kw in batch:
                all_results[kw] = []
                api_failed.add(kw)

        # Rate limiting: wait between batches
        if batch_idx < len(batches) - 1:
            time.sleep(BATCH_DELAY)

    # Fill in keywords that had no results (genuine no-data, not API error)
    for kw in keywords:
        if kw not in all_results:
            all_results[kw] = []

    return all_results, api_failed


def get_longterm_trend(keywords: list[str]) -> tuple[dict[str, list[dict]], set[str]]:
    """
    Get long-term trend data from 2016-01-01 to today, monthly.

    Returns:
        Tuple of (results dict, set of API-failed keywords)
    """
    start_date = "2016-01-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    print(f"\n[API] 장기 트렌드 조회 시작 (2016-01-01 ~ {end_date}, 월별)")
    return get_search_trend(keywords, start_date, end_date, time_unit="month")


def get_shortterm_trend(keywords: list[str], months: int = 12) -> tuple[dict[str, list[dict]], set[str]]:
    """
    Get short-term trend data for the last N months, weekly.

    Returns:
        Tuple of (results dict, set of API-failed keywords)
    """
    end_date = datetime.now()
    start_date = end_date - relativedelta(months=months)

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    print(f"\n[API] 단기 트렌드 조회 시작 ({months}개월, {start_str} ~ {end_str}, 주별)")
    return get_search_trend(keywords, start_str, end_str, time_unit="week")


def get_all_trend_data(
    keywords: list[str],
    existing_data: dict = None,
    on_period_complete=None,
) -> dict:
    """
    Fetch all trend data for a list of keywords.
    existing_data: 이전 실행에서 저장된 부분 데이터 (이어하기 시 사용)
    on_period_complete: 각 기간 수집 완료 후 호출되는 콜백 (진행 상황 저장용)
    """
    print(f"\n[API] 총 {len(keywords)}개 키워드의 전체 트렌드 데이터 수집 시작")

    results = dict(existing_data) if existing_data else {}
    results.pop("_api_failures", None)  # 이전 실패 기록은 초기화
    failure_map: dict[str, list[str]] = {}

    def record_failures(failed_set: set[str], period_label: str):
        for kw in failed_set:
            failure_map.setdefault(kw, []).append(period_label)

    periods = [
        ("longterm",      "[1/4] 장기 트렌드 (2016~현재, 월별)",    lambda: get_longterm_trend(keywords)),
        ("shortterm_1yr", "[2/4] 단기 트렌드 (최근 1년, 주별)",      lambda: get_shortterm_trend(keywords, months=12)),
        ("shortterm_3mo", "[3/4] 단기 트렌드 (최근 3개월, 주별)",    lambda: get_shortterm_trend(keywords, months=3)),
        ("shortterm_1mo", "[4/4] 단기 트렌드 (최근 1개월, 일별)",    lambda: _fetch_1mo(keywords)),
    ]

    for key, label, fetch_fn in periods:
        if key in results and results[key]:
            kw_count = sum(1 for v in results[key].values() if v)
            print(f"\n[API] {label} → 이전 작업 데이터 사용 ({kw_count}개 키워드, 건너뜀)")
            continue

        print(f"\n[API] {label} 수집 중...")
        data, failed = fetch_fn()
        results[key] = data
        record_failures(failed, label)

        if on_period_complete:
            on_period_complete(results)

        time.sleep(PERIOD_DELAY)

    results["_api_failures"] = failure_map

    trend_keys = ["longterm", "shortterm_1yr", "shortterm_3mo", "shortterm_1mo"]
    total_with_data = sum(
        1 for kw in keywords
        if any(results[k].get(kw) for k in trend_keys)
    )
    total_failed = len(failure_map)
    print(f"\n[API] 트렌드 데이터 수집 완료: {total_with_data}/{len(keywords)}개 키워드 데이터 있음")
    if total_failed:
        print(f"[API] API 오류로 누락된 키워드: {total_failed}개")

    return results


def _fetch_1mo(keywords: list[str]) -> tuple[dict, set]:
    """최근 1개월 일별 트렌드 조회 헬퍼."""
    end_date = datetime.now()
    start_date = end_date - relativedelta(months=1)
    return get_search_trend(
        keywords,
        start_date.strftime("%Y-%m-%d"),
        end_date.strftime("%Y-%m-%d"),
        time_unit="date",
    )


if __name__ == "__main__":
    test_keywords = ["홍삼", "비타민C", "유산균", "오메가3", "콜라겐"]
    print("=== Naver DataLab API 테스트 ===")
    trend_data = get_all_trend_data(test_keywords)

    for kw in test_keywords:
        lt = trend_data["longterm"].get(kw, [])
        print(f"\n{kw}: 장기 데이터 {len(lt)}개 포인트")
        if lt:
            print(f"  최근: {lt[-1]}")
