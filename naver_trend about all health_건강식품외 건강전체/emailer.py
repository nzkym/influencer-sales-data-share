"""
이메일 발송 모듈.
분석 완료 후 PDF 리포트를 지정된 이메일 주소로 발송합니다.

설정 (.env):
    EMAIL_FROM        : 보내는 사람 이메일 (Gmail 또는 Naver)
    EMAIL_PASSWORD    : 앱 비밀번호
                        - Gmail: Google 계정 > 보안 > 앱 비밀번호 생성
                        - Naver: 네이버 메일 > 환경설정 > POP3/IMAP 설정 > 앱 비밀번호
    EMAIL_SMTP_SERVER : smtp.gmail.com (기본) 또는 smtp.naver.com
    EMAIL_SMTP_PORT   : 587 (기본, STARTTLS)
    EMAIL_TO          : 받는 사람 이메일 (기본: hsbchong@naver.com)
"""

import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DEFAULT_TO = "hsbchong@naver.com"


def send_report(
    pdf_path: Path,
    txt_path: Path | None = None,
    analyzed_keywords: list[dict] | None = None,
) -> bool:
    """
    PDF 리포트를 이메일로 발송합니다.

    Args:
        pdf_path  : 첨부할 PDF 파일 경로
        txt_path  : 첨부할 TXT 파일 경로 (선택)
        analyzed_keywords: 분석 결과 (이메일 본문 요약에 사용)

    Returns:
        True if sent successfully, False otherwise.
    """
    smtp_server = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
    smtp_port   = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    from_addr   = os.getenv("EMAIL_FROM", "").strip()
    password    = os.getenv("EMAIL_PASSWORD", "").strip()
    to_addr     = os.getenv("EMAIL_TO", DEFAULT_TO).strip()

    if not from_addr or not password:
        print(
            "\n[이메일] 발송 건너뜀 — .env에 EMAIL_FROM 또는 EMAIL_PASSWORD가 설정되지 않았습니다.\n"
            "  설정 방법:\n"
            "    EMAIL_FROM=본인이메일@gmail.com\n"
            "    EMAIL_PASSWORD=앱비밀번호  (구글 앱 비밀번호 또는 네이버 앱 비밀번호)\n"
            "    EMAIL_SMTP_SERVER=smtp.gmail.com  (네이버는 smtp.naver.com)\n"
        )
        return False

    today = datetime.now().strftime("%Y년 %m월 %d일 %H:%M")
    subject = f"[건강식품 트렌드 분석] {today} 리포트"

    # 이메일 본문 구성
    body_lines = [
        f"건강식품 트렌드 분석 리포트입니다.",
        f"",
        f"생성일시 : {today}",
        f"분석 카테고리 : 식품 > 건강식품 (네이버 쇼핑인사이트)",
    ]

    if analyzed_keywords:
        total = len(analyzed_keywords)
        early = sum(1 for k in analyzed_keywords if k.get("trend_phase") == "early_rising")
        growing = sum(1 for k in analyzed_keywords if k.get("trend_phase") == "growing")
        top3 = [k["keyword"] for k in analyzed_keywords[:3]]

        body_lines += [
            f"",
            f"── 분석 요약 ──────────────────────────",
            f"분석 키워드 수  : {total}개",
            f"얼리라이징      : {early}개  |  성장중 : {growing}개",
            f"기회점수 TOP 3  : {', '.join(top3)}",
            f"",
        ]

    body_lines += [
        f"── 첨부 파일 ──────────────────────────",
        f"• PDF 리포트 : {Path(pdf_path).name}",
    ]
    if txt_path:
        body_lines.append(f"• TXT 리포트 : {Path(txt_path).name}")

    body_lines += [
        f"",
        f"본 메일은 자동 발송되었습니다.",
    ]

    body_text = "\n".join(body_lines)

    # MIMEMultipart 메시지 구성
    msg = MIMEMultipart()
    msg["From"]    = from_addr
    msg["To"]      = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    # PDF 첨부
    pdf_path = Path(pdf_path)
    if pdf_path.exists():
        with open(pdf_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=pdf_path.name)
        part["Content-Disposition"] = f'attachment; filename="{pdf_path.name}"'
        msg.attach(part)
        print(f"[이메일] PDF 첨부: {pdf_path.name} ({pdf_path.stat().st_size // 1024}KB)")
    else:
        print(f"[이메일] 경고: PDF 파일 없음 ({pdf_path})")

    # TXT 첨부 (선택)
    if txt_path:
        txt_path = Path(txt_path)
        if txt_path.exists():
            with open(txt_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=txt_path.name)
            part["Content-Disposition"] = f'attachment; filename="{txt_path.name}"'
            msg.attach(part)
            print(f"[이메일] TXT 첨부: {txt_path.name}")

    # SMTP 발송
    try:
        print(f"\n[이메일] {smtp_server}:{smtp_port} 연결 중...")
        context = ssl.create_default_context()

        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_bytes())

        print(f"[이메일] ✅ 발송 완료 → {to_addr}")
        return True

    except smtplib.SMTPAuthenticationError:
        print(
            f"\n[이메일] ❌ 인증 실패 — 앱 비밀번호를 확인해주세요.\n"
            f"  Gmail: https://myaccount.google.com/apppasswords\n"
            f"  Naver: 네이버 메일 > 환경설정 > POP3/IMAP 설정"
        )
        return False
    except smtplib.SMTPException as e:
        print(f"[이메일] ❌ SMTP 오류: {e}")
        return False
    except Exception as e:
        print(f"[이메일] ❌ 발송 실패: {e}")
        return False
