# 쿠팡 로켓배송 입고 데이터 집계 프로그램

## 프로그램 개요
쿠팡 로켓 입고상세내역(원본 시트)을 매일 자동으로 읽어,
월별/SKU별 입고수량과 입고금액을 집계해서 결과 시트에 덮어쓴다.
담당직원 인센티브 계산용 자료로 사용한다.

---

## 데이터 흐름

```
원본 시트 (쿠팡 로켓 입고상세내역, 2025.01~)
  └─ 행 단위: 수집일시 / 구분 / SKU명 / 입고·반출일자 / 수량 / 총 단가 ...
       ↓ 구분 == "발주" 인 행만, (년월, SKU명)별로 집계
결과 시트 (쿠팡 로켓 인센티브)
  ├─ '쿠팡 로켓 입고내역' 탭: 년월 / SKU명 / 입고수량 (수량 합계)
  └─ '쿠팡 로켓배송' 탭:     년월 / SKU명 / 입고금액(총단가) (총 단가 합계)
```

매 실행마다 두 탭을 전체 재계산해서 덮어쓰므로, 원본 시트에 새 행이
추가되기만 하면 별도 작업 없이 다음 실행에 반영된다.
각 탭 1행에 "마지막 업데이트: YYYY-MM-DD HH:MM" (한국시간)을 기록한다.

---

## 실행 방법

```bash
python main.py
```

- 로컬 실행: `run.bat` 더블클릭
- 자동 실행: GitHub Actions (`.github/workflows/coupang-rocket-sales.yml`)
  - 매일 08:00 KST 자동 실행 + 수동 실행 버튼(workflow_dispatch)

---

## 파일 구조

```
조은혜인센티브관련 쿠팡,올리브영데이터조사/
├── main.py           # 집계 + 시트 업데이트 + 텔레그램 오류 알림
├── requirements.txt
├── run.bat           # 로컬 실행용 (더블클릭)
├── .env              # 텔레그램 설정 (GitHub 업로드 안 됨)
└── .env.example      # .env 템플릿
```

구글 인증은 `influencer data shared/credentials/google-credentials.json`
(다른 프로젝트와 공유하는 서비스 계정)을 그대로 사용한다.

---

## .env 구조

```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

---

## 시트 정보

- 원본: `1EekZ89wq_Dk4AY844QwROMUcQN2yL1jY_6Ja1YAO_mU` (gid=617188056, 쿠팡 로켓 입고상세내역)
- 결과: `1ab_Pha20ULYGh__gzzV59BRIoR4x_HSCEY9TiAMYWPw`
  - gid=2075262872 → 쿠팡 로켓 입고내역
  - gid=0 → 쿠팡 로켓배송

---

## 주의사항

- `.env` 파일은 절대 Git에 올리지 않는다
- 오류 발생 시 텔레그램으로 알림 전송 (`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 미설정 시 생략)
- 원본 시트에서 "구분"이 "발주"가 아닌 행(반출/취소 등)이 생기면
  `aggregate()` 함수의 필터 로직을 함께 검토해야 한다
- 올리브영 데이터 가공은 별도 작업 예정 (대상 시트의 '올리브영' 탭은 아직 비어있음)
