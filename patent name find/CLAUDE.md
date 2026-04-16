# 상표 선점 후보 탐색 프로그램 (Patent Name Finder)

## 프로그램 개요
해외 건강식품/웰니스 업계의 최신 트렌드를 매주 자동 수집·분석하여,
국내 상표 선점 가치가 있는 단어/개념 TOP 10을 HTML 보고서로 생성하고
텔레그램으로 링크를 전송한다.

**배경**: 고려은단의 '메가도스' 상표 선점 사례처럼, 해외에서 부상 중인 용어를
국내에 먼저 상표 등록하는 전략적 목적으로 개발됨.

---

## 실행 방법

```bash
python main.py                  # 전체 실행 (수집 + 분석 + 보고서 + 텔레그램)
python main.py --no-telegram    # 보고서만 생성 (텔레그램 전송 없이, 테스트용)
```

---

## 파일 구조

```
patent name find/
├── main.py          # 메인 오케스트레이터
├── searcher.py      # 해외 RSS 피드 수집 (최근 7일 기사)
├── analyzer.py      # Claude API로 TOP 10 후보 분석
├── reporter.py      # HTML 보고서 생성
├── notifier.py      # 텔레그램 링크 전송
├── requirements.txt # 패키지 목록
├── setup_cloud.sh   # GCP VM 초기 세팅 스크립트
├── run.bat          # 로컬 Windows 테스트용
├── .env             # API 키 (GitHub 업로드 안 됨)
├── .env.example     # 환경변수 예시
├── reports/         # 생성된 HTML 보고서 (날짜별)
└── logs/            # cron 실행 로그
```

---

## .env 구조

```
ANTHROPIC_API_KEY=sk-ant-...       # Claude API 키
TELEGRAM_BOT_TOKEN=...             # 텔레그램 봇 토큰
TELEGRAM_CHAT_ID=...               # 수신 채팅 ID
REPORT_BASE_URL=http://35.222.61.113:8080  # 보고서 URL 베이스
```

---

## 실행 흐름

```
1. searcher.py  → 해외 RSS 6개 피드에서 최근 7일 기사 수집
                  (NutraIngredients, Nutritional Outlook 등)
2. analyzer.py  → Claude API (claude-sonnet-4-6)에 기사 전달
                  → 상표 후보 TOP 10 JSON 반환
3. reporter.py  → HTML 보고서 생성 (reports/YYYY-MM-DD.html)
4. notifier.py  → 텔레그램으로 링크만 전송
```

---

## 보고서 구조

- 헤더: 생성일 + 주간 인사이트 한 줄
- 빠른 요약 테이블: 10개 후보 한눈에 보기
- 상세 카드: 해외 현황 / 국내 현황 / 상표 전략 / 시장 잠재력
- 등급: ★★★(강력추천) / ★★(주목) / ★(관찰)
- 긴급도: 긴급 / 보통 / 여유

---

## 서버 환경 (GCP VM)

| 항목 | 내용 |
|------|------|
| VM IP | 35.222.61.113 |
| 보고서 서빙 | Python http.server, 포트 8080 |
| 자동 실행 | crontab, 매주 목요일 09:00 KST |
| 보고서 URL 형식 | http://35.222.61.113:8080/YYYY-MM-DD.html |

### GCP 방화벽 설정 필요
Cloud Console → VPC 네트워크 → 방화벽 → tcp:8080 허용

### SSH 접속 후 수동 실행
```bash
cd ~/patent-name-find
source .venv/bin/activate
python3 main.py
```

---

## 수집 RSS 소스

- NutraIngredients (글로벌/미국)
- Nutritional Outlook
- Food Navigator
- Nutraceuticals World
- Supply Side SJ

---

## 주의사항

- `.env` 파일은 절대 Git에 올리지 않는다
- 보고서 링크는 VM이 켜져있을 때만 접속 가능 (GCP VM 상시 운영 중)
- 상표 출원 전 반드시 키프리스(KIPRIS) + 변리사 확인 필요
- Claude API 비용: 주 1회 실행 기준 약 50~100원 수준
