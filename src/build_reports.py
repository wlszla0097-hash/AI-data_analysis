"""근로자별 월 출결 캘린더 데이터·리포트 + 기업 보고서 + 관리판 데이터. analysis.py → eligibility.py 다음에 실행.

캘린더는 지침을 모르는 근로자도 읽을 수 있게 만든다.
- 하루 칸: 그날 계획(장일 9h·단축일 4h·통상 8h), 출퇴근 시각, 실근로, 부족·누락을 쉬운 말로
- 주 줄: 그 주 계획 합계 대비 실적 (일반 근로자 = 주 소정근로 40시간 판정, 선택근무 = 참고)
- 월 아래: 정산기간 합계, 장려금 요건(월 단축 6시간·누락 5회 미만), 해야 할 일

출력
- reports/calendar_data.json         모든 근로자 캘린더 데이터 (관리판과 리포트가 같은 데이터를 씀)
- reports/workers/1_선택근무/E07_조현우.html 등   근로자별 1년치 월 캘린더 리포트 (인쇄 가능, 오프라인)
- reports/company_progress_report.md  기업 담당자·고용센터 검토용 보고서
- dashboard/data.js                   관리판 데이터
실행: python src/build_reports.py [기준월 YYYY-MM, 기본 마지막 달]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis import CFG, DESIG, DOWS, REG, RULE, TOL_D, TOL_M, TOL_W  # noqa: E402
from eligibility import headcount_cap, period_months, round_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "processed"
REP = ROOT / "reports"
DOWK = "월화수목금토일"
SHORT_NEED_H = RULE["short"]["month_cut_min"] / 60
MISS_FAIL = RULE["record"]["fail_at"]


def hm(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = "−" if x < 0 else ""
    x = int(round(abs(x)))
    return f"{s}{x // 60}:{x % 60:02d}"


def dur(x) -> str:
    """분 → '1시간 30분' (쉬운 말)."""
    x = int(round(abs(x)))
    h, m = divmod(x, 60)
    return (f"{h}시간 " if h else "") + (f"{m}분" if m else "").strip() or "0분"


def clock(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    x = int(round(x)) % 1440
    return f"{x // 60:02d}:{x % 60:02d}"


def md(d: pd.Timestamp) -> str:
    return f"{d.month}/{d.day}({DOWK[d.weekday()]})"


# ───────────────────────── 하루 칸 ─────────────────────────
def day_cell(r, flex: bool) -> dict:
    plan, st, miss = r["plan"], r["status"], r["miss_type"]
    c = {"p": plan, "s": st, "i": clock(r["start_min"]), "o": clock(r["end_min"]), "w": None if np.isnan(r["worked_min"]) else int(r["worked_min"]),
         "pm": int(r["plan_min"]), "h": r["holiday"], "m": miss, "cut": int(r["cut_min"]) if r["cut_min"] else 0}
    if st == "휴일":
        c["k"], c["n"] = "hol", f"{r['holiday'] or '휴일'} 근무 {hm(r['worked_min'])} → 휴일근로" if c["w"] else (r["holiday"] or "휴일")
        return c
    if not r["workday"]:
        c["k"], c["n"] = "hol", r["holiday"] or "휴일"
        return c
    if st == "연차":
        c["k"], c["n"] = "leave", f"연차 ({dur(r['plan_min'])} 인정)"
        return c
    if st == "누락":
        c["k"] = "bad"
        c["n"] = {"기록 없음": "출퇴근 기록 없음 → 근무했다면 정정 신청, 아니면 연차·결근 처리",
                  "퇴근 미기록": "퇴근 버튼 안 누름 → 퇴근 시각 정정 신청",
                  "16시간 이상 체류": "다음 날 퇴근 버튼 → 실제 퇴근 시각 정정 신청"}[miss]
        return c
    w, gap = r["worked_min"], r["day_gap_min"]
    notes, k = [], "ok"
    if plan == "단축일":
        if r["short_under"]:
            k = "bad"
            notes.append(f"단축일 최소 4시간인데 {hm(w)}만 근무 ({dur(240 - w)} 일찍 퇴근)")
        else:
            k = "short"
            notes.append("단축일 ✓")
    elif gap < -TOL_D:
        k = "warn"
        notes.append(f"계획 {hm(r['plan_min'])}보다 {dur(-gap)} 적게 근무")
    else:
        notes.append("✓")
    if c["cut"]:
        notes.append(f"장려금 단축 {hm(c['cut'])} 인정")
    elif flex and r["is_short_actual"] and miss:
        notes.append("기록 보정일이라 장려금 단축 증빙 안 됨")
    if r["late"]:
        notes.append(f"지각 {clock(r['start_min'])}")
        if k == "ok":
            k = "warn"
    if miss == "사후입력":
        notes.append("사업주 보정 기록" + (" → 누락 1회로 셈" if flex else ""))
        if k in ("ok", "short") and flex:
            k = "warn"
    if gap > 30 and plan != "단축일":
        notes.append(f"초과 {hm(gap)} → 연장수당")
    c["k"], c["n"] = k, " · ".join(notes)
    return c


# ───────────────────────── 주·월 ─────────────────────────
def week_row(wr, flex: bool) -> dict:
    gap = wr["gap_h"] * 60
    if wr["missing"] and wr["actual_h"] * 60 < wr["plan_min"] - TOL_W:
        note = f"누락 {int(wr['missing'])}일 정정 전"
    elif wr["under"]:
        note = f"{dur(-gap)} 부족" + (" (참고 — 판정은 월 합계)" if flex else "")
    elif gap > 30:
        note = f"✓ +{hm(gap)} 초과" + ("" if flex else " → 연장수당")
    else:
        note = "✓ 충족"
    return {"plan": round(wr["plan_min"] / 60, 2), "act": round(wr["actual_h"], 2), "gap": round(wr["gap_h"], 2),
            "miss": int(wr["missing"]), "under": bool(wr["under"]), "ref": flex, "n": note,
            "k": "bad" if (wr["under"] and not flex) else ("warn" if (wr["under"] or wr["missing"]) else "ok")}


def month_block(mr, g: pd.DataFrame, flex: bool) -> dict:
    """월 판정·점검 항목·근로자에게 보낼 문안."""
    items, todo = [], []
    miss = g[g["miss_type"] != ""]
    miss_list = ", ".join(f"{md(x.date)} {x.miss_type}" for x in miss.itertuples())
    n_miss = int(mr["missing"])
    # ① 출결 기록
    if flex:
        lv = "bad" if n_miss >= MISS_FAIL else ("warn" if n_miss else "ok")
        items.append({"k": lv, "t": f"① 출결 기록 누락 {n_miss}회", "d": miss_list or "누락 없음",
                      "b": f"선택근무는 월 {MISS_FAIL}회 이상 누락되면 그 달 장려금 미지급 (사업계획서 별지 유의사항). 사업주 보정(사후입력)도 누락으로 셈"})
        if n_miss:
            todo.append(f"출결 누락 {n_miss}회 정정 신청 ({miss_list})")
    else:
        open_ = miss[miss["miss_type"] != "사후입력"]
        lv = "bad" if len(open_) >= RULE["record"]["warn_at"] else ("warn" if len(open_) else "ok")
        items.append({"k": lv, "t": f"① 출결 기록 누락 {len(open_)}회" + (f" (사업주 보정 {len(miss) - len(open_)}회 별도)" if len(miss) > len(open_) else ""),
                      "d": miss_list or "누락 없음", "b": "전자 출결 기록은 임금·근로시간 산정 근거. 누락일은 근무 여부 확인 후 정정"})
        if len(open_):
            todo.append(f"출결 누락 {len(open_)}회 정정 ({', '.join(f'{md(x.date)} {x.miss_type}' for x in open_.itertuples())})")
    # ② 소정근로시간
    req = mr["required_h"]
    under_days = g[g["under_day"] & (g["plan"] != "단축일")]
    ud = ", ".join(f"{md(x.date)} {hm(x.worked_min)}/{hm(x.plan_min)}" for x in under_days.itertuples())
    if flex:
        bal = mr["balance_h"] * 60
        lv = "bad" if bal < -TOL_M else "ok"
        items.append({"k": lv, "t": f"② 정산기간 소정근로 {'부족 ' + dur(-bal) if bal < -TOL_M else '충족'}",
                      "d": f"근무일 {int(mr['workdays'])}일 × 8시간 = {req:.0f}시간, 실적(연차 포함) {hm(mr['actual_h'] * 60)}"
                           + (f", 누락일 계획 {hm((req - mr['actual_h']) * 60 + bal)} 제외" if n_miss and mr['missing'] - mr['miss_사후입력'] > 0 else "")
                           + (f" · 계획보다 적게 일한 날: {ud}" if ud else ""),
                      "b": "근로기준법 제52조: 선택근무는 정산기간(1개월) 총 근로시간으로 판단. 주별 부족은 참고"})
        if bal < -TOL_M:
            todo.append(f"정산기간 근무시간 {dur(-bal)} 부족 — 장일(9시간)은 19:00 퇴근 ({ud})" if ud else f"정산기간 근무시간 {dur(-bal)} 부족")
    else:
        uw = int(mr["under_weeks"])
        lv = "bad" if uw >= 2 else ("warn" if uw else "ok")
        early = g[(g["end_min"] < 18 * 60 - TOL_D) & g["worked_min"].notna() & g["workday"]]
        items.append({"k": lv, "t": f"② 주 소정근로(40시간) 부족 {uw}주",
                      "d": (f"계획보다 적게 일한 날: {ud}" if ud else "부족한 날 없음") + (f" · 지각 {int(mr['late'])}회" if mr["late"] else ""),
                      "b": "근로기준법 제50조: 1주 40시간(공휴일 주는 근무일 × 8시간). 부족분은 결근·조퇴로 처리하거나 연차 신청"})
        if uw or len(early):
            todo.append(f"조퇴·지각으로 부족한 주 {uw}주 — 연차(반차) 신청 또는 근태 처리" if uw else f"조퇴 {len(early)}회 확인")
    # ③ 단축일 4시간 (선택근무)
    if flex:
        su = g[g["short_under"]]
        sl = ", ".join(f"{md(x.date)} {x.start_min and clock(x.start_min)}~{clock(x.end_min)} ({hm(x.worked_min)})" for x in su.itertuples())
        items.append({"k": "warn" if len(su) else "ok", "t": f"③ 단축일 4시간 미달 {len(su)}일", "d": sl or "없음",
                      "b": "선택근무 근무계획: 단축일 09:00~13:30(휴게 12:00~12:30) = 4시간. 장려금 단축 시간에는 포함되지만 회사 근무계획 위반"})
        if len(su):
            todo.append(f"단축일 4시간 미달 {len(su)}일 — 단축일도 13:30까지 근무 ({sl})")
        # ④ 장려금 단축
        sd = g[g["is_short_actual"]]
        cut_h = mr["cut_h"]
        lst = ", ".join(f"{md(x.date)} {hm(x.cut_min) if x.cut_min else '증빙 안 됨'}" for x in sd.itertuples())
        items.append({"k": "bad" if cut_h < SHORT_NEED_H else "ok", "t": f"④ 장려금 단축 합계 {hm(cut_h * 60)} (기준 6:00)",
                      "d": lst or "단축한 날 없음",
                      "b": "2026 유연근무 장려금: 월 6시간 이상 단축, 1일 소정 8시간 대비 1시간 이상 단축한 날만. 기록 보정일은 증빙 제외"})
        if cut_h < SHORT_NEED_H:
            todo.append(f"장려금 단축 {hm(cut_h * 60)}로 6시간 미달 — 6시간까지 {hm((SHORT_NEED_H - cut_h) * 60)} 모자람. 단축일(4시간 근무)을 월 2일 이상 넣기")
    else:
        if mr["ot_h"] > 0:
            items.append({"k": "info", "t": f"연장근로 {hm(mr['ot_h'] * 60)}", "d": "1일 8시간 초과분 합계", "b": "근로기준법 제56조 연장근로수당 50% 가산"})
    return {"level": mr["level"], "pay": None if not flex else bool(mr["pay"]), "fails": mr["fail_reasons"] if isinstance(mr["fail_reasons"], str) else "",
            "req": req, "act": mr["actual_h"], "bal": mr["balance_h"], "miss": n_miss, "cut": mr["cut_h"], "su": int(mr["short_under"]),
            "uw": int(mr["under_weeks"]), "late": int(mr["late"]), "ot": mr["ot_h"], "wd": int(mr["workdays"]),
            "dows": [int(mr[f"short_{d}"]) for d in DOWS], "items": items, "todo": todo}


def message(name: str, ym: str, mb: dict, flex: bool) -> str:
    y, m = ym.split("-")
    head = f"{name}님, {int(y)}년 {int(m)}월 출결 확인 결과입니다."
    if not mb["todo"]:
        return head + "\n이번 달은 출퇴근 기록과 근무시간 모두 기준을 채웠습니다. 감사합니다."
    lines = [head]
    if flex:
        lines.append("이번 달 유연근무 장려금: " + ("지급 대상입니다." if mb["pay"] else f"미지급 예정입니다({mb['fails']})."))
    lines.append("확인·조치 부탁드립니다.")
    lines += [f"- {t}" for t in mb["todo"]]
    if flex:
        lines.append("※ 출퇴근 기록이 한 달에 5번 이상 빠지면 그 달 장려금이 나오지 않습니다. 출근·퇴근 버튼을 꼭 눌러 주세요.")
    return "\n".join(lines)


def build(ref: str) -> dict:
    p = pd.read_csv(P / "daily_panel.csv", parse_dates=["date", "week"], keep_default_na=False, na_values=[""])
    for col in ("holiday", "miss_type"):
        p[col] = p[col].fillna("")
    for col in ("workday", "under_day", "short_under", "late", "is_short_actual", "proof"):
        p[col] = p[col].astype(str).str.lower().eq("true")
    w = pd.read_csv(P / "weekly.csv", parse_dates=["week"])
    m = pd.read_csv(P / "monthly.csv")
    pref = pd.read_csv(P / "short_preference.csv").set_index("emp_id")
    ro = pd.read_csv(ROOT / "data" / "roster.csv", dtype=str, keep_default_na=False).set_index("emp_id")
    emps = pd.read_csv(ROOT / "data" / "employees.csv", dtype=str, keep_default_na=False).set_index("emp_id")
    rc = pd.read_csv(P / "roster_check.csv", dtype=str, keep_default_na=False).set_index("emp_id")
    months = period_months()
    workers = []
    for eid, e in emps.iterrows():
        cat = ro.loc[eid, "관리구분"]
        flex = cat == DESIG
        g = p[p["emp_id"] == eid]
        days = {r["date"].strftime("%Y-%m-%d"): day_cell(r, flex) for _, r in g.iterrows() if r["workday"] or not np.isnan(r["worked_min"])}
        weeks = {r["week"].strftime("%Y-%m-%d"): week_row(r, flex) for _, r in w[w["emp_id"] == eid].iterrows()}
        mo = {}
        for _, mr in m[m["emp_id"] == eid].iterrows():
            mb = month_block(mr, g[g["ym"] == mr["ym"]], flex)
            mb["msg"] = message(e["alias"].replace("(가상)", ""), mr["ym"], mb, flex)
            if flex:
                mb["round"] = round_of(mr["ym"], ro.loc[eid, "지원시작월"])
            mo[mr["ym"]] = mb
        wk = {"id": eid, "name": e["alias"], "dept": e["dept"], "cat": cat, "flex": flex, "hire": e["hire_date"], "exit": e["exit_date"],
              "start": ro.loc[eid, "지원시작월"], "excl": rc.loc[eid, "지원제외사유"] if eid in rc.index else "",
              "days": days, "weeks": weeks, "months": mo}
        if flex and eid in pref.index:
            pr = pref.loc[eid]
            wk["pref"] = {"top": DOWS[int(pr["top"])], "share": float(pr["top_share"]), "stable": pr["stable_from"]}
        workers.append(wk)
    hc = headcount_cap()
    hol = dict(pd.read_csv(ROOT / "data" / "holidays_kr.csv", dtype=str).values)
    return {"ref": ref, "months": months, "company": "(가상)디딤테크", "workers": workers, "holidays": hol,
            "rule": {"short_need_h": SHORT_NEED_H, "day_cut_h": RULE["short"]["day_cut_min"] / 60, "miss_fail": MISS_FAIL,
                     "amount": CFG["support"]["monthly_amount_won"], "max": CFG["support"]["max_per_worker_won"], "headcount": {"insured": hc[0], "cap": hc[1]},
                     "text": "2026 선택근무 장려금: 월 6시간 이상 단축(1일 8시간 대비 1시간 이상 단축한 날만) · 출결 누락 월 5회 이상이면 그 달 미지급 · 1인 월 30만원(1년 최대 360만원)"},
            "schedule": {"일반": "09:00~18:00 (휴게 12~13시) · 1일 8시간 · 주 40시간",
                         "선택근무": "장일 09:00~19:00(9시간) · 단축일 09:00~13:30(휴게 12:00~12:30, 4시간) · 통상 09:00~18:00(8시간) — 근무계획표 기준, 정산기간 1개월"},
            "source": "합성 데이터 (src/generate_data.py, seed 20261018) — 실제 인물·기업 아님"}


# ───────────────────────── 근로자별 HTML 리포트 ─────────────────────────
def worker_html(wk: dict, data: dict, css: str, js: str) -> str:
    one = {**{k: v for k, v in data.items() if k != "workers"}, "workers": [wk]}
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{wk['name']} 출결 캘린더</title><style>{css}
body{{max-width:1100px;margin:0 auto;padding:16px}} .month-wrap{{break-inside:avoid;margin:0 0 28px}} @media print{{.no-print{{display:none}}}}</style></head>
<body><div id="app"></div><script>window.FLEX_DATA={json.dumps(one, ensure_ascii=False, separators=(',', ':'))};</script>
<script>{js}</script><script>renderWorkerReport(document.getElementById('app'), window.FLEX_DATA, '{wk['id']}');</script></body></html>"""


# ───────────────────────── 기업 보고서 ─────────────────────────
def company_md(data: dict) -> str:
    ref = data["ref"]
    flexw = [w for w in data["workers"] if w["flex"]]
    regw = [w for w in data["workers"] if not w["flex"]]
    rnd = flexw[0]["months"][ref]["round"]
    rmonths = [ym for ym in data["months"] if flexw[0]["months"].get(ym, {}).get("round") == rnd]
    L = [f"# 선택근무·근태 점검 보고 — {data['company']} ({ref[:4]}년 {int(ref[5:])}월 기준, {rnd}차 신청 회차)", "",
         f"> {data['rule']['text']}", f"> 근무시간표: 일반 {data['schedule']['일반']} / 선택근무 {data['schedule']['선택근무']}",
         f"> {data['source']}. 계산은 판단 보조용이며 지급 여부는 고용센터 심사로 결정됩니다.", ""]
    # 1. 이번 회차 판정
    L += [f"## 1. {rnd}차 신청 회차 판정 ({rmonths[0]} ~ {rmonths[-1]})", "",
          "| 근로자 | 선호 단축 요일 | " + " | ".join(f"{int(x[5:])}월" for x in rmonths) + " | 신청 금액 | 조치 |", "|---|---|" + "---|" * len(rmonths) + "---:|---|"]
    tot = lost = 0
    sym = {"정상": "○ 지급", "조치 필요": "△ 지급·조치", "미지급": "**× 미지급**"}
    for w in flexw:
        cells, won, todo = [], 0, []
        for ym in rmonths:
            mb = w["months"].get(ym)
            if not mb:
                cells.append("–")
                continue
            cells.append(sym[mb["level"]] + (f" ({mb['fails']})" if mb["fails"] else ""))
            won += data["rule"]["amount"] if mb["pay"] else 0
            lost += 0 if mb["pay"] else data["rule"]["amount"]
            todo += [f"{int(ym[5:])}월 {t.split(' (')[0].split(' —')[0]}" for t in mb["todo"]]
        tot += won
        pr = w.get("pref", {})
        L.append(f"| {w['name']} | {pr.get('top', '')}요일 {pr.get('share', 0) * 100:.0f}% | " + " | ".join(cells) + f" | {won:,}원 | {'; '.join(todo[:3]) + (' 외' if len(todo) > 3 else '') or '–'} |")
    L += ["", f"- 이번 회차 신청 예상액 **{tot:,}원**, 미지급 {lost:,}원", ""]
    # 2. 1년 전체
    L += ["## 2. 1년 전체 (근로자별)", "", "| 근로자 | 지급 개월 | 미지급 달 (사유) | 조치 필요 달 | 예상 지급액 |", "|---|---:|---|---:|---:|"]
    year_tot = 0
    for w in flexw:
        ms = w["months"]
        paid = sum(1 for x in ms.values() if x["pay"])
        fails = ", ".join(f"{int(k[5:])}월({v['fails']})" for k, v in ms.items() if not v["pay"]) or "–"
        act = sum(1 for x in ms.values() if x["level"] == "조치 필요")
        year_tot += paid * data["rule"]["amount"]
        L.append(f"| {w['name']} | {paid}/{len(ms)} | {fails} | {act} | {paid * data['rule']['amount']:,}원 |")
    L += ["", f"- 1년 예상 지급액 **{year_tot:,}원** / 최대 {len(flexw) * 12 * data['rule']['amount']:,}원", ""]
    # 3. 집중 관리 근로자
    L += ["## 3. 집중 관리가 필요한 근로자", ""]
    for w in flexw:
        ms = w["months"]
        bad = [k for k, v in ms.items() if v["level"] != "정상"]
        if len(bad) < 4 and all(v["pay"] for v in ms.values()):
            continue
        n_miss = sum(v["miss"] for v in ms.values())
        n_su = sum(v["su"] for v in ms.values())
        under = [k for k, v in ms.items() if v["bal"] * 60 < -TOL_M]
        L.append(f"### {w['name']} ({w['dept']}) — 문제 있는 달 {len(bad)}/12")
        if n_miss:
            L.append(f"- 출결 누락 1년 {n_miss}회" + (f", 월 5회 이상 {sum(1 for v in ms.values() if v['miss'] >= MISS_FAIL)}개월(미지급)" if any(v['miss'] >= MISS_FAIL for v in ms.values()) else ""))
        if under:
            txt = ", ".join(str(int(k[5:])) + "월 " + hm(ms[k]["bal"] * 60) for k in under)
            L.append(f"- 정산기간 소정근로 부족 {len(under)}개월 ({txt}) — 장일(9시간)에 18시대 퇴근")
        if n_su:
            L.append(f"- 단축일 4시간 미달 {n_su}일 — 단축일에 13:30 전 퇴근")
        lows = [k for k, v in ms.items() if v["cut"] < SHORT_NEED_H]
        if lows:
            txt = ", ".join(str(int(k[5:])) + "월 " + hm(ms[k]["cut"] * 60) for k in lows)
            L.append(f"- 단축 6시간 미달 {txt} — 그 달 단축일을 거의 쓰지 않음")
        L.append(f"- 개인 캘린더: `reports/workers/1_선택근무/{w['id']}_{w['name'].replace('(가상)', '')}.html`")
        L.append("")
    # 4. 단축 요일 선호
    L += ["## 4. 단축 요일 선호 (인사·업무 배치 참고)", "", "| 근로자 | 부서 | 월 | 화 | 수 | 목 | 금 | 주 선호 | 8주 이동창 최저 일치율 |", "|---|---|---:|---:|---:|---:|---:|---|---:|"]
    pref = pd.read_csv(P / "short_preference.csv").set_index("emp_id")
    fri = [0] * 5
    for w in flexw:
        c = np.sum([v["dows"] for v in w["months"].values()], axis=0)
        fri = [a + b for a, b in zip(fri, c)]
        pr = pref.loc[w["id"]]
        L.append(f"| {w['name']} | {w['dept']} | " + " | ".join(str(int(x)) for x in c) + f" | **{DOWS[int(pr['top'])]}** {pr['top_share'] * 100:.0f}% | {pr['roll_min'] * 100:.0f}% |")
    tot_s = sum(fri)
    L += ["", f"- 전체 단축일 {tot_s}일 중 금요일 {fri[4] / tot_s * 100:.0f}%, 월요일 {fri[0] / tot_s * 100:.0f}%, 수요일 {fri[2] / tot_s * 100:.0f}%, 목요일 {fri[3] / tot_s * 100:.0f}%, 화요일 {fri[1]}일",
          "- 금요일 오후에 참여자 절반가량이 빠지므로 금요일 오후 회의·고객 응대 배치를 피하거나 요일을 나누도록 조정", ""]
    # 5. 일반 근로자
    L += [f"## 5. 일반 근로자 근태 ({ref})", "", "| 근로자 | 부서 | 판정 | 누락(보정 포함) | 부족 주 | 지각 | 연장 | 해야 할 일 |", "|---|---|---|---:|---:|---:|---:|---|"]
    for w in regw:
        mb = w["months"].get(ref)
        if not mb:
            continue
        if mb["level"] == "정상":
            continue
        L.append(f"| {w['name']} | {w['dept']} | {mb['level']} | {mb['miss']} | {mb['uw']} | {mb['late']} | {hm(mb['ot'] * 60)} | {'; '.join(mb['todo']) or '–'} |")
    ok_n = sum(1 for w in regw if w["months"].get(ref, {}).get("level") == "정상")
    L += ["", f"- 그 외 {ok_n}명 정상", ""]
    yr = [(w["name"], sum(1 for v in w["months"].values() if v["level"] != "정상")) for w in regw]
    yr = sorted([x for x in yr if x[1] >= 4], key=lambda x: -x[1])
    if yr:
        L += ["- 1년 중 주의 이상인 달이 4개월 이상인 근로자: " + ", ".join(f"{n} {k}개월" for n, k in yr), ""]
    # 6. 명부
    rc = pd.read_csv(P / "roster_check.csv", dtype=str, keep_default_na=False)
    flg = rc[(rc["지원제외사유"] != "") | (rc["명부경고"] != "")]
    hc = data["rule"]["headcount"]
    L += ["## 6. 명부 점검", "", f"- 인원 한도: 직전년도 말 피보험자 {hc['insured']}명 × 30% = **{hc['cap']}명**, 지정 {len(flexw)}명"]
    for r in flg.itertuples():
        extra = " · 임금 확인 필요(최저임금법 위반 소지)" if "최저임금" in r.지원제외사유 else ""
        L.append(f"- {r.alias} ({r.관리구분}): {(r.지원제외사유 + ' ' + r.명부경고).strip()} → 지원대상 지정 불가{extra}")
    L += ["", "## 7. 이 보고서가 확인하지 않는 것", ""] + [f"- {x}" for x in CFG["not_checked"]]
    return "\n".join(L) + "\n"


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else CFG["period"]["to"]
    data = build(ref)
    (REP / "workers").mkdir(parents=True, exist_ok=True)
    for f in list(REP.glob("workers/**/*")):
        if f.is_file():
            f.unlink()
    for f in ("feedback.json", "worker_reports.html"):
        (REP / f).unlink(missing_ok=True)
    (REP / "calendar_data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    css = (ROOT / "dashboard" / "calendar.css").read_text(encoding="utf-8")
    js = (ROOT / "dashboard" / "calendar.js").read_text(encoding="utf-8")
    for wk in data["workers"]:
        d = REP / "workers" / ("1_선택근무" if wk["flex"] else "2_일반")
        d.mkdir(exist_ok=True)
        (d / f"{wk['id']}_{wk['name'].replace('(가상)', '')}.html").write_text(worker_html(wk, data, css, js), encoding="utf-8")
    (REP / "company_progress_report.md").write_text(company_md(data), encoding="utf-8")
    out = ROOT / "dashboard" / "data.js"
    out.write_text("window.FLEX_DATA = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"근로자 리포트 {len(data['workers'])}개, 기업 보고서, {out.name} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
