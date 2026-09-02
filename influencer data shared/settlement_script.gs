// ─────────────────────────────────────────
// 정산서 생성 스크립트
// Google Sheets → 확장 프로그램 → Apps Script에 붙여넣기
// ─────────────────────────────────────────

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📋 정산서')
    .addItem('정산서 생성', 'generateSettlement')
    .addToUi();
}

function generateSettlement() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  var row   = sheet.getActiveRange().getRow();

  if (row <= 1) {
    SpreadsheetApp.getUi().alert('정산할 캠페인 행을 먼저 클릭한 뒤 실행해주세요.');
    return;
  }

  var vals       = sheet.getRange(row, 1, 1, 10).getValues()[0];
  var title      = vals[1] || '-';
  var dateFrom   = vals[2] || '-';
  var dateTo     = vals[3] || '-';
  var payment    = Number(String(vals[8]).replace(/[^0-9.]/g, '')) || 0;
  var commission = Number(String(vals[9]).replace(/[^0-9.]/g, '')) || 0;
  var commRate   = commission > 1 ? commission / 100 : commission;
  var settlement = Math.round(payment * commRate);

  if (!payment) {
    SpreadsheetApp.getUi().alert('I열(결제금액)을 먼저 입력해주세요.');
    return;
  }

  // 캠페인 실적 탭에서 제품명 조회 (title + 시작일로 정확히 매칭)
  var productName = '';
  try {
    var summary = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('캠페인 실적(자사확인용)')
               || SpreadsheetApp.getActiveSpreadsheet().getSheetByName('캠페인 실적');
    if (summary) {
      var rows = summary.getDataRange().getValues();
      var dateFromStr = dateFrom instanceof Date
        ? Utilities.formatDate(dateFrom, 'Asia/Seoul', 'yyyy-MM-dd')
        : String(dateFrom).replace(/\./g, '-').substring(0, 10);
      // 1차: title + 시작일 정확히 일치
      for (var i = 1; i < rows.length; i++) {
        if (rows[i][1] === title) {
          var rowDate = rows[i][4];
          var rowDateStr = rowDate instanceof Date
            ? Utilities.formatDate(rowDate, 'Asia/Seoul', 'yyyy-MM-dd')
            : String(rowDate).replace(/\./g, '-').substring(0, 10);
          if (rowDateStr === dateFromStr) { productName = rows[i][2] || ''; break; }
        }
      }
      // 2차 fallback: title만 일치
      if (!productName) {
        for (var i = 1; i < rows.length; i++) {
          if (rows[i][1] === title) { productName = rows[i][2] || ''; break; }
        }
      }
    }
  } catch(e) {}

  var today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy년 MM월 dd일');

  var html = '<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>정산서</title><style>'
    + '@page{size:A4;margin:12mm 15mm}'
    + '*{box-sizing:border-box;margin:0;padding:0}'
    + 'body{font-family:"Malgun Gothic","맑은 고딕",sans-serif;background:#fff;color:#1a1a2e;max-width:680px;margin:0 auto;padding:0}'

    /* 인쇄 버튼 영역 */
    + '.print-area{padding:12px 16px;background:#f8f9fa;border-bottom:1px solid #e0e0e0;display:flex;align-items:center;gap:12px}'
    + '.btn{background:#1a2744;color:#fff;border:none;padding:9px 22px;cursor:pointer;border-radius:4px;font-size:13px;letter-spacing:0.5px}'
    + '.btn:hover{background:#2d3f6b}'
    + '.print-tip{font-size:11px;color:#888;line-height:1.5}'

    /* 상단 배너 */
    + '.banner{background:linear-gradient(135deg,#1a2744 0%,#2d3f6b 100%);padding:32px 40px 28px;position:relative;overflow:hidden}'
    + '.banner::after{content:"";position:absolute;right:-20px;top:-20px;width:120px;height:120px;border-radius:50%;background:rgba(255,255,255,0.05)}'
    + '.banner-title{color:#ffffff;font-size:30px;letter-spacing:6px;font-weight:700;margin-bottom:6px;text-shadow:0 1px 3px rgba(0,0,0,0.3)}'
    + '.banner-sub{color:rgba(255,255,255,0.9);font-size:13px;letter-spacing:2px;font-weight:500}'
    + '.banner-date{color:rgba(255,255,255,0.8);font-size:12px;text-align:right;margin-top:10px}'

    /* 본문 */
    + '.body{padding:28px 40px 32px}'
    + '.confirm-box{background:#f0f4ff;border-left:4px solid #1a2744;padding:12px 16px;font-size:14px;color:#333;margin-bottom:24px;line-height:1.6}'

    /* 테이블 */
    + 'table{width:100%;border-collapse:collapse;margin-bottom:6px}'
    + '.tbl-head td{background:#1a2744;color:#fff;padding:11px 16px;font-size:13px;font-weight:600;letter-spacing:0.5px}'
    + 'tr td{padding:11px 16px;border-bottom:1px solid #eaecf4;font-size:14px}'
    + 'tr:nth-child(even) td{background:#f8f9fb}'
    + 'tr td:first-child{color:#555;width:38%}'
    + '.total-row td{font-weight:700;font-size:15px;color:#1a2744;background:#eef1fa!important;border-top:2px solid #1a2744}'
    + '.note{font-size:11.5px;color:#888;margin-top:8px;margin-bottom:24px;padding-left:2px}'

    /* 업체 정보 */
    + '.company{border:1px solid #dde1ee;border-radius:8px;padding:18px 22px;background:#fafbff}'
    + '.company-title{font-size:13px;font-weight:700;color:#1a2744;margin-bottom:10px;display:flex;align-items:center;gap:6px}'
    + '.company-title::before{content:"";display:inline-block;width:4px;height:14px;background:#1a2744;border-radius:2px}'
    + '.company-body{font-size:13px;color:#555;line-height:2}'

    /* 서명 */

    + '@media print{'
    + '.print-area{display:none}'
    + 'body{padding:0}'
    + '}'
    + '</style></head><body>'

    /* 버튼 영역 */
    + '<div class="print-area">'
    + '<button class="btn" onclick="window.print()">🖨️ 인쇄 / PDF 저장</button>'
    + '<span class="print-tip">※ 날짜·URL 머리글 제거 방법<br>인쇄 → 더보기 설정 → <b>머리글 및 바닥글</b> 체크 해제</span>'
    + '</div>'

    /* 배너 */
    + '<div class="banner">'
    + '<div class="banner-title">정 산 서</div>'
    + '<div class="banner-sub">SETTLEMENT STATEMENT</div>'
    + '<div class="banner-date">발행일: ' + today + '</div>'
    + '</div>'

    /* 본문 */
    + '<div class="body">'
    + '<div class="confirm-box">공구진행에 따른 정산내역을 확인합니다.</div>'
    + '<table>'
    + '<tr class="tbl-head"><td colspan="2">정산 내역</td></tr>'
    + '<tr><td>인플루언서</td><td>' + title + '</td></tr>'
    + (productName ? '<tr><td>제품명</td><td>' + productName + '</td></tr>' : '')
    + '<tr><td>진행기간</td><td>' + dateFrom + ' ~ ' + dateTo + '</td></tr>'
    + '<tr><td>총 결제금액</td><td>' + payment.toLocaleString('ko-KR') + '원</td></tr>'
    + '<tr><td>수수료</td><td>' + (commRate * 100).toFixed(1) + '%</td></tr>'
    + '<tr class="total-row"><td>정산기준금액</td><td>' + settlement.toLocaleString('ko-KR') + '원</td></tr>'
    + '</table>'
    + '<p class="note">* 세금관련부분은 협의된 내용으로 처리됩니다. (ex) 부가세 포함, 프리랜서 공제 등</p>'

    + '<div class="company">'
    + '<div class="company-title">정산 업체 정보</div>'
    + '<div class="company-body">'
    + '업체명&nbsp;&nbsp;&nbsp;주식회사 정담건강<br>'
    + '사업자번호&nbsp;&nbsp;&nbsp;391-86-00889<br>'
    + '주소&nbsp;&nbsp;&nbsp;경기도 시흥시 서울대학로278번길61, 431-2호'
    + '</div></div>'

    + '</div>'
    + '</body></html>';

  SpreadsheetApp.getUi().showModalDialog(
    HtmlService.createHtmlOutput(html).setWidth(740).setHeight(700),
    '정산서'
  );
}
