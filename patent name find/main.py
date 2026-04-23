"""
상표 선점 후보 탐색 프로그램
- 해외 건강식품/웰니스 RSS 트렌드 수집
- Claude AI로 분석 및 TOP 10 선별
- HTML 보고서 생성 (웹서버에서 서빙)
- 텔레그램으로 링크 전송

실행:
  python main.py            # 전체 실행 (수집 + 분석 + 보고서 + 텔레그램)
  python main.py --no-telegram  # 텔레그램 전송 없이 보고서만 생성
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# VM 환경에서 현재 디렉토리를 모듈 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"

load_dotenv(BASE_DIR / ".env")


def _check_env():
    missing = [k for k in ["ANTHROPIC_API_KEY"] if not os.getenv(k)]
    if missing:
        print("[오류] .env 파일에 아래 항목을 입력해주세요:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def main():
    send_telegram_flag = "--no-telegram" not in sys.argv
    date_str = datetime.now().strftime("%Y-%m-%d")

    print("=" * 55)
    print(f"  상표 선점 후보 탐색 — {date_str}")
    print(f"  텔레그램: {'전송' if send_telegram_flag else '생략'}")
    print("=" * 55 + "\n")

    _check_env()

    # ── 1. Claude 웹 검색 + 분석 ────────────────────────────
    print("[1/3] Claude 웹 검색 + AI 분석 중... (1~2분 소요)")
    from analyzer import analyze_trends
    data = analyze_trends()

    # ── 2. HTML 보고서 생성 ─────────────────────────────────
    print("\n[2/3] HTML 보고서 생성 중...")
    from reporter import generate_html
    report_path = generate_html(data, REPORTS_DIR)

    # ── 3. 텔레그램 전송 ────────────────────────────────────
    print(f"\n[3/3] 텔레그램 전송 중...")
    if send_telegram_flag:
        from notifier import send_telegram
        candidates = data.get("candidates", [])
        send_telegram(report_path, date_str, candidates[:3])
    else:
        print("  (--no-telegram 옵션으로 생략)")

    print(f"\n{'=' * 55}")
    print(f"  완료!")
    print(f"  보고서 파일: {report_path}")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    main()
