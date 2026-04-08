# 인플루언서 판매 데이터 공유 프로그램

## 프로그램 개요
네이버 스마트스토어 판매 데이터를 인플루언서별 구글 시트에 자동 업데이트하는 프로그램.
매 시간 정각마다 자동 실행되며, 캠페인 기간 내 데이터만 수집한다.

---

## 운영 환경

| 역할 | 위치 |
|------|------|
| 실제 실행 서버 | Google Cloud VM (IP: 35.222.61.113, us-central1-a) |
| 코드 보관 | GitHub (github.com/nzkym/influencer-sales-data-share) |
| 코드 수정 | 이 PC에서 Claude와 대화 후 git push |

### Google Cloud 접속
Google Cloud Console → Compute Engine → VM 인스턴스 → influencer-server → SSH 버튼

### 코드 수정 후 서버 반영
crontab에 `git pull`이 포함되어 있어 매 정각 자동 반영됨.
수동 반영이 필요하면 SSH에서:
```bash
cd ~/influencer-sales-data-share && git pull && cd 'influencer data shared' && source .venv/bin/activate && python3 main.py --once
```

---

## 파일 구조

```
influencer data shared/
├── main.py          # 메인 실행 파일 (캠페인 읽기 + 실행)
├── naver_api.py     # 네이버 커머스 API 클라이언트 (bcrypt 인증)
├── sheets.py        # 구글 시트 기록 모듈
├── requirements.txt # 패키지 목록
├── run.bat          # 로컬 실행용 (더블클릭)
├── .env             # API 키 (GitHub 업로드 안 됨)
└── credentials/
    └── google-credentials.json  # 구글 서비스 계정 키
```

---

## .env 구조 (Google Cloud 서버에만 있음)

```
MASTER_SHEET_URL=캠페인관리시트URL

NUTONE_CLIENT_ID=...
NUTONE_CLIENT_SECRET=...

JDHEALTH_CLIENT_ID=...
JDHEALTH_CLIENT_SECRET=...

NUTPET_CLIENT_ID=...
NUTPET_CLIENT_SECRET=...

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

API 키 변경 시 Google Cloud SSH에서:
```bash
cd ~/influencer-sales-data-share/'influencer data shared' && nano .env
```

---

## 캠페인 관리 구글 시트
- URL: https://docs.google.com/spreadsheets/d/1CpEkXu-U7U3RH4d9fUZ9fpUbQ8yGZjpQ7D6cNpWTLQM
- 열 구조: No / 제목 / 시작일자 / 종료일자 / 상품링크 / 데이터공유 구글스프레드_인플루언서전달링크 / 스토어
- 스토어 열: `nutone` / `jdhealth` / `nutpet` 드롭다운으로 선택

---

## 지원 스토어
| 스토어명 | URL |
|---------|-----|
| nutone | brand.naver.com/nutone |
| jdhealth | brand.naver.com/jdhealth |
| nutpet | brand.naver.com/nutpet (= smartstore.naver.com/nutpet) |

> brand.naver.com과 smartstore.naver.com은 동일하게 처리

---

## 구글 시트 출력 구조 (판매현황 탭)
- 행1: 상품명 + D+일째 (우측)
- 행2: 마지막 업데이트 시각 (KST)
- 행3: 총 주문수 | 총 제품수
- 행4: 주의사항
- 행6: 헤더 (날짜 / 옵션 / 주문수 / 제품수)
- 행7~: 데이터
- F열: 옵션별 순위 (옵션 2개 이상 + 주문수 다를 때만 순위번호 표시)
- 차트: 데이터 아래에 위치 (주문수/제품수 2개 시리즈)

### 제품수 계산 방식
옵션명에서 BOX 수량 자동 추출 → 주문수 × BOX수 = 제품수
예: "선택: 12BOX(50%)" × 2주문 = 24개

---

## 오류 알림
텔레그램 봇(@jdhealth_bot)으로 오류 발생 시 자동 알림
- 오류 원인, 상품명, 발생 시각 포함
- 정상 실행 시에는 알림 없음

---

## 자동 실행 스케줄
매 시간 정각 (crontab: `0 * * * *`)
실행 전 git pull로 최신 코드 자동 반영
