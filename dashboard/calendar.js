/* 월 출결 캘린더 렌더러 — 관리판(index.html)과 근로자별 리포트(reports/workers/*.html)가 같이 쓴다.
   데이터: window.FLEX_DATA (src/build_reports.py 가 만든 calendar_data) */
(function (g) {
  'use strict';
  var DOWK = '월화수목금토일';
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function hm(min) {
    if (min == null || isNaN(min)) return '';
    var s = min < 0 ? '−' : ''; min = Math.round(Math.abs(min));
    return s + Math.floor(min / 60) + ':' + ('0' + (min % 60)).slice(-2);
  }
  function hmH(h) { return hm(h * 60); }
  function addDays(iso, n) { var d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
  function dowOf(iso) { return (new Date(iso + 'T00:00:00Z').getUTCDay() + 6) % 7; }
  function mondayOf(iso) { return addDays(iso, -dowOf(iso)); }
  function lastDay(ym) { var y = +ym.slice(0, 4), m = +ym.slice(5, 7); return ym + '-' + ('0' + new Date(Date.UTC(y, m, 0)).getUTCDate()).slice(-2); }
  function ymLabel(ym) { return ym.slice(0, 4) + '년 ' + (+ym.slice(5)) + '월'; }
  function shortName(n) { return String(n).replace('(가상)', ''); }

  var PLAN = { '장일': ['장일 9h', 'p-long'], '단축일': ['단축 4h', 'p-short'], '통상': ['통상 8h', ''], '휴일': ['휴일', ''] };

  var HOL = {};
  function dayHTML(iso, c, inMonth) {
    var d = +iso.slice(8);
    if (!c && HOL[iso]) return '<div class="day k-hol' + (inMonth ? '' : ' out') + '"><div class="top"><span class="num">' + d + '</span><span class="plan">' + esc(HOL[iso]) + '</span></div></div>';
    if (!c) return '<div class="day empty' + (inMonth ? '' : ' out') + '"><div class="top"><span class="num">' + d + '</span></div></div>';
    var p = PLAN[c.p] || [c.p, ''];
    var body = '';
    if (c.s === '근무') {
      body = '<div class="t">' + esc(c.i) + '–' + esc(c.o) + '</div><div class="w">' + hm(c.w) + '</div>';
    } else if (c.s === '누락') {
      body = '<div class="t">' + (c.i ? esc(c.i) + ' 출근 · 퇴근 ?' : '기록 없음') + '</div><div class="w">?</div>';
    } else if (c.s === '연차') {
      body = '<div class="w">연차</div>';
    }
    var plan = c.k === 'hol' && !c.w ? '<span class="plan">' + esc(c.h || '휴일') + '</span>' : '<span class="plan ' + p[1] + '">' + p[0] + '</span>';
    return '<div class="day k-' + c.k + (inMonth ? '' : ' out') + '" title="' + esc(iso + ' ' + c.n) + '"><div class="top"><span class="num">' + d + '</span>' + plan + '</div>' +
      body + '<div class="n">' + esc(c.k === 'hol' && !c.w ? '' : c.n) + '</div></div>';
  }

  function weekHTML(wk, extra) {
    if (!wk) return '<div class="wk"></div>';
    return '<div class="wk k-' + wk.k + '"><span class="muted tiny">주 합계' + (wk.ref ? ' (참고)' : ' · 주 소정') + '</span>' +
      '<span>계획 <b>' + hmH(wk.plan) + '</b></span><span>실적 <b>' + hmH(wk.act) + '</b></span>' +
      '<span>' + esc(wk.n) + '</span>' + (extra ? '<span class="tiny">' + esc(extra) + '</span>' : '') +
      (wk.ref ? '<span class="ref">선택근무는 월 합계로 판정</span>' : '') + '</div>';
  }

  function calendarHTML(w, ym) {
    HOL = (g.FLEX_DATA && g.FLEX_DATA.holidays) || {};
    var first = ym + '-01', last = lastDay(ym), mon = mondayOf(first), h = '';
    for (var i = 0; i < 5; i++) h += '<div class="hd">' + DOWK[i] + '</div>';
    h += '<div class="hd">주 합계</div>';
    while (mon <= last) {
      for (var k = 0; k < 5; k++) {
        var iso = addDays(mon, k);
        h += dayHTML(iso, w.days[iso], iso.slice(0, 7) === ym);
      }
      var extra = [];
      [5, 6].forEach(function (k) { var iso = addDays(mon, k), c = w.days[iso]; if (c && c.w) extra.push(DOWK[k] + ' ' + hm(c.w) + ' 휴일근로'); });
      h += weekHTML(w.weeks[mon], extra.join(', '));
      mon = addDays(mon, 7);
    }
    return '<div class="cal">' + h + '</div>';
  }

  function verdictHTML(w, mb) {
    var cls, big;
    if (w.flex) {
      cls = mb.level === '미지급' ? 'bad' : mb.level === '조치 필요' ? 'warn' : 'good';
      big = mb.level === '미지급' ? '장려금 미지급 — ' + mb.fails : mb.level === '조치 필요' ? '장려금 지급 대상 · 확인할 것 있음' : '장려금 지급 대상 · 이상 없음';
      return '<div class="verdict ' + cls + '"><span class="big">' + esc(big) + '</span>' +
        '<span class="kv">단축 합계 <b>' + hmH(mb.cut) + '</b> / 기준 6:00</span>' +
        '<span class="kv">출결 누락 <b>' + mb.miss + '회</b> / 5회 이상 미지급</span>' +
        '<span class="kv">정산기간 소정 <b>' + hmH(mb.req) + '</b> · 실적 <b>' + hmH(mb.act) + '</b> · 과부족 <b>' + (mb.bal >= 0 ? '+' : '') + hmH(mb.bal) + '</b>' + (mb.miss ? ' (누락일 제외)' : '') + '</span>' +
        (mb.round ? '<span class="chip">' + mb.round + '차 신청분</span>' : '') + '</div>';
    }
    cls = mb.level === '확인 필요' ? 'bad' : mb.level === '주의' ? 'warn' : 'good';
    big = mb.level === '정상' ? '근태 정상' : '근태 ' + mb.level;
    return '<div class="verdict ' + cls + '"><span class="big">' + big + '</span>' +
      '<span class="kv">근무일 <b>' + mb.wd + '일</b> · 소정 <b>' + hmH(mb.req) + '</b> · 실적 <b>' + hmH(mb.act) + '</b></span>' +
      '<span class="kv">누락 <b>' + mb.miss + '</b> · 부족 주 <b>' + mb.uw + '</b> · 지각 <b>' + mb.late + '</b></span>' +
      (mb.ot > 0 ? '<span class="kv">연장 <b>' + hmH(mb.ot) + '</b></span>' : '') + '</div>';
  }

  function itemsHTML(mb) {
    var ic = { ok: '✓', warn: '!', bad: '×', info: 'i' };
    return '<ul class="items">' + mb.items.map(function (it) {
      return '<li class="k-' + it.k + '"><span class="ic">' + ic[it.k] + '</span><div><div class="tt">' + esc(it.t) + '</div><div class="dd">' + esc(it.d) +
        '</div><div class="bb">근거: ' + esc(it.b) + '</div></div></li>';
    }).join('') + '</ul>';
  }

  function legendHTML(flex) {
    return '<div class="legend"><span><i class="l-short"></i>단축일 정상' + (flex ? '' : '(해당 없음)') + '</span><span><i></i>계획대로 근무</span>' +
      '<span><i class="l-warn"></i>계획보다 적게 · 지각 · 사업주 보정</span><span><i class="l-bad"></i>출결 누락 · 단축일 4시간 미달</span>' +
      '<span><i class="l-leave"></i>연차 · 공휴일</span></div>';
  }

  function monthView(w, ym, opts) {
    opts = opts || {};
    var mb = w.months[ym];
    if (!mb) return '<p class="muted">' + ymLabel(ym) + ' 근무 기록 없음 (입사 전·퇴사 후)</p>';
    var id = 'msg-' + w.id + '-' + ym;
    return '<div class="month-wrap"><div class="cal-head"><h3>' + ymLabel(ym) + '</h3><span class="muted small">' + esc(shortName(w.name)) + ' · ' + esc(w.dept) + '</span></div>' +
      (opts.legend ? legendHTML(w.flex) : '') + calendarHTML(w, ym) + verdictHTML(w, mb) + itemsHTML(mb) +
      '<div class="cal-head" style="margin-top:4px"><b>근로자에게 보낼 안내</b>' +
      (opts.copy ? '<button class="btn" data-copy="' + id + '">문안 복사</button>' : '') + '</div><pre class="msg" id="' + id + '">' + esc(mb.msg) + '</pre></div>';
  }

  function levelClass(w, mb) {
    if (!mb) return '';
    if (w.flex) return mb.level === '미지급' ? 'bad' : mb.level === '조치 필요' ? 'warn' : 'good';
    return mb.level === '확인 필요' ? 'bad' : mb.level === '주의' ? 'warn' : 'good';
  }

  function guideHTML(data, w) {
    var sch = w.flex ? data.schedule['선택근무'] : data.schedule['일반'];
    var s = '<section class="guide"><h3 style="margin-bottom:6px">이 캘린더 읽는 법</h3><ul class="small" style="margin:0;padding-left:18px">' +
      '<li>내 근무계획: ' + esc(sch) + '</li>' +
      '<li>하루 칸: 그날 계획(장일·단축·통상), 출근–퇴근 시각, 실제 근무시간(휴게 뺀 시간), 문제가 있으면 무엇을 하면 되는지</li>' +
      '<li>오른쪽 \'주 합계\': 그 주 계획 시간과 실제 시간. ' + (w.flex ? '선택근무는 한 달 합계로 판단하므로 주 합계는 참고용입니다.' : '주 40시간(공휴일 있는 주는 근무일 × 8시간)을 채워야 합니다.') + '</li>' +
      (w.flex ? '<li>장려금: 한 달에 <b>단축 6시간 이상</b>(8시간보다 1시간 이상 적게 일한 날만, 단축일 하루 = 4시간 단축) + <b>출퇴근 기록 누락 5회 미만</b>이면 그 달 지급됩니다.</li>' : '') +
      '<li>출퇴근 버튼을 안 누르면 그날은 근무시간을 셀 수 없습니다. 빨간 칸이 보이면 정정 신청을 해 주세요.</li></ul></section>';
    return s;
  }

  function renderWorkerReport(el, data, id) {
    var w = data.workers.filter(function (x) { return x.id === id; })[0];
    var yms = data.months.filter(function (ym) { return w.months[ym]; });
    var strip = yms.map(function (ym) {
      var mb = w.months[ym], c = levelClass(w, mb);
      return '<a href="#m-' + ym + '" class="chip ' + c + '">' + (+ym.slice(5)) + '월 ' + (w.flex ? (mb.pay ? (mb.level === '정상' ? '○' : '△') : '×') : (mb.level === '정상' ? '○' : '!')) + '</a>';
    }).join(' ');
    var paid = yms.filter(function (ym) { return w.months[ym].pay; }).length;
    var h = '<header style="margin:6px 0 14px"><div class="muted small">' + esc(data.company) + ' · 출결 캘린더 리포트 · ' + esc(data.source) + '</div>' +
      '<h1 style="font-size:24px;margin:4px 0">' + esc(shortName(w.name)) + ' <span class="muted" style="font-size:15px">' + esc(w.dept) + ' · ' + (w.flex ? '선택근무 참여자(장려금 지원대상, ' + esc(w.start) + '~)' : '일반 근로자(9 to 6)') + '</span></h1>' +
      (w.flex ? '<p style="margin:4px 0">장려금 지급 예상 <b>' + paid + '/' + yms.length + '개월</b> · 선호 단축 요일 <b>' + esc(w.pref ? w.pref.top + '요일 ' + Math.round(w.pref.share * 100) + '%' : '-') + '</b></p>' : '') +
      '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0">' + strip + '</div></header>' + guideHTML(data, w) + legendHTML(w.flex);
    h += yms.map(function (ym) { return '<section id="m-' + ym + '">' + monthView(w, ym, {}) + '</section>'; }).join('');
    el.innerHTML = h;
  }

  g.FC = { esc: esc, hm: hm, hmH: hmH, ymLabel: ymLabel, shortName: shortName, calendarHTML: calendarHTML, monthView: monthView, levelClass: levelClass, legendHTML: legendHTML, guideHTML: guideHTML, DOWK: DOWK };
  g.renderWorkerReport = renderWorkerReport;
})(window);
