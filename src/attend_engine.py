"""이음24 출결 분석 엔진(js/attend-analyze.js)의 하루 근로시간 계산을 Python으로 옮긴 것.

원본: didimdol-ieum-ai/js/attend-analyze.js (5a09f8e, 2026-10-16) — vendor/attend-analyze.js 에 사본
옮긴 함수: breakFor(휴게 차감), effStart(소정 출근 시각 이전 출근 보정), measureDay(하루 여러 기록 합산·야간)
검증: src/verify_engine.py 가 모든 기록을 node로 원본 JS에 넣어 결과가 1분 단위까지 같은지 대조한다.

근거 조문 (원본 주석 그대로)
- 근로기준법 제54조: 근로 4시간이면 30분, 8시간이면 1시간 이상 휴게
- 제50조(1일 8시간·1주 40시간), 제56조(야간 22:00~06:00 가산)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Sched:
    tStart: str = "09:00"
    tEnd: str = "18:00"
    bStart: str = "12:00"
    bEnd: str = "13:00"
    fromContract: bool = True


FIXED = Sched()  # 일반 근로자 09~18, 휴게 12~13 (근로계약서 휴게 1시간)
# 선택근무 참여자: 출근 09:00, 장일(9시간) 휴게 12~13 1시간 / 단축일(4시간) 휴게 12:00~12:30 30분.
# 날마다 휴게 길이가 달라서 '계약서 휴게 없음' 규칙(체류 8시간 이상 1시간, 4시간 이상 30분)을 쓰면 두 날 모두 계약과 같은 값이 나온다.
FLEX = Sched(tStart="09:00", tEnd="19:00", fromContract=False)


def to_min(t: str | None) -> int | None:
    if not t or len(t) < 4 or ":" not in t:
        return None
    h, m = t.split(":")[:2]
    return int(h) * 60 + int(m[:2])


def ms(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp() * 1000


def day_start(d: str) -> float:
    return datetime.fromisoformat(f"{d}T00:00:00+09:00").timestamp() * 1000


def break_for(span_min: int, s: Sched) -> int:
    """원본 breakFor 와 동일."""
    if span_min < 240:
        return 0
    legal = 60 if span_min >= 510 else 30
    bs, be = to_min(s.bStart), to_min(s.bEnd)
    if s.fromContract and bs is not None and be is not None and be > bs:
        contract = be - bs
    else:
        contract = 60 if span_min >= 480 else 30
    return min(span_min, max(contract, legal))


def eff_start(rec: dict, s: Sched, clamp: bool) -> float:
    a = ms(rec["in"])
    st = day_start(rec["d"]) + (to_min(s.tStart) or 0) * 6e4
    return st if clamp and a < st else a


def _overlap(a1, a2, b1, b2):
    return max(0, min(a2, b2) - max(a1, b1))


def _round(x: float) -> int:
    """JS Math.round (0.5는 +무한대 쪽)."""
    import math
    return int(math.floor(x + 0.5))


def measure_day(recs: list[dict], s: Sched, clamp: bool) -> dict | None:
    """원본 measureDay 와 동일: 겹치는 기록은 합치고, 소정근로일이면 소정 출근 시각 이전은 세지 않는다."""
    done = sorted([r for r in recs if r.get("in") and r.get("out") and ms(r["out"]) > ms(r["in"])], key=lambda r: ms(r["in"]))
    if not done:
        return None
    iv: list[list[float]] = []
    for r in done:
        a, b = eff_start(r, s, clamp), ms(r["out"])
        if b <= a:
            continue
        if iv and a <= iv[-1][1]:
            iv[-1][1] = max(iv[-1][1], b)
        else:
            iv.append([a, b])
    if not iv:
        return {"span": 0, "brk": 0, "worked": 0, "night": 0, "early": 0, "overlapped": False}
    base = day_start(done[0]["d"])
    span = _round(sum(b - a for a, b in iv) / 6e4)
    brk = break_for(span, s)
    night = 0.0
    for a, b in iv:
        for k in (-1, 0, 1):
            night += _overlap(a, b, base + k * 864e5 + 22 * 36e5, base + k * 864e5 + 30 * 36e5)
    return {"span": span, "brk": brk, "worked": max(0, span - brk), "night": _round(night / 6e4),
            "early": _round((iv[0][0] - ms(done[0]["in"])) / 6e4), "overlapped": len(done) > len(iv)}
