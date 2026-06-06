"""
리뷰 분석 결과를 텔레그램으로 전송.
- 4096자 초과 시 자동 분할
- 월간 리포트 / 히스토리 리포트 포맷 포함
"""

import os
import requests
from datetime import datetime

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message: str):
    """텔레그램 메시지 전송 (4000자 초과 시 분할)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[주의] 텔레그램 환경변수 미설정 → 콘솔 출력으로 대체")
        print(message)
        return

    max_len = 4000
    chunks = []
    while message:
        if len(message) <= max_len:
            chunks.append(message)
            break
        # 줄 경계에서 자르기
        cut = message[:max_len].rfind("\n")
        if cut == -1:
            cut = max_len
        chunks.append(message[:cut])
        message = message[cut:].lstrip("\n")

    for i, chunk in enumerate(chunks, 1):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                timeout=15,
            )
            if not r.ok:
                print(f"  [텔레그램 오류] {r.status_code}: {r.text[:100]}")
        except Exception as e:
            print(f"  [텔레그램 전송 실패] {e}")


# ── 월간 리포트 ─────────────────────────────────────────────

def format_monthly_report(analyses: list) -> str:
    now = datetime.now()
    header = (
        f"📊 [{now.year}년 {now.month}월] 월간 리뷰 분석 리포트\n"
        f"{'━' * 32}\n\n"
    )

    blocks = []
    for item in analyses:
        name = item["name"]
        a    = item["analysis"]

        avg  = a.get("avg_star", 0)
        cnt  = a.get("count", 0)

        # 별 이모지
        if avg >= 4.5:
            stars = "⭐⭐⭐⭐⭐"
        elif avg >= 4.0:
            stars = "⭐⭐⭐⭐"
        elif avg >= 3.0:
            stars = "⭐⭐⭐"
        else:
            stars = "⭐⭐"

        lines = [f"📦 {name}"]
        lines.append(f"{stars} {avg}점  |  리뷰 {cnt}건")

        if cnt == 0:
            lines.append("  이번 달 리뷰 없음")
            blocks.append("\n".join(lines))
            continue

        good     = a.get("good", [])
        bad      = a.get("bad", [])
        improve  = a.get("improve", [])
        critical = a.get("critical", [])
        summary  = a.get("summary", "")

        if good:
            lines.append("✅ 잘하는 점")
            lines.extend(f"  • {g}" for g in good[:5])

        if bad:
            lines.append("❌ 불만 사항")
            lines.extend(f"  • {b}" for b in bad[:5])

        if improve:
            lines.append("💡 개선 제안")
            lines.extend(f"  • {imp}" for imp in improve[:3])

        if critical:
            lines.append("")
            lines.append("🚨 즉시 확인 필요!")
            lines.extend(f"  🔴 {c}" for c in critical)

        if summary:
            lines.append(f"\n📝 {summary}")

        blocks.append("\n".join(lines))

    body = ("\n" + "─" * 30 + "\n\n").join(blocks)
    footer = f"\n\n🕐 {now.strftime('%Y-%m-%d %H:%M')} KST"
    return header + body + footer


# ── 히스토리 리포트 (5년 트렌드) ───────────────────────────

def format_history_report(trend_analyses: list) -> str:
    now = datetime.now()
    header = (
        "📈 [5년 리뷰 트렌드 분석]\n"
        f"기간: 2021년 ~ {now.year}년\n"
        f"{'━' * 32}\n\n"
    )

    # 악화 → 유지 → 개선 순 정렬 (중요한 것부터)
    def sort_key(item):
        d = item["trend"].get("trend_direction", "stable")
        return {"down": 0, "stable": 1, "up": 2}.get(d, 1)

    ordered = sorted(trend_analyses, key=sort_key)

    blocks = []
    for item in ordered:
        name  = item["name"]
        trend = item["trend"]

        direction = trend.get("trend_direction", "stable")
        dir_emoji = {"up": "📈", "down": "📉", "stable": "➡️"}.get(direction, "➡️")

        years = trend.get("years", {})
        year_str = "  ".join(
            f"{y}년 {s['avg_star']}점({s['count']}건)"
            for y, s in sorted(years.items())
        )

        lines = [f"{dir_emoji} {name}"]
        if year_str:
            lines.append(f"  {year_str}")

        trend_text = trend.get("trend", "")
        if trend_text:
            lines.append(f"  → {trend_text}")

        changes = trend.get("key_changes", [])
        if changes:
            lines.append("  변화 포인트")
            lines.extend(f"    • {c}" for c in changes[:3])

        issues = trend.get("current_issues", [])
        if issues:
            lines.append("  현재 문제")
            lines.extend(f"    ⚠️ {iss}" for iss in issues[:3])

        strengths = trend.get("strengths", [])
        if strengths:
            lines.append("  꾸준한 강점")
            lines.extend(f"    ✅ {s}" for s in strengths[:3])

        blocks.append("\n".join(lines))

    body = ("\n" + "─" * 30 + "\n\n").join(blocks)
    footer = f"\n\n🕐 {now.strftime('%Y-%m-%d %H:%M')} KST"
    return header + body + footer
