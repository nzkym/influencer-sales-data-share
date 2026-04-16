"""
HTML 보고서 생성 모듈
"""

from datetime import datetime
from pathlib import Path


TIER_COLOR = {
    "★★★": "#e74c3c",
    "★★":  "#f39c12",
    "★":   "#27ae60",
}

URGENCY_BADGE = {
    "high":   '<span style="background:#e74c3c;color:#fff;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:bold;">긴급</span>',
    "medium": '<span style="background:#f39c12;color:#fff;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:bold;">보통</span>',
    "low":    '<span style="background:#27ae60;color:#fff;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:bold;">여유</span>',
}


def _build_card(c: dict) -> str:
    tier = c.get("tier", "★")
    color = TIER_COLOR.get(tier, "#888")
    urgency_html = URGENCY_BADGE.get(c.get("urgency", "low"), "")

    return f"""
    <div class="card" style="border-left:5px solid {color};">
      <div class="card-header">
        <div class="rank-box">
          <span class="rank">#{c.get('rank', 0)}</span>
          <span class="tier" style="color:{color};">{tier}</span>
        </div>
        <div class="term-box">
          <h3>{c.get('term_ko', '')} <small>({c.get('term_en', '')})</small></h3>
          <p class="summary">{c.get('summary', '')}</p>
        </div>
        <div class="urgency-box">{urgency_html}</div>
      </div>
      <div class="card-body">
        <div class="grid2">
          <div class="info-item">
            <span class="label">🌍 해외 현황</span>
            <p>{c.get('overseas_status', '')}</p>
          </div>
          <div class="info-item">
            <span class="label">🇰🇷 국내 현황</span>
            <p>{c.get('korea_status', '')}</p>
          </div>
        </div>
        <div class="grid2">
          <div class="info-item">
            <span class="label">📋 상표 전략</span>
            <p>{c.get('trademark_strategy', '')}</p>
          </div>
          <div class="info-item">
            <span class="label">💰 시장 잠재력</span>
            <p>{c.get('market_potential', '')}</p>
          </div>
        </div>
      </div>
    </div>"""


def generate_html(data: dict, output_dir: Path) -> Path:
    """분석 결과를 HTML 보고서로 생성하고 저장된 경로 반환"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}.html"
    output_path = output_dir / filename

    candidates = data.get("candidates", [])
    weekly_insight = data.get("weekly_insight", "이번 주 분석 완료")

    cards_html = "\n".join(_build_card(c) for c in candidates)

    # 요약 테이블 (상단 빠른 뷰)
    table_rows = ""
    for c in candidates:
        tier = c.get("tier", "★")
        color = TIER_COLOR.get(tier, "#888")
        urgency_text = {"high": "긴급", "medium": "보통", "low": "여유"}.get(c.get("urgency", "low"), "")
        table_rows += f"""
        <tr>
          <td style="color:#888;">{c.get('rank', '')}</td>
          <td style="color:{color};font-weight:bold;">{tier}</td>
          <td><strong>{c.get('term_ko', '')}</strong><br><span style="color:#888;font-size:12px;">{c.get('term_en', '')}</span></td>
          <td style="color:#ccc;font-size:13px;">{c.get('summary', '')}</td>
          <td><span style="color:{color};">{urgency_text}</span></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>상표 선점 후보 리포트 — {date_str}</title>
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family:'Apple SD Gothic Neo','Noto Sans KR',sans-serif; background:#0f1117; color:#e0e0e0; line-height:1.6; }}

    /* 헤더 */
    .header {{ background:linear-gradient(135deg,#1a1a2e,#16213e); padding:40px 20px; text-align:center; border-bottom:1px solid #2a2a4a; }}
    .header h1 {{ font-size:26px; color:#fff; margin-bottom:8px; }}
    .header .date {{ color:#666; font-size:13px; margin-bottom:20px; }}
    .insight-box {{ display:inline-block; background:rgba(255,255,255,0.04); border:1px solid #2a2a4a; border-radius:10px; padding:14px 24px; max-width:720px; font-size:14px; color:#bbb; }}

    /* 컨테이너 */
    .container {{ max-width:960px; margin:0 auto; padding:30px 20px; }}

    /* 요약 테이블 */
    .section-title {{ font-size:14px; color:#555; letter-spacing:1px; text-transform:uppercase; margin-bottom:14px; padding-bottom:8px; border-bottom:1px solid #222; }}
    .summary-table {{ width:100%; border-collapse:collapse; margin-bottom:40px; font-size:14px; }}
    .summary-table th {{ text-align:left; color:#555; font-weight:normal; padding:8px 12px; border-bottom:1px solid #222; font-size:12px; text-transform:uppercase; }}
    .summary-table td {{ padding:10px 12px; border-bottom:1px solid #1a1a2e; }}
    .summary-table tr:hover td {{ background:rgba(255,255,255,0.02); }}

    /* 카드 */
    .card {{ background:#141420; border:1px solid #2a2a4a; border-radius:12px; margin-bottom:20px; overflow:hidden; }}
    .card-header {{ display:flex; align-items:flex-start; gap:16px; padding:20px; background:rgba(255,255,255,0.02); }}
    .rank-box {{ min-width:52px; text-align:center; }}
    .rank {{ display:block; font-size:24px; font-weight:bold; color:#fff; }}
    .tier {{ font-size:13px; }}
    .term-box {{ flex:1; }}
    .term-box h3 {{ font-size:19px; color:#fff; margin-bottom:5px; }}
    .term-box h3 small {{ font-size:13px; color:#666; font-weight:normal; }}
    .summary {{ color:#999; font-size:13px; }}
    .urgency-box {{ padding-top:4px; }}
    .card-body {{ padding:18px 20px; }}
    .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:12px; }}
    .info-item .label {{ display:block; font-size:11px; color:#666; font-weight:bold; letter-spacing:0.5px; margin-bottom:5px; text-transform:uppercase; }}
    .info-item p {{ font-size:13px; color:#bbb; }}

    /* 푸터 */
    .footer {{ text-align:center; padding:30px 20px; color:#444; font-size:12px; border-top:1px solid #1a1a1a; margin-top:20px; }}

    @media(max-width:640px) {{
      .card-header {{ flex-wrap:wrap; }}
      .grid2 {{ grid-template-columns:1fr; }}
      .summary-table {{ font-size:12px; }}
    }}
  </style>
</head>
<body>

<div class="header">
  <h1>📊 상표 선점 후보 리포트</h1>
  <p class="date">생성일: {date_str} &nbsp;|&nbsp; 해외 트렌드 + Claude AI 분석</p>
  <div class="insight-box">💡 {weekly_insight}</div>
</div>

<div class="container">

  <p class="section-title">빠른 요약</p>
  <table class="summary-table">
    <thead>
      <tr>
        <th>#</th><th>등급</th><th>후보어</th><th>요약</th><th>긴급도</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>

  <p class="section-title">상세 분석</p>
  {cards_html}

</div>

<div class="footer">
  ⚠️ 본 리포트는 AI 분석 기반 참고자료입니다.<br>
  상표 출원 전 반드시 <strong>키프리스(KIPRIS)</strong> 검색 및 변리사 확인이 필요합니다.<br><br>
  Patent Name Finder &nbsp;|&nbsp; {date_str} 자동 생성
</div>

</body>
</html>"""

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  → 보고서 저장: {output_path}")
    return output_path
