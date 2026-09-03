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

    # 캠페인 실적 탭에서 제품명 가져오기 (제목+시작일 기준으로 저장)
    product_names = {}  # (title, date_from_str) → product_name, fallback: title → product_name
    try:
        for row in spreadsheet.worksheet("캠페인 실적(자사확인용)").get_all_values():
            if not row or len(row) < 3:
                continue
            t = str(row[1]).strip()
            p = str(row[2]).strip() if len(row) > 2 else ""
            if not t or not p:
                continue
            # 날짜 정규화 (E열 = index 4)
            raw_date = row[4] if len(row) > 4 else ""
            parts = str(raw_date).replace(".", "-").split("-")
            if len(parts) == 3:
                d = parts[0] + "-" + ("0" + parts[1])[-2:] + "-" + ("0" + parts[2])[-2:]
            else:
                d = ""
            if d:
                product_names[(t, d)] = p
            if t not in product_names:
                product_names[t] = p  # fallback: title-only
    except Exception:
        pass

    return campaigns, product_names, spreadsheet


def to_num(val):
    s = re.sub(r"[^0-9.]", "", str(val or ""))
    return float(s) if s else 0.0


# ── HTML 정산서 생성 ──────────────────────────────────────
def _get_q_payment(spreadsheet, title, date_from_str, date_to_str):
    """파마브로스정산 탭에서 유효 주문(취소/반품 제외) 합계 반환.
    p_to_q_sync.py로 동기화된 P데이터 기반. 없으면 (0, '') 반환."""
    try:
        pb_ws = spreadsheet.worksheet("파마브로스정산")
        rows = pb_ws.get_all_values()
    except Exception:
        return 0, ""

    total = 0
    count = 0
    for row in rows[1:]:
        if not row or str(row[0]).strip() != title:
            continue
        col1 = str(row[1]).strip() if len(row) > 1 else ""
        # 신버전: col1이 YYYY-MM-DD (시작일)
        is_new = bool(re.match(r'^\d{4}-\d{2}-\d{2}$', col1))
        if is_new:
            if col1 != date_from_str:
                continue
            status = str(row[4]).strip() if len(row) > 4 else ""
            try:
                val = int(str(row[8]).replace(",", "")) if len(row) > 8 else 0
            except (ValueError, TypeError):
                val = 0
        else:
            # 구버전 8열: [제목,주문번호,주문일시,주문상태,옵션,주문수량,단가,단가총합]
            od = str(row[2])[:10] if len(row) > 2 else ""
            if od and not (date_from_str <= od <= date_to_str):
                continue
            status = str(row[3]).strip() if len(row) > 3 else ""
            try:
                val = int(str(row[7]).replace(",", "")) if len(row) > 7 else 0
            except (ValueError, TypeError):
                val = 0

        if "취소" in status or "반품" in status:
            continue
        total += val
        count += 1

    return total, f"Q데이터({count}건)"


def build_html(campaign, product_name, p_payment=None, p_source="", spreadsheet=None):
    title       = campaign.get("제목", "-")
    date_from   = str(campaign.get("시작일자", "-"))
    date_to     = str(campaign.get("종료일자", "-"))
    product_url = str(campaign.get("상품링크", "")).split("?")[0]

    if p_payment:
        payment = p_payment
        payment_note = p_source
    else:
        payment = to_num(campaign.get("결제금액", 0))
        payment_note = "I열(결제금액)"

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
    rows_html += f"<tr><td>총 결제금액</td><td>{int(payment):,}원 <small style='color:#888'>({payment_note})</small></td></tr>"
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
        self.spreadsheet = None
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
            self.campaigns, self.product_names, self.spreadsheet = load_data()
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
        date_from_raw = str(c.get("시작일자", ""))
        date_to_raw   = str(c.get("종료일자", ""))

        # 날짜 정규화
        parts = date_from_raw.replace(".", "-").split("-")
        date_from_str = (parts[0] + "-" + ("0"+parts[1])[-2:] + "-" + ("0"+parts[2])[-2:]
                         if len(parts) == 3 else date_from_raw[:10])
        parts_to = date_to_raw.replace(".", "-").split("-")
        date_to_str = (parts_to[0] + "-" + ("0"+parts_to[1])[-2:] + "-" + ("0"+parts_to[2])[-2:]
                       if len(parts_to) == 3 else date_to_raw[:10])

        # 제품명: 제목+시작일 → fallback: 제목만
        product_name = (
            self.product_names.get((title, date_from_str), "")
            or self.product_names.get(title, "")
        )

        # Q데이터(파마브로스정산 탭) 기준 결제금액 계산 — p_to_q_sync로 P→Q 동기화된 값
        self.status.set("파마브로스정산 탭 확인 중...")
        self.root.update()
        q_payment, q_source = _get_q_payment(self.spreadsheet, title, date_from_str, date_to_str)

        if not q_payment and not to_num(c.get("결제금액", 0)):
            messagebox.showwarning("금액 필요",
                                   f"'{title}'\n파마브로스정산 탭에 데이터가 없고 I열(결제금액)도 비어있습니다.\n"
                                   "p_to_q_sync.py를 먼저 실행하거나 I열을 직접 입력해주세요.")
            self.status.set(f"총 {len(self.campaigns)}개 캠페인")
            return

        html = build_html(c, product_name, q_payment or None, q_source)

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
