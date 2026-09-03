"""
파마브로스 OAuth2 토큰 발급 (1회 실행)
- client_secret.json으로 Google OAuth 인증
- .env에 OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REFRESH_TOKEN, PHARMABROS_DRIVE_FOLDER_ID 추가

사용법:
  python pharmabros_oauth_setup.py
  브라우저가 열리면 사장님 구글 계정으로 로그인 후 허용
"""
import json
import sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

CLIENT_SECRET_FILE = Path(__file__).parent / "client_secret.json"
TOKEN_OUT_FILE     = Path(__file__).parent / "pharmabros_token.json"
SCOPES = ["https://www.googleapis.com/auth/drive"]

def main():
    if not CLIENT_SECRET_FILE.exists():
        print("client_secret.json 파일이 없습니다.")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    info = {
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    TOKEN_OUT_FILE.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ 토큰 저장 완료: {TOKEN_OUT_FILE}")
    print("\n.env에 아래 항목을 추가하세요 (PHARMABROS_DRIVE_FOLDER_ID는 직접 확인):")
    print(f"OAUTH_CLIENT_ID={creds.client_id}")
    print(f"OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("PHARMABROS_DRIVE_FOLDER_ID=<파마브로스 공유 드라이브 메인 폴더 ID>")
    print("PHARMABROS_DONE_FOLDER_ID=<완료 하위폴더 ID>")

if __name__ == "__main__":
    main()
