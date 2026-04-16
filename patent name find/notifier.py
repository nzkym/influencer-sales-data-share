"""
텔레그램 링크 전송 모듈
"""

import os
import requests


def send_telegram(report_url: str, date_str: str, top3: list) -> bool:
    """텔레그램으로 보고서 링크만 전송"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("  [경고] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 없음 — 전송 생략")
        return False

    # TOP 3 요약 텍스트
    top3_lines = []
    for c in top3:
        tier = c.get("tier", "")
        name = c.get("term_ko", "")
        en   = c.get("term_en", "")
        top3_lines.append(f"  {c.get('rank', '')}. {name} ({en}) {tier}")
    top3_text = "\n".join(top3_lines)

    text = (
        f"📊 상표 선점 후보 주간 리포트\n"
        f"🗓 {date_str}\n"
        f"\n"
        f"이번 주 TOP 3:\n"
        f"{top3_text}\n"
        f"\n"
        f"📎 전체 보고서:\n"
        f"{report_url}"
    )

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=15,
        )
        if resp.ok:
            print("  → 텔레그램 전송 완료")
            return True
        else:
            print(f"  [텔레그램 오류] {resp.status_code}: {resp.text[:100]}")
            return False
    except Exception as e:
        print(f"  [텔레그램 오류] {e}")
        return False
