# 네이버 건강관련 전체 트렌드 분석기

## 프로그램 개요
`naver_health_trend`(건강식품 전용)의 확장판.
건강식품 외에도 건강과 연관된 **5개 대분류**를 종합 스캔하여
아직 건강식품 카테고리에 진입하지 않았거나 다른 분류로 등록된
기회 키워드를 선제적으로 포착합니다.

### 분석 대상 카테고리
| 대분류 | 건강 연관 예시 |
|--------|----------------|
| 식품 전체 | 다이어트식품·가루/분말류·음료·건강식품 (레몬즙, 헛개나무분말, 가르시니아 등) |
| 생활/건강 | 건강관리용품·당뇨관리·구강위생·반려동물 영양제 등 |
| 출산/육아 | 아기간식·이유식·어린이 배도리지차·유아 위생건강용품 등 |
| 화장품/미용 | 약국 화장품·선케어·마스크팩 등 |
| 디지털/가전 | 이미용가전·가정용 의료기기(메디큐브형) 등 |

### naver_health_trend와의 차이
- **건강식품 전용** → **5개 대분류 종합** (건강 관련 광범위 기회 탐색)
- 카테고리당 상위 30개 키워드 × 5개 = 최대 150개 키워드 (중복 제거 후 약 100~150개)
- 스크래핑 시간이 더 길어짐 (~15~20분)
- **매주 목요일** 자동 실행 (naver_health_trend는 화요일)

---

## 실행 방법

```bash
python main.py                        # 기본 실행 (카테고리당 상위 30개)
python main.py --top 20               # 카테고리당 상위 20개
python main.py --no-scrape            # 스크레이핑 없이 캐시된 키워드 사용
python main.py --keywords 레몬즙 헛개나무  # 키워드 직접 지정
python main.py --no-chart             # 차트 생성 건너뜀
```

---

## 파일 구조

```
naver_trend about all health_건강식품외 건강전체/
├── main.py            # 메인 오케스트레이터 (naver_health_trend와 동일 구조)
├── scraper.py         # 다중 카테고리 Playwright 스크래퍼 ← 이 파일이 핵심 차이점
├── naver_api.py       # 네이버 데이터랩 API 클라이언트 (동일)
├── analyzer.py        # 트렌드 분석 (동일)
├── keyword_volume.py  # 검색량 조회 (동일)
├── reporter.py        # AI 브리핑 — 광범위 건강 시장 관점으로 프롬프트 수정
├── pdf_report.py      # PDF 리포트 (동일)
├── emailer.py         # 이메일 전송 (동일)
├── requirements.txt   # 패키지 목록 (동일)
├── 실행.bat           # 로컬 실행용
├── run.sh             # 리눅스/서버 실행용
├── setup_cloud.sh     # 클라우드 서버 초기 세팅 (목요일 cron 등록)
├── .env               # API 키 (Git 업로드 안 됨)
├── .env.example       # API 키 양식
└── 결과값/            # 생성된 보고서 저장
```

---

## .env 구조

```
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
ANTHROPIC_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## 분석 흐름

```
1. scraper.py    → 5개 대분류 × 3기간 = 15회 Playwright 스크래핑
                   (카테고리당 상위 30개, 중복 제거 후 통합)
2. naver_api.py  → 데이터랩 API로 트렌드 데이터 수집 (장기/단기 4종)
3. analyzer.py   → 트렌드 분석: 성장률·추세단계·기회점수
4. reporter.py   → 광범위 건강 시장 관점의 AI 브리핑 생성
5. pdf_report.py → PDF 리포트 생성
6. 텔레그램      → PDF 자동 발송
```

---

## cron 스케줄

- **매주 목요일 19:00 KST** (10:00 UTC) 자동 실행
- `setup_cloud.sh`를 실행하면 crontab에 자동 등록됩니다

---

## 주의사항

- 5개 카테고리를 순차 스크래핑하므로 **초기 실행 시 15~20분** 소요
- 캐시 유효 기간: 24시간 (재실행 시 자동 재사용)
- `naver_health_trend`와 동일 VM에서 실행 가능 (동일 .env 공유 불가 — 별도 .env 필요)
- 키워드 트렌드 API 할당량 주의: 키워드 수가 많으면 여러 API 키 설정 권장
