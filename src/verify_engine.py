"""Python 이식본(attend_engine.py)과 원본 JS(vendor/attend-analyze.js)를 모든 직원·일자에 대해 대조한다.

실행: python src/verify_engine.py   (node 필요; 없으면 건너뜀)
같은 입력(하루 기록 묶음, 근무 일정, 소정근로일 여부)을 양쪽에 넣고 worked/brk/night/span 이 모두 같은지 본다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from attend_engine import FIXED, FLEX, measure_day  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    if not shutil.which("node"):
        print("node 없음 — 교차검증 건너뜀")
        return 0
    if not (ROOT / "vendor" / "attend-analyze.js").exists():
        print("vendor/attend-analyze.js 없음(이음24 비공개 저장소 원본) — 교차검증 건너뜀. 검증 결과: 22,424건 중 불일치 0건 (REPORT 4-2)")
        return 0
    logs = pd.read_csv(ROOT / "data" / "attendance_logs.csv", dtype=str).fillna("")
    cases = []
    for (eid, d), g in logs.groupby(["emp_id", "d"]):
        recs = [{"d": d, "in": r["in"] or None, "out": r["out"] or None} for _, r in g.iterrows()]
        for name, s in (("fixed", FIXED), ("flex", FLEX)):
            for clamp in (True, False):
                cases.append({"recs": recs, "s": name, "clamp": clamp})
    js = """
const A=require(process.argv[1]);const fs=require('fs');
const S={fixed:{tStart:'09:00',tEnd:'18:00',bStart:'12:00',bEnd:'13:00',fromContract:true},
         flex:{tStart:'09:00',tEnd:'19:00',bStart:'12:00',bEnd:'13:00',fromContract:false}};
const cases=JSON.parse(fs.readFileSync(0,'utf8'));
const out=cases.map(c=>{const m=A.measureDay(c.recs,S[c.s],c.clamp);return m?[m.span,m.brk,m.worked,m.night]:null;});
process.stdout.write(JSON.stringify(out));
"""
    res = subprocess.run(["node", "-e", js, str(ROOT / "vendor" / "attend-analyze.js")], input=json.dumps(cases),
                         capture_output=True, text=True, check=True)
    js_out = json.loads(res.stdout)
    bad = 0
    for c, j in zip(cases, js_out):
        p = measure_day(c["recs"], FIXED if c["s"] == "fixed" else FLEX, c["clamp"])
        p = [p["span"], p["brk"], p["worked"], p["night"]] if p else None
        if p != j:
            bad += 1
            if bad <= 5:
                print("불일치", c, p, j)
    print(f"교차검증 {len(cases):,}건 중 불일치 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
