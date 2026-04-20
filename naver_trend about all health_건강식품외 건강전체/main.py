"""
네이버 데이터랩 건강식품 트렌드 분석기
메인 오케스트레이터 스크립트

사용법:
    python main.py                    # 기본 실행 (상위 50개 키워드)
    python main.py --top 100          # 상위 100개 키워드 분석
    python main.py --no-scrape        # 스크레이핑 없이 캐시된 키워드 사용
    python main.py --output report.txt # 결과 파일명 지정
    python main.py --no-chart         # 차트 생성 건너뜀
    python main.py --keywords 홍삼 비타민C 유산균  # 직접 키워드 지정
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Fix Windows console encoding for Unicode/emoji output
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

# Project root directory
PROJECT_DIR = Path(__file__).parent
PROGRESS_FILE = PROJECT_DIR / "결과값" / ".progress.json"


# ---------------------------------------------------------------------------
# 진행 상황 저장 / 불러오기 / 이어하기 선택
# ---------------------------------------------------------------------------

def save_progress(step: int, data: dict) -> None:
    """현재까지 완료된 단계와 데이터를 파일에 저장합니다."""
    PROGRESS_FILE.parent.mkdir(exist_ok=True)
    progress = {
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "step": step,
        **data,
    }
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def load_progress() -> dict | None:
    """저장된 진행 파일이 있으면 불러옵니다."""
    if not PROGRESS_FILE.exists():
        return None
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


RESUME_CONTINUE = "continue"   # 이어서 진행
RESUME_SUPPLEMENT = "supplement"  # 누락 키워드만 보완
RESUME_NEW = "new"             # 새로 시작


def ask_resume(progress: dict) -> str:
    """이전 작업을 이어할지, 누락 데이터를 보완할지, 새로 시작할지 사용자에게 묻습니다.
    Returns: RESUME_CONTINUE | RESUME_SUPPLEMENT | RESUME_NEW
    """
    step_names = {
        1: "키워드 수집 완료",
        2: "트렌드 데이터 수집 완료 (또는 일부 수집)",
        3: "트렌드 분석 완료",
        4: "AI 브리핑 생성 완료",
    }
    step = progress.get("step", 0)
    saved_at = progress.get("saved_at", "알 수 없음")
    keywords = progress.get("keywords", [])
    api_failures = (progress.get("trend_data") or {}).get("_api_failures", {})

    print("\n" + "=" * 60)
    print("  이전에 완료하지 못한 작업이 저장되어 있습니다.")
    print("=" * 60)
    print(f"  저장 시각  : {saved_at}")
    print(f"  마지막 단계: {step_names.get(step, f'{step}단계')}")
    print(f"  키워드 수  : {len(keywords)}개")

    if api_failures:
        print(f"\n  ⚠️  데이터 수집 실패 키워드: {len(api_failures)}개")
        for kw, periods in list(api_failures.items())[:5]:
            print(f"     - {kw}: {', '.join(periods)}")
        if len(api_failures) > 5:
            print(f"     ... 외 {len(api_failures) - 5}개")

    print("\n" + "-" * 60)
    print("  [1] 이어서 진행     (저장된 데이터부터 계속)")
    if api_failures:
        print("  [2] 누락 데이터 보완 (수집 실패 키워드만 다시 수집 후 리포트 생성)")
        print("  [3] 새로 시작       (저장 데이터 삭제 후 처음부터)")
    else:
        print("  [2] 새로 시작       (저장 데이터 삭제 후 처음부터)")
    print("=" * 60)

    valid_choices = {"1", "2", "3"} if api_failures else {"1", "2"}
    while True:
        try:
            choice = input(f"\n  선택 ({'/'.join(sorted(valid_choices))}): ").strip()
        except (EOFError, KeyboardInterrupt):
            # 비대화형(백그라운드) 실행 시: 이전 작업 이어서 진행
            print("  → 비대화형 실행 감지 — 이전 작업을 자동으로 이어서 진행합니다.\n")
            return RESUME_CONTINUE

        if choice == "1":
            print("  → 이전 작업을 이어서 진행합니다.\n")
            return RESUME_CONTINUE
        elif choice == "2" and api_failures:
            print("  → 누락된 데이터를 보완하여 진행합니다.\n")
            return RESUME_SUPPLEMENT
        elif (choice == "2" and not api_failures) or (choice == "3" and api_failures):
            PROGRESS_FILE.unlink(missing_ok=True)
            print("  → 새로 시작합니다.\n")
            return RESUME_NEW
        else:
            print(f"  {'/'.join(sorted(valid_choices))} 중 하나를 입력하세요.")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="네이버 데이터랩 건강식품 트렌드 분석기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  python main.py                                      기본 실행 (상위 50개)
  python main.py --top 30                             상위 30개 키워드만 분석
  python main.py --no-scrape                          캐시된 키워드 사용 (재스크래핑 없이)
  python main.py --output my_report.txt               출력 파일명 지정
  python main.py --keywords 홍삼 비타민C               지정 키워드만 분석
  python main.py --reuse-json 결과값/report_XXX.json  기존 분석 재사용 (검색량만 보완)
        """,
    )
    parser.add_argument(
        "--top",
        type=int,
        default=int(os.getenv("TOP_KEYWORDS_COUNT", "50")),
        help="분석할 상위 키워드 수 (기본값: 50)",
    )
    parser.add_argument(
        "--no-scrape",
        action="store_true",
        help="스크레이핑 없이 캐시된 키워드 사용",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="결과 저장 파일명 (기본: report_YYYYMMDD_HHMMSS.txt)",
    )
    parser.add_argument(
        "--no-chart",
        action="store_true",
        help="트렌드 차트 생성 건너뜀",
    )
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=None,
        help="직접 키워드 지정 (스크래핑 건너뜀)",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="AI 브리핑 생성 건너뜀 (데이터 분석만 수행)",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="이전 진행 데이터를 무시하고 처음부터 새로 시작",
    )
    parser.add_argument(
        "--reuse-json",
        type=str,
        default=None,
        metavar="JSON파일",
        help=(
            "기존 분석 JSON 파일을 재사용하여 리포트 재생성 (트렌드 수집 건너뜀).\n"
            "검색량(PC/모바일)만 새로 수집 후 AI 브리핑 + 리포트 + PDF를 다시 만듭니다.\n"
            "예) --reuse-json 결과값/report_20260405_205341.json"
        ),
    )
    return parser.parse_args()


def load_latest_tv_data() -> dict | None:
    """결과값/tv_monitor/ 폴더에서 가장 최근 TV 모니터링 JSON을 로드."""
    tv_dir = PROJECT_DIR / "결과값" / "tv_monitor"
    if not tv_dir.exists():
        return None
    files = sorted(tv_dir.glob("tv_report_*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[TV데이터] 최근 TV 리포트 로드: {files[0].name}")
        count = len(data.get("aggregated_ingredients", {}))
        print(f"[TV데이터] 언급 성분 {count}개 포함")
        return data
    except Exception as e:
        print(f"[TV데이터] 로드 실패: {e}")
        return None


def print_banner():
    """Print program banner."""
    print("\n" + "=" * 60)
    print("  네이버 데이터랩 건강관련 전체 트렌드 분석기")
    print(f"  실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)


def check_env_vars(skip_ai: bool = False) -> bool:
    """Check that required environment variables are set."""
    required = {
        "NAVER_CLIENT_ID": "네이버 API Client ID",
        "NAVER_CLIENT_SECRET": "네이버 API Client Secret",
    }
    if not skip_ai:
        required["ANTHROPIC_API_KEY"] = "Anthropic API Key"

    missing = []
    for var, name in required.items():
        if not os.getenv(var):
            missing.append(f"  - {var} ({name})")

    if missing:
        print("\n[오류] 다음 환경 변수가 설정되지 않았습니다:")
        for m in missing:
            print(m)
        print("\n.env 파일을 생성하거나 .env.example을 참고하세요.")
        print("  cp .env.example .env  # 그리고 값을 채워넣으세요")
        return False

    return True


def send_telegram_report(analyzed: list[dict], pdf_path, txt_path, is_critical: bool = False, category_notes: dict = None) -> None:
    """텔레그램으로 분석 완료 알림 및 PDF 파일 전송."""
    import requests as _requests

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("[텔레그램] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID가 .env에 없습니다. 건너뜁니다.")
        return

    today = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")

    # 오류 과다 시 실패 알림 발송 후 종료
    if is_critical:
        no_data = sum(1 for k in analyzed if k.get("data_quality") == "no_data")
        total = len(analyzed)
        message = (
            f"❌ [건강관련 전체 트렌드 분석 실패]\n\n"
            f"📅 {today}\n"
            f"⚠️ 데이터 오류가 너무 많아 리포트를 생성하지 않았습니다.\n\n"
            f"📊 전체 키워드: {total}개\n"
            f"🚫 데이터 없음: {no_data}개 ({no_data/total*100:.0f}%)\n\n"
            f"👉 다음 실행 시 자동으로 이어서 진행됩니다.\n"
            f"   처음부터 다시 하려면: bash run.sh --fresh"
        )
        try:
            _requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": message},
                timeout=10,
            )
            print("[텔레그램] 실패 알림 발송 완료")
        except Exception as e:
            print(f"[텔레그램] 실패 알림 발송 오류: {e}")
        return

    # 정상 완료 메시지
    total = len(analyzed)
    early = sum(1 for k in analyzed if k.get("trend_phase") == "early_rising")
    growing = sum(1 for k in analyzed if k.get("trend_phase") == "growing")
    top3 = [k["keyword"] for k in analyzed[:3]]

    category_warning = ""
    if category_notes:
        lines = [f"  • {name}: {note}" for name, note in list(category_notes.items())[:5]]
        category_warning = f"\n\n⚠️ 카테고리 수집 이슈 ({len(category_notes)}건):\n" + "\n".join(lines)

    message = (
        f"✅ [건강관련 전체 트렌드 분석 완료 — 식품·생활건강·출산육아·화장품·가전]\n\n"
        f"📅 {today}\n"
        f"🔍 분석 키워드: {total}개\n"
        f"🚀 얼리라이징: {early}개  |  성장중: {growing}개\n"
        f"🏆 기회점수 TOP 3: {', '.join(top3)}"
        f"{category_warning}\n\n"
        f"📄 PDF 리포트를 첨부합니다."
    )

    # 텍스트 메시지 발송
    try:
        _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message},
            timeout=10,
        )
        print("[텔레그램] 메시지 발송 완료")
    except Exception as e:
        print(f"[텔레그램] 메시지 발송 실패: {e}")
        return

    # PDF 파일 전송
    pdf = Path(pdf_path) if pdf_path else None
    if pdf and pdf.exists():
        try:
            with open(pdf, "rb") as f:
                _requests.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": chat_id},
                    files={"document": (pdf.name, f, "application/pdf")},
                    timeout=60,
                )
            print(f"[텔레그램] PDF 전송 완료: {pdf.name}")
        except Exception as e:
            print(f"[텔레그램] PDF 전송 실패: {e}")
    else:
        print("[텔레그램] PDF 파일 없음 — 메시지만 발송됨")


def send_email_report(pdf_path, txt_path) -> None:
    """이메일로 PDF 리포트 발송 (.env에 EMAIL_* 설정 시에만 동작)."""
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    user = os.getenv("EMAIL_USER", "").strip()
    password = os.getenv("EMAIL_PASSWORD", "").strip()
    to = os.getenv("EMAIL_TO", "").strip()

    if not user or not password or not to:
        print("[이메일] EMAIL_USER/EMAIL_PASSWORD/EMAIL_TO가 .env에 없습니다. 건너뜁니다.")
        return

    today = datetime.now().strftime("%Y년 %m월 %d일")
    pdf = Path(pdf_path) if pdf_path else None

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = f"[건강관련 전체 트렌드] {today} 리포트"
    msg.attach(MIMEText("PDF 리포트를 첨부합니다.", "plain", "utf-8"))

    if pdf and pdf.exists():
        with open(pdf, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={pdf.name}")
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(user, password)
            server.sendmail(user, to, msg.as_string())
        print(f"[이메일] 발송 완료 → {to}")
    except Exception as e:
        print(f"[이메일] 발송 실패: {e}")


async def step1_scrape_keywords(args) -> tuple:
    """Step 1: Scrape keywords from Naver Shopping Insight."""
    print("\n" + "-" * 50)
    print("[1단계] 키워드 수집")
    print("-" * 50)

    if args.keywords:
        print(f"[1단계] 직접 지정된 키워드 사용: {args.keywords}")
        return args.keywords, {}

    from scraper import get_all_period_keywords, CACHE_FILE

    use_cache = args.no_scrape

    if use_cache and CACHE_FILE.exists():
        print(f"[1단계] 캐시 파일 사용: {CACHE_FILE}")
    elif use_cache and not CACHE_FILE.exists():
        print("[1단계] 캐시 파일이 없습니다. 스크래핑을 시작합니다...")
        use_cache = False

    scrape_results = await get_all_period_keywords(max_rank=args.top, use_cache=use_cache)

    # Get combined unique keywords
    combined = scrape_results.get("combined", [])
    keywords = [kw["keyword"] for kw in combined[:args.top]]

    # Also collect from individual periods
    if not keywords:
        all_kws = set()
        for period in ["1년", "3개월", "1개월"]:
            for kw_data in scrape_results.get(period, []):
                all_kws.add(kw_data["keyword"])
        keywords = list(all_kws)[:args.top]

    print(f"\n[1단계] 총 {len(keywords)}개 고유 키워드 수집 완료")
    if keywords:
        print(f"[1단계] 상위 10개: {', '.join(keywords[:10])}")

    return keywords, scrape_results


async def step2_fetch_trend_data(
    keywords: list[str],
    existing_data: dict = None,
    on_period_complete=None,
) -> dict:
    """Step 2: Fetch trend data from Naver DataLab API."""
    print("\n" + "-" * 50)
    print("[2단계] 트렌드 데이터 수집 (네이버 DataLab API)")
    print("-" * 50)

    from naver_api import get_all_trend_data

    start_time = time.time()
    trend_data = get_all_trend_data(
        keywords,
        existing_data=existing_data,
        on_period_complete=on_period_complete,
    )
    elapsed = time.time() - start_time

    has_data = sum(
        1 for kw in keywords
        if any(trend_data.get(k, {}).get(kw) for k in trend_data)
    )

    print(f"\n[2단계] 완료 (소요시간: {elapsed:.1f}초)")
    print(f"[2단계] 데이터 있는 키워드: {has_data}/{len(keywords)}개")

    return trend_data


def step3_analyze_trends(keywords: list[str], trend_data: dict) -> list[dict]:
    """Step 3: Analyze trend data."""
    print("\n" + "-" * 50)
    print("[3단계] 트렌드 분석")
    print("-" * 50)

    from analyzer import TrendAnalyzer

    analyzer = TrendAnalyzer()
    analyzed = analyzer.analyze_keywords(trend_data)

    # Get summary stats
    summary = analyzer.get_summary_stats(analyzed)

    print(f"\n[3단계] 분석 완료")
    print(f"[3단계] 트렌드 단계 분포: {summary.get('phase_distribution', {})}")
    print(f"[3단계] 얼리라이징 키워드: {summary.get('early_rising_count', 0)}개")
    print(f"[3단계] 평균 성장률: {summary.get('avg_recent_growth_rate', 0):.1f}%")
    print(f"[3단계] 최고 기회 키워드: {summary.get('top_opportunity', 'N/A')}")

    return analyzed


def step4_generate_briefing(analyzed: list[dict], trend_data: dict) -> str:
    """Step 4: Generate AI briefing."""
    print("\n" + "-" * 50)
    print("[4단계] AI 브리핑 생성")
    print("-" * 50)

    from reporter import generate_briefing

    briefing = generate_briefing(analyzed, trend_data)
    return briefing


def step5_generate_charts(analyzed: list[dict], trend_data: dict, output_dir: Path) -> list[str]:
    """Step 5: Generate trend charts using matplotlib."""
    print("\n" + "-" * 50)
    print("[5단계] 트렌드 차트 생성")
    print("-" * 50)

    try:
        import matplotlib
        matplotlib.use("Agg")  # Non-interactive backend
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import numpy as np
    except ImportError:
        print("[5단계] matplotlib이 설치되지 않아 차트 생성을 건너뜁니다.")
        return []

    chart_files = []

    # Try to set Korean font
    # 1) 폰트 파일 직접 지정 (Linux/Windows 경로 순서대로 시도)
    korean_font_files = [
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/nanum/NanumGothic.ttf",
        "C:/Windows/Fonts/malgun.ttf",
    ]
    font_set = False
    for font_file in korean_font_files:
        if Path(font_file).exists():
            try:
                fm.fontManager.addfont(font_file)
                prop = fm.FontProperties(fname=font_file)
                plt.rcParams["font.family"] = prop.get_name()
                plt.rcParams["axes.unicode_minus"] = False
                font_set = True
                print(f"[5단계] 한글 폰트 설정: {font_file}")
                break
            except Exception:
                continue

    # 2) 폰트 파일 없으면 캐시 갱신 후 이름으로 재시도
    if not font_set:
        fm.fontManager.__init__()  # 캐시 갱신
        for font_name in ["NanumGothic", "Malgun Gothic", "AppleGothic"]:
            if any(font_name in f.name for f in fm.fontManager.ttflist):
                plt.rcParams["font.family"] = font_name
                plt.rcParams["axes.unicode_minus"] = False
                font_set = True
                print(f"[5단계] 한글 폰트 설정: {font_name}")
                break

    if not font_set:
        print("[5단계] 한글 폰트를 찾지 못했습니다. 영문으로 차트를 생성합니다.")

    # Chart 1: Opportunity Score Bar Chart
    try:
        top_n = min(15, len(analyzed))
        top_analyzed = analyzed[:top_n]

        fig, ax = plt.subplots(figsize=(12, 7))

        keywords = [a["keyword"] for a in top_analyzed]
        scores = [a["opportunity_score"] for a in top_analyzed]
        phases = [a["trend_phase"] for a in top_analyzed]

        # Color by trend phase
        phase_colors = {
            "early_rising": "#FF4B4B",
            "growing": "#FF8C00",
            "stable": "#4CAF50",
            "peak": "#2196F3",
            "declining": "#9E9E9E",
            "unknown": "#CCCCCC",
        }
        colors = [phase_colors.get(p, "#CCCCCC") for p in phases]

        bars = ax.barh(range(len(keywords)), scores, color=colors, edgecolor="white", linewidth=0.5)
        ax.set_yticks(range(len(keywords)))
        ax.set_yticklabels(keywords, fontsize=10)
        ax.set_xlabel("기회 점수 (0-100)", fontsize=11)
        ax.set_title("건강식품 키워드 시장 기회 점수 TOP 15", fontsize=14, fontweight="bold", pad=15)
        ax.set_xlim(0, 105)
        ax.invert_yaxis()

        # Add value labels
        for bar, score in zip(bars, scores):
            ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                    f"{score:.1f}", va="center", fontsize=9)

        # Legend
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor=c, label=p)
            for p, c in phase_colors.items()
            if p in phases
        ]
        ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

        plt.tight_layout()
        chart1_path = output_dir / "chart_opportunity_scores.png"
        plt.savefig(chart1_path, dpi=150, bbox_inches="tight")
        plt.close()
        chart_files.append(str(chart1_path))
        print(f"[5단계] 차트 저장: {chart1_path.name}")
    except Exception as e:
        print(f"[5단계] 기회 점수 차트 생성 실패: {e}")

    # Chart 2: Long-term trend lines for top 5 keywords
    try:
        top5 = [a for a in analyzed[:10] if trend_data.get("longterm", {}).get(a["keyword"])][:5]

        if top5:
            # 모든 키워드의 날짜를 수집해 공통 날짜 축 생성
            all_dates = set()
            kw_series = {}
            for kw_data in top5:
                kw = kw_data["keyword"]
                lt = trend_data.get("longterm", {}).get(kw, [])
                if not lt:
                    continue
                series = {d["period"]: d["ratio"] for d in lt}
                kw_series[kw] = series
                all_dates.update(series.keys())

            # 날짜 정렬 (YYYY-MM 형식 문자열 정렬)
            sorted_dates = sorted(all_dates)

            fig, ax = plt.subplots(figsize=(14, 7))
            colors_line = ["#FF4B4B", "#FF8C00", "#4CAF50", "#2196F3", "#9C27B0"]

            for i, (kw, series) in enumerate(kw_series.items()):  # noqa: B007
                y_vals = [series.get(d, np.nan) for d in sorted_dates]
                ax.plot(range(len(sorted_dates)), y_vals,
                        color=colors_line[i % len(colors_line)], label=kw,
                        linewidth=2, marker="o", markersize=3)

            # X축: 연도 단위로 눈금 표시
            year_ticks = [i for i, d in enumerate(sorted_dates) if d.endswith("-01")]
            year_labels = [d[:4] for i, d in enumerate(sorted_dates) if d.endswith("-01")]
            ax.set_xticks(year_ticks)
            ax.set_xticklabels(year_labels, fontsize=9)

            ax.set_title("건강식품 키워드 장기 트렌드 (2016~현재)", fontsize=14, fontweight="bold", pad=15)
            ax.set_ylabel("검색량 지수 (0-100)", fontsize=11)
            ax.set_xlabel("연도", fontsize=11)
            ax.legend(fontsize=10)
            ax.grid(True, alpha=0.3)

            plt.tight_layout()
            chart2_path = output_dir / "chart_longterm_trends.png"
            plt.savefig(chart2_path, dpi=150, bbox_inches="tight")
            plt.close()
            chart_files.append(str(chart2_path))
            print(f"[5단계] 차트 저장: {chart2_path.name}")
    except Exception as e:
        print(f"[5단계] 장기 트렌드 차트 생성 실패: {e}")

    # Chart 3: Scatter plot - Growth Rate vs Early Mover Score
    try:
        valid = [a for a in analyzed if a["data_quality"] != "no_data"]

        if len(valid) >= 3:
            fig, ax = plt.subplots(figsize=(14, 9))

            phase_colors = {
                "early_rising": "#FF4B4B",
                "growing": "#FF8C00",
                "stable": "#4CAF50",
                "peak": "#2196F3",
                "declining": "#9E9E9E",
                "unknown": "#CCCCCC",
            }

            for item in valid:
                color = phase_colors.get(item["trend_phase"], "#CCCCCC")
                ax.scatter(item["recent_growth_rate"], item["early_mover_score"],
                           c=color, s=80, alpha=0.7, zorder=2)

            # Label top candidates — adjustText로 겹침 방지, 없으면 화살표로 대체
            top_candidates = sorted(valid, key=lambda x: x["opportunity_score"], reverse=True)[:15]
            texts = []
            try:
                from adjustText import adjust_text
                for item in top_candidates:
                    t = ax.text(
                        item["recent_growth_rate"], item["early_mover_score"],
                        item["keyword"], fontsize=8, zorder=5,
                    )
                    texts.append(t)
                adjust_text(
                    texts, ax=ax,
                    arrowprops=dict(arrowstyle="-", color="gray", lw=0.6),
                    expand_points=(1.5, 1.5),
                )
            except ImportError:
                # adjustText 없으면 방향을 분산해서 표시
                for idx, item in enumerate(top_candidates):
                    offset_x = [8, -60, 8, -60, 8, -60, 8, -60, 8, -60, 8, -60, 8, -60, 8][idx]
                    offset_y = [8, 8, -14, -14, 22, 22, -28, -28, 36, 36, -42, -42, 50, -50, 50][idx]
                    ax.annotate(
                        item["keyword"],
                        (item["recent_growth_rate"], item["early_mover_score"]),
                        textcoords="offset points", xytext=(offset_x, offset_y),
                        fontsize=8, alpha=0.9,
                        arrowprops=dict(arrowstyle="-", color="gray", lw=0.5),
                    )

            ax.axvline(x=0, color="gray", linestyle="--", alpha=0.5)
            ax.axhline(y=50, color="gray", linestyle="--", alpha=0.5)
            ax.set_xlabel("최근 성장률 (%)", fontsize=11)
            ax.set_ylabel("얼리무버 점수 (0-100)", fontsize=11)
            ax.set_title("키워드 포지셔닝 맵: 성장률 vs 얼리무버 점수", fontsize=14, fontweight="bold", pad=15)

            from matplotlib.patches import Patch
            legend_elements = [
                Patch(facecolor=c, label=p)
                for p, c in phase_colors.items()
                if any(a["trend_phase"] == p for a in valid)
            ]
            ax.legend(handles=legend_elements, loc="lower right", fontsize=9)
            ax.grid(True, alpha=0.3)

            plt.tight_layout()
            chart3_path = output_dir / "chart_positioning_map.png"
            plt.savefig(chart3_path, dpi=150, bbox_inches="tight")
            plt.close()
            chart_files.append(str(chart3_path))
            print(f"[5단계] 차트 저장: {chart3_path.name}")
    except Exception as e:
        print(f"[5단계] 포지셔닝 맵 차트 생성 실패: {e}")

    print(f"\n[5단계] 총 {len(chart_files)}개 차트 생성 완료")
    return chart_files


def step6_save_report(
    report_text: str,
    analyzed: list[dict],
    trend_data: dict,
    output_path: Path,
    chart_files: list[str],
) -> None:
    """Step 6: Save the report to file."""
    print("\n" + "-" * 50)
    print("[6단계] 리포트 저장")
    print("-" * 50)

    # Save main text report
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)
        if chart_files:
            f.write("\n\n## 생성된 차트 파일\n")
            for chart in chart_files:
                f.write(f"  - {chart}\n")

    print(f"[6단계] 텍스트 리포트 저장: {output_path}")

    # Save raw analysis data as JSON
    json_path = output_path.with_suffix(".json")
    analysis_data = {
        "generated_at": datetime.now().isoformat(),
        "analyzed_keywords": analyzed,
        "charts": chart_files,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(analysis_data, f, ensure_ascii=False, indent=2)
    print(f"[6단계] 분석 데이터 저장: {json_path}")


async def _run_reuse_json_mode(args, output_path: Path, output_dir: Path) -> None:
    """
    --reuse-json 모드:
    기존 분석 JSON 파일에서 analyzed_keywords를 불러온 뒤,
    PC/모바일 검색량만 새로 수집하여 AI 브리핑 + 리포트 + PDF를 재생성합니다.
    트렌드 데이터 재수집 없이 약 10~15분 안에 완료됩니다.
    """
    total_start = time.time()

    json_path = Path(args.reuse_json)
    if not json_path.is_absolute():
        json_path = PROJECT_DIR / json_path

    if not json_path.exists():
        print(f"\n[오류] JSON 파일을 찾을 수 없습니다: {json_path}")
        sys.exit(1)

    print("\n" + "-" * 50)
    print("[1단계] 기존 분석 데이터 불러오기")
    print("-" * 50)

    with open(json_path, "r", encoding="utf-8") as f:
        saved = json.load(f)

    analyzed: list[dict] = saved.get("analyzed_keywords", [])
    if not analyzed:
        print("[오류] JSON 파일에 analyzed_keywords 데이터가 없습니다.")
        sys.exit(1)

    keywords = [kw["keyword"] for kw in analyzed]
    print(f"[1단계] {len(keywords)}개 키워드 불러옴: {', '.join(keywords[:10])}{'...' if len(keywords) > 10 else ''}")
    print(f"[1단계] 원본 생성일시: {saved.get('generated_at', '알 수 없음')}")

    # 검색량 데이터가 이미 있는 키워드 확인
    already_has_volume = sum(1 for kw in analyzed if kw.get("monthly_total_search", 0) > 0)
    print(f"[1단계] 기존 검색량 보유: {already_has_volume}/{len(keywords)}개 (새로 수집하여 덮어씁니다)")

    # === 검색량 새로 수집 ===
    from keyword_volume import get_search_volumes, merge_volumes_into_analyzed
    print("\n" + "-" * 50)
    print("[2단계] 월간 검색량 재수집 (네이버 검색광고 API)")
    print("-" * 50)
    volumes = get_search_volumes(keywords)
    if volumes:
        merge_volumes_into_analyzed(analyzed, volumes)
        found = sum(1 for kw in analyzed if kw.get("monthly_total_search", 0) > 0)
        print(f"[2단계] 검색량 수집 완료: {found}/{len(keywords)}개")
    else:
        print("[2단계] 검색량 수집 불가 (광고 API 키 미설정 또는 오류)")

    # trend_data는 없으므로 빈 dict 사용 (AI 브리핑에 일부 정보 제한)
    trend_data: dict = {}

    # === AI 브리핑 재생성 ===
    briefing = ""
    if not args.no_ai:
        print("\n" + "-" * 50)
        print("[3단계] AI 브리핑 재생성")
        print("-" * 50)
        try:
            briefing = step4_generate_briefing(analyzed, trend_data)
        except Exception as e:
            print(f"[3단계] AI 브리핑 생성 실패: {e}")
            briefing = f"[AI 브리핑 생성 실패: {e}]\n\n분석 데이터를 참고하세요."
    else:
        print("\n[3단계] AI 브리핑 건너뜀 (--no-ai 옵션)")
        briefing = "AI 브리핑이 비활성화되었습니다."

    # === 차트 재생성 ===
    chart_files: list[str] = []
    if not args.no_chart:
        print("\n" + "-" * 50)
        print("[4단계] 차트 재생성")
        print("-" * 50)
        try:
            chart_files = step5_generate_charts(analyzed, trend_data, output_dir)
        except Exception as e:
            print(f"[4단계] 차트 생성 실패: {e}")
    else:
        print("\n[4단계] 차트 생성 건너뜀 (--no-chart 옵션)")

    # === 리포트 저장 ===
    from reporter import format_report, format_missing_data_warning, _is_missing_data_critical

    is_critical_missing, missing_kws = _is_missing_data_critical(analyzed, trend_data)
    tv_data = load_latest_tv_data()

    if is_critical_missing:
        print(f"\n[5단계] ⚠️  중요 데이터 누락 감지 ({len(missing_kws)}개 키워드) — 경고 요약본 생성")
        full_report = format_missing_data_warning(analyzed, trend_data)
    else:
        full_report = format_report(briefing, analyzed, None, trend_data, tv_data)

    step6_save_report(full_report, analyzed, trend_data, output_path, chart_files)

    # === PDF 생성 ===
    print("\n" + "-" * 50)
    print("[6단계] PDF 리포트 생성")
    print("-" * 50)
    pdf_path = None
    if is_critical_missing:
        print("[6단계] 데이터 누락 경고 모드 — PDF 생성 건너뜀")
    else:
        try:
            from pdf_report import generate_pdf
            pdf_path = generate_pdf(
                output_path=output_path.with_suffix(".pdf"),
                analyzed_keywords=analyzed,
                briefing=briefing,
                scrape_results=None,
                trend_data=trend_data,
                chart_files=chart_files,
                tv_data=tv_data,
            )
        except Exception as e:
            print(f"[6단계] PDF 생성 실패: {e}")

    # === 텔레그램 알림 발송 ===
    print("\n" + "-" * 50)
    print("[7단계] 텔레그램 알림 발송")
    print("-" * 50)
    send_telegram_report(analyzed, pdf_path, output_path, is_critical=is_critical_missing)

    elapsed = time.time() - total_start
    print(f"\n[완료] 소요 시간: {elapsed:.1f}초")
    print(f"[완료] 결과 폴더: {output_dir}")
    print(f"[완료] TXT 리포트: {output_path.name}")
    if pdf_path:
        print(f"[완료] PDF 리포트: {pdf_path.name}")


async def main():
    """Main orchestrator function."""
    print_banner()
    args = parse_args()

    # Validate environment variables
    if not check_env_vars(skip_ai=args.no_ai):
        sys.exit(1)

    # Ensure output directory exists
    output_dir = PROJECT_DIR / "결과값"
    output_dir.mkdir(exist_ok=True)

    # Determine output path
    if args.output:
        output_path = output_dir / args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"report_{timestamp}.txt"

    print(f"\n[설정]")
    if args.reuse_json:
        print(f"  - 모드: JSON 재사용 (트렌드 수집 건너뜀)")
        print(f"  - 원본 JSON: {args.reuse_json}")
    else:
        print(f"  - 분석 키워드 수: {args.top}개")
        print(f"  - 스크래핑: {'건너뜀 (캐시 사용)' if args.no_scrape else '실행'}")
    print(f"  - AI 브리핑: {'건너뜀' if args.no_ai else '생성'}")
    print(f"  - 차트 생성: {'건너뜀' if args.no_chart else '생성'}")
    print(f"  - 출력 파일: {output_path}")

    total_start = time.time()

    # === --reuse-json 모드: 기존 JSON에서 분석 결과를 불러와 검색량만 보완 ===
    if args.reuse_json:
        await _run_reuse_json_mode(args, output_path, output_dir)
        return

    # === 이어하기 확인 ===
    if args.fresh:
        PROGRESS_FILE.unlink(missing_ok=True)
        print("[설정] --fresh 옵션 — 이전 데이터 삭제 후 처음부터 시작\n")
    progress = load_progress()
    resume_mode = RESUME_NEW  # 기본값: 새로 시작
    saved_keywords = None
    saved_scrape_results = {}
    saved_trend_data = None
    saved_analyzed = None
    saved_briefing = None
    last_step = 0

    if progress:
        resume_mode = ask_resume(progress)
        if resume_mode in (RESUME_CONTINUE, RESUME_SUPPLEMENT):
            last_step = progress.get("step", 0)
            saved_keywords = progress.get("keywords")
            saved_scrape_results = progress.get("scrape_results", {})
            saved_trend_data = progress.get("trend_data")
            saved_analyzed = progress.get("analyzed")
            saved_briefing = progress.get("briefing")
            saved_out = progress.get("output_path")
            if saved_out:
                output_path = Path(saved_out)

    resuming = resume_mode == RESUME_CONTINUE
    supplementing = resume_mode == RESUME_SUPPLEMENT

    # === Step 1: Scrape keywords ===
    if resuming and saved_keywords:
        keywords = saved_keywords
        scrape_results = saved_scrape_results
        print(f"\n[1단계] 이전 작업에서 키워드 불러옴: {len(keywords)}개 (건너뜀)")
    else:
        keywords, scrape_results = await step1_scrape_keywords(args)
        save_progress(1, {
            "keywords": keywords,
            "scrape_results": scrape_results,
            "output_path": str(output_path),
        })

    if not keywords:
        print("\n[오류] 키워드를 수집하지 못했습니다. 프로그램을 종료합니다.")
        sys.exit(1)

    print(f"\n[진행] {len(keywords)}개 키워드 분석 시작")

    # === Step 2: Fetch trend data ===
    if resuming and saved_trend_data and last_step >= 2:
        trend_data = saved_trend_data
        kw_count = sum(
            1 for kw in keywords
            if any(saved_trend_data.get(k, {}).get(kw) for k in ["longterm", "shortterm_1yr"])
        )
        print(f"\n[2단계] 이전 작업에서 트렌드 데이터 불러옴: {kw_count}개 키워드 (건너뜀)")
    elif supplementing and saved_trend_data:
        # 누락 키워드만 보완: 기존 데이터를 유지하고 실패한 키워드만 재수집
        failed_keywords = list((saved_trend_data or {}).get("_api_failures", {}).keys())
        if failed_keywords:
            print(f"\n[2단계] 누락 키워드 보완 모드: {len(failed_keywords)}개 키워드 재수집")
            print(f"  재수집 대상: {', '.join(failed_keywords[:10])}")

            def _save_partial_suppl(partial_results):
                save_progress(2, {
                    "keywords": keywords,
                    "scrape_results": scrape_results,
                    "trend_data": partial_results,
                    "output_path": str(output_path),
                })

            trend_data = await step2_fetch_trend_data(
                failed_keywords,
                existing_data=saved_trend_data,
                on_period_complete=_save_partial_suppl,
            )
        else:
            print(f"\n[2단계] 보완할 누락 키워드 없음. 저장된 데이터 사용.")
            trend_data = saved_trend_data
        save_progress(2, {
            "keywords": keywords,
            "scrape_results": scrape_results,
            "trend_data": trend_data,
            "output_path": str(output_path),
        })
    else:
        # 이어하기인데 step 1만 완료된 경우: 부분 데이터 있을 수 있음
        existing = saved_trend_data if resuming else None

        def _save_partial(partial_results):
            save_progress(2, {
                "keywords": keywords,
                "scrape_results": scrape_results,
                "trend_data": partial_results,
                "output_path": str(output_path),
            })

        trend_data = await step2_fetch_trend_data(
            keywords,
            existing_data=existing,
            on_period_complete=_save_partial,
        )
        save_progress(2, {
            "keywords": keywords,
            "scrape_results": scrape_results,
            "trend_data": trend_data,
            "output_path": str(output_path),
        })

    # === Step 2.5: Fetch search volumes ===
    from keyword_volume import get_search_volumes, merge_volumes_into_analyzed
    print("\n" + "-" * 50)
    print("[2.5단계] 월간 검색량 조회 (네이버 검색광고 API)")
    print("-" * 50)
    volumes = get_search_volumes(keywords)

    # === Step 3: Analyze trends ===
    if resuming and saved_analyzed and last_step >= 3:
        analyzed = saved_analyzed
        print(f"\n[3단계] 이전 작업에서 분석 결과 불러옴: {len(analyzed)}개 (건너뜀)")
    else:
        analyzed = step3_analyze_trends(keywords, trend_data)
        save_progress(3, {
            "keywords": keywords,
            "scrape_results": scrape_results,
            "trend_data": trend_data,
            "analyzed": analyzed,
            "output_path": str(output_path),
        })

    # Merge search volumes into analysis results
    if volumes:
        merge_volumes_into_analyzed(analyzed, volumes)

    if not analyzed:
        print("\n[오류] 트렌드 분석 결과가 없습니다.")
        sys.exit(1)

    # === Step 4: Generate AI briefing ===
    briefing = ""
    if not args.no_ai:
        if resuming and saved_briefing and last_step >= 4:
            briefing = saved_briefing
            print(f"\n[4단계] 이전 작업에서 AI 브리핑 불러옴 (건너뜀)")
        else:
            try:
                briefing = step4_generate_briefing(analyzed, trend_data)
                save_progress(4, {
                    "keywords": keywords,
                    "scrape_results": scrape_results,
                    "trend_data": trend_data,
                    "analyzed": analyzed,
                    "briefing": briefing,
                    "output_path": str(output_path),
                })
            except Exception as e:
                print(f"\n[4단계] AI 브리핑 생성 실패: {e}")
                briefing = f"[AI 브리핑 생성 실패: {e}]\n\n분석 데이터를 참고하세요."
    else:
        print("\n[4단계] AI 브리핑 건너뜀 (--no-ai 옵션)")
        briefing = "AI 브리핑이 비활성화되었습니다. --no-ai 옵션 없이 실행하면 생성됩니다."

    # === Step 5: Generate charts ===
    chart_files = []
    if not args.no_chart:
        try:
            chart_files = step5_generate_charts(analyzed, trend_data, output_dir)
        except Exception as e:
            print(f"\n[5단계] 차트 생성 실패: {e}")
    else:
        print("\n[5단계] 차트 생성 건너뜀 (--no-chart 옵션)")

    # === Step 6: Format and save report ===
    from reporter import format_report, format_missing_data_warning, _is_missing_data_critical

    is_critical_missing, missing_kws = _is_missing_data_critical(analyzed, trend_data)

    tv_data = load_latest_tv_data()

    category_notes = scrape_results.get("category_notes", {}) if scrape_results else {}

    if is_critical_missing:
        print(f"\n[6단계] ⚠️  중요 데이터 누락 감지 ({len(missing_kws)}개 키워드)")
        print("  → 불완전한 리포트 대신 누락 경고 요약본을 생성합니다.")
        full_report = format_missing_data_warning(analyzed, trend_data)
    else:
        full_report = format_report(
            briefing, analyzed,
            scrape_results if scrape_results else None,
            trend_data, tv_data,
            category_notes=category_notes,
        )

    step6_save_report(full_report, analyzed, trend_data, output_path, chart_files)

    # === Step 7: Generate PDF ===
    print("\n" + "-" * 50)
    print("[7단계] PDF 리포트 생성")
    print("-" * 50)
    pdf_path = None
    if is_critical_missing:
        print("[7단계] 데이터 누락 경고 모드 — PDF 생성 건너뜀")
    else:
        try:
            from pdf_report import generate_pdf
            pdf_path = generate_pdf(
                output_path=output_path.with_suffix(".pdf"),
                analyzed_keywords=analyzed,
                briefing=briefing,
                scrape_results=scrape_results if scrape_results else None,
                trend_data=trend_data,
                chart_files=chart_files,
                tv_data=tv_data,
            )
        except Exception as e:
            print(f"[7단계] PDF 생성 실패: {e}")

    # === Print report to console ===
    print("\n" + "=" * 60)
    print("  최종 리포트")
    print("=" * 60)
    print(full_report)

    # === Step 8: 텔레그램 알림 발송 ===
    print("\n" + "-" * 50)
    print("[8단계] 텔레그램 알림 발송")
    print("-" * 50)
    send_telegram_report(analyzed, pdf_path, output_path, is_critical=is_critical_missing, category_notes=category_notes or {})

    # 정상 완료 시 진행 파일 삭제
    PROGRESS_FILE.unlink(missing_ok=True)

    total_elapsed = time.time() - total_start
    print(f"\n[완료] 전체 실행 시간: {total_elapsed:.1f}초")
    print(f"[완료] 결과 폴더: {output_dir}")
    print(f"[완료] PDF 리포트: {pdf_path.name if pdf_path else '생성 실패'}")
    print(f"[완료] TXT 리포트: {output_path.name}")
    if chart_files:
        print(f"[완료] 차트 파일: {', '.join([Path(c).name for c in chart_files])}")


if __name__ == "__main__":
    asyncio.run(main())
