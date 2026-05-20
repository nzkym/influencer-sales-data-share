"""
구글 드라이브 업로드용 OAuth2 토큰 발급 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【실행 전 준비】
1. GCP 콘솔 → APIs & Services → 사용자 인증 정보
   → 사용자 인증 정보 만들기 → OAuth 2.0 클라이언트 ID
   → 애플리케이션 유형: "데스크톱 앱" → 만들기
   → JSON 다운로드 → 이 파일과 같은 폴더에 "client_secret.json" 으로 저장

2. 이 스크립트 실행:
   python get_drive_token.py

3. 브라우저에서 사장님 구글 계정으로 로그인 → 권한 허용

4. 출력된 값 3개를 서버 .env에 복사
"""

import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]
SECRET_FILE = Path(__file__).parent / "client_secret.json"

if not SECRET_FILE.exists():
    print("❌ client_secret.json 파일이 없습니다.")
    print("   GCP 콘솔에서 OAuth 클라이언트 JSON을 다운로드해서")
    print(f"   이 폴더에 'client_secret.json' 이름으로 저장해주세요.")
    print(f"   저장 위치: {SECRET_FILE}")
    exit(1)

print("브라우저가 열립니다. 사장님 구글 계정으로 로그인 후 권한을 허용해주세요...\n")
flow = InstalledAppFlow.from_client_secrets_file(str(SECRET_FILE), SCOPES)
creds = flow.run_local_server(port=0)

print("\n" + "="*55)
print("✅ 인증 완료! 아래 3줄을 서버 .env 파일에 추가하세요:")
print("="*55)
print(f"OAUTH_CLIENT_ID={creds.client_id}")
print(f"OAUTH_CLIENT_SECRET={creds.client_secret}")
print(f"OAUTH_REFRESH_TOKEN={creds.refresh_token}")
print("="*55)
print("\n서버 SSH에서:")
print("nano ~/influencer-sales-data-share/'influencer data shared'/.env")
print("위 3줄을 붙여넣고 저장하세요.")
