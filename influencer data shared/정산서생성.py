"""
정산서 생성 프로그램
실행: 정산서생성.bat 더블클릭
"""
import re
import os
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from datetime import datetime
import webbrowser
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

CREDENTIALS_PATH = str(BASE_DIR / "credentials" / "google-credentials.json")
MASTER_SHEET_URL = os.getenv("MASTER_SHEET_URL", "")
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

COMPANY = {
    "name":    "주식회사 정담건강",
    "biz_no":  "391-86-00889",
    "address": "경기도 시흥시 서울대학로278번길61, 431-2호",
}


# ── 데이터 조회 ───────────────────────────────────────────
def load_data():
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    client = gspread.authorize(creds)
    sheet_id = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", MASTER_SHEET_URL).group(1)
    spreadsheet = client.open_by_key(sheet_id)
    ws = spreadsheet.sheet1
    campaigns = [r for r in ws.get_all_records() if str(r.get("제목", "")).strip()]

    # 캠페인 실적 탭에서 제품명 가져오기
    product_names = {}
    try:
        for row in spreadsheet.worksheet("캠페인 실적(자사확인용)").get_all_records():
            t = str(row.get("제목", "")).strip()
            p = str(row.get("제품명", "")).strip()
            if t and p:
                product_names[t] = p
    except Exception:
        pass

    return campaigns, product_names


def to_num(val):
    s = re.sub(r"[^0-9.]", "", str(val or ""))
    return float(s) if s else 0.0


# ── HTML 정산서 생성 ──────────────────────────────────────
def build_html(campaign, product_name):
    title       = campaign.get("제목", "-")
    date_from   = str(campaign.get("시작일자", "-"))
    date_to     = str(campaign.get("종료일자", "-"))
    product_url = str(campaign.get("상품링크", "")).split("?")[0]
    payment     = to_num(campaign.get("결제금액", 0))
    comm_raw    = to_num(campaign.get("수수료(%)", 0))
    comm_rate   = comm_raw / 100 if comm_raw > 1 else comm_raw
    settlement  = round(payment * comm_rate)
    today       = datetime.now().strftime("%Y년 %m월 %d일")

    rows_html = ""
    rows_html += f"<tr><td>인플루언서</td><td>{title}</td></tr>"
    if product_name:
        rows_html += f"<tr><td>제품명</td><td>{product_name}</td></tr>"
    rows_html += f"<tr><td>진행기간</td><td>{date_from} ~ {date_to}</td></tr>"
    if product_url:
        rows_html += f'<tr><td>상품링크</td><td><a href="{product_url}" target="_blank">{product_url}</a></td></tr>'
    rows_html += f"<tr><td>총 결제금액</td><td>{int(payment):,}원</td></tr>"
    rows_html += f"<tr><td>수수료</td><td>{comm_rate * 100:.1f}%</td></tr>"
    rows_html += f'<tr class="total"><td>정산기준금액</td><td>{settlement:,}원</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>정산서 - {title}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Malgun Gothic','맑은 고딕',sans-serif;padding:40px;color:#222;max-width:700px;margin:0 auto}}
  .btn{{background:#4472C4;color:#fff;border:none;padding:10px 24px;cursor:pointer;border-radius:4px;font-size:14px;margin-bottom:30px}}
  .btn:hover{{background:#2d5aa0}}
  h1{{text-align:center;font-size:28px;letter-spacing:4px;border-bottom:2px solid #222;padding-bottom:14px;margin-bottom:10px}}
  .confirm{{text-align:center;color:#444;font-size:15px;margin-bottom:6px}}
  .issued{{text-align:right;font-size:13px;color:#666;margin-bottom:24px}}
  table{{width:100%;border-collapse:collapse;margin-bottom:30px}}
  th{{background:#4472C4;color:#fff;padding:12px 16px;text-align:left;font-size:14px}}
  td{{padding:11px 16px;border-bottom:1px solid #ddd;font-size:14px}}
  tr:nth-child(even) td{{background:#f5f8ff}}
  .total td{{font-weight:bold;font-size:16px;color:#2d5aa0;background:#eef3ff!important}}
  .company{{border:1px solid #ccc;border-radius:6px;padding:18px 22px;font-size:13px;color:#555;line-height:1.9}}
  .company b{{display:block;font-size:15px;color:#222;margin-bottom:8px}}
  .sign{{margin-top:50px;text-align:right;font-size:14px;line-height:2.2;color:#444}}
  @media print{{.btn{{display:none}}body{{padding:20px}}}}
</style>
</head>
<body>
  <button class="btn" onclick="window.print()">🖨️ 인쇄 / PDF 저장</button>
  <h1>정 산 서</h1>
  <p class="confirm">공구진행에 따른 정산내역을 확인합니다.</p>
  <p class="issued">발행일: {today}</p>
  <table>
    <tr><th colspan="2">정산 내역</th></tr>
    {rows_html}
  </table>
  <div class="company">
    <b>정산 업체 정보</b>
    업체명: {COMPANY["name"]}<br>
    사업자번호: {COMPANY["biz_no"]}<br>
    주소: {COMPANY["address"]}
  </div>
  <div class="sign">
    위 정산내역이 맞음을 확인합니다.<br>
    {today}<br><br>
    인플루언서: _____________________________ (서명)
  </div>
</body>
</html>"""


# ── GUI ───────────────────────────────────────────────────
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("정산서 생성")
        self.root.geometry("580x480")
        self.root.resizable(False, False)
        self.campaigns = []
        self.product_names = {}
        self._build()
        self._load()

    def _build(self):
        tk.Label(self.root, text="정산서 생성",
                 font=("Malgun Gothic", 16, "bold"), fg="#4472C4").pack(pady=(20, 4))
        tk.Label(self.root, text="정산할 캠페인을 선택하세요",
                 font=("Malgun Gothic", 10), fg="#666").pack(pady=(0, 12))

        frm = tk.Frame(self.root)
        frm.pack(fill="both", expand=True, padx=30)
        sb = tk.Scrollbar(frm)
        sb.pack(side="right", fill="y")
        self.lb = tk.Listbox(frm, font=("Malgun Gothic", 11), height=12,
                              selectmode="single", yscrollcommand=sb.set,
                              selectbackground="#4472C4", selectforeground="white",
                              borderwidth=1, relief="solid")
        self.lb.pack(side="left", fill="both", expand=True)
        sb.config(command=self.lb.yview)

        self.status = tk.StringVar(value="데이터 불러오는 중...")
        tk.Label(self.root, textvariable=self.status,
                 font=("Malgun Gothic", 10), fg="#888").pack(pady=8)

        self.btn = tk.Button(self.root, text="📄  정산서 생성",
                             font=("Malgun Gothic", 12, "bold"),
                             bg="#4472C4", fg="white", padx=20, pady=8,
                             command=self._generate, state="disabled",
                             cursor="hand2", relief="flat")
        self.btn.pack(pady=(0, 20))

    def _load(self):
        try:
            self.campaigns, self.product_names = load_data()
            self.lb.delete(0, tk.END)
            for c in self.campaigns:
                label = f"{c.get('제목','')}  ({c.get('시작일자','')} ~ {c.get('종료일자','')})"
                pay = to_num(c.get("결제금액", 0))
                if pay:
                    label += f"  💰{int(pay):,}원"
                self.lb.insert(tk.END, label)
            self.status.set(f"총 {len(self.campaigns)}개 캠페인")
            self.btn.config(state="normal")
        except Exception as e:
            self.status.set(f"오류: {e}")

    def _generate(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showwarning("선택 필요", "캠페인을 선택해주세요.")
            return
        c = self.campaigns[sel[0]]
        title = c.get("제목", "정산서")

        if not to_num(c.get("결제금액", 0)):
            messagebox.showwarning("금액 필요",
                                   f"'{title}'\nI열(결제금액)을 먼저 입력해주세요.")
            return

        html = build_html(c, self.product_names.get(title, ""))

        # 바탕화면에 저장
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home() / "바탕 화면"
        if not desktop.exists():
            desktop = Path.home()

        safe = re.sub(r'[\\/:*?"<>|]', "_", title)
        fname = desktop / f"정산서_{safe}_{datetime.now().strftime('%Y%m%d')}.html"
        fname.write_text(html, encoding="utf-8")
        webbrowser.open(fname.as_uri())

        self.status.set(f"✅ {fname.name}")
        messagebox.showinfo("완료",
                            f"바탕화면에 저장됐습니다.\n\n{fname.name}\n\n"
                            "브라우저에서 [인쇄 → PDF로 저장]으로\nPDF 파일 저장 가능합니다.")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
