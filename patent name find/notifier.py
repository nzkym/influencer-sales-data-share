"""
텔레그램 파일 전송 모듈
"""

import os
import requests
from pathlib import Path


def send_telegram(report_path: Path, date_str: str, top3: list) -> bool:
    """텔레그램으로 HTML 보고서 파일 직접 전송"""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id   = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("  [경고] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 없음 — 전송 생략")
        return False

    # TOP 3 요약 캡션
    top3_lines = []
    for c in top3:
        tier = c.get("tier", "")
        name = c.get("term_ko", "")
        en   = c.get("term_en", "")
        top3_lines.append(f"  {c.get('rank', '')}. {name} ({en}) {tier}")
    top3_text = "\n".join(top3_lines)

    caption = (
        f"📊 상표 선점 후보 주간 리포트\n"
        f"🗓 {date_str}\n"
        f"\n"
        f"이번 주 TOP 3:\n"
        f"{top3_text}"
    )

    try:
        with open(report_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": (report_path.name, f, "text/html")},
                timeout=30,
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
