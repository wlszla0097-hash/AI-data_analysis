"""합성 출퇴근 기록 생성기 — 9 to 6 사무직 회사 + 선택근무제(2026 유연근무 장려금) 참여자.

회사 가정 ((가상)디딤테크, 24명, 2025-07-01 ~ 2026-06-30 = 장려금 지원 기간 1년)
- 일반 근로자 17명: 09:00~18:00, 휴게 12:00~13:00 → 1일 8시간, 주 40시간
- 선택근무 참여자 7명 (장려금 지원대상): 정산기간 1개월. 근무계획표(data/work_plan.csv)를 미리 낸다
    장일   09:00~19:00 (휴게 12:00~13:00)   = 9시간
    단축일 09:00~13:30 (휴게 12:00~12:30)   = 4시간    → 장일 4일 + 단축일 1일 = 주 40시간
    통상   09:00~18:00                       = 8시간    (월 합계를 근무일 × 8시간에 맞추려고 일부 날을 통상으로 둔다)
  단축 요일은 근로자가 고른다(선호 요일이 사람마다 다름). 공휴일과 겹치면 그 주 다른 날로 옮긴다.
- 연차는 별도 대장(data/leave.csv)에 승인 기록이 남는다. 출퇴근 기록도 연차도 없는 근무일 = 출결 누락.

현실적인 비율: 대부분은 계획대로 근무하고, 문제는 몇 명에게 몰린다.
- E05 정지우: 장일(9시간)인데 18시대에 퇴근하는 습관 → 정산기간 소정근로 부족
- E06 강예린: 평소엔 괜찮지만 바쁜 달(2025-11, 2026-03)에 퇴근 버튼 누락이 몰림 → 월 5회 이상 = 그 달 미지급
- E07 조현우: 단축일에 13:30 전에 퇴근(4시간 미달), 출결 누락 잦음, 2026-01 영업 성수기에 단축일을 거의 안 씀 → 월 6시간 단축 미달
- 일반 근로자: E13 지각 습관, E18 퇴근 미기록 습관, E22 조퇴 잦음
초과근무(연장)는 수당 처리 대상으로 기록만 한다.

실제 고객 데이터를 쓰지 않는다는 원칙 때문에 만든 가상 데이터이며, 시드 고정으로 누구나 같은 파일을 재생성할 수 있다.
실행: python src/generate_data.py  → data/attendance_logs.csv, work_plan.csv, employees.csv, leave.csv, holidays_kr.csv
"""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

SEED = 20261018
START, END = date(2025, 7, 1), date(2026, 6, 30)
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

HOLIDAYS = {
    "2025-08-15": "광복절", "2025-10-03": "개천절", "2025-10-06": "추석", "2025-10-07": "추석 연휴",
    "2025-10-08": "추석 대체공휴일", "2025-10-09": "한글날", "2025-12-25": "성탄절", "2026-01-01": "신정",
    "2026-02-16": "설날 연휴", "2026-02-17": "설날", "2026-02-18": "설날 연휴", "2026-03-02": "삼일절 대체공휴일",
    "2026-05-01": "근로자의 날", "2026-05-05": "어린이날", "2026-05-25": "부처님오신날 대체공휴일", "2026-06-03": "지방선거일",
}
BIG = [d for d, n in HOLIDAYS.items() if "설날" in n or "추석" in n]
PLAN_MIN = {"장일": 540, "단축일": 240, "통상": 480}

# 선택근무 참여자: (id, 가명, 부서, 단축 요일 선호 {요일번호: 확률}, 습관)
#   long_short: 장일에 18시대 퇴근 확률 / short_early: 단축일 13:30 전 퇴근 확률 / forget: 출퇴근 누락 확률
#   bad: {월: 그 달 누락 확률} / no_short: {월: 단축일을 그 달 1번만 쓰는 달}
FLEX = [
    ("E01", "(가상)김하늘", "개발", {4: 1.0}, dict(long_short=0.005, short_early=0.0, forget=0.004)),
    ("E02", "(가상)이도윤", "개발", {4: 0.85, 0: 0.15}, dict(long_short=0.01, short_early=0.005, forget=0.006)),
    ("E03", "(가상)박서연", "기획", {0: 0.75, 4: 0.25}, dict(long_short=0.01, short_early=0.005, forget=0.006)),
    ("E04", "(가상)최민준", "기획", {2: 0.85, 4: 0.15}, dict(long_short=0.008, short_early=0.0, forget=0.005)),
    ("E05", "(가상)정지우", "디자인", {3: 0.55, 4: 0.45}, dict(long_short=0.28, short_early=0.0, forget=0.006)),
    ("E06", "(가상)강예린", "경영지원", {4: 0.9, 0: 0.1}, dict(long_short=0.01, short_early=0.0, forget=0.008,
                                                        bad={"2025-11": 0.40, "2026-03": 0.32})),
    ("E07", "(가상)조현우", "영업", {4: 0.7, 0: 0.3}, dict(long_short=0.03, short_early=0.35, forget=0.06,
                                                     no_short={"2026-01"})),
]
REG_NAMES = ["윤수아", "장건우", "임채원", "한지호", "오나은", "서준혁", "신유나", "권태양", "황보람", "안소율",
             "송민재", "류하은", "문지안", "배도현", "홍가을", "노시우", "유채린"]
REG_DEPTS = ["고객지원", "고객지원", "영업", "영업", "물류", "물류", "물류", "개발", "개발", "기획",
             "경영지원", "경영지원", "고객지원", "물류", "디자인", "영업", "고객지원"]
# 일반 근로자 습관: (지각 확률, 조퇴 확률, 출퇴근 누락 확률)
REG_BASE = (0.008, 0.004, 0.004)
REG_HABIT = {"E13": (0.09, 0.004, 0.006), "E18": (0.01, 0.004, 0.05), "E22": (0.01, 0.045, 0.008)}

rng = np.random.default_rng(SEED)


def iso(day: date, minutes: float) -> str:
    extra, m = divmod(int(round(minutes)), 1440)
    t = datetime(day.year, day.month, day.day) + timedelta(days=extra, minutes=m)
    return t.strftime("%Y-%m-%dT%H:%M:00+09:00")


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def leave_prob(d: date) -> float:
    p = 0.025
    if d.month == 8 and d.day <= 16:
        p = 0.13
    if d.month == 12 and d.day >= 24:
        p = 0.12
    if any(0 < abs((d - date.fromisoformat(h)).days) <= 2 for h in BIG):
        p = max(p, 0.18)
    return p


def make_plan(pref: dict[int, float], habit: dict) -> dict[date, str]:
    """근로자 1명의 1년 근무계획표. 주마다 선호 요일 하나를 단축일로 두고, 달마다 합계를 근무일 × 8시간에 맞춘다."""
    plan: dict[date, str] = {}
    d = START - timedelta(days=START.weekday())
    dows, probs = list(pref), np.array(list(pref.values())) / sum(pref.values())
    while d <= END:
        work = [x for x in (d + timedelta(days=k) for k in range(5)) if START <= x <= END and is_workday(x)]
        want = int(rng.choice(dows, p=probs))
        cand = [x for x in work if x.weekday() == want] or ([work[-1]] if work else [])
        for x in work:
            plan[x] = "단축일" if cand and x == cand[0] else "장일"
        d += timedelta(days=7)
    # 달마다 맞추기: 합계 − 소정 = 장일 수 × 1h − 단축일 수 × 4h
    by_m: dict[str, list[date]] = defaultdict(list)
    for x in sorted(plan):
        by_m[x.strftime("%Y-%m")].append(x)
    for ym, days in by_m.items():
        shorts = [x for x in days if plan[x] == "단축일"]
        if ym in habit.get("no_short", ()):
            for x in shorts[1:]:          # 성수기: 단축일을 한 번만 쓰고 나머지는 통상
                plan[x] = "통상"
        surplus = sum(plan[x] == "장일" for x in days) - 4 * sum(plan[x] == "단축일" for x in days)
        for x in reversed([x for x in days if plan[x] == "단축일"]):
            if surplus >= 0:
                break
            plan[x], surplus = "통상", surplus + 4
        weeks_wo_short = {x - timedelta(days=x.weekday()) for x in days} - {x - timedelta(days=x.weekday()) for x in days if plan[x] == "단축일"}
        longs = sorted([x for x in days if plan[x] == "장일"], key=lambda x: (x - timedelta(days=x.weekday()) not in weeks_wo_short, -x.toordinal()))
        for x in longs[:max(0, surplus)]:
            plan[x] = "통상"
    return plan


def main() -> None:
    DATA.mkdir(exist_ok=True)
    emps = [(e[0], e[1], e[2], "선택근무", START.isoformat()) for e in FLEX]
    for i, (n, dpt) in enumerate(zip(REG_NAMES, REG_DEPTS)):
        emps.append((f"E{8 + i:02d}", f"(가상){n}", dpt, "고정근무", ""))
    hire = {e[0]: "2024-0{}-0{}".format(1 + i % 9, 1 + i % 8) if i % 3 else "2022-03-02" for i, e in enumerate(emps)}
    for e in FLEX:
        hire[e[0]] = min(hire[e[0]], "2025-01-02")     # 참여자는 지원 시작 전 입사
    exit_ = {"E15": "2026-02-27"}  # 지원 기간 중 퇴사
    flex = {e[0]: e for e in FLEX}
    plans = {eid: make_plan(e[3], e[4]) for eid, e in flex.items()}

    rows, leaves = [], []
    bias = {e[0]: rng.normal(0, 2) for e in emps}
    day = START
    while day <= END:
        if not is_workday(day):
            day += timedelta(days=1)
            continue
        ym = day.strftime("%Y-%m")
        for eid, alias, dept, kind, _ in emps:
            if day < date.fromisoformat(hire[eid]) or (eid in exit_ and day > date.fromisoformat(exit_[eid])):
                continue
            if rng.random() < leave_prob(day):
                kind_plan = plans[eid][day] if kind == "선택근무" else "통상"
                leaves.append({"emp_id": eid, "date": day.isoformat(), "type": "연차", "hours": PLAN_MIN[kind_plan] / 60})
                continue
            if rng.random() < 0.0005:  # 무단결근 또는 출결 기록 자체 누락
                continue
            start = 8 * 60 + 50 + bias[eid] + rng.normal(0, 3)
            if kind == "선택근무":
                habit = flex[eid][4]
                pl = plans[eid][day]
                if rng.random() < 0.01:
                    start = 9 * 60 + rng.uniform(6, 20)
                if pl == "단축일":
                    end = 13 * 60 + 30 + abs(rng.normal(2, 2))
                    if rng.random() < habit["short_early"]:
                        end = 13 * 60 + 30 - rng.uniform(20, 75)   # 단축일 4시간 미만
                elif pl == "통상":
                    end = 18 * 60 + abs(rng.normal(3, 3))
                else:
                    end = 19 * 60 + abs(rng.normal(3, 3))
                    if rng.random() < habit["long_short"]:
                        end = 18 * 60 + rng.uniform(0, 40)        # 장일인데 18시대 퇴근
                    elif rng.random() < 0.05:
                        end += rng.uniform(30, 90)                 # 연장근로
                forget = habit.get("bad", {}).get(ym, habit["forget"])
            else:
                late_p, early_p, forget = REG_HABIT.get(eid, REG_BASE)
                if rng.random() < late_p:
                    start = 9 * 60 + rng.uniform(6, 35)
                end = 18 * 60 + abs(rng.normal(3, 3))
                if rng.random() < early_p:
                    end = 18 * 60 - rng.uniform(30, 150)           # 조퇴
                elif rng.random() < 0.06 + (0.08 if day.day >= 26 else 0):
                    end += rng.uniform(30, 120)                    # 연장근로 (월말 많음)
            rec = {"emp_id": eid, "d": day.isoformat(), "in": iso(day, start), "out": iso(day, end), "edited": 0, "edit_reason": ""}
            r = rng.random()
            if r < forget:
                if rng.random() < 0.4:
                    rec["edited"], rec["edit_reason"] = 1, "퇴근 미기록 — 근무 확인 후 사업주 입력"
                else:
                    rec["out"] = ""
            elif r < forget + 0.0015:
                rec["out"] = iso(day, 24 * 60 + 8 * 60 + 50)      # 다음 날 아침 퇴근 버튼
            rows.append(rec)
            if rng.random() < 0.002:
                t = rng.uniform(60, 240)
                rows.append({"emp_id": eid, "d": day.isoformat(), "in": iso(day, t), "out": iso(day, t + 1), "edited": 0, "edit_reason": ""})
        day += timedelta(days=1)

    with open(DATA / "attendance_logs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["emp_id", "d", "in", "out", "edited", "edit_reason"])
        w.writeheader()
        w.writerows(rows)
    with open(DATA / "work_plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["emp_id", "date", "plan", "plan_min"])
        for eid, pl in plans.items():
            for x in sorted(pl):
                w.writerow([eid, x.isoformat(), pl[x], PLAN_MIN[pl[x]]])
    with open(DATA / "leave.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["emp_id", "date", "type", "hours"])
        w.writeheader()
        w.writerows(leaves)
    with open(DATA / "employees.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["emp_id", "alias", "dept", "work_type", "flex_from", "hire_date", "exit_date"])
        for eid, alias, dept, kind, ff in emps:
            w.writerow([eid, alias, dept, kind, ff, hire[eid], exit_.get(eid, "")])
    with open(DATA / "holidays_kr.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "name"])
        for k, v in sorted(HOLIDAYS.items()):
            w.writerow([k, v])
    n_plan = sum(len(p) for p in plans.values())
    print(f"출퇴근 기록 {len(rows):,}건, 근무계획 {n_plan:,}일, 연차 {len(leaves):,}건, 직원 {len(emps)}명 → {DATA}")


if __name__ == "__main__":
    main()
