"""전자출결 시계열로 근로시간 판별 — 9 to 6 사무직 + 선택근무(장일 9시간·단축일 4시간) 참여자.

질문
1. 근로자별 하루·주·정산기간(월) 실근로시간이 근무계획(소정근로시간)을 채웠나? (초과 → 연장수당, 부족 → 문제)
2. 선택근무 참여자는 어느 요일을 단축일로 쓰나? 한 주만 보면 흔들리므로 월 단위로 누적하면 선호가 보이나?
3. 기준에 못 미치는 근로자는 누구이고 왜인가? ① 전자출결 누락 ② 소정근로시간 부족 ③ 단축일 최소 근로시간(4시간) 미달

판정 원칙
- 하루: 근무계획표(data/work_plan.csv; 일반 근로자는 매일 통상 8시간) 대비 실근로(휴게 차감, 이음24 엔진)
- 주: 일반 근로자는 주 소정근로(40시간, 공휴일 주는 근무일 × 8시간) — 근로기준법 제50조
       선택근무는 주 단위가 법적 기준이 아니므로(제52조) 그 주 근무계획 대비 '참고'로만 표시
- 정산기간(월): 선택근무 참여자는 월 합계(근무일 × 8시간)로 판단
- 출결 누락일은 ② 부족 계산에서 빼고 ① 누락으로만 센다(같은 날을 두 번 문제 삼지 않음)

실행: python src/analysis.py   (먼저 python src/generate_data.py)
출력: data/processed/{daily_panel,weekly,monthly}.csv, images/01~03, results.json
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
from matplotlib import font_manager  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attend_engine import FIXED, FLEX, measure_day, ms  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT, IMG = ROOT / "data", ROOT / "data" / "processed", ROOT / "images"
CFG = json.loads((ROOT / "config" / "requirements_2026.json").read_text(encoding="utf-8"))
SCH, RULE = CFG["schedules"], CFG["rules"]
TOL_D, TOL_W, TOL_M = CFG["tolerance"]["daily_min"], CFG["tolerance"]["weekly_min"], CFG["tolerance"]["monthly_min"]
P_FROM = pd.Timestamp(CFG["period"]["from"] + "-01")
P_TO = pd.Period(CFG["period"]["to"], "M").end_time.normalize()
DESIG, REG = "지원대상", "일반"
DOWS = "월화수목금"
MISS_TYPES = ("기록 없음", "퇴근 미기록", "16시간 이상 체류", "사후입력")

# 차트 공통: 단일 축, 얇은 선, 옅은 격자. 요일은 범주형 5색 고정 순서.
INK, INK2, GRID, BG = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"
DOW_COLOR = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]  # 월~금
GOOD, WARN, CRIT = "#0ca30c", "#fab219", "#d03b3b"


def setup_font() -> None:
    for name in ("Noto Sans CJK KR", "NanumGothic", "Malgun Gothic", "AppleGothic"):
        if any(name == f.name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    plt.rcParams.update({
        "axes.unicode_minus": False, "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 13, "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "xtick.color": INK2, "ytick.color": INK2, "legend.frameon": False, "figure.dpi": 110, "savefig.dpi": 150,
        "savefig.bbox": "tight", "figure.facecolor": BG, "axes.facecolor": BG,
    })


def save(fig, name: str) -> str:
    IMG.mkdir(exist_ok=True)
    fig.savefig(IMG / name)
    plt.close(fig)
    return name


def hm(x) -> str:
    """분 → 'H:MM' (부호 포함 가능)."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = "-" if x < 0 else ""
    x = int(round(abs(x)))
    return f"{s}{x // 60}:{x % 60:02d}"


# ───────────────────────── 1. 불러오기·기본 정보·정제 ─────────────────────────
def load():
    logs = pd.read_csv(DATA / "attendance_logs.csv", dtype=str, keep_default_na=False)
    emps = pd.read_csv(DATA / "employees.csv", dtype=str, keep_default_na=False)
    roster = pd.read_csv(DATA / "roster.csv", dtype=str, keep_default_na=False)
    leave = pd.read_csv(DATA / "leave.csv", dtype=str, keep_default_na=False)
    hol = pd.read_csv(DATA / "holidays_kr.csv", dtype=str)
    plan = pd.read_csv(DATA / "work_plan.csv", dtype=str)
    return logs, emps.merge(roster, on="emp_id"), leave, hol, plan


def profile(logs, emps, leave, plan) -> dict:
    return {"rows": len(logs), "columns": list(logs.columns), "employees": len(emps),
            "groups": emps["관리구분"].value_counts().to_dict(), "period": [logs["d"].min(), logs["d"].max()],
            "days_with_records": int(logs["d"].nunique()), "blank_out": int((logs["out"] == "").sum()),
            "edited_records": int((logs["edited"] == "1").sum()), "leave_days": len(leave), "plan_days": len(plan),
            "emp_days_multi": int((logs.groupby(["emp_id", "d"]).size() > 1).sum())}


def clean(logs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """결측(퇴근 미기록)·이상치(16시간 이상 체류)·오입력(10분 미만)을 표시하고 계산에서 뺀다."""
    df = logs.copy()
    ok = (df["in"] != "") & (df["out"] != "")
    df["stay_min"] = np.nan
    df.loc[ok, "stay_min"] = [(ms(o) - ms(i)) / 6e4 for i, o in zip(df.loc[ok, "in"], df.loc[ok, "out"])]
    df["flag"] = "정상"
    df.loc[df["out"] == "", "flag"] = "퇴근 미기록(결측)"
    df.loc[df["stay_min"] < 10, "flag"] = "오입력(10분 미만)"
    df.loc[df["stay_min"] >= 16 * 60, "flag"] = "이상치(16시간 이상 체류)"
    return df[df["flag"] == "정상"].copy(), df, {k: int(v) for k, v in df["flag"].value_counts().items()}


# ───────────────────────── 2. 직원×일 패널 ─────────────────────────
def build_panel(usable, flagged, emps, leave, hol, plan) -> pd.DataFrame:
    holidays = dict(zip(hol["date"], hol["name"]))
    lv = set(zip(leave["emp_id"], leave["date"]))
    pl = {(r.emp_id, r.date): (r.plan, int(r.plan_min)) for r in plan.itertuples()}
    recs = {k: g for k, g in usable.groupby(["emp_id", "d"])}
    bad = flagged[flagged["flag"].isin(["퇴근 미기록(결측)", "이상치(16시간 이상 체류)"])].groupby(["emp_id", "d"])["flag"].first().to_dict()
    raw_in = flagged[flagged["in"] != ""].groupby(["emp_id", "d"])["in"].min().to_dict()
    edited = set(flagged.loc[flagged["edited"] == "1", ["emp_id", "d"]].itertuples(index=False, name=None))
    st = 9 * 60
    rows = []
    for _, e in emps.iterrows():
        eid, cat = e["emp_id"], e["관리구분"]
        hire = pd.Timestamp(e["hire_date"])
        ex = pd.Timestamp(e["exit_date"]) if e["exit_date"] else None
        for day in pd.date_range(P_FROM, P_TO):
            if day < hire or (ex is not None and day > ex):
                continue
            d = day.strftime("%Y-%m-%d")
            workday = day.weekday() < 5 and d not in holidays
            g = recs.get((eid, d))
            if not workday and g is None:
                continue
            if workday:
                p_kind, p_min = pl.get((eid, d), ("통상", 480)) if cat == DESIG else ("통상", 480)
            else:
                p_kind, p_min = ("휴일", 0)
            rr = [] if g is None else [{"d": d, "in": r["in"], "out": r["out"]} for _, r in g.iterrows()]
            m = measure_day(rr, FLEX if cat == DESIG else FIXED, workday) if rr else None
            base = pd.Timestamp(f"{d}T00:00:00+09:00").timestamp() * 1000
            start = (min(ms(r["in"]) for r in rr) - base) / 6e4 if rr else np.nan
            end = (max(ms(r["out"]) for r in rr) - base) / 6e4 if rr else np.nan
            if np.isnan(start) and (eid, d) in raw_in:   # 퇴근 미기록이어도 출근 시각은 보여 준다
                start = (ms(raw_in[(eid, d)]) - base) / 6e4
            on_leave = (eid, d) in lv
            issue = bad.get((eid, d), "")
            miss = ""
            if workday and not m and not on_leave:
                miss = {"퇴근 미기록(결측)": "퇴근 미기록", "이상치(16시간 이상 체류)": "16시간 이상 체류"}.get(issue, "기록 없음")
            elif m and (eid, d) in edited:
                miss = "사후입력"
            status = "근무" if m else ("연차" if on_leave else ("누락" if workday else "휴일"))
            worked = float(m["worked"]) if m else np.nan
            credit = worked if m else (p_min if on_leave else 0.0)
            rows.append({"emp_id": eid, "alias": e["alias"], "category": cat, "date": day, "workday": workday,
                         "holiday": holidays.get(d, ""), "plan": p_kind, "plan_min": p_min, "status": status,
                         "miss_type": miss, "start_min": start, "end_min": end, "worked_min": worked, "credit_min": credit})
    p = pd.DataFrame(rows)
    p["ym"] = p["date"].dt.strftime("%Y-%m")
    p["week"] = p["date"] - pd.to_timedelta(p["date"].dt.weekday, unit="D")
    p["dow"] = p["date"].dt.weekday
    worked = p["worked_min"]
    is_flex = p["category"] == DESIG
    rec_ok = p["workday"] & worked.notna()
    p["day_gap_min"] = np.where(rec_ok, worked - p["plan_min"], np.nan)          # 계획 대비 (+초과 / −부족)
    p["under_day"] = rec_ok & (p["day_gap_min"] < -TOL_D)
    p["short_under"] = rec_ok & (p["plan"] == "단축일") & (worked < RULE["short_min"]["min_min"] - TOL_D)
    p["late"] = rec_ok & (p["start_min"] > st + RULE["regular"]["late_grace_min"])
    p["ot_min"] = np.where(rec_ok, np.maximum(0, p["day_gap_min"].fillna(0)), 0.0)
    p.loc[~p["workday"] & worked.notna(), "ot_min"] = worked                        # 휴일 근무는 전부 연장·휴일근로
    # 장려금 단축(2026): 1일 소정 8시간 대비 1시간 이상 적게 일한 날의 단축 시간. 기록이 온전한 날만 증빙
    s = RULE["short"]
    cut = s["base_day_min"] - worked
    p["is_short_actual"] = is_flex & rec_ok & (cut >= s["day_cut_min"])
    p["proof"] = p["is_short_actual"] & (p["miss_type"] == "")
    p["cut_min"] = np.where(p["proof"], cut, 0.0)
    return p


# ───────────────────────── 3. 주·월(정산기간) 집계 ─────────────────────────
def weekly(p: pd.DataFrame) -> pd.DataFrame:
    """주(월~금 전체) 단위. 누락일은 소정에서 빼고(① 누락으로 따로 셈) 나머지로 부족을 본다."""
    wd = p[p["workday"]].copy()
    wd["miss_plan"] = np.where(wd["status"] == "누락", wd["plan_min"], 0)
    w = wd.groupby(["emp_id", "alias", "category", "week"]).agg(
        workdays=("workday", "sum"), plan_min=("plan_min", "sum"), credit=("credit_min", "sum"), miss_plan=("miss_plan", "sum"),
        missing=("miss_type", lambda x: int((x != "").sum())), worked=("worked_min", lambda x: x.sum(skipna=True)),
        short_days=("is_short_actual", "sum")).reset_index()
    w["required_h"] = w["plan_min"] / 60
    w["actual_h"] = w["credit"] / 60
    w["gap_h"] = (w["credit"] - (w["plan_min"] - w["miss_plan"])) / 60          # 누락일 제외
    w["under"] = w["gap_h"] < -TOL_W / 60
    w["over52"] = w["worked"] / 60 > RULE["weekly_cap"]["weekly_max_h"]
    w["ym"] = (w["week"] + pd.Timedelta(days=3)).dt.strftime("%Y-%m")         # 목요일이 속한 달 (경계 주 중복 방지)
    return w


def monthly(p: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    rows = []
    rs = RULE["short"]
    for (eid, ym), g in p.groupby(["emp_id", "ym"]):
        wd = g[g["workday"]]
        if wd.empty:
            continue
        flex = g["category"].iloc[0] == DESIG
        wk = w[(w["emp_id"] == eid) & (w["ym"] == ym)]
        miss = wd[wd["miss_type"] != ""]
        miss_plan = wd.loc[wd["status"] == "누락", "plan_min"].sum()
        req = len(wd) * 8 * 60
        act = g["credit_min"].sum()
        bal = act - (req - miss_plan)
        sd = g[g["is_short_actual"]]
        cut = float(g["cut_min"].sum())
        open_miss = int((miss["miss_type"] != "사후입력").sum())     # 사후입력(사업주 보정) 제외
        r_missing = len(miss) > 0 if flex else open_miss > 0
        r_hours = bool(bal < -TOL_M) if flex else bool(wk["under"].sum() > 0)
        r_short = int(g["short_under"].sum()) > 0
        row = {
            "emp_id": eid, "alias": g["alias"].iloc[0], "category": g["category"].iloc[0], "ym": ym,
            "workdays": len(wd), "leave_days": int((wd["status"] == "연차").sum()), "days_worked": int(g["worked_min"].notna().sum()),
            "required_h": round(req / 60, 2), "plan_h": round(wd["plan_min"].sum() / 60, 2), "actual_h": round(act / 60, 2),
            "balance_h": round(bal / 60, 2), "under_weeks": int(wk["under"].sum()), "under_days": int(g["under_day"].sum()),
            "missing": len(miss), "missing_open": open_miss, **{f"miss_{k}": int((miss["miss_type"] == k).sum()) for k in MISS_TYPES},
            "short_under": int(g["short_under"].sum()), "late": int(g["late"].sum()),
            "ot_h": round(max(0.0, bal / 60) if flex else float(g["ot_min"].sum()) / 60, 2), "over52_weeks": int(wk["over52"].sum()),
            "short_days": len(sd), "proof_days": int(g["proof"].sum()), "cut_h": round(cut / 60, 2),
            **{f"short_{DOWS[i]}": int((sd["dow"] == i).sum()) for i in range(5)},
            "r_missing": r_missing, "r_hours": r_hours, "r_short": r_short,
        }
        if flex:
            fails = []
            if cut < rs["month_cut_min"]:
                fails.append("단축 6시간 미달")
            if len(miss) >= RULE["record"]["fail_at"]:
                fails.append("출결 누락 월 5회 이상")
            row["pay"] = not fails
            row["fail_reasons"] = " · ".join(fails)
            row["level"] = "미지급" if fails else ("조치 필요" if (r_missing or r_hours or r_short) else "정상")
        else:
            row["pay"], row["fail_reasons"] = None, ""
            serious = open_miss >= RULE["record"]["warn_at"] or row["under_weeks"] >= 2 or row["over52_weeks"] > 0
            row["level"] = "확인 필요" if serious else ("주의" if (r_missing or r_hours or row["late"] >= 3) else "정상")
        rows.append(row)
    return pd.DataFrame(rows)


# ───────────────────────── 4. 단축 요일 선호 (시계열) ─────────────────────────
def short_pref(p: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """주별 선택(어느 요일을 단축했나) → 월별 누적 비중(expanding) → 8주 이동창 일관성(rolling)."""
    f = p[p["is_short_actual"]]
    weeks = sorted(p.loc[p["category"] == DESIG, "week"].unique())
    wsel = f.groupby(["emp_id", "week"])["dow"].agg(lambda s: int(s.iloc[0])).unstack().reindex(columns=weeks)  # 주별 선택 요일
    mon = f.groupby(["emp_id", "ym", "dow"]).size().unstack(fill_value=0).reindex(columns=range(5), fill_value=0)
    cum = mon.groupby(level=0).cumsum()
    share = cum.div(cum.sum(axis=1), axis=0)                                 # 월별 누적 비중
    rows = []
    for eid, s in wsel.iterrows():
        top = int(s.mode().iloc[0])
        hit = (s == top).astype(float).where(s.notna())
        roll = hit.rolling(8, min_periods=4).mean()
        rows.append({"emp_id": eid, "top": top, "top_share": round(float(hit.mean()), 3),
                     "roll_min": round(float(roll.min()), 3), "roll_last": round(float(roll.dropna().iloc[-1]), 3),
                     "first_month_share": round(float(share.loc[eid].iloc[0, top]), 3),
                     "stable_from": next((ym for ym, v in share.loc[eid][top].items() if abs(v - share.loc[eid][top].iloc[-1]) <= 0.05), None)})
    return wsel, share, pd.DataFrame(rows)


# ───────────────────────── 5. 그래프 ─────────────────────────
def plots(p, m, wsel, share, pref) -> list[str]:
    files = []
    alias = p.drop_duplicates("emp_id").set_index("emp_id")["alias"].str.replace("(가상)", "", regex=False)
    emps = sorted(p.loc[p["category"] == DESIG, "emp_id"].unique())
    weeks = list(wsel.columns)

    # 01 주별 단축 요일 선택 현황 (한 칸 = 1주)
    fig, ax = plt.subplots(figsize=(12.5, 0.52 * len(emps) + 1.7))
    su = p[p["short_under"]].groupby(["emp_id", "week"]).size()
    for r, eid in enumerate(emps):
        for i, wk in enumerate(weeks):
            v = wsel.loc[eid, wk] if eid in wsel.index else np.nan
            if np.isnan(v):
                ax.text(i + 0.5, r + 0.55, "·", ha="center", va="center", fontsize=10, color=INK2)
                continue
            ax.add_patch(plt.Rectangle((i + 0.06, r + 0.12), 0.88, 0.76, color=DOW_COLOR[int(v)], linewidth=0))
            if (eid, wk) in su.index:
                ax.add_patch(plt.Rectangle((i + 0.06, r + 0.12), 0.88, 0.76, fill=False, ec=INK, lw=1.4))
    ax.set_xlim(0, len(weeks))
    ax.set_ylim(len(emps), 0)
    fri = [wk + pd.Timedelta(days=4) for wk in weeks]
    ticks = [0] + [i for i in range(1, len(weeks)) if fri[i].month != fri[i - 1].month]
    ax.set_xticks([t + 0.5 for t in ticks], [fri[t].strftime("%y.%m") for t in ticks], fontsize=9)
    ax.set_yticks(np.arange(len(emps)) + 0.5, [f"{alias[e]}" for e in emps], fontsize=9.5)
    ax.grid(False)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=DOW_COLOR[i]) for i in range(5)] + [plt.Rectangle((0, 0), 1, 1, fill=False, ec=INK, lw=1.4)]
    ax.legend(handles, [f"{DOWS[i]}요일" for i in range(5)] + ["단축일 4시간 미만 퇴근"], loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=6, fontsize=9)
    ax.set_title("① 주별 단축 요일 선택 — 한 칸 = 1주, 색 = 단축한 요일, 점 = 그 주 단축 없음")
    files.append(save(fig, "01_short_weekday_weekly.png"))

    # 02 근로자별 단축 요일 누적 비중 (월 단위로 쌓을수록 선호가 굳어지는지)
    months = sorted(p["ym"].unique())
    cols = 4
    rows_ = int(np.ceil(len(emps) / cols))
    fig, axes = plt.subplots(rows_, cols, figsize=(13, 3.0 * rows_), sharey=True)
    axes = axes.ravel()
    x = np.arange(len(months))
    for ax, eid in zip(axes, emps):
        s = share.loc[eid].reindex(months).ffill().fillna(0)
        bottom = np.zeros(len(months))
        for d in range(5):
            v = s[d].values * 100
            if v.max() > 0:
                ax.bar(x, v, bottom=bottom, width=0.8, color=DOW_COLOR[d], linewidth=0)
            bottom += v
        pr = pref.set_index("emp_id").loc[eid]
        ax.set_title(f"{alias[eid]} — {DOWS[int(pr['top'])]}요일 {pr['top_share'] * 100:.0f}%", fontsize=10.5)
        ax.set_xticks([0, 3, 6, 9, len(months) - 1], [months[i][2:].replace("-", ".") for i in (0, 3, 6, 9, len(months) - 1)], fontsize=8)
        ax.set_ylim(0, 100)
        ax.grid(axis="x", visible=False)
    for ax in axes[len(emps):]:
        ax.axis("off")
    handles = [plt.Rectangle((0, 0), 1, 1, color=DOW_COLOR[i]) for i in range(5)]
    axes[len(emps)].legend(handles, [f"{DOWS[i]}요일" for i in range(5)], loc="center", fontsize=10, title="단축 요일")
    axes[0].set_ylabel("누적 비중 (%)")
    axes[cols].set_ylabel("누적 비중 (%)")
    fig.suptitle("② 근로자별 단축 요일 누적 비중 — 첫 달부터 그 달까지 쌓은 단축일 중 요일별 비율", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    files.append(save(fig, "02_short_weekday_cumulative.png"))

    # 03 요일별 단축 인원 (월별) — 같은 요일 오후에 몇 명이 빠지나
    f = p[p["is_short_actual"]]
    t = f.groupby(["ym", "dow"]).size().unstack(fill_value=0).reindex(index=months, columns=range(5), fill_value=0)
    nweek = p[p["workday"]].groupby(["ym", "dow"])["date"].nunique().unstack(fill_value=0).reindex(index=months, columns=range(5), fill_value=0)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    xs = np.arange(len(t))
    bottom = np.zeros(len(t))
    for i in [d for d in range(5) if t[d].sum() > 0]:
        ax.bar(xs, t[i].values, bottom=bottom, width=0.62, color=DOW_COLOR[i], label=f"{DOWS[i]}요일", edgecolor=BG, linewidth=2)
        bottom += t[i].values
    fri = (t[4] / nweek[4].replace(0, np.nan))
    for i, (b, fr) in enumerate(zip(bottom, fri)):
        ax.text(i, b + 0.4, f"금 {fr:.1f}명", ha="center", fontsize=8.5, color=INK)
    ax.set_xticks(xs, [m_[2:].replace("-", ".") for m_ in t.index], fontsize=9)
    ax.set_ylabel("단축일 수 (참여자 7명 합계)")
    ax.set_title("③ 월별 요일별 단축일 — 막대 위 = 금요일 오후 평균 부재 인원")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=5, fontsize=9)
    ax.grid(axis="x", visible=False)
    files.append(save(fig, "03_short_weekday_by_month.png"))
    return files


# ───────────────────────── 6. (보너스) 분해·간단 예측·변화율 ─────────────────────────
def decompose_forecast(p: pd.DataFrame) -> tuple[list[str], dict]:
    """하루 단축 인원(그날 단축한 참여자 수)을 추세·요일 계절성·잡음으로 나누고, 6월을 베이스라인으로 예측한다.

    - 추세: 5근무일(1주) 중심 이동평균 → 주 안의 요일 차이를 지운 큰 흐름
    - 계절성: (값 − 추세)의 요일별 평균 → 매주 반복되는 요일 패턴
    - 잡음: 값 − 추세 − 계절성 → 남은 불규칙 변화(공휴일·연차 등)
    - 예측: 2025-07~2026-05로 학습, 2026-06 근무일 예측. ① 전체 평균 ② 지난주 같은 요일 ③ 최근 8주 요일 평균
    """
    flex = p[(p["category"] == DESIG) & p["workday"]]
    y = flex.groupby("date")["is_short_actual"].sum().astype(float)
    y.index = pd.to_datetime(y.index)
    dow = y.index.weekday
    trend = y.rolling(5, center=True, min_periods=3).mean()
    seas_prof = (y - trend).groupby(dow).mean()
    seas_prof -= seas_prof.mean()
    seas = pd.Series(seas_prof.reindex(dow).values, index=y.index)
    resid = y - trend - seas
    files = []
    fig, axes = plt.subplots(4, 1, figsize=(12, 9), sharex=True)
    parts = [(y, "① 원래 값: 그날 단축한 참여자 수(명)", "#2a78d6"), (trend, "② 추세: 1주 이동평균", INK),
             (seas, "③ 계절성: 요일마다 반복되는 몫", "#e87ba4"), (resid, "④ 잡음: 나머지 불규칙 변화", INK2)]
    for ax, (s_, t_, c_) in zip(axes, parts):
        if s_ is resid:
            ax.vlines(s_.index, 0, s_.values, color=c_, lw=1)
        else:
            ax.plot(s_.index, s_.values, color=c_, lw=1.2 if s_ is not trend else 2)
        ax.set_title(t_, fontsize=11)
        ax.axhline(0, color=GRID, lw=1)
    axes[-1].xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%y.%m"))
    fig.suptitle("⑥ (보너스) 시계열 분해 — 하루 단축 인원 = 추세 + 요일 계절성 + 잡음", x=0.01, ha="left", fontsize=13, fontweight="bold")
    fig.tight_layout()
    files.append(save(fig, "06_decomposition.png"))

    cut = pd.Timestamp("2026-06-01")
    tr, te = y[y.index < cut], y[y.index >= cut]
    f_mean = pd.Series(tr.mean(), index=te.index)
    lastweek = y.shift(5)  # 근무일 5개 전 ≈ 지난주 같은 요일 (공휴일 주는 근사)
    f_naive = lastweek[te.index]
    recent = tr[tr.index >= cut - pd.Timedelta(weeks=8)]
    f_dow = pd.Series(recent.groupby(recent.index.weekday).mean().reindex(te.index.weekday).values, index=te.index)
    mae = {k: round(float((te - f).abs().mean()), 2) for k, f in (("전체 평균", f_mean), ("지난주 같은 요일", f_naive), ("최근 8주 요일 평균", f_dow))}
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(te.index, te.values, color=INK, lw=2.2, marker="o", ms=6, label="실제", zorder=3)
    for (k, f), c in zip((("전체 평균", f_mean), ("지난주 같은 요일", f_naive), ("최근 8주 요일 평균", f_dow)), ("#8a8984", "#eb6834", "#2a78d6")):
        ax.plot(f.index, f.values, color=c, lw=1.6, ls="--", marker="s", ms=4, label=f"{k} (평균 오차 {mae[k]}명)")
    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter("%m/%d"))
    ax.set_xticks(te.index)
    ax.set_xticklabels([f"{d.month}/{d.day}\n{DOWS[d.weekday()]}" for d in te.index], fontsize=8)
    ax.set_ylabel("단축 인원 (명)")
    ax.legend(loc="upper left", fontsize=9)
    ax.set_title("⑦ (보너스) 간단 예측 — 2026년 6월 하루 단축 인원, 베이스라인 3가지 비교")
    files.append(save(fig, "07_baseline_forecast.png"))

    # 변화율: 참여자 월별 출결 누락 횟수·단축일 수의 전월 대비 변화율
    mm = flex.groupby("ym").agg(missing=("miss_type", lambda x: int((x != "").sum())), short=("is_short_actual", "sum"))
    mm["missing_mom_pct"] = (mm["missing"].pct_change() * 100).replace([np.inf, -np.inf], np.nan).round(0)  # 0 → n 은 계산 불가(빈칸)
    mm["short_mom_pct"] = (mm["short"].pct_change() * 100).round(0)
    info = {"seasonal_by_dow": {DOWS[int(k)]: round(float(v), 2) for k, v in seas_prof.items()},
            "trend_range": [round(float(trend.min()), 2), round(float(trend.max()), 2)],
            "resid_std": round(float(resid.std()), 2), "y_std": round(float(y.std()), 2),
            "resid_top": [{"date": d.strftime("%Y-%m-%d"), "dow": DOWS[d.weekday()], "resid": round(float(v), 2)} for d, v in resid.abs().sort_values(ascending=False).head(5).items()],
            "forecast_mae": mae, "june_days": len(te),
            "monthly_change": mm.reset_index().replace({np.nan: None}).to_dict("records")}
    return files, info


# ───────────────────────── 7. 실행 ─────────────────────────
def main() -> None:
    setup_font()
    OUT.mkdir(parents=True, exist_ok=True)
    for f in IMG.glob("*.png"):
        f.unlink()
    logs, emps, leave, hol, plan = load()
    prof = profile(logs, emps, leave, plan)
    usable, flagged, flags = clean(logs)
    p = build_panel(usable, flagged, emps, leave, hol, plan)
    w = weekly(p)
    m = monthly(p, w)
    wsel, share, pref = short_pref(p)
    p.to_csv(OUT / "daily_panel.csv", index=False)
    w.to_csv(OUT / "weekly.csv", index=False)
    m.to_csv(OUT / "monthly.csv", index=False)
    pref.to_csv(OUT / "short_preference.csv", index=False)
    share.reset_index().rename(columns={i: DOWS[i] for i in range(5)}).to_csv(OUT / "short_share_cumulative.csv", index=False)
    files = plots(p, m, wsel, share, pref)
    f2, ts_info = decompose_forecast(p)
    files += f2

    f = p[p["is_short_actual"]]
    fri_days = p[p["workday"] & (p["dow"] == 4)]["date"].nunique()
    by_cat = {}
    for cat in (DESIG, REG):
        mm = m[m["category"] == cat]
        by_cat[cat] = {"emp_months": len(mm), "workers": int(mm["emp_id"].nunique()),
                       "level": mm["level"].value_counts().to_dict(),
                       "r_missing": int(mm["r_missing"].sum()), "r_hours": int(mm["r_hours"].sum()), "r_short": int(mm["r_short"].sum()),
                       "any": int((mm["r_missing"] | mm["r_hours"] | mm["r_short"]).sum()),
                       "missing_total": int(mm["missing"].sum()), "missing_by_type": {k: int(mm[f"miss_{k}"].sum()) for k in MISS_TYPES},
                       "workers_with_issue": mm.loc[mm["level"] != "정상", "emp_id"].value_counts().to_dict(),
                       "late": int(mm["late"].sum()), "ot_h": round(float(mm["ot_h"].sum()), 1), "over52_weeks": int(mm["over52_weeks"].sum())}
    res = {
        "profile": prof, "cleaning": flags, "panel_rows": len(p), "workday_points": int(p[p["workday"]]["date"].nunique()),
        "weeks": int(w["week"].nunique()), "by_category": by_cat,
        "short_pref": pref.assign(top=pref["top"].map(lambda i: DOWS[i])).to_dict(orient="records"),
        "short_weekday_total": {DOWS[k]: int(v) for k, v in f["dow"].value_counts().sort_index().items()},
        "short_weekday_share": {DOWS[k]: round(float(v), 3) for k, v in f["dow"].value_counts(normalize=True).sort_index().items()},
        "friday_absent_avg": round(len(f[f["dow"] == 4]) / fri_days, 2),
        "weeks_without_short_share": round(float(wsel.isna().mean().mean()), 3),
        "images": files,
        "timeseries": ts_info,
    }
    (ROOT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("profile", "cleaning", "by_category", "short_pref", "short_weekday_share", "friday_absent_avg")},
                     ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
