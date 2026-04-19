"""
네이버 검색광고 키워드도구 API 래퍼.
각 키워드의 실제 월간 검색량 (PC + 모바일)을 조회합니다.

공식 API: GET https://api.naver.com/keywordstool

필요한 .env 설정:
    NAVER_AD_API_KEY       : 검색광고 API 라이선스 키
    NAVER_AD_SECRET_KEY    : 검색광고 API 시크릿 키
    NAVER_AD_CUSTOMER_ID   : 광고계정 고객번호 (URL에서 확인: /ad-accounts/1850040/)

API 키 발급 경로:
    네이버 광고 (https://searchad.naver.com) 로그인
    → 도구 > API 사용 관리 > API 라이선스 등록
"""

import base64
import hashlib
import hmac
import os
import time
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

KEYWORD_TOOL_URL = "https://api.naver.com/keywordstool"
MAX_HINTS_PER_REQUEST = 5   # API 최대 동시 조회 키워드 수
RATE_LIMIT_DELAY = 0.5      # 요청 간 딜레이 (초)


def _is_configured() -> bool:
    """광고 API 키가 .env에 설정되어 있는지 확인."""
    return bool(
        os.getenv("NAVER_AD_API_KEY")
        and os.getenv("NAVER_AD_SECRET_KEY")
        and os.getenv("NAVER_AD_CUSTOMER_ID")
    )


def _make_signature(timestamp: str, method: str, path: str, secret_key: str) -> str:
    """
    HMAC-SHA256 서명 생성.
    서명 대상: "{timestamp}.{METHOD}.{path}"
    """
    message = f"{timestamp}.{method}.{path}"
    raw = hmac.new(
        secret_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(raw).decode("utf-8")


def _get_headers() -> dict:
    """광고 API 인증 헤더 생성."""
    api_key = os.getenv("NAVER_AD_API_KEY", "").strip()
    secret_key = os.getenv("NAVER_AD_SECRET_KEY", "").strip()
    customer_id = os.getenv("NAVER_AD_CUSTOMER_ID", "").strip()

    timestamp = str(int(time.time() * 1000))
    signature = _make_signature(timestamp, "GET", "/keywordstool", secret_key)

    return {
        "X-Timestamp": timestamp,
        "X-API-KEY": api_key,
        "X-Customer": customer_id,
        "X-Signature": signature,
    }


def _parse_count(val) -> int:
    """
    API 응답의 검색량 값을 정수로 변환.
    값이 '< 10'(문자열) 또는 숫자로 반환될 수 있음.
    """
    if isinstance(val, str):
        # '< 10' 형태
        return 5
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _find_case_insensitive(result: dict, keyword: str) -> dict | None:
    """대소문자 구분 없이 키워드를 result에서 찾습니다."""
    if keyword in result:
        return result[keyword]
    keyword_lower = keyword.lower()
    for k, v in result.items():
        if k.lower() == keyword_lower:
            return v
    return None


def _fetch_batch(keywords: list[str]) -> dict[str, dict]:
    """
    최대 5개 키워드의 월간 검색량을 조회합니다.

    Returns:
        {keyword: {"pc": int, "mobile": int, "total": int, "competition": str}}
        키는 요청한 원본 키워드 (대소문자 유지)
    """
    params = {
        "hintKeywords": ",".join(keywords),
        "showDetail": "1",
    }
    try:
        response = requests.get(
            KEYWORD_TOOL_URL,
            headers=_get_headers(),
            params=params,
            timeout=15,
        )

        if response.status_code == 200:
            data = response.json()
            # API 반환값을 소문자 기준으로 인덱싱 (대소문자 무시)
            raw_result: dict[str, dict] = {}
            for item in data.get("keywordList", []):
                kw = item.get("relKeyword", "")
                pc = _parse_count(item.get("monthlyPcQcCnt", 0))
                mobile = _parse_count(item.get("monthlyMobileQcCnt", 0))
                competition = item.get("compIdx", "")
                raw_result[kw] = {
                    "pc": pc,
                    "mobile": mobile,
                    "total": pc + mobile,
                    "competition": competition,  # "높음" | "중간" | "낮음"
                }
            # 요청 키워드 기준으로 결과를 재매핑 (대소문자 무시 매칭)
            result: dict[str, dict] = {}
            for req_kw in keywords:
                found = _find_case_insensitive(raw_result, req_kw)
                if found:
                    result[req_kw] = found
            return result

        elif response.status_code == 401:
            print("[검색량] API 인증 실패 (401) - NAVER_AD_API_KEY / NAVER_AD_SECRET_KEY 확인")
            return {}
        elif response.status_code == 403:
            print("[검색량] API 접근 거부 (403) - NAVER_AD_CUSTOMER_ID 확인")
            return {}
        else:
            print(f"[검색량] HTTP {response.status_code}: {response.text[:200]}")
            return {}

    except requests.exceptions.Timeout:
        print("[검색량] 요청 타임아웃")
        return {}
    except Exception as e:
        print(f"[검색량] 오류: {e}")
        return {}


def get_search_volumes(keywords: list[str]) -> dict[str, dict]:
    """
    키워드 목록 전체의 월간 검색량을 조회합니다.

    Args:
        keywords: 조회할 키워드 리스트

    Returns:
        {
            keyword: {
                "pc": int,        # 월간 PC 검색량
                "mobile": int,    # 월간 모바일 검색량
                "total": int,     # 월간 총 검색량 (PC + 모바일)
                "competition": str  # 경쟁 강도 ("높음" | "중간" | "낮음")
            }
        }
        API 미설정 시 빈 dict 반환.
    """
    if not _is_configured():
        print(
            "\n[검색량] 건너뜀 - .env에 광고 API 키가 설정되지 않았습니다.\n"
            "  설정 방법 (네이버 광고 > 도구 > API 사용 관리):\n"
            "    NAVER_AD_API_KEY=라이선스키\n"
            "    NAVER_AD_SECRET_KEY=시크릿키\n"
            "    NAVER_AD_CUSTOMER_ID=1850040\n"
        )
        return {}

    print(f"\n[검색량] {len(keywords)}개 키워드 월간 검색량 조회 중...")

    all_volumes = {}
    # 5개씩 배치 처리
    batches = [keywords[i:i + MAX_HINTS_PER_REQUEST] for i in range(0, len(keywords), MAX_HINTS_PER_REQUEST)]

    for idx, batch in enumerate(batches):
        print(f"[검색량] 배치 {idx + 1}/{len(batches)}: {batch}")
        batch_result = _fetch_batch(batch)

        # API가 hint 키워드 외 연관어도 반환할 수 있으므로, 요청한 키워드만 저장
        for kw in batch:
            if kw in batch_result:
                all_volumes[kw] = batch_result[kw]
            else:
                # 검색량 0 또는 데이터 없음
                all_volumes[kw] = {"pc": 0, "mobile": 0, "total": 0, "competition": ""}

        if idx < len(batches) - 1:
            time.sleep(RATE_LIMIT_DELAY)

    found = sum(1 for v in all_volumes.values() if v["total"] > 0)
    print(f"[검색량] 완료: {found}/{len(keywords)}개 키워드 검색량 확인")
    return all_volumes


def merge_volumes_into_analyzed(
    analyzed: list[dict],
    volumes: dict[str, dict],
) -> list[dict]:
    """
    분석 결과 리스트에 검색량 데이터를 병합합니다.
    각 키워드 dict에 아래 필드를 추가:
        monthly_pc_search    : int
        monthly_mobile_search: int
        monthly_total_search : int
        ad_competition       : str
    """
    # 대소문자 무시 검색을 위해 소문자 기준 인덱스 생성
    volumes_lower: dict[str, dict] = {k.lower(): v for k, v in volumes.items()}

    for item in analyzed:
        kw = item["keyword"]
        # 정확히 일치하면 바로 사용, 없으면 소문자 기준으로 재탐색
        vol = volumes.get(kw) or volumes_lower.get(kw.lower(), {})
        item["monthly_pc_search"]     = vol.get("pc", 0)
        item["monthly_mobile_search"] = vol.get("mobile", 0)
        item["monthly_total_search"]  = vol.get("total", 0)
        item["ad_competition"]        = vol.get("competition", "")
    return analyzed


def format_volume(n: int) -> str:
    """검색량을 쉼표 포함 숫자로 포맷. 예: 65123 → '65,123'"""
    if n == 0:
        return "-"
    return f"{n:,}"
