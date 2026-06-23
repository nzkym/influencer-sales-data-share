// ─────────────────────────────────────────
// 통합 스크립트: 정산서 + 마진계산기
// ─────────────────────────────────────────

const REF_TAB  = "이익계산참고사항(자사확인용)";
const CALC_TAB = "마진계산기";

const BOX_PRESETS = {
  "전체 (1~10bx)": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  "3,6,9bx":       [3, 6, 9],
  "3,6,9,12bx":    [3, 6, 9, 12],
  "3,6,12bx":      [3, 6, 12],
  "2,4,6bx":       [2, 4, 6],
  "2,4,6,12bx":    [2, 4, 6, 12],
  "1,2,4,6bx":     [1, 2, 4, 6],
  "직접입력":       "custom",
};

// ── 제품 드롭다운 새로고침 ─────────────────────────────────
function refreshProductDropdown() {
  const ss  = SpreadsheetApp.getActiveSpreadsheet();
  const ref = ss.getSheetByName(REF_TAB);
  const ws  = ss.getSheetByName(CALC_TAB);
  if (!ref || !ws) return;

  const allNames = [];
  const refData  = ref.getDataRange().getValues();
  for (let i = 1; i < refData.length; i++) {
    const n = String(refData[i][0] || '').trim();
    if (n && !allNames.includes(n)) allNames.push(n);
  }

  const prodOptions = ["전체 (모든 제품)"].concat(allNames);
  ws.getRange("B2").setDataValidation(
    SpreadsheetApp.newDataValidation()
      .requireValueInList(prodOptions, true)
      .setAllowInvalid(false)
      .build()
  );
}

// ── 수기 수정 시 자동 재계산 + 참고시트 변경 시 드롭다운 갱신 ──
function onEdit(e) {
  const sheet = e.range.getSheet();

  // 이익계산참고사항 탭 수정 → 제품 드롭다운 자동 갱신
  if (sheet.getName() === REF_TAB) {
    refreshProductDropdown();
    return;
  }

  if (sheet.getName() !== CALC_TAB) return;

  const col = e.range.getColumn();
  const row = e.range.getRow();

  // 데이터 행(7행~)의 B~I열 수정인지 확인
  if (row < 7 || col < 2 || col > 9) return;

  // J(이익), K(이익률) 수식 재적용
  sheet.getRange(row, 10).setFormula(
    `=F${row}-D${row}-(F${row}*G${row})-(F${row}*H${row})-I${row}`
  );
  sheet.getRange(row, 11).setFormula(
    `=IF(F${row}>0,J${row}/F${row},0)`
  );
}

// ── 파마브로스 양식 정산서 ─────────────────────────────────
function generatePharmabrosSettlement() {
  var ui    = SpreadsheetApp.getUi();
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var row   = sheet.getActiveRange().getRow();
  if (row <= 1) { ui.alert('캠페인 행을 먼저 클릭한 뒤 실행해주세요.'); return; }

  var vals = sheet.getRange(row, 1, 1, 11).getValues()[0];
  var title      = vals[1] || '-';
  var dateFrom   = vals[2] || '-';
  var dateTo     = vals[3] || '-';
  var kCol       = String(vals[10] || '').replace(/\s/g, '');
  var commission = Number(String(vals[9]).replace(/[^0-9.]/g, '')) || 0;
  var commRate   = commission > 1 ? commission / 100 : commission;

  if (kCol !== '파마브로스파일공유') {
    ui.alert('K열이 "파마브로스파일공유"인 캠페인에서만 사용 가능합니다.');
    return;
  }

  // 파마브로스정산 탭에서 Drive xlsx 기준 매출합계 계산 (취소 제외)
  var totalPayment = 0;
  try {
    var pbTab = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('파마브로스정산');
    if (pbTab) {
      var pbData = pbTab.getDataRange().getValues();
      for (var i = 1; i < pbData.length; i++) {
        if (String(pbData[i][0]).trim() !== String(title).trim()) continue;
        var status = String(pbData[i][3] || '');
        if (status.indexOf('취소') >= 0 || status.indexOf('반품') >= 0) continue;
        totalPayment += Number(pbData[i][7]) || 0;
      }
    }
  } catch(e) {}

  if (!totalPayment) {
    ui.alert('파마브로스 정산 데이터가 없습니다.\n서버에서 자동 계산 후 이용 가능합니다.');
    return;
  }

  var settlement = Math.round(totalPayment * commRate);
  var productName = '';
  try {
    var summary = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('캠페인 실적(자사확인용)');
    if (summary) {
      var sRows = summary.getDataRange().getValues();
      for (var j = 1; j < sRows.length; j++) {
        if (sRows[j][1] === title) { productName = sRows[j][2] || ''; break; }
      }
    }
  } catch(e) {}

  var today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy년 MM월 dd일');

  var html = '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>파마브로스 정산서</title><style>'
    + '@page{size:A4;margin:12mm 15mm}*{box-sizing:border-box;margin:0;padding:0}'
    + 'body{font-family:"Malgun Gothic","맑은 고딕",sans-serif;background:#fff;color:#1a1a2e;max-width:680px;margin:0 auto;padding:0}'
    + '.print-area{padding:12px 16px;background:#f8f9fa;border-bottom:1px solid #e0e0e0;display:flex;align-items:center;gap:12px}'
    + '.btn{background:#1a2744;color:#fff;border:none;padding:9px 22px;cursor:pointer;border-radius:4px;font-size:13px;letter-spacing:0.5px}'
    + '.btn:hover{background:#2d3f6b}.print-tip{font-size:11px;color:#888;line-height:1.5}'
    + '.banner{background:linear-gradient(135deg,#1a2744 0%,#2d3f6b 100%);padding:32px 40px 28px;position:relative;overflow:hidden}'
    + '.banner::after{content:"";position:absolute;right:-20px;top:-20px;width:120px;height:120px;border-radius:50%;background:rgba(255,255,255,0.05)}'
    + '.banner-title{color:#fff;font-size:30px;letter-spacing:6px;font-weight:700;margin-bottom:6px}'
    + '.banner-sub{color:rgba(255,255,255,0.9);font-size:13px;letter-spacing:2px;font-weight:500}'
    + '.banner-date{color:rgba(255,255,255,0.8);font-size:12px;text-align:right;margin-top:10px}'
    + '.body{padding:28px 40px 32px}.confirm-box{background:#f0f4ff;border-left:4px solid #1a2744;padding:12px 16px;font-size:14px;color:#333;margin-bottom:24px;line-height:1.6}'
    + 'table{width:100%;border-collapse:collapse;margin-bottom:6px}'
    + '.tbl-head td{background:#1a2744;color:#fff;padding:11px 16px;font-size:13px;font-weight:600;letter-spacing:0.5px}'
    + 'tr td{padding:11px 16px;border-bottom:1px solid #eaecf4;font-size:14px}'
    + 'tr:nth-child(even) td{background:#f8f9fb}tr td:first-child{color:#555;width:38%}'
    + '.total-row td{font-weight:700;font-size:15px;color:#1a2744;background:#eef1fa!important;border-top:2px solid #1a2744}'
    + '.note{font-size:11.5px;color:#888;margin-top:8px;margin-bottom:24px;padding-left:2px}'
    + '.company{border:1px solid #dde1ee;border-radius:8px;padding:18px 22px;background:#fafbff}'
    + '.company-title{font-size:13px;font-weight:700;color:#1a2744;margin-bottom:10px;display:flex;align-items:center;gap:6px}'
    + '.company-title::before{content:"";display:inline-block;width:4px;height:14px;background:#1a2744;border-radius:2px}'
    + '.company-body{font-size:13px;color:#555;line-height:2}'
    + '@media print{.print-area{display:none}body{padding:0}}</style></head><body>'
    + '<div class="print-area"><button class="btn" onclick="window.print()">🖨️ 인쇄 / PDF 저장</button>'
    + '<span class="print-tip">※ 날짜·URL 머리글 제거 방법<br>인쇄 → 더보기 설정 → <b>머리글 및 바닥글</b> 체크 해제</span></div>'
    + '<div class="banner"><div class="banner-title">정 산 서</div>'
    + '<div class="banner-sub">SETTLEMENT STATEMENT (파마브로스 양식)</div>'
    + '<div class="banner-date">발행일: ' + today + '</div></div>'
    + '<div class="body"><div class="confirm-box">공구진행에 따른 정산내역을 확인합니다.</div>'
    + '<table><tr class="tbl-head"><td colspan="2">정산 내역</td></tr>'
    + '<tr><td>인플루언서</td><td>' + title + '</td></tr>'
    + (productName ? '<tr><td>제품명</td><td>' + productName + '</td></tr>' : '')
    + '<tr><td>진행기간</td><td>' + dateFrom + ' ~ ' + dateTo + '</td></tr>'
    + '<tr><td>총 결제금액</td><td>' + totalPayment.toLocaleString('ko-KR') + '원</td></tr>'
    + '<tr><td>수수료</td><td>' + (commRate * 100).toFixed(1) + '%</td></tr>'
    + '<tr class="total-row"><td>정산기준금액</td><td>' + settlement.toLocaleString('ko-KR') + '원</td></tr>'
    + '</table>'
    + '<div class="company"><div class="company-title">정산 업체 정보</div>'
    + '<div class="company-body">업체명&nbsp;&nbsp;&nbsp;주식회사 정담건강<br>'
    + '사업자번호&nbsp;&nbsp;&nbsp;391-86-00889<br>'
    + '주소&nbsp;&nbsp;&nbsp;경기도 시흥시 서울대학로278번길61, 431-2호'
    + '</div></div>'
    + '<p class="note">*세금관련부분은 협의된 내용으로 처리가 됩어 실제 입금금액은 위 정산기준금액과 일부 상이할수도있습니다. (ex&gt;부가세여부, 프리랜서공제&lt;3.3%공제된 금액입금&gt; 등)</p>'
    + '</div></body></html>';

  SpreadsheetApp.getUi().showModalDialog(
    HtmlService.createHtmlOutput(html).setWidth(740).setHeight(750), '파마브로스 양식 정산서');
}

// ── 메뉴 등록 ──────────────────────────────────────────────
function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('📋 정산서')
    .addItem('정산서 생성', 'generateSettlement')
    .addItem('파마브로스 양식 정산서', 'generatePharmabrosSettlement')
    .addToUi();
  SpreadsheetApp.getActiveSpreadsheet().addMenu("📊 마진계산기", [
    { name: "▶ 계산 실행",       functionName: "runMarginCalc"  },
    { name: "🗑️ 결과 초기화",    functionName: "clearResults"   },
    { name: "⚙️ 시트 초기 설정", functionName: "setupCalcSheet" },
  ]);
}

// ─────────────────────────────────────────
// 정산서 생성
// ─────────────────────────────────────────
function generateSettlement() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var row   = sheet.getActiveRange().getRow();
  if (row <= 1) { SpreadsheetApp.getUi().alert('정산할 캠페인 행을 먼저 클릭한 뒤 실행해주세요.'); return; }

  var vals       = sheet.getRange(row, 1, 1, 10).getValues()[0];
  var title      = vals[1] || '-';
  var dateFrom   = vals[2] || '-';
  var dateTo     = vals[3] || '-';
  var payment    = Number(String(vals[8]).replace(/[^0-9.]/g, '')) || 0;
  var commission = Number(String(vals[9]).replace(/[^0-9.]/g, '')) || 0;
  var commRate   = commission > 1 ? commission / 100 : commission;
  var settlement = Math.round(payment * commRate);

  if (!payment) { SpreadsheetApp.getUi().alert('I열(결제금액)을 먼저 입력해주세요.'); return; }

  var productName = '';
  try {
    var summary = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('캠페인 실적(자사확인용)');
    if (summary) {
      var rows = summary.getDataRange().getValues();
      for (var i = 1; i < rows.length; i++) {
        if (rows[i][1] === title) { productName = rows[i][2] || ''; break; }
      }
    }
  } catch(e) {}

  var today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy년 MM월 dd일');

  var html = '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>정산서</title><style>'
    + '@page{size:A4;margin:12mm 15mm}*{box-sizing:border-box;margin:0;padding:0}'
    + 'body{font-family:"Malgun Gothic","맑은 고딕",sans-serif;background:#fff;color:#1a1a2e;max-width:680px;margin:0 auto;padding:0}'
    + '.print-area{padding:12px 16px;background:#f8f9fa;border-bottom:1px solid #e0e0e0;display:flex;align-items:center;gap:12px}'
    + '.btn{background:#1a2744;color:#fff;border:none;padding:9px 22px;cursor:pointer;border-radius:4px;font-size:13px;letter-spacing:0.5px}'
    + '.btn:hover{background:#2d3f6b}.print-tip{font-size:11px;color:#888;line-height:1.5}'
    + '.banner{background:linear-gradient(135deg,#1a2744 0%,#2d3f6b 100%);padding:32px 40px 28px;position:relative;overflow:hidden}'
    + '.banner::after{content:"";position:absolute;right:-20px;top:-20px;width:120px;height:120px;border-radius:50%;background:rgba(255,255,255,0.05)}'
    + '.banner-title{color:#fff;font-size:30px;letter-spacing:6px;font-weight:700;margin-bottom:6px}'
    + '.banner-sub{color:rgba(255,255,255,0.9);font-size:13px;letter-spacing:2px;font-weight:500}'
    + '.banner-date{color:rgba(255,255,255,0.8);font-size:12px;text-align:right;margin-top:10px}'
    + '.body{padding:28px 40px 32px}.confirm-box{background:#f0f4ff;border-left:4px solid #1a2744;padding:12px 16px;font-size:14px;color:#333;margin-bottom:24px;line-height:1.6}'
    + 'table{width:100%;border-collapse:collapse;margin-bottom:6px}'
    + '.tbl-head td{background:#1a2744;color:#fff;padding:11px 16px;font-size:13px;font-weight:600;letter-spacing:0.5px}'
    + 'tr td{padding:11px 16px;border-bottom:1px solid #eaecf4;font-size:14px}'
    + 'tr:nth-child(even) td{background:#f8f9fb}tr td:first-child{color:#555;width:38%}'
    + '.total-row td{font-weight:700;font-size:15px;color:#1a2744;background:#eef1fa!important;border-top:2px solid #1a2744}'
    + '.note{font-size:11.5px;color:#888;margin-top:8px;margin-bottom:24px;padding-left:2px}'
    + '.company{border:1px solid #dde1ee;border-radius:8px;padding:18px 22px;background:#fafbff}'
    + '.company-title{font-size:13px;font-weight:700;color:#1a2744;margin-bottom:10px;display:flex;align-items:center;gap:6px}'
    + '.company-title::before{content:"";display:inline-block;width:4px;height:14px;background:#1a2744;border-radius:2px}'
    + '.company-body{font-size:13px;color:#555;line-height:2}'
    + '@media print{.print-area{display:none}body{padding:0}}</style></head><body>'
    + '<div class="print-area"><button class="btn" onclick="window.print()">🖨️ 인쇄 / PDF 저장</button>'
    + '<span class="print-tip">※ 날짜·URL 머리글 제거 방법<br>인쇄 → 더보기 설정 → <b>머리글 및 바닥글</b> 체크 해제</span></div>'
    + '<div class="banner"><div class="banner-title">정 산 서</div>'
    + '<div class="banner-sub">SETTLEMENT STATEMENT</div>'
    + '<div class="banner-date">발행일: ' + today + '</div></div>'
    + '<div class="body"><div class="confirm-box">공구진행에 따른 정산내역을 확인합니다.</div>'
    + '<table><tr class="tbl-head"><td colspan="2">정산 내역</td></tr>'
    + '<tr><td>인플루언서</td><td>' + title + '</td></tr>'
    + (productName ? '<tr><td>제품명</td><td>' + productName + '</td></tr>' : '')
    + '<tr><td>진행기간</td><td>' + dateFrom + ' ~ ' + dateTo + '</td></tr>'
    + '<tr><td>총 결제금액</td><td>' + payment.toLocaleString('ko-KR') + '원</td></tr>'
    + '<tr><td>수수료</td><td>' + (commRate * 100).toFixed(1) + '%</td></tr>'
    + '<tr class="total-row"><td>정산기준금액</td><td>' + settlement.toLocaleString('ko-KR') + '원</td></tr>'
    + '</table>'
    + '<div class="company"><div class="company-title">정산 업체 정보</div>'
    + '<div class="company-body">업체명&nbsp;&nbsp;&nbsp;주식회사 정담건강<br>'
    + '사업자번호&nbsp;&nbsp;&nbsp;391-86-00889<br>'
    + '주소&nbsp;&nbsp;&nbsp;경기도 시흥시 서울대학로278번길61, 431-2호'
    + '</div></div>'
    + '<p class="note">*세금관련부분은 협의된 내용으로 처리가 됩어 실제 입금금액은 위 정산기준금액과 일부 상이할수도있습니다. (ex&gt;부가세여부, 프리랜서공제&lt;3.3%공제된 금액입금&gt; 등)</p>'
    + '</div></body></html>';

  SpreadsheetApp.getUi().showModalDialog(
    HtmlService.createHtmlOutput(html).setWidth(740).setHeight(700), '정산서');
}

// ─────────────────────────────────────────
// 마진계산기 탭 초기 설정
//
//   Row 1 : 타이틀
//   Row 2 : 제품 선택  (B2 드롭다운 — 전체 또는 개별 제품명)
//   Row 3 : 공구수수료
//   Row 4 : 박스 구성
//   Row 5 : 배너
//   Row 6 : 테이블 헤더
//   Row 7~: 계산 결과
// ─────────────────────────────────────────
function setupCalcSheet() {
  const ss  = SpreadsheetApp.getActiveSpreadsheet();
  const ref = ss.getSheetByName(REF_TAB);
  let ws = ss.getSheetByName(CALC_TAB);
  if (!ws) ws = ss.insertSheet(CALC_TAB);
  else {
    ws.clear();
    ws.getRange(1, 1, ws.getMaxRows(), ws.getMaxColumns()).clearDataValidations();
  }

  const C_HDR_BG  = "#3c5da0";
  const C_HDR_FG  = "#ffffff";
  const C_IN_BG   = "#e8f0fe";
  const C_LBL_BG  = "#f1f3f4";
  const C_NOTE_FG = "#888888";

  const widths = [180, 180, 200, 90, 68, 95, 85, 85, 72, 95, 72];
  widths.forEach((w, i) => ws.setColumnWidth(i + 1, w));

  // ── Row 1: 타이틀 ────────────────────────────────────────
  ws.setRowHeight(1, 44);
  ws.getRange("A1:K1").merge()
    .setValue("📊 공구 마진 계산기")
    .setBackground(C_HDR_BG).setFontColor(C_HDR_FG)
    .setFontSize(15).setFontWeight("bold")
    .setHorizontalAlignment("center").setVerticalAlignment("middle");

  // ── Row 2: 제품 선택 드롭다운 ───────────────────────────
  const allNames = [];
  if (ref) {
    const refData = ref.getDataRange().getValues();
    for (let i = 1; i < refData.length; i++) {
      const n = String(refData[i][0] || '').trim();
      if (n && !allNames.includes(n)) allNames.push(n);
    }
  }

  const prodOptions = ["전체 (모든 제품)"].concat(allNames);
  const prodRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(prodOptions, true)
    .setAllowInvalid(false)
    .build();

  ws.getRange("A2").setValue("제품 선택")
    .setBackground(C_LBL_BG).setFontWeight("bold");
  ws.getRange("B2").setValue("전체 (모든 제품)")
    .setBackground(C_IN_BG).setHorizontalAlignment("center")
    .setDataValidation(prodRule);
  ws.getRange("C2:K2").merge()
    .setValue("← 드롭다운에서 선택 (전체 또는 개별 제품 1개) / 제품 추가 시 [⚙️ 초기 설정] 재실행")
    .setFontColor(C_NOTE_FG).setFontStyle("italic");

  // ── Row 3: 공구수수료 ────────────────────────────────────
  ws.getRange("A3").setValue("공구수수료 (%)").setBackground(C_LBL_BG).setFontWeight("bold");
  ws.getRange("B3").setValue(40)
    .setBackground(C_IN_BG).setHorizontalAlignment("center")
    .setFontWeight("bold").setFontColor("#c62828").setFontSize(12);
  ws.getRange("C3:K3").merge()
    .setValue("← 숫자만 입력 (예: 40) / 빈칸이면 제품별 기본값 사용")
    .setFontColor(C_NOTE_FG).setFontStyle("italic");

  // ── Row 4: 박스 구성 ─────────────────────────────────────
  ws.getRange("A4").setValue("박스 구성").setBackground(C_LBL_BG).setFontWeight("bold");
  ws.getRange("B4").setValue("3,6,9bx")
    .setBackground(C_IN_BG).setHorizontalAlignment("center");
  const boxRule = SpreadsheetApp.newDataValidation()
    .requireValueInList(Object.keys(BOX_PRESETS), true)
    .setAllowInvalid(false).build();
  ws.getRange("B4").setDataValidation(boxRule);
  ws.getRange("C4:K4").merge()
    .setValue("← 프리셋 선택 / '직접입력' 선택 후 [▶ 계산 실행] 클릭 시 숫자 입력창 표시")
    .setFontColor(C_NOTE_FG).setFontStyle("italic");

  // ── Row 5: 배너 ─────────────────────────────────────────
  ws.getRange("A5:K5").merge()
    .setValue("⬆ 설정 완료 후  [📊 마진계산기 → ▶ 계산 실행]  클릭  /  계산 후 할인율·수수료 수기 수정 시 판매가·이익 자동 재계산")
    .setBackground("#fff3e0").setFontColor("#bf360c")
    .setFontWeight("bold").setHorizontalAlignment("center");

  // ── Row 6: 테이블 헤더 ───────────────────────────────────
  const headers = ["제품명","옵션","정가","제조원가","할인율","판매가","공구수수료","채널수수료","배송비","이익","이익률"];
  ws.getRange("A6:K6").setValues([headers])
    .setBackground(C_HDR_BG).setFontColor(C_HDR_FG)
    .setFontWeight("bold").setHorizontalAlignment("center");

  ss.toast("✅ 설정 완료! B2에서 제품 선택 후 [▶ 계산 실행] 클릭", "마진계산기", 5);
}

// ─────────────────────────────────────────
// 결과 초기화 (설정은 유지, 계산 결과만 삭제)
// ─────────────────────────────────────────
function clearResults() {
  const ss   = SpreadsheetApp.getActiveSpreadsheet();
  const calc = ss.getSheetByName(CALC_TAB);
  if (!calc) { SpreadsheetApp.getUi().alert("마진계산기 탭이 없습니다."); return; }
  const last = calc.getLastRow();
  if (last >= 7) {
    calc.getRange(7, 1, last - 6, 11).clearContent().clearFormat();
  }
  ss.toast("✅ 계산 결과가 초기화되었습니다.", "마진계산기", 3);
}

// ─────────────────────────────────────────
// 계산 실행
// ─────────────────────────────────────────
function runMarginCalc() {
  const ss   = SpreadsheetApp.getActiveSpreadsheet();
  const ref  = ss.getSheetByName(REF_TAB);
  const calc = ss.getSheetByName(CALC_TAB);

  if (!ref)  { SpreadsheetApp.getUi().alert("'" + REF_TAB + "' 탭을 찾을 수 없습니다."); return; }
  if (!calc) { SpreadsheetApp.getUi().alert("'" + CALC_TAB + "' 탭이 없습니다.\n먼저 [⚙️ 시트 초기 설정]을 실행하세요."); return; }

  // B2: 제품 선택
  const prodVal = String(calc.getRange(2, 2).getValue()).trim();

  // 전체 제품 목록
  const allNames = [];
  const refData  = ref.getDataRange().getValues();
  for (let i = 1; i < refData.length; i++) {
    const n = String(refData[i][0] || '').trim();
    if (n && !allNames.includes(n)) allNames.push(n);
  }

  const selectedNames = (!prodVal || prodVal === "전체 (모든 제품)") ? allNames : [prodVal];

  if (selectedNames.length === 0) {
    SpreadsheetApp.getUi().alert("'" + REF_TAB + "' 탭에 제품 데이터가 없습니다.");
    return;
  }

  // B4: 박스 구성
  const boxPreset = String(calc.getRange(4, 2).getValue()).trim();
  let customStr = "";
  if (BOX_PRESETS[boxPreset] === "custom") {
    const resp = SpreadsheetApp.getUi().prompt(
      "직접입력 박스수",
      "박스 수를 쉼표로 구분해 입력하세요  예: 1,3,6,9",
      SpreadsheetApp.getUi().ButtonSet.OK_CANCEL
    );
    if (resp.getSelectedButton() !== SpreadsheetApp.getUi().Button.OK) return;
    customStr = resp.getResponseText().trim();
    if (!customStr) { SpreadsheetApp.getUi().alert("박스 수를 입력하지 않아 취소되었습니다."); return; }
  }

  // 현재 마지막 데이터 행 다음에 추가 (최소 row 7)
  const currentLast = calc.getLastRow();
  const OUT = currentLast < 7 ? 7 : currentLast + 1;

  _executeCalc(selectedNames, ref, calc, OUT, 3, 4, boxPreset, customStr);
}

// ─────────────────────────────────────────
// 실제 계산 처리
// ─────────────────────────────────────────
function _executeCalc(selectedNames, ref, calc, OUT, gongguRow, boxRow, boxPreset, customStr) {
  const selectedSet    = new Set(selectedNames);
  const gongguRaw      = String(calc.getRange(gongguRow, 2).getValue()).trim();
  const gongguOverride = gongguRaw !== "" ? Number(gongguRaw) / 100 : null;

  let allowedBx;
  if (BOX_PRESETS[boxPreset] === "custom") {
    allowedBx = String(customStr).split(",").map(x => parseInt(x.trim())).filter(n => !isNaN(n) && n > 0);
    if (!allowedBx.length) { SpreadsheetApp.getUi().alert("박스 수 형식 오류\n예: 1,3,6,9"); return; }
  } else {
    allowedBx = BOX_PRESETS[boxPreset] || [3, 6, 9];
  }

  const refData = ref.getDataRange().getValues();
  function toNum(v) { return Number(String(v).replace(/,/g,"").trim()) || 0; }
  function toPct(v) {
    const s = String(v).replace(/,/g,"").trim();
    if (s.endsWith("%")) return parseFloat(s) / 100;
    const n = parseFloat(s);
    return isNaN(n) ? 0 : (n > 1 ? n / 100 : n);
  }

  const dataRows    = [];
  const formulaInfo = [];
  const profitRates = [];

  for (let i = 1; i < refData.length; i++) {
    const r    = refData[i];
    const name = String(r[0] || '').trim();
    if (!name || !selectedSet.has(name)) continue;

    const listPrice1  = toNum(r[2]);
    const cost1       = toNum(r[3]);
    const discRate    = toPct(r[4]);
    const defGonggu   = toPct(r[6]);
    const channelComm = toPct(r[7]);
    const delivery    = toNum(r[8]);
    if (listPrice1 === 0) continue;

    const gongguComm = gongguOverride !== null ? gongguOverride : defGonggu;

    for (const bx of allowedBx) {
      const listN  = listPrice1 * bx;
      const saleN  = Math.round(listN * (1 - discRate));
      const profit = saleN - cost1*bx - saleN*gongguComm - saleN*channelComm - delivery;
      const pRate  = saleN > 0 ? profit / saleN : 0;

      dataRows.push([name, bx, listN, cost1*bx, discRate, saleN, gongguComm, channelComm, delivery, Math.round(profit), pRate]);
      formulaInfo.push({ listPrice1, cost1, bx, listN });
      profitRates.push(pRate);
    }
  }

  if (!dataRows.length) {
    calc.getRange(OUT, 1).setValue("조회된 데이터가 없습니다.");
    return;
  }

  calc.getRange(OUT, 1, dataRows.length, 11).setValues(dataRows);

  const cF = formulaInfo.map((fi, i) => [`=${fi.listPrice1}*B${OUT+i}`]);
  const dF = formulaInfo.map((fi, i) => [`=${fi.cost1}*B${OUT+i}`]);
  const fF = formulaInfo.map((fi, i) => [`=ROUND(C${OUT+i}*(1-E${OUT+i}),0)`]);
  const jF = dataRows.map((_, i) => [`=F${OUT+i}-D${OUT+i}-(F${OUT+i}*G${OUT+i})-(F${OUT+i}*H${OUT+i})-I${OUT+i}`]);
  const kF = dataRows.map((_, i) => [`=IF(F${OUT+i}>0,J${OUT+i}/F${OUT+i},0)`]);

  calc.getRange(OUT, 3,  dataRows.length, 1).setFormulas(cF);
  calc.getRange(OUT, 4,  dataRows.length, 1).setFormulas(dF);
  calc.getRange(OUT, 6,  dataRows.length, 1).setFormulas(fF);
  calc.getRange(OUT, 10, dataRows.length, 1).setFormulas(jF);
  calc.getRange(OUT, 11, dataRows.length, 1).setFormulas(kF);

  const fmt = ["@",'0"bx"',"#,##0","#,##0","0%","#,##0","0%","0.00%","#,##0","#,##0","0.00%"];
  fmt.forEach((f, i) => calc.getRange(OUT, i+1, dataRows.length, 1).setNumberFormat(f));

  for (let i = 0; i < dataRows.length; i++) {
    calc.getRange(OUT+i, 1, 1, 11).setBackground(i%2===0 ? "#e8f4fd" : "#ffffff");
    const pr = profitRates[i];
    let fc = "#1b5e20";
    if      (pr < 0)    fc = "#b71c1c";
    else if (pr < 0.05) fc = "#e65100";
    else if (pr < 0.10) fc = "#f57f17";
    calc.getRange(OUT+i, 11).setFontColor(fc).setFontWeight("bold");
  }

  const prodCount = new Set(dataRows.map(r => r[0])).size;
  SpreadsheetApp.getActiveSpreadsheet()
    .toast("✅ " + prodCount + "개 제품 × " + allowedBx.length + "개 옵션 = " + dataRows.length + "행 완료", "마진계산기", 4);
}
