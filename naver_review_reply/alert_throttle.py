"""반복되는 실패만 텔레그램으로 알린다(2026-07-27 요청).
하루 정도의 일시적 문제(로그인 실패, 회로차단기 발동 등)는 다음 자동실행 때 저절로
해결될 가능성이 높은데, 매번 알리면 사장님 입장에서 알림 피로감만 준다 - "오늘 뭔가
실패해도 내일 잘 될 것 같으면 알리지 말고, 며칠 반복될 때만 알려달라"는 요청을 반영.

2026-07-27 수정: 처음엔 report_failure 호출 횟수로 세었는데, 디버깅하며 같은 날 안에
poster.py를 여러 번 반복 실행하니 "며칠째 실패"라는 알림이 나가버리는 오탐이 있었다
(하루 안의 반복 호출이 실제 "여러 날 반복"으로 잘못 집계됨). 그래서 호출 횟수가 아니라
**서로 다른 날짜 수**로 세도록 바꿨다 - 같은 날 안에 몇 번을 실패하든 1일로만 친다.

2026-07-27 추가: 임계치에 도달해도 이제 바로 사람에게 알리지 않는다. 사장님 요청
("실패했을 때 나한테 말하면 너가 알아서 고치잖아 - 그것도 자동화할 수 없냐", "정말
필요할 때만 나를 불러줘")에 따라, 먼저 `self_heal.trigger()`로 Claude Code를
헤드리스로 한 번 더 실행시켜 스스로 진단+수정을 시도하게 한다. 사람에게 실제로
알리는 것은 이제 그 자동수정 시도 자체가 (a) 원인을 못 찾아서 스스로 "확인 필요"라고
보고하거나 (b) 아예 시작/완료를 못 했을 때뿐이다 - 둘 다 self_heal.py 안에서 처리됨.

2026-09-02 수정: 카테고리(스토어)별로 자동수정을 각각 띄우다 보니, **원인이 하나인데
자동수정 세션이 스토어 수만큼 중복 실행**되는 낭비가 실측으로 확인됐다. 이날 크롬
드라이버 버전 불일치로 3개 스토어 수집이 동시에 실패했는데, collect_NUTONE 세션이
20:00~20:07에 원인을 찾아 고치고 커밋까지 마쳤는데도 collect_JDHEALTH(20:07~20:13),
collect_NUTPET(20:13~) 세션이 이미 고쳐진 같은 오류로 연달아 실행됐다 - 야간 파이프라인이
스토어당 최대 15분씩(3개면 45분) 붙잡히고, 사장님한테도 같은 내용 알림이 여러 번 갈 수
있는 구조였다. 그래서 같은 날 안에서는 "같은 오류 서명"에 대해 자동수정을 한 번만
띄운다(_error_signature 참고)."""
import hashlib
import json
import logging
import re
from datetime import date

import config
import self_heal

log = logging.getLogger(__name__)

THRESHOLD = 2  # 이 "날짜 수" 이상 실패가 있어야 실제로 자동수정을 시도한다
_STATE_PATH = config.DATA_DIR / "alert_throttle_state.json"
# 같은 날 이미 자동수정을 띄운 "오류 서명" 기록(날짜가 바뀌면 통째로 버린다)
_SIGNATURE_STATE_PATH = config.DATA_DIR / "self_heal_signature_state.json"

_STORE_NAME_RE = re.compile("|".join(re.escape(s) for s in config.STORES))


def _load() -> dict:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def _save(state: dict):
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _error_signature(category: str, message: str) -> str:
    """"어느 스토어냐"만 다르고 실제 원인은 같은 실패를 하나로 묶는 서명.
    스토어 이름(NUTONE/JDHEALTH/NUTPET)과 공백 차이를 지운 뒤 해시한다 - 오류 메시지
    본문이 글자 단위로 같을 때만 같은 서명이 되므로, 원인이 실제로 다른 실패(예: 한
    스토어만 쿠키 만료)는 그대로 각각 자동수정을 받는다."""
    kind = _STORE_NAME_RE.sub("", category).rstrip("_")
    body = re.sub(r"\s+", " ", _STORE_NAME_RE.sub("", message)).strip()
    return hashlib.sha1(f"{kind}|{body}".encode("utf-8")).hexdigest()[:16]


def _healed_signature_today(signature: str, today: str) -> str | None:
    """오늘 이미 이 서명으로 자동수정을 띄웠으면 그때의 category를 반환한다."""
    if not _SIGNATURE_STATE_PATH.exists():
        return None
    try:
        data = json.loads(_SIGNATURE_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("date") != today:  # 날짜가 바뀌면 어제 기록은 무효
        return None
    return data.get("signatures", {}).get(signature)


def _mark_signature_healed(signature: str, category: str, today: str):
    data = {"date": today, "signatures": {}}
    if _SIGNATURE_STATE_PATH.exists():
        try:
            prev = json.loads(_SIGNATURE_STATE_PATH.read_text(encoding="utf-8"))
            if prev.get("date") == today:
                data["signatures"] = prev.get("signatures", {})
        except (json.JSONDecodeError, ValueError):
            pass
    data["signatures"][signature] = category
    _SIGNATURE_STATE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def report_failure(category: str, message: str):
    """실패를 기록한다. 같은 category가 서로 다른 날짜로 THRESHOLD일 이상 쌓였을 때만
    자동수정을 시도한다(같은 날 안에서의 반복 호출은 하루로만 치고, 그 전까지는
    로컬에만 기록하고 조용히 다음 실행에서 재시도되게 둔다). 자동수정도 하루 한 번만
    시도한다(healed_date) - 같은 실행 안에서 같은 category가 여러 번 report_failure를
    불러도 claude 프로세스를 중복으로 여러 개 띄우지 않기 위함."""
    state = _load()
    entry = state.get(category)
    if not isinstance(entry, dict):
        entry = {"count": 0, "last_date": None, "healed_date": None}
    today = date.today().isoformat()
    if entry.get("last_date") != today:
        entry["count"] = entry.get("count", 0) + 1
        entry["last_date"] = today
    state[category] = entry
    _save(state)
    count = entry["count"]
    if count >= THRESHOLD and entry.get("healed_date") != today:
        entry["healed_date"] = today
        state[category] = entry
        _save(state)
        # 원인이 같은(=오류 서명이 같은) 실패는 오늘 이미 자동수정을 띄웠으면 다시
        # 띄우지 않는다. healed_date는 위에서 이미 오늘로 찍었으니 같은 날 다시
        # 시도되지 않고, 내일도 여전히 실패하면 그때 다시 한 번 시도된다.
        signature = _error_signature(category, message)
        already = _healed_signature_today(signature, today)
        if already:
            log.info(
                f"[self_heal] {category} 자동수정 스킵 - 오늘 {already}에서 같은 원인으로 이미 시도함"
            )
            return
        _mark_signature_healed(signature, category, today)
        self_heal.trigger(category, message, count)


def report_success(category: str):
    """성공하면 연속 실패 카운트를 0으로 초기화한다."""
    state = _load()
    entry = state.get(category)
    if isinstance(entry, dict) and entry.get("count", 0) != 0:
        state[category] = {"count": 0, "last_date": None, "healed_date": None}
        _save(state)
    elif isinstance(entry, (int, float)) and entry != 0:
        state[category] = {"count": 0, "last_date": None, "healed_date": None}
        _save(state)
