"""유연근무 장려금(선택근무, 2026) 지급 판정·회차별 금액·명부 점검. analysis.py 실행 후.

지원대상(선택근무 참여자) 월 판정 — 고용센터 심사 관점
  지급   : 월 단축 합계 6시간 이상(1일 소정 8시간 대비 1시간 이상 단축한 날만, 기록 온전한 날만) AND 출결 누락 월 5회 미만
  미지급 : 위 둘 중 하나라도 못 채움
  (회사 관리용) 조치 필요 : 지급은 되지만 ① 출결 누락 ② 정산기간 소정근로 부족 ③ 단축일 4시간 미달 중 하나라도 있음
금액: 1인 월 30만원, 1년 최대 360만원, 3개월마다 신청(1~4차)

출력: data/processed/eligibility_monthly.csv, eligibility_amounts.csv, roster_check.csv, images/04~05, results_eligibility.json
실행: python src/eligibility.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analysis import BG, CFG, CRIT, DESIG, GOOD, INK, INK2, WARN, save, setup_font  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "processed"
SUP = CFG["support"]
LEVEL_COLOR = {"정상": "#e8f3e8", "조치 필요": WARN, "미지급": CRIT, "주의": WARN, "확인 필요": CRIT}


def period_months() -> list[str]:
    a, b = pd.Period(CFG["period"]["from"], "M"), pd.Period(CFG["period"]["to"], "M")
    return [str(a + i) for i in range((b - a).n + 1)]


def round_of(ym: str, start: str) -> int:
    """지원시작월부터 3개월마다 1차·2차·3차·4차."""
    k = (pd.Period(ym, "M") - pd.Period(start, "M")).n
    return k // SUP["claim_unit_months"] + 1 if 0 <= k < SUP["months_per_worker"] else 0


# ───────────────────────── 명부 ─────────────────────────
def load_roster() -> pd.DataFrame:
    ro = pd.read_csv(ROOT / "data" / "roster.csv", dtype=str, keep_default_na=False)
    emps = pd.read_csv(ROOT / "data" / "employees.csv", dtype=str, keep_default_na=False)
    ro = ro.merge(emps, on="emp_id", how="left")
    ro["주소정근로시간"] = pd.to_numeric(ro["주소정근로시간"], errors="coerce").fillna(40)
    ro["월보수"] = pd.to_numeric(ro["월보수"], errors="coerce")
    return ro.set_index("emp_id", drop=False)


def monthly_paid_hours(weekly: float) -> int:
    """이음24 core/labor.py monthly_hours 와 같은 식: (주 소정 + 주휴) × 365/7/12. 40시간 → 209시간."""
    weekly = min(weekly, 40)
    return round((weekly + (weekly / 40 * 8 if weekly >= 15 else 0)) * 365 / 7 / 12)


def headcount_cap() -> tuple[int, int]:
    ro = load_roster()
    ye = pd.Timestamp(f"{int(CFG['period']['from'][:4]) - 1}-12-31")
    ex = pd.to_datetime(ro["exit_date"].replace("", None))
    alive = (pd.to_datetime(ro["hire_date"]) <= ye) & (ex.isna() | (ex > ye)) & (ro["고용보험"] == "Y")
    n = int(alive.sum())
    return n, min(int(n * SUP["headcount_ratio"]), SUP["headcount_cap"])


def roster_check() -> pd.DataFrame:
    ro, ex = load_roster(), CFG["exclusions"]
    rows = []
    for eid, r in ro.iterrows():
        rs = []
        for y, rate in ex["min_wage_hourly"].items():
            need = rate * monthly_paid_hours(r["주소정근로시간"])
            if pd.notna(r["월보수"]) and r["월보수"] < need:
                rs.append(f"{y}년 최저임금 미달 의심 (월 {int(r['월보수']):,}원 < {need:,}원)")
        if r["사업주가족"] == "Y":
            rs.append("사업주(대표이사)의 배우자·직계 존비속")
        if r["국적_체류"] not in ("내국인", *ex["allowed_visas"]):
            rs.append(f"외국인({r['국적_체류']}) — F-2·F-5·F-6만 지원 가능")
        if r["고용보험"] != "Y":
            rs.append("고용보험 미가입")
        if r["고용형태"] == "일용":
            rs.append("1개월 미만 고용(일용)")
        warn = []
        if r["관리구분"] == DESIG and rs:
            warn.append("지원제외 대상인데 지원대상으로 지정됨")
        if r["관리구분"] == DESIG and not r["지원시작월"]:
            warn.append("지원시작월 미입력")
        rows.append({"emp_id": eid, "alias": r["alias"], "dept": r["dept"], "관리구분": r["관리구분"], "지원시작월": r["지원시작월"],
                     "지원제외사유": " · ".join(rs), "명부경고": " · ".join(warn), "비고": r["비고"]})
    return pd.DataFrame(rows)


# ───────────────────────── 판정·금액 ─────────────────────────
def run() -> tuple[pd.DataFrame, pd.DataFrame]:
    m = pd.read_csv(P / "monthly.csv")
    ro = load_roster()
    d = m[m["category"] == DESIG].copy()
    d["start"] = d["emp_id"].map(ro["지원시작월"])
    d["round"] = [round_of(ym, s) for ym, s in zip(d["ym"], d["start"])]
    d = d[d["round"] > 0]
    d["won"] = np.where(d["pay"].astype(bool), SUP["monthly_amount_won"], 0)
    d["lost_won"] = SUP["monthly_amount_won"] - d["won"]
    am = d.groupby(["round", "emp_id", "alias"]).agg(months=("ym", "count"), paid=("pay", "sum"), won=("won", "sum"), lost_won=("lost_won", "sum"),
                                                    fails=("fail_reasons", lambda s: " / ".join(sorted({x for x in s.dropna() if x})))).reset_index()
    return d, am


def plots(d: pd.DataFrame) -> list[str]:
    files = []
    months = period_months()
    emps = sorted(d["emp_id"].unique())
    alias = d.drop_duplicates("emp_id").set_index("emp_id")["alias"].str.replace("(가상)", "", regex=False)

    # 04 참여자 × 월 판정표 — 정상(옅은 초록) / 조치 필요(노랑) / 미지급(빨강), 칸 안 = 단축 시간·누락 횟수
    fig, ax = plt.subplots(figsize=(13, 0.62 * len(emps) + 1.9))
    for r, eid in enumerate(emps):
        for c, ym in enumerate(months):
            row = d[(d["emp_id"] == eid) & (d["ym"] == ym)]
            if row.empty:
                continue
            x = row.iloc[0]
            col = LEVEL_COLOR[x["level"]]
            ax.add_patch(plt.Rectangle((c + 0.04, r + 0.06), 0.92, 0.88, color=col, linewidth=0))
            fg = "white" if x["level"] == "미지급" else INK
            ax.text(c + 0.5, r + 0.38, f"단축 {x['cut_h']:.0f}h", ha="center", va="center", fontsize=8, color=fg)
            ax.text(c + 0.5, r + 0.70, f"누락 {int(x['missing'])}" + (f" · {x['balance_h']:+.1f}h" if x["r_hours"] else ""),
                    ha="center", va="center", fontsize=7.5, color=fg)
    ax.set_xlim(0, len(months))
    ax.set_ylim(len(emps), 0)
    ax.set_xticks(np.arange(len(months)) + 0.5, [f"{x[2:].replace('-', '.')}" for x in months], fontsize=9)
    ax.set_yticks(np.arange(len(emps)) + 0.5, [alias[e] for e in emps], fontsize=10)
    for k in range(1, 4):
        ax.axvline(k * 3, color=INK, lw=1.2)
    for k in range(4):
        ax.text(k * 3 + 1.5, -0.12, f"{k + 1}차 신청", ha="center", va="bottom", fontsize=9, color=INK2)
    ax.grid(False)
    ax.tick_params(length=0)
    for sp in ax.spines.values():
        sp.set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=LEVEL_COLOR[k]) for k in ("정상", "조치 필요", "미지급")]
    ax.legend(handles, ["정상", "조치 필요 (지급은 되나 누락·부족·4시간 미달)", "미지급 (단축 6시간 미달 또는 누락 5회 이상)"],
              loc="upper center", bbox_to_anchor=(0.5, -0.04), ncol=3, fontsize=9)
    ax.set_title("④ 선택근무 참여자 월별 판정 — 칸 안: 월 단축 합계 · 출결 누락 횟수 · (정산 부족 시간)", pad=26)
    files.append(save(fig, "04_participant_monthly_status.png"))

    # 05 월 단축 합계 추이 — 6시간 기준선 (근로자별 작은 그래프)
    cols = 4
    rows_ = int(np.ceil(len(emps) / cols))
    fig, axes = plt.subplots(rows_, cols, figsize=(13, 2.7 * rows_), sharey=True)
    axes = axes.ravel()
    x = np.arange(len(months))
    for ax, eid in zip(axes, emps):
        g = d[d["emp_id"] == eid].set_index("ym").reindex(months)
        cut = g["cut_h"].values
        colors = [CRIT if (not np.isnan(v) and v < 6) else "#2a78d6" for v in cut]
        ax.bar(x, np.nan_to_num(cut), color=colors, width=0.7)
        ax.axhline(6, color=INK, lw=1, ls="--")
        miss5 = g["missing"].values >= 5
        for i in np.where(miss5)[0]:
            ax.text(i, np.nan_to_num(cut[i]) + 0.6, "누락", ha="center", fontsize=7.5, color=CRIT, fontweight="bold")
        ax.set_title(alias[eid], fontsize=10.5)
        ax.set_xticks([0, 3, 6, 9], [months[i][2:].replace("-", ".") for i in (0, 3, 6, 9)], fontsize=8)
        ax.grid(axis="x", visible=False)
    for ax in axes[len(emps):]:
        ax.axis("off")
    axes[len(emps)].text(0.02, 0.5, "점선 = 지급 기준 월 6시간\n빨간 막대 = 6시간 미달 → 미지급\n'누락' = 출결 누락 월 5회 이상 → 미지급\n\n단축일 하루(4시간 근무) = 4시간 단축\n→ 한 달에 단축일 2일이면 기준 충족",
                         fontsize=9.5, va="center", color=INK2, transform=axes[len(emps)].transAxes)
    axes[0].set_ylabel("월 단축 합계 (시간)")
    axes[cols].set_ylabel("월 단축 합계 (시간)")
    fig.suptitle("⑤ 근로자별 월 단축 합계 — 2026 지급 요건(월 6시간 이상, 단축일 1시간 이상)", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    files.append(save(fig, "05_monthly_cut_hours.png"))
    return files


def main() -> None:
    setup_font()
    d, am = run()
    rc = roster_check()
    n_ins, cap = headcount_cap()
    d.to_csv(P / "eligibility_monthly.csv", index=False)
    am.to_csv(P / "eligibility_amounts.csv", index=False)
    rc.to_csv(P / "roster_check.csv", index=False)
    files = plots(d)
    per = d.groupby(["emp_id", "alias"]).agg(paid=("pay", "sum"), months=("ym", "count"), won=("won", "sum"), lost=("lost_won", "sum"),
                                             action=("level", lambda s: int((s == "조치 필요").sum()))).reset_index()
    res = {
        "rule": CFG["rules"]["short"]["basis"],
        "months": len(d), "paid_months": int(d["pay"].sum()), "fail_months": int((~d["pay"].astype(bool)).sum()),
        "level": d["level"].value_counts().to_dict(),
        "expected_won": int(d["won"].sum()), "max_won": int(len(d) * SUP["monthly_amount_won"]), "lost_won": int(d["lost_won"].sum()),
        "fails": d.loc[~d["pay"].astype(bool), ["emp_id", "alias", "ym", "fail_reasons", "cut_h", "missing"]].to_dict("records"),
        "rounds": am.groupby("round").agg(won=("won", "sum"), lost=("lost_won", "sum")).reset_index().to_dict("records"),
        "per_worker": per.to_dict("records"),
        "cut_h_median": round(float(d["cut_h"].median()), 1), "cut_h_min_paid": round(float(d.loc[d["pay"].astype(bool), "cut_h"].min()), 1),
        "headcount": {"insured_prev_yearend": n_ins, "cap": cap, "designated": int((rc["관리구분"] == DESIG).sum())},
        "roster_flags": rc[(rc["지원제외사유"] != "") | (rc["명부경고"] != "")].to_dict("records"),
        "images": files,
    }
    (ROOT / "results_eligibility.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("months", "paid_months", "level", "expected_won", "max_won", "lost_won", "fails", "rounds", "headcount")},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
