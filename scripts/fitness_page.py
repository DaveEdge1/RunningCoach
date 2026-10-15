"""Fitness tab (docs/fitness.html): volume, training load, aerobic efficiency, zones, race fitness.

Built from data/runs.csv by scripts/build_site.py.
"""
import csv, html, json, math, re, statistics
from datetime import date, timedelta
from pathlib import Path

from analyze import EF_HR_BAND, EF_MIN_SECS
from build_site import BASE_CSS, tabs
from config import (ACUTE_DAYS, ACWR_DANGER, ACWR_SWEET, AET, AET_MAX_GRADE, AET_MAX_S, AET_WARMUP_S, CHRONIC_DAYS,
                    CROSS_DEFAULT_RATE, CROSS_NAMES, DANIELS_FLOOR_FT, DANIELS_SPM, DECOUPLE_LIMIT, DECOUPLE_MIN_S, EFFORT_MIN_M, HARD_BOUT,
                    HOME_ELEV_FT, HOME_NAME, LT, MANUAL_LOAD_PER_MIN, RECOVERY_KM_PER_DAY, REP_DIST_TOL, REP_PACE_TOL, ROOT,
                    WORKOUT_Z5_PCT, ZONE_NAMES as ZONE_COLS, ZONES, convert_range, convert_time, load_races, next_race,
                    cross_kind, recovery_days, trail_days, zone_range)

CTL_DAYS, ATL_DAYS = 42, 7
TREND_DAYS = 21
TREND_MIN_RUNS = 3  # runs that must qualify in the window
EF_ELEV_TOL_FT = 1000  # pace-at-AeT uses only runs within this of home elevation
FRESH, TIRED = 5, -10  # form % thresholds
RATE_DAYS = 42  # load per mile for the projection comes from this many recent days
GOAL_EFFORTS_PER_MONTH = 3  # training efforts drawn on the fitness-vs-goal chart
M_PER_MI = 1609.344
FT_PER_M = 3.281
DISTANCES = [(5000, "5K"), (10000, "10K"), (15000, "15K"), (21097.5, "Half marathon"), (42195, "Marathon")]


def dist_label(meters):
    m, name = min(DISTANCES, key=lambda x: abs(x[0] - meters))
    return name if abs(m - meters) / m < 0.04 else f"{meters / M_PER_MI:.1f} mi"


# ---------- Daniels VDOT ----------

def vo2(v):  # v in m/min
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def pct_max(t):  # t in minutes
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)


def vdot(meters, secs):
    t = secs / 60
    return vo2(meters / t) / pct_max(t)


def predict(v, meters):
    lo, hi = 60.0, 36000.0
    for _ in range(60):  # bisection on time
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if vdot(meters, mid) > v else (lo, mid)
    return (lo + hi) / 2


def pace_at(v, frac):
    """sec/mi at the velocity where VO2 = frac * VDOT."""
    a, b, c = 0.000104, 0.182258, -4.60 - frac * v
    vel = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)
    return M_PER_MI / vel * 60


def hms(s):
    s = round(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------- data ----------

def load_runs():
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_cross():
    """Recorded hikes and XC skis (data/cross.csv): load counts, miles don't."""
    path = ROOT / "data" / "cross.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def series(runs, end, planned=None, cross=()):
    """Daily miles, load, long-term/recent load, form, and acute:chronic ratio from the first run through `end`.
    Cross-training adds load (also kept as xload) but no miles. `planned` ({date: (miles, run load, cross load)})
    adds planned days, for the projection to race day."""
    planned = planned or {}
    first = min(date.fromisoformat(r["date"]) for r in runs)
    days = [first + timedelta(i) for i in range((end - first).days + 1)]
    miles = {d: 0.0 for d in days}
    load = {d: 0.0 for d in days}
    xload = {d: 0.0 for d in days}
    for r in runs:
        d = date.fromisoformat(r["date"])
        if d in miles:
            miles[d] += float(r["miles"])
            load[d] += float(r["load"] or 0)
    for c in cross:
        d = date.fromisoformat(c["date"])
        if d in miles:
            load[d] += float(c["load"] or 0)
            xload[d] += float(c["load"] or 0)
    for d, (mi, ld, xl) in planned.items():
        if d in miles:
            miles[d] += mi
            load[d] += ld + xl
            xload[d] += xl
    # seed both averages with the first 4 weeks' mean so history doesn't start from zero
    seed = sum(load[d] for d in days[:28]) / min(28, len(days))
    ctl = atl = seed
    out = []
    for i, d in enumerate(days):
        form_pct = 100 * (ctl - atl) / ctl if ctl else 0  # going into the day
        ctl += (load[d] - ctl) / CTL_DAYS
        atl += (load[d] - atl) / ATL_DAYS
        r7 = sum(miles[days[j]] for j in range(max(0, i - 6), i + 1))
        r28 = sum(miles[days[j]] for j in range(max(0, i - 27), i + 1)) / 4
        acute = sum(load[days[j]] for j in range(max(0, i - ACUTE_DAYS + 1), i + 1))
        chronic = sum(load[days[j]] for j in range(max(0, i - CHRONIC_DAYS + 1), i + 1)) * ACUTE_DAYS / CHRONIC_DAYS
        out.append({"d": d.isoformat(), "mi": round(miles[d], 2), "load": round(load[d]), "xload": round(xload[d]),
                    "ctl": round(ctl, 1), "atl": round(atl, 1), "form": round(ctl - atl, 1),
                    "formPct": round(form_pct, 1), "r7": round(r7, 1),
                    "r28": round(r28, 1) if i >= 27 else None,
                    "acwr": round(acute / chronic, 2) if i >= CHRONIC_DAYS - 1 and chronic else None,
                    "plan": d in planned})
    return out


def load_rates(runs, plan, race_days, today):
    """Load per mile over the last RATE_DAYS days, by kind of run: race, workout (a planned workout day, or with no
    plan row, at least WORKOUT_Z5_PCT % of the time in Z5), or easy (everything else). Returns ({kind: rate}, counts)."""
    sums = {"easy": [0.0, 0.0, 0], "workout": [0.0, 0.0, 0], "race": [0.0, 0.0, 0]}
    for r in runs:
        d = date.fromisoformat(r["date"])
        if not r["hr_secs"] or not 0 <= (today - d).days < RATE_DAYS or not float(r["miles"]):
            continue
        p = plan.get(d)
        kind = ("race" if d in race_days else
                "workout" if (p["type"] == "workout" if p else float(r["Z5 >LT"] or 0) >= WORKOUT_Z5_PCT) else "easy")
        s = sums[kind]
        s[0], s[1], s[2] = s[0] + float(r["load"]), s[1] + float(r["miles"]), s[2] + 1
    rate = {k: s[0] / s[1] for k, s in sums.items() if s[1]}
    rate.setdefault("easy", MANUAL_LOAD_PER_MIN * 10)  # no history: ~10 min/mi at the manual-run rate
    rate.setdefault("workout", rate["easy"])
    rate.setdefault("race", rate["workout"])
    return rate, {k: s[2] for k, s in sums.items()}


def cross_rates(cross):
    """Load per minute by cross-training kind, from recorded sessions with HR; CROSS_DEFAULT_RATE until there are
    any. Returns ({kind: rate}, {kind: sessions})."""
    sums = {}
    for c in cross:
        if c["hr_secs"] and int(c["hr_secs"]) and float(c["secs"]):
            s = sums.setdefault(c["kind"], [0.0, 0.0, 0])
            s[0], s[1], s[2] = s[0] + float(c["load"]), s[1] + float(c["secs"]) / 60, s[2] + 1
    rate = {k: (sums[k][0] / sums[k][1] if k in sums else v) for k, v in CROSS_DEFAULT_RATE.items()}
    return rate, {k: sums[k][2] if k in sums else 0 for k in CROSS_DEFAULT_RATE}


def planned_load(runs, plan, rate, xrate, today, end):
    """{date: (miles, run load, cross load)} for plan days after today (and today, if no run is recorded yet),
    through end. Race days use the race rate for the race distance and the easy rate for the rest (warm-up).
    Cross-training counts when the session names a kind (cross_kind) and plan.csv gives its cross_min."""
    ran = {r["date"] for r in runs}
    races_by_day = {r["date"]: r for r in load_races()}
    out = {}
    for d, p in plan.items():
        if not (today < d <= end or (d == today and d.isoformat() not in ran)):
            continue
        mi = float(p["miles"] or 0)
        kind, mins = cross_kind(p["session"]), float(p.get("cross_min") or 0)
        xl = mins * xrate[kind] if kind and mins else 0.0
        if not mi and not xl:
            continue
        if p["type"] == "race":
            race_mi = min(mi, races_by_day[d]["meters"] / M_PER_MI) if d in races_by_day else mi
            out[d] = (mi, race_mi * rate["race"] + (mi - race_mi) * rate["easy"], xl)
        else:
            out[d] = (mi, mi * rate["workout" if p["type"] == "workout" else "easy"], xl)
    return out


def efficiency(runs, daily, rc):
    """Per-run pace at AeT, plus the trend. Runs in a race's recovery window (recovery_days after it)
    and trail runs (notes.md) are kept as points but marked and left out of the trend."""
    by_day = {x["d"]: x for x in daily}
    recovering = {}
    for r in rc:
        for i in range(recovery_days(r["meters"]) + 1):
            recovering[(r["d"] + timedelta(i)).isoformat()] = (r["name"], i)
    trails = trail_days()
    pts = []
    for r in runs:
        near_home = not r.get("elev_avg_ft") or abs(int(r["elev_avg_ft"]) - HOME_ELEV_FT) <= EF_ELEV_TOL_FT
        if r["aet_pace_secs"] and r["date"] in by_day and near_home:
            f = by_day[r["date"]]["formPct"]
            rec = recovering.get(r["date"])
            why = (f"{rec[1]} day{'s' * (rec[1] != 1)} after {rec[0]}" if rec else
                   "trail run" if r["date"] in trails else None)
            pts.append({"d": r["date"], "p": int(r["aet_pace_secs"]), "n": int(r["aet_secs"]),
                        "mi": float(r["miles"]), "formPct": f,
                        "why": why,
                        "cls": "fresh" if f > FRESH else "tired" if f < TIRED else "neutral"})
    use = [p for p in pts if not p["why"]]
    trend = []
    for x in daily:
        d = date.fromisoformat(x["d"])
        win = [p for p in use if 0 <= (d - date.fromisoformat(p["d"])).days < TREND_DAYS]
        if len(win) >= TREND_MIN_RUNS:
            trend.append({"d": x["d"], "p": round(sum(p["p"] * p["n"] for p in win) / sum(p["n"] for p in win), 1)})
    return pts, trend


def weekly(runs, daily, cross=()):
    weeks = {}
    new = lambda: {"mi": 0.0, "runs": 0, "hr": 0, "z": [0.0] * 5, "load": 0, "xh": 0.0}
    for c in cross:
        d = date.fromisoformat(c["date"])
        w = weeks.setdefault(d - timedelta(d.weekday()), new())
        w["load"] += float(c["load"] or 0)
        w["xh"] += float(c["secs"] or 0) / 3600
    for r in runs:
        d = date.fromisoformat(r["date"])
        w = weeks.setdefault(d - timedelta(d.weekday()), new())
        w["mi"] += float(r["miles"])
        w["runs"] += 1
        w["load"] += float(r["load"] or 0)
        if r["hr_secs"]:
            n = int(r["hr_secs"])
            w["hr"] += n
            w["z"] = [z + float(r[c]) * n / 100 for z, c in zip(w["z"], ZONE_COLS)]
    by_day = {x["d"]: x for x in daily}
    out = []
    for wk0 in sorted(weeks):
        w = weeks[wk0]
        end = min(wk0 + timedelta(6), date.fromisoformat(daily[-1]["d"]))
        x = by_day.get(end.isoformat(), {})
        out.append({"w": wk0.isoformat(), "mi": round(w["mi"], 1), "runs": w["runs"], "load": round(w["load"]),
                    "xh": round(w["xh"], 1),
                    "hrH": round(w["hr"] / 3600, 1),
                    "z": [round(100 * z / w["hr"]) if w["hr"] else 0 for z in w["z"]],
                    "ctl": x.get("ctl"), "atl": x.get("atl"), "partial": end < wk0 + timedelta(6)})
    return out


def races(runs, plan):
    """Completed races (races.csv or plan type race) and time trials (plan session named so), with results."""
    listed = {r["date"]: r for r in load_races()}
    out = []
    for r in runs:
        d = date.fromisoformat(r["date"])
        p, rc = plan.get(d), listed.get(d)
        is_race = rc is not None or (p is not None and p["type"] == "race")
        if not (is_race or (p is not None and "time trial" in p["session"].lower())):
            continue
        m, s = r["pace"].split(":")
        secs = (int(m) * 60 + int(s)) * float(r["miles"])
        meters = float(r["miles"]) * M_PER_MI
        elev = int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else (rc or {}).get("elev")
        name = rc["name"] if rc else f"{dist_label(meters)} {'race' if is_race else 'time trial'}"
        out.append({"d": d, "name": name, "meters": meters, "secs": secs, "elev": elev,
                    "gap": float(r["gap_secs"]) if r.get("gap_secs") else secs, "vdot": vdot(meters, secs)})
    return out


def src_elev(r):  # no GPS altitude or races.csv elevation: assume home
    return r["elev"] if r["elev"] is not None else HOME_ELEV_FT


def vdot_range(r, to_ft):  # result re-scored as if run at to_ft, across the altitude methods
    lo, hi = convert_range(r["secs"], r["meters"], src_elev(r), to_ft)
    return vdot(r["meters"], hi), vdot(r["meters"], lo)  # slower time -> lower VDOT


def sl_vdot(meters, secs, elev):
    """Sea-level VDOT: Daniels (average of its s/mi range) averaged with NCAA."""
    sl = {k: vdot(meters, t) for k, t in convert_time(secs, meters, elev, 0).items()}
    daniels = [v for k, v in sl.items() if k.startswith("Daniels")]
    return (sum(daniels) / len(daniels) + sl["NCAA"]) / 2


def effort_label(meters):
    mi = meters / M_PER_MI
    return f"{round(mi):g} mi" if abs(mi - round(mi)) < 0.01 else dist_label(meters)


def load_efforts(runs, rc):
    """Best efforts (data/efforts.csv), scored on grade-adjusted time, and why each can't stand in for a race
    (empty if it can): shorter than EFFORT_MIN_M, a net grade steeper than AET_MAX_GRADE (where Minetti's running
    model fits walking poorly), max HR below LT (not a hard effort, or a GPS glitch), or on a race / time-trial day
    (the result itself counts). "secs" is the grade-adjusted time, so the effort's VDOT and any paces from it are
    flat-ground equivalents; "clock" is the time as run."""
    path = ROOT / "data" / "efforts.csv"
    if not path.exists():
        return []
    elev = {(r["date"], r["start"]): int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else None for r in runs}
    race_days = {r["d"] for r in rc}
    out = []
    with open(path, encoding="utf-8") as f:
        for e in csv.DictReader(f):
            d, m, clock = date.fromisoformat(e["date"]), float(e["meters"]), float(e["secs"])
            secs = float(e["gap_secs"]) if e.get("gap_secs") else clock
            el = elev.get((e["date"], e["start"]))
            net = int(e["net_elev_ft"]) if e["net_elev_ft"] else None
            mhr = int(e["max_hr"]) if e["max_hr"] else None
            why = ("race day" if d in race_days else
                   f"under {effort_label(EFFORT_MIN_M)}" if m < EFFORT_MIN_M - 1 else
                   "no elevation" if net is None else
                   "too steep to grade-adjust" if abs(net) / FT_PER_M / m > AET_MAX_GRADE else
                   "no HR" if mhr is None else
                   f"max HR under {LT}" if mhr < LT else "")
            out.append({"d": d, "start": e["start"], "meters": m, "secs": secs, "clock": clock, "elev": el, "net": net,
                        "hr": int(e["avg_hr"]) if e["avg_hr"] else None, "maxhr": mhr,
                        "vdot": vdot(m, secs), "sl": sl_vdot(m, secs, el if el is not None else HOME_ELEV_FT),
                        "name": f"Best {effort_label(m)} in training", "why": why})
    return out


def fitness_basis(rc, efforts, before=None):
    """Current fitness: the latest race or time trial before `before`, unless a later best effort that qualifies
    (load_efforts) has a higher sea-level VDOT; then the highest such effort. Both sides are compared on
    grade-adjusted time, so hills count the same way in a race and in training."""
    races_ = [r for r in rc if before is None or r["d"] < before]
    last = races_[-1] if races_ else None
    floor = sl_vdot(last["meters"], last["gap"], src_elev(last)) if last else 0
    better = [e for e in efforts if not e["why"] and (before is None or e["d"] < before)
              and (last is None or e["d"] > last["d"]) and e["sl"] > floor]
    if better:
        return dict(max(better, key=lambda e: e["sl"]), effort=True)
    return last


def decoupling_points(runs, plan, rc):
    """Decoupling for steady runs only: a planned easy or long day without a steady finish, or with no plan row,
    a run under WORKOUT_Z5_PCT % in Z5. Race days are left out."""
    race_days, trails, out = {r["d"] for r in rc}, trail_days(), []
    for r in runs:
        if not r.get("decouple_pct"):
            continue
        d = date.fromisoformat(r["date"])
        p = plan.get(d)
        steady = (p["type"] in ("easy", "long") and "steady" not in p["session"].lower() if p else
                  float(r["Z5 >LT"] or 0) < WORKOUT_Z5_PCT)
        if steady and d not in race_days:
            out.append({"d": r["date"], "pct": float(r["decouple_pct"]), "min": int(r["decouple_min"]),
                        "mi": float(r["miles"]), "trail": r["date"] in trails})
    return out


REP_RE = re.compile(r"(\d+)\s*[×x]\s*(\d+(?:\.\d+)?)\s*(km|mi|k)\b", re.I)
CAP_RE = re.compile(r"HR\s*(?:≤|<=|<)\s*(\d+)", re.I)


def load_laps():
    out = {}
    with open(ROOT / "data" / "laps.csv", encoding="utf-8") as f:
        for l in csv.DictReader(f):
            out.setdefault((l["date"], l["start"]), []).append(l)
    return out


def clock_secs(s):
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def rep_target(session, basis, elev, nr):
    """(label, (fast, slow) sec/mi at elev, HR cap or None) for a workout session; None if it names no pace."""
    s = session.lower()
    cap = CAP_RE.search(session)
    cap = int(cap.group(1)) if cap else None
    if "goal" in s and nr and nr["goal_secs"] and nr["elev"] is not None:
        lo, hi = convert_range(nr["goal_secs"], nr["meters"], nr["elev"], elev)
        mi = nr["meters"] / M_PER_MI
        return f"goal {dist_label(nr['meters'])} pace", (lo / mi, hi / mi), cap
    if not basis:
        return None
    vlo, vhi = vdot_range(basis, elev)
    if "threshold" in s:  # training-plan.md: threshold reps stay at or below LT - 1
        return "threshold", (pace_at(vhi, 0.88), pace_at(vlo, 0.88)), cap or LT - 1
    if "10k" in s or "interval" in s:
        ten = (predict(vhi, 10000) / (10000 / M_PER_MI), predict(vlo, 10000) / (10000 / M_PER_MI))
        if "interval" in s:
            return "10K to interval", (pace_at(vhi, 0.98), ten[1]), cap
        return "10K", ten, cap
    return None


def workouts(runs, plan, rc, efforts, nr):
    """Planned workouts with reps ("N × D"), matched to laps: the N fastest laps within REP_DIST_TOL of D.
    Rep pace is judged on grade-adjusted pace against the target at the run's elevation, from the fitness basis
    as of that day."""
    laps = load_laps()
    by_day = {}
    for r in runs:
        by_day.setdefault(date.fromisoformat(r["date"]), []).append(r)
    out = []
    for d in sorted(plan):
        p = plan[d]
        m = REP_RE.search(p["session"])
        if p["type"] != "workout" or not m or d not in by_day:
            continue
        n, x, unit = int(m.group(1)), float(m.group(2)), m.group(3).lower()
        dist = x * (M_PER_MI if unit == "mi" else 1000)
        best = None
        for r in by_day[d]:
            ls = laps.get((r["date"], r["start"]), [])
            cand = [l for l in ls if abs(float(l["miles"]) * M_PER_MI - dist) <= REP_DIST_TOL * dist and l["pace"]]
            reps = sorted(sorted(cand, key=lambda l: clock_secs(l["pace"]))[:n], key=lambda l: int(l["lap"]))
            if best is None or len(reps) > len(best[1]):
                best = (r, reps)
        r, reps = best
        elev = int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else HOME_ELEV_FT
        tgt = rep_target(p["session"], fitness_basis(rc, efforts, d), elev, nr)
        rows = []
        for l in reps:
            gap = clock_secs(l["gap"]) if l.get("gap") else clock_secs(l["pace"])
            pace_ok = hr_ok = None
            if tgt:
                fast, slow = tgt[1]
                pace_ok = "fast" if gap < fast - REP_PACE_TOL else "slow" if gap > slow + REP_PACE_TOL else "on"
                if tgt[2] and l["max_hr"]:
                    hr_ok = int(l["max_hr"]) <= tgt[2]
            rows.append({"lap": l["lap"], "mi": float(l["miles"]), "secs": int(l["secs"]), "pace": l["pace"],
                         "gap": gap, "hr": l["avg_hr"], "max": l["max_hr"], "paceOk": pace_ok, "hrOk": hr_ok})
        out.append({"d": d, "session": p["session"], "n": n, "elev": elev, "target": tgt, "reps": rows})
    return out

# ---------- page ----------

def acwr_word(x):
    lo, hi = ACWR_SWEET
    return ("below the sweet spot" if x < lo else "sweet spot" if x <= hi else
            "above the sweet spot" if x < ACWR_DANGER else "danger zone")


def basis_phrase(b):
    when = f'{b["d"]:%b} {b["d"].day}, {ft(b["elev"]) or "elevation unknown"}'
    if b.get("effort"):
        return (f'a training run, not a race: your best {effort_label(b["meters"])} on {when} ({hms(b["clock"])}, '
                f'{hms(b["secs"])} grade-adjusted) beat '
                f'your last race or time trial (sea-level VDOT {b["sl"]:.1f}). See Best efforts')
    return f'your most recent race or time trial ({html.escape(b["name"])}, {when})'


def tile(label, value, sub="", delta=""):
    return (f'<div class="tile"><div class="tl">{label}</div><div class="tv">{value}</div>'
            f'{f"<div class=td>{delta}</div>" if delta else ""}'
            f'{f"<div class=ts>{sub}</div>" if sub else ""}</div>')


def signed(x, fmt="{:.1f}", good_up=True):
    if x is None:
        return ""
    cls = "flat" if abs(x) < 1e-9 else ("up" if (x > 0) == good_up else "down")
    sign = "+" if x > 0 else "−" if x < 0 else "±"
    return f'<span class="{cls}">{sign}{fmt.format(abs(x))}</span>'


def ft(x):
    return f"{x:,} ft" if x is not None else ""


def hms_range(lo, hi):
    a, b = hms(lo), hms(hi)
    return a if a == b else f"{a}–{b}"


def panel(title, big, rows, note=""):
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    return (f'<div class="panel"><h3>{title}</h3>' + (f'<div class="big">{big}</div>' if big else "")
            + f'<table class="t kv">{body}</table>' + (f'<p class="note">{note}</p>' if note else "") + "</div>")


def next_time_trial(plan, today, before):
    """(date, meters, label) of the next planned session named "time trial" before `before`, if its distance parses."""
    for d in sorted(plan):
        sess = plan[d]["session"]
        if today <= d < before and "time trial" in sess.lower():
            m = re.search(r"(\d+(?:\.\d+)?)\s*(km|k|mi|mile)\b", sess, re.I)
            if m:
                n, unit = float(m.group(1)), m.group(2).lower()
                return d, n * (M_PER_MI if unit.startswith("mi") else 1000), sess
    return None


def mmss_range(lo, hi):
    a, b = mmss(lo), mmss(hi)
    return a if a == b else f"{a}–{b}"


def mmss(secs):
    secs = round(secs)
    return f"{secs // 60}:{secs % 60:02d}"


def build(plan, built):
    runs = load_runs()
    today = date.today()
    cross = load_cross()
    daily = series(runs, today, cross=cross)
    rc = races(runs, plan)
    pts, trend = efficiency(runs, daily, rc)
    weeks = weekly(runs, daily, cross)
    efforts = load_efforts(runs, rc)
    nr = next_race(today)
    now, ago = daily[-1], daily[-29] if len(daily) > 28 else None

    # projection to race day: actual load so far, then the plan at recent load per mile
    race_days = {r["d"] for r in rc} | {r["date"] for r in load_races()}
    rate, rate_n = load_rates(runs, plan, race_days, today)
    xrate, xrate_n = cross_rates(cross)
    proj = []
    if nr:
        proj = series(runs, nr["date"], planned_load(runs, plan, rate, xrate, today, nr["date"]), cross)
        for x in proj:
            p = plan.get(date.fromisoformat(x["d"]))
            x["s"] = p["session"] if p else ""

    # tiles
    prev28 = sum(x["mi"] for x in daily[-56:-28]) / 4 if len(daily) >= 56 else None
    t_now = trend[-1]["p"] if trend else None
    t_ago = next((t["p"] for t in trend if t["d"] == ago["d"]), None) if ago and trend else None
    fp = now["formPct"]
    form_word = "fresh" if fp > FRESH else "carrying fatigue" if fp < TIRED else "neutral"
    tiles = [
        tile("Last 7 days", f"{now['r7']:.1f} mi"),
        tile("28-day average", f"{now['r28']:.1f} mi/wk",
             delta=(signed(now["r28"] - prev28) + " vs. previous 28 days") if prev28 else ""),
        tile("Long-term load", f"{now['ctl']:.0f}", f"{CTL_DAYS}-day average daily load",
             (signed(now["ctl"] - ago["ctl"], "{:.0f}") + " vs. 4 weeks ago") if ago else ""),
        tile("Form", f"{now['form']:+.0f}".replace("-", "−"), f"{fp:+.0f}% · {form_word}".replace("-", "−")),
    ]
    if t_now:
        tiles.append(tile(f"Pace at AeT ({AET} bpm)", f"{mmss(t_now)}/mi",
                          f"at {HOME_ELEV_FT:,} ft ({HOME_NAME}) · {TREND_DAYS}-day trend, grade-adjusted easy running",
                          (signed((t_now - t_ago) / 1, "{:.0f}", good_up=False) + " s/mi vs. 4 weeks ago") if t_ago else ""))
    if now["acwr"] is not None:
        tiles.append(tile("Load ramp", f"{now['acwr']:.2f}",
                          f"{ACUTE_DAYS}-day ÷ {CHRONIC_DAYS}-day load · {acwr_word(now['acwr'])}"))
    best = fitness_basis(rc, efforts)

    def vrange(lo, hi):
        return f"{lo:.1f}" if f"{lo:.1f}" == f"{hi:.1f}" else f"{lo:.1f}–{hi:.1f}"

    if best:
        tiles.append(tile("VDOT (sea level)", f"{sl_vdot(best['meters'], best['secs'], src_elev(best)):.1f}",
                          f"{html.escape(best['name'])}, {best['d']:%b} {best['d'].day}, at {ft(src_elev(best))} · "
                          f"Daniels/NCAA average"))

    spm = "–".join(f"{k:g}" for k in DANIELS_SPM)
    methods_note = (f"Altitude is converted two published ways and shown as a range: Daniels "
                    f"({spm} s/mi per 1,000 ft above {DANIELS_FLOOR_FT:,} ft) "
                    f"and the NCAA altitude tables (% by altitude and distance).")

    # next race
    next_html = ""
    if nr:
        days = (nr["date"] - today).days
        when = f"{nr['date']:%a %b} {nr['date'].day}, {nr['date'].year} · " + (f"{days} days" if days else "today")
        mi = nr["meters"] / M_PER_MI
        facts = [("Date", when), ("Location", html.escape(nr["location"]) or "—"),
                 ("Elevation", ft(nr["elev"]) or "—"),
                 ("Distance", f"{nr['meters'] / 1000:g} km ({mi:.2f} mi)"),
                 ("Course", html.escape(nr["notes"]) or "—")]
        panels = [panel("Race", None, facts)]
        cur = None
        if best and nr["elev"] is not None:
            t = predict(best["vdot"], nr["meters"])
            by = convert_time(t, nr["meters"], src_elev(best), nr["elev"])
            cur = (min(by.values()), max(by.values()))
            panels.append(panel("Current fitness", hms_range(*cur),
                                [("Pace", f"{mmss_range(cur[0] / mi, cur[1] / mi)}/mi"),
                                 ("Basis", f"{html.escape(best['name'])}, {best['d']:%b} {best['d'].day}")]))
            age = (today - best["d"]).days
            parts = " · ".join(f"{k}: {hms(v)}" for k, v in by.items())
            basis = (f'<p class="note"><b>Current fitness</b> (no projected training gains): {html.escape(best["name"])} on '
                     f'{best["d"]:%b} {best["d"].day} ({age} days ago), {hms(best["secs"])} at {ft(best["elev"]) or "unknown elevation"}, '
                     f'VDOT {best["vdot"]:.1f}. Derived as: that result → VDOT → equivalent {dist_label(nr["meters"])} at '
                     f'{ft(src_elev(best))} ({hms(t)}) → converted to {ft(nr["elev"])}: {parts}.</p>')
        else:
            reason = "this race has no elevation in races.csv" if best else "no completed race or time trial yet"
            basis = f'<p class="note">No estimate: {reason}.</p>'

        g = nr["goal_secs"]
        if g:
            items = [("Pace", f"{mmss(g / mi)}/mi" + (' <span class="dim">(from goal time)</span>' if nr["goal_from"] == "time" else "")),
                     ("Needs", f"VDOT {vdot(nr['meters'], g):.1f} at {ft(nr['elev']) or 'race elevation'}")]
            if nr["goal_from"] == "pace":
                items.insert(0, ("Time", f'{hms(g)} <span class="dim">(from goal pace)</span>'))
            if cur:
                lo, hi = cur
                items.append(("Vs. now", "within current range" if lo <= g <= hi else
                              f"{lo - g:.0f}–{hi - g:.0f} s faster" if g < lo else f"{g - hi:.0f}–{g - lo:.0f} s slower"))
            if nr["elev"] is not None:
                hlo, hhi = convert_range(g, nr["meters"], nr["elev"], HOME_ELEV_FT)
                items.append((f"At {HOME_NAME}", f"{hms_range(hlo, hhi)} · {mmss_range(hlo / mi, hhi / mi)}/mi"))
            panels.append(panel("Goal", hms(g), items))

            tt = next_time_trial(plan, today, nr["date"])
            if tt and cur and nr["elev"] is not None and best["d"] < tt[0]:
                d_tt, m_tt, label = tt
                frac = (d_tt - best["d"]).days / (nr["date"] - best["d"]).days
                v_goal = vdot(nr["meters"], g)
                on_track, full = [], []
                for k, t_now in by.items():  # per altitude method, used the same way in both directions
                    v_now = vdot(nr["meters"], t_now)
                    t_equiv = predict(v_now + frac * (v_goal - v_now), nr["meters"])  # linear in VDOT
                    back = lambda t: convert_time(t, nr["meters"], nr["elev"], HOME_ELEV_FT)[k]
                    on_track.append(predict(vdot(nr["meters"], back(t_equiv)), m_tt))
                    full.append(predict(vdot(nr["meters"], back(g)), m_tt))
                tdays = (d_tt - today).days
                tt_mi = m_tt / M_PER_MI
                panels.append(panel(
                    f"Checkpoint: {html.escape(label)}",
                    f'{hms_range(min(on_track), max(on_track))} <span class="dim">on track</span>',
                    [("Date", f"{d_tt:%a %b} {d_tt.day} · {tdays} days · at {HOME_NAME}"),
                     ("On-track pace", f"{mmss_range(min(on_track) / tt_mi, max(on_track) / tt_mi)}/mi"),
                     ("Goal-equivalent", f"{hms_range(min(full), max(full))} · "
                                         f"{mmss_range(min(full) / tt_mi, max(full) / tt_mi)}/mi")],
                    f"<b>On track</b>: steady progress from current fitness ({best['d']:%b} {best['d'].day}) to "
                    f"{hms(g)} on race day; {d_tt:%b} {d_tt.day} is {frac:.0%} of the way. "
                    f"<b>Goal-equivalent</b>: the {dist_label(m_tt)} you'd run at {HOME_NAME} if already at goal fitness."))

        next_html = (f'\n<section class="card next"><h2>Next race: {html.escape(nr["name"])}</h2>\n'
                     f'<div class="panels">{"".join(panels)}</div>\n{basis}\n</section>')

    race_html = ""
    if best:
        places = [(f"{HOME_NAME} ({HOME_ELEV_FT:,} ft)", HOME_ELEV_FT), ("Sea level", 0)]
        dists = list(DISTANCES[:2])
        if nr and all(abs(m - nr["meters"]) > 1 for m, _ in dists):
            dists.append((nr["meters"], dist_label(nr["meters"])))

        def at_elev(m, ft_):  # equivalent time range for distance m at elevation ft_
            return convert_range(predict(best["vdot"], m), m, src_elev(best), ft_)

        head = "<tr><th></th>" + "".join(f"<th>{n}</th>" for n, _ in places) + "</tr>"
        time_rows = "".join(f"<tr><td>{n}</td>" + "".join(f"<td>{hms_range(*at_elev(m, e))}</td>" for _, e in places)
                            + "</tr>" for m, n in dists)
        vs = [vdot_range(best, e) for _, e in places]  # (low, high) VDOT per place
        pace_fns = [("Easy", lambda v: f"{mmss(pace_at(v[1], 0.70))}–{mmss(pace_at(v[0], 0.62))}"),
                    ("Threshold", lambda v: mmss_range(pace_at(v[1], 0.88), pace_at(v[0], 0.88))),
                    ("Interval", lambda v: mmss_range(pace_at(v[1], 0.98), pace_at(v[0], 0.98)))]
        pace_rows = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{fn(v)}</td>" for v in vs) + "</tr>"
                            for label, fn in pace_fns)
        vdot_row = "<tr><td>VDOT</td>" + "".join(f"<td>{vrange(*v)}</td>" for v in vs) + "</tr>"
        rows = "".join(f"<tr><td>{r['d']:%b} {r['d'].day}</td><td>{html.escape(r['name'])}</td>"
                       f"<td>{r['meters'] / 1000:.2f} km</td><td>{hms(r['secs'])}</td>"
                       f"<td>{ft(r['elev'])}</td><td>{r['vdot']:.1f}</td><td>{vrange(*vdot_range(r, 0))}</td></tr>"
                       for r in reversed(rc))
        race_html = (
            '\n<section class="card"><h2>Race fitness</h2>\n'
            f'<p class="note">From {basis_phrase(best)}. {methods_note}</p>\n'
            '<div class="grid2">\n'
            f'<div><h3>Equivalent times</h3><table class="t full"><thead>{head}</thead><tbody>{time_rows}</tbody></table></div>\n'
            f'<div><h3>Training paces (per mile)</h3><table class="t full"><thead>{head}</thead><tbody>{pace_rows}{vdot_row}</tbody></table></div>\n'
            '</div>\n'
            '<details><summary>Races and time trials</summary><div class="scroll"><table class="t full"><thead><tr><th>Date</th>'
            '<th>Event</th><th>Distance</th><th>Time</th><th>Elevation</th><th>VDOT</th><th>VDOT (sea level)</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></details>\n</section>')

    # load ahead: projection to race day
    ahead_html, iso_today = "", today.isoformat()
    if proj:
        rd = proj[-1]
        fut = [x for x in proj if x["d"] > iso_today] or [rd]
        peak = max(fut, key=lambda x: x["ctl"])
        spans = []  # consecutive days above the sweet spot
        for x in fut:
            if x["acwr"] is not None and x["acwr"] > ACWR_SWEET[1]:
                if spans and (date.fromisoformat(x["d"]) - date.fromisoformat(spans[-1][-1]["d"])).days == 1:
                    spans[-1].append(x)
                else:
                    spans.append([x])
        def span_txt(s):
            a, b = date.fromisoformat(s[0]["d"]), date.fromisoformat(s[-1]["d"])
            top = max(x["acwr"] for x in s)
            return (f"{a:%b} {a.day}" + (f"–{b:%b} {b.day}" if b != a else "")
                    + f" (up to {top:.2f}{', danger zone' if top >= ACWR_DANGER else ''})")
        rp = rd["formPct"]
        mx = max((x for x in fut if x["acwr"] is not None), key=lambda x: x["acwr"], default=None)
        ahead_panels = [
            panel(f"Race day: {nr['date']:%b} {nr['date'].day}", f"{rp:+.0f}%".replace("-", "−") + ' <span class="dim">form going in</span>',
                  [("Long-term load", f"{rd['ctl']:.0f} (today {now['ctl']:.0f})"),
                   ("Reading", "fresh" if rp > FRESH else "carrying fatigue" if rp < TIRED else "neutral")]),
            panel("Peak long-term load", f"{peak['ctl']:.0f}",
                  [("Date", f"{date.fromisoformat(peak['d']):%a %b} {date.fromisoformat(peak['d']).day}")]),
            panel("Load ramp ahead", f"{mx['acwr']:.2f}" if mx else "—",
                  [("Highest", f"{date.fromisoformat(mx['d']):%b} {date.fromisoformat(mx['d']).day}" if mx else "—"),
                   (f"Above {ACWR_SWEET[1]:g}", "; ".join(span_txt(s) for s in spans) or "none")]),
        ]
        xrate_txt = ("Hikes and XC skis count at " + ", ".join(
            f"{CROSS_NAMES[k].lower()} {v:.1f}/min" + (f" ({xrate_n[k]} recorded)" if xrate_n[k] else " (default)")
            for k, v in xrate.items()) + "; paddling doesn't count. ")
        missing = [d for d in sorted(plan) if today < d <= nr["date"] and cross_kind(plan[d]["session"])
                   and not float(plan[d].get("cross_min") or 0)]
        missing_txt = ("No cross_min in plan.csv for " + ", ".join(f"{d:%b} {d.day}" for d in missing)
                       + ", so those days add no cross-training load. ") if missing else ""
        kinds = [("easy", "easy"), ("workout", "workout"), ("race", "race")]
        rate_txt = ", ".join(f"{label} {rate[k]:.0f}" + (f" ({rate_n[k]} run{'s' * (rate_n[k] != 1)})" if rate_n[k] else " (no recent runs; borrowed)")
                             for k, label in kinds)
        ahead_html = (
            '\n<section class="card"><h2>Load ahead: projection to race day</h2>\n'
            f'<p class="note">Actual load through today, then plan.csv at your load per mile over the last {RATE_DAYS} days: '
            f'{rate_txt}. {xrate_txt}{missing_txt}Dashed lines are planned.</p>\n'
            f'<div class="panels">{"".join(ahead_panels)}</div>\n'
            '<div class="chart" id="cProj"></div>\n'
            '<div class="legend"><span><span class="ln" style="background:var(--s1)"></span>Long-term load</span>'
            '<span><span class="ln" style="background:var(--s2)"></span>Recent load</span>'
            '<span><span class="ln dash"></span>Planned</span></div>\n'
            f'<h3>Load ramp: {ACUTE_DAYS}-day ÷ {CHRONIC_DAYS}-day load</h3>\n'
            '<div class="chart short" id="cAcwr"></div>\n'
            f'<div class="legend"><span><i class="band ok"></i>Sweet spot {ACWR_SWEET[0]:g}–{ACWR_SWEET[1]:g}</span>'
            f'<span><i class="band bad"></i>Danger zone ≥ {ACWR_DANGER:g}</span></div>\n</section>')

    # workouts vs. targets
    wo = workouts(runs, plan, rc, efforts, nr)

    def target_txt(t, elev, basis):
        if not t:
            return "no pace target for this session"
        label, (fast, slow), cap = t
        src = ("from the goal time" if label.startswith("goal") else
               f"from {html.escape(basis['name'])}, {basis['d']:%b} {basis['d'].day}" if basis else "")
        return (f"{label} {mmss_range(fast, slow)}/mi grade-adjusted at {elev:,} ft ({src})"
                + (f" · HR ≤ {cap}" if cap else ""))

    wo_parts = []
    nxt = next(((d, plan[d]) for d in sorted(plan) if d >= today and plan[d]["type"] == "workout"
                and REP_RE.search(plan[d]["session"]) and d not in {w["d"] for w in wo}), None)
    if nxt:
        d, p = nxt
        el = 0 if "sea level" in p["session"].lower() else HOME_ELEV_FT
        wo_parts.append(f'<p class="note"><b>Next: {d:%a %b} {d.day}, {html.escape(p["session"])}</b>. Target: '
                        f'{target_txt(rep_target(p["session"], best, el, nr), el, best)}.</p>')
    for w in reversed(wo):
        b = fitness_basis(rc, efforts, w["d"])
        reps = w["reps"]
        n_on = sum(r["paceOk"] == "on" for r in reps)
        n_over = sum(r["hrOk"] is False for r in reps)
        summary = (f"Found {len(reps)} of {w['n']} reps" + ("" if len(reps) == w["n"] else
                   " (lap each rep with the lap button so they can be matched)")
                   + (f". {n_on} on target pace" if w["target"] and reps else "")
                   + (f", {n_over} over the HR cap" if w["target"] and w["target"][2] and reps else "") + ".")
        verdict = {"on": "on target", "fast": "fast", "slow": "slow", None: ""}
        body = "".join(
            f"<tr><td>{i}</td><td>{r['mi']:.2f}</td><td>{hms(r['secs'])}</td><td>{r['pace']}</td><td>{mmss(r['gap'])}</td>"
            f"<td>{r['hr'] or ''}</td><td>{r['max'] or ''}</td><td>{verdict[r['paceOk']]}</td>"
            f"<td>{'' if r['hrOk'] is None else 'under cap' if r['hrOk'] else 'over cap'}</td></tr>"
            for i, r in enumerate(reps, 1))
        wo_parts.append(
            f'<h3>{w["d"]:%a %b} {w["d"].day} · {html.escape(w["session"])}</h3>'
            f'<p class="note">Target: {target_txt(w["target"], w["elev"], b)}. {summary}</p>'
            + (f'<div class="scroll"><table class="t full"><thead><tr><th>Rep</th><th>Miles</th><th>Time</th><th>Pace</th>'
               f'<th>Grade-adj.</th><th>Avg HR</th><th>Max HR</th><th>Pace check</th><th>HR check</th></tr></thead>'
               f'<tbody>{body}</tbody></table></div>' if reps else ""))
    if not wo:
        wo_parts.append('<p class="note">No planned workouts with reps completed yet.</p>')
    wo_html = ('\n<section class="card"><h2>Workouts vs. targets</h2>\n'
               f'<p class="note">Reps are the laps closest to the planned distance (within {REP_DIST_TOL:.0%}), fastest first. '
               f'Pace is judged on grade-adjusted pace, within {REP_PACE_TOL} s/mi of the target range, which comes from '
               'current fitness at the time (Daniels threshold, 10K and interval paces) or the goal time, converted to '
               'the run\'s elevation.</p>\n' + "\n".join(wo_parts) + "\n</section>")

    # best efforts
    eff_html = ""
    if efforts:
        cut = today - timedelta(28)
        rows = []
        for m in sorted({e["meters"] for e in efforts}):
            at = [e for e in efforts if e["meters"] == m]
            recent = [e for e in at if e["d"] > cut]
            top_all = max(at, key=lambda e: e["sl"])
            top_28 = max(recent, key=lambda e: e["sl"]) if recent else None
            shown = ([("Last 28 days", top_28)] if top_28 else []) + ([("All data", top_all)] if top_all is not top_28 else [])
            if top_28 is top_all:
                shown = [("Both", top_28)]  # best of the last 28 days is also the best overall
            for i, (period, e) in enumerate(shown):
                net = "" if e["net"] is None else f"{e['net']:+,}".replace("-", "−")
                rows.append(
                    f"<tr><td>{effort_label(m) if i == 0 else ''}</td><td>{period}</td>"
                    f"<td>{hms(e['clock'])}</td><td>{hms(e['secs'])}</td><td>{mmss(e['secs'] / (m / M_PER_MI))}</td>"
                    f"<td>{e['d']:%b} {e['d'].day}</td><td>{ft(e['elev'])}</td><td>{net}</td>"
                    f"<td>{e['maxhr'] or ''}</td><td>{e['sl']:.1f}</td><td>{e['why'] or 'yes'}</td></tr>")
        eff_html = (
            '\n<section class="card"><h2>Best efforts</h2>\n'
            '<p class="note">Fastest grade-adjusted time over each distance inside any run, in the last 28 days and in all '
            'data ("Both" when one effort is best in both). Grade adjustment (Minetti) turns each stretch into its flat-ground '
            'equivalent, so a downhill stretch is slowed and a hilly one credited; the VDOT is on that time, at sea level, so '
            'efforts at different elevations compare fairly. These are training efforts, usually not all-out. One can stand '
            'in for a race only if it comes after your latest race or time trial and beats it (the race grade-adjusted too), '
            f'is at least {effort_label(EFFORT_MIN_M)}, has a net grade under {AET_MAX_GRADE:.0%}, and reaches LT ({LT} bpm).</p>\n'
            '<div class="scroll"><table class="t full"><thead><tr><th>Distance</th><th>Period</th><th>Time</th>'
            '<th>Grade-adj.</th><th>Pace (adj.)</th>'
            '<th>Date</th><th>Elevation</th><th>Net elev. (ft)</th><th>Max HR</th><th>VDOT (sea level)</th>'
            f'<th>Counts for race fitness</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>\n</section>')

    # fitness vs. goal (sea-level VDOT)
    goal = None
    if nr and nr["goal_secs"] and nr["elev"] is not None:
        per_run = {}  # best hard effort (max HR at LT or above) per run, from EFFORT_MIN_M up; races plot as races
        for e in efforts:
            k = (e["d"], e["start"])
            hard = e["maxhr"] is not None and e["maxhr"] >= LT and e["why"] != "race day"
            if hard and e["meters"] >= EFFORT_MIN_M - 1 and (k not in per_run or e["sl"] > per_run[k]["sl"]):
                per_run[k] = e
        by_month = {}  # then only the top few per calendar month, so easy-day efforts don't flatten the y-axis
        for e in per_run.values():
            by_month.setdefault((e["d"].year, e["d"].month), []).append(e)
        top = [e for es in by_month.values() for e in sorted(es, key=lambda e: -e["sl"])[:GOAL_EFFORTS_PER_MONTH]]
        if len(top) >= 4:  # drop slow outliers below Tukey's lower fence (Q1 - 1.5 IQR); fast ones are the signal
            q1, _, q3 = statistics.quantiles([e["sl"] for e in top], n=4, method="inclusive")
            top = [e for e in top if e["sl"] >= q1 - 1.5 * (q3 - q1)]
        goal = {"v": round(sl_vdot(nr["meters"], nr["goal_secs"], nr["elev"]), 2), "race": nr["date"].isoformat(),
                "name": nr["name"], "t": hms(nr["goal_secs"]),
                "races": [{"d": r["d"].isoformat(), "v": round(sl_vdot(r["meters"], r["secs"], src_elev(r)), 2),
                           "name": r["name"], "t": hms(r["secs"])} for r in rc],
                "efforts": [{"d": e["d"].isoformat(), "v": round(e["sl"], 2), "name": effort_label(e["meters"]),
                             "t": f"{hms(e['clock'])} ({hms(e['secs'])} grade-adjusted)", "why": e["why"]} for e in top],
                "basis": {"d": best["d"].isoformat(), "v": round(sl_vdot(best["meters"], best["secs"], src_elev(best)), 2)}
                if best else None}
        tt = next_time_trial(plan, today, nr["date"])
        if tt:
            goal["tt"] = {"d": tt[0].isoformat(), "name": tt[2]}
    goal_html = "" if not goal else (
        '\n<section class="card"><h2>Fitness vs. goal</h2>\n'
        f'<p class="note">Sea-level VDOT over time. The goal ({goal["t"]} at {ft(nr["elev"])}) needs a sea-level VDOT of {goal["v"]:.1f}. '
        'The dotted line is steady progress from current fitness to the goal on race day, the same path the checkpoint '
        f'uses. Training best efforts are the top {GOAL_EFFORTS_PER_MONTH} per month (best per run, '
        f'{effort_label(EFFORT_MIN_M)} and up, from runs that reached LT), leaving out slow outliers (below Tukey\'s '
        'lower fence, Q1 − 1.5 × IQR). They are usually not all-out, so they sit below race fitness.</p>\n'
        '<div class="chart" id="cGoal"></div>\n'
        '<div class="legend"><span><span class="dt" style="background:var(--s1)"></span>Race or time trial</span>'
        '<span><span class="dt" style="background:var(--card);box-shadow:inset 0 0 0 1.5px var(--muted)"></span>Training best effort</span>'
        '<span><span class="ln" style="background:var(--s2)"></span>Goal</span>'
        '<span><span class="ln dot"></span>On-track path</span></div>\n</section>')

    wk_rows = "".join(
        f"<tr><td>{date.fromisoformat(w['w']):%b} {date.fromisoformat(w['w']).day}{' (so far)' if w['partial'] else ''}</td>"
        f"<td>{w['mi']}</td><td>{w['runs']}</td><td>{w['load']}</td><td>{w['ctl'] if w['ctl'] is not None else ''}</td>"
        f"<td>{w['atl'] if w['atl'] is not None else ''}</td><td>{w['xh'] or ''}</td>"
        + "".join(f"<td>{z}%</td>" for z in w["z"]) + "</tr>"
        for w in reversed(weeks))

    data = {"daily": daily, "weeks": weeks, "eff": pts, "trend": trend, "trendDays": TREND_DAYS, "today": today.isoformat(),
            "aet": AET, "zones": [f"{n} {zone_range(lo, hi)}" for n, lo, hi in ZONES],
            "breaks": [1, 3],  # zone indexes below the LT1 (AeT) and LT2 (LT) boundaries
            "proj": [{k: x[k] for k in ("d", "ctl", "atl", "formPct", "acwr", "plan", "mi", "s", "xload")} for x in proj],
            "acwrSweet": ACWR_SWEET, "acwrDanger": ACWR_DANGER,
            "dec": decoupling_points(runs, plan, rc), "decLimit": DECOUPLE_LIMIT, "goal": goal}
    first = date.fromisoformat(daily[0]["d"])
    about = (
        f"<p><b>Load</b> is HR-zone-weighted minutes: each minute in Z1 counts 1, Z2 counts 2, and so on up to Z5 = 5. "
        f"Runs without real HR count {MANUAL_LOAD_PER_MIN:g} per minute.</p>\n"
        f"<p><b>Long-term / recent load</b> are exponentially weighted averages of daily load over {CTL_DAYS} and {ATL_DAYS} days "
        "(often called fitness and fatigue, CTL and ATL). They measure how much you train, not how fit you are: steady training "
        "keeps them flat even while you improve. Fitness gains show up in pace at AeT and VDOT. "
        f"History begins {first:%b} {first.day}, {first.year}, so both start at the average load of the first four weeks.</p>\n"
        "<p><b>Form</b> measures training fatigue only. Sleep, stress, heat, and illness aren't included, "
        "so read single runs with that in mind.</p>\n"
        f"<p><b>Pace at AeT</b> uses grade-adjusted pace (Minetti et al. 2002) from seconds with HR {EF_HR_BAND[0]}–{EF_HR_BAND[1]} bpm, "
        f"moving faster than walking, on grades under {AET_MAX_GRADE:.0%}, scaled to exactly {AET} bpm. It skips the first "
        f"{AET_WARMUP_S // 60} minutes (warm-up), everything after {AET_MAX_S // 60} minutes (cardiac drift), and everything after a hard bout, "
        f"when HR stays high for the rest of the run. A hard bout is {HARD_BOUT[0][1] // 60} minutes at {HARD_BOUT[0][0]} bpm or "
        f"{HARD_BOUT[1][1] // 60} at {HARD_BOUT[1][0]}, on an exponential curve through those points "
        f"(shorter the higher the HR), counted after the warm-up. Runs within one day per {RECOVERY_KM_PER_DAY:g} km of a race (Daniels' easy days "
        f"after racing: 3 days for a 10K) and trail runs (marked in notes.md; grade adjustment doesn't account for rough footing) "
        f"show as open circles and stay out of the trend. A run needs at least {EF_MIN_SECS // 60} minutes "
        f"of those seconds. The trend weights each run by how many seconds qualified over the past {TREND_DAYS} days (at least {TREND_MIN_RUNS} runs), and each "
        f"point is drawn at the center of its window, so the line stops about {TREND_DAYS // 2} days short of today. "
        f"Only runs within {EF_ELEV_TOL_FT:,} ft of {HOME_NAME} ({HOME_ELEV_FT:,} ft) count, so the number is a "
        f"{HOME_NAME}-altitude pace.</p>\n"
        f"<p><b>Zones</b> come from config.json: LT1 (AeT) {AET} bpm and LT2 (LT) {LT} bpm. "
        "The intensity chart puts wider breaks at those two thresholds.</p>\n"
        "<p><b>VDOT</b> is Jack Daniels' formula applied to your most recent race (races.csv, or a planned race) or time trial "
        "(a planned session named \"time trial\"). A training best effort replaces it when it comes later and has a higher "
        f"sea-level VDOT, is at least {effort_label(EFFORT_MIN_M)}, reaches LT ({LT} bpm) as a check on GPS glitches, and "
        f"has a net grade under {AET_MAX_GRADE:.0%}. That comparison uses grade-adjusted time (Minetti) for both the effort "
        "and the race, so hills count the same way in each; a winning effort's paces are flat-ground equivalents. "
        "Race results themselves show actual times. "
        "The next-race estimate uses that current fitness with no projected gains. "
        "Elevation comes from the run's own file.</p>\n"
        f"<p><b>Load ramp</b> is the acute:chronic workload ratio: load over the last {ACUTE_DAYS} days divided by the weekly "
        f"average over the last {CHRONIC_DAYS}. "
        '<a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC4789704">Gabbett (2016, Br J Sports Med 50:273)</a> calls '
        f"{ACWR_SWEET[0]:g}–{ACWR_SWEET[1]:g} the sweet spot and ≥ {ACWR_DANGER:g} the danger zone for injury. The ratio has been "
        "criticized as a predictor (Impellizzeri et al. 2020, Int J Sports Physiol Perform 15:907), so read it as a guide "
        "to how fast load is rising, not a risk score. It needs four weeks of history.</p>\n"
        "<p><b>Projection</b> uses plan.csv after today, with each planned mile worth your recent load per mile for that kind "
        f"of run (planned workout days, and before the plan, runs with ≥ {WORKOUT_Z5_PCT}% of the time in Z5, count as "
        "workouts). Planned hikes and XC skis (named in the session, with minutes in plan.csv's cross_min) count at your "
        "load per minute from recorded sessions of that kind or, until you have any, a default from config.json: "
        + ", ".join(f"{CROSS_NAMES[k].lower()} {v:g}" for k, v in CROSS_DEFAULT_RATE.items()) + " per minute. The hike "
        "default is the Z1 weight: the Pandolf load-carriage equation and Swain's %VO2max-to-%HRmax relation put a "
        "35-lb-pack hike on 0 to 10% grades at about 105 to 130 bpm (data/ref/README.md). Sea-level days and steady "
        "finishes aren't modeled.</p>\n"
        "<p><b>Cross-training</b> (hikes and XC skis recorded on the watch) adds HR-zone load the same way runs do, but no "
        "miles. HR load misses the muscle damage from long loaded descents, so expect to feel more tired after the Grand "
        "Canyon than form shows.</p>\n"
        "<p><b>Decoupling</b> (Pa:HR) compares grade-adjusted pace per heartbeat in the first and second halves of a run, "
        f"after the {AET_WARMUP_S // 60}-minute warm-up, counting running seconds on grades under {AET_MAX_GRADE:.0%}. "
        f"Below {DECOUPLE_LIMIT:g}% means your aerobic endurance holds for that long "
        '(<a href="https://www.trainingpeaks.com/learn/articles/aerobic-endurance-and-decoupling/">Friel, TrainingPeaks</a>). '
        f"Only steady runs count (planned easy or long days without a steady finish, or before the plan, runs under "
        f"{WORKOUT_Z5_PCT}% in Z5), with at least {DECOUPLE_MIN_S // 60} minutes of qualifying running.</p>\n"
        "<p><b>Workout reps</b> come from laps, so press lap at the start and end of each rep. Grade-adjusted lap pace uses "
        "the same Minetti model as pace at AeT.</p>\n"
        "<p><b>Altitude</b> is converted two published ways and shown as a range. "
        '<a href="https://news.vdoto2.com/2011/03/ask-a-coach-how-do-you-adjust-threshold-pace-at-altitude/">Daniels</a>: '
        f"paces slow {spm} s/mi per 1,000 ft above {DANIELS_FLOOR_FT:,} ft (15–18 s/mi at 7,000 ft). "
        '<a href="https://www.ustfccca.org/assets/ncaa-info/2009-outdoor-alt-adjust.pdf">NCAA 2009 altitude tables</a>: '
        "% added by venue altitude (0–7,703 ft) for the mile, 5K and 10K, interpolated by altitude and distance, and held at "
        "the nearest of those distances outside that span. Neither accounts for acclimatization.</p>")
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("fitness.html"), "TILES": "".join(tiles), "RACE": race_html,
            "AHEAD": ahead_html, "WORKOUTS": wo_html, "EFFORTS": eff_html, "GOAL": goal_html,
            "DEC_LIMIT": f"{DECOUPLE_LIMIT:g}",
            "WEEK_ROWS": wk_rows, "DATA": json.dumps(data, separators=(",", ":")),
            "NEXT": next_html, "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}",
            "HOME_ELEV": f"{HOME_ELEV_FT:,}", "TREND_DAYS": str(TREND_DAYS), "CTL": str(CTL_DAYS), "ATL": str(ATL_DAYS), "FRESH": str(FRESH), "TIRED": str(TIRED).replace("-", "−"), "AET": str(AET), "ABOUT": about,
            "ZLEG": "".join(f'<span><i class="z{i + 1}"></i>{n} {zone_range(lo, hi)}</span>' for i, (n, lo, hi) in enumerate(ZONES))
                    + '<span><span class="ln brk"></span>LT1 / LT2 break</span>'}
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs" / "fitness.html").write_text(page, encoding="utf-8")
    print("Wrote docs/fitness.html")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Running Fitness</title>
<style>
%%BASE_CSS%%
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin: 4px 0 20px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.tl { font-size: 12px; color: var(--muted); }
.tv { font-size: 26px; font-weight: 600; margin-top: 2px; letter-spacing: -0.01em; }
.td, .ts { font-size: 12px; color: var(--muted); margin-top: 2px; }
.td .up { color: var(--ok); } .td .down { color: var(--tired); } .td .flat { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.card h2 { font-size: 16px; margin: 0 0 2px; }
.card h3 { font-size: 13px; margin: 8px 0 4px; color: var(--muted); font-weight: 600; }
.note { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.legend { margin: 6px 0 0; }
.legend .ln { display: inline-block; width: 16px; height: 2px; vertical-align: 3px; margin-right: 5px; border-radius: 1px; }
.legend .dt { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
.chart { position: relative; height: 240px; margin-top: 6px; touch-action: pan-y; }
.range { position: sticky; top: 0; z-index: 5; background: var(--bg); display: flex; flex-wrap: wrap; align-items: center;
  gap: 6px 8px; padding: 8px 0; margin-bottom: 6px; }
.range button { font: inherit; font-size: 13px; color: var(--ink); background: var(--card); border: 1px solid var(--line);
  border-radius: 6px; padding: 5px 11px; cursor: pointer; }
.range button:disabled { opacity: .4; cursor: default; }
.range .seg { display: inline-flex; } .range .seg button { border-radius: 0; margin-left: -1px; }
.range .seg button:first-child { border-radius: 6px 0 0 6px; margin-left: 0; } .range .seg button:last-child { border-radius: 0 6px 6px 0; }
.range .seg button[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
#rLabel { min-width: 120px; text-align: center; font-variant-numeric: tabular-nums; font-size: 13px; }
.chart svg { display: block; width: 100%; height: 100%; overflow: visible; }
.chart.short { height: 150px; }
.legend .ln.dash { background: repeating-linear-gradient(90deg, var(--muted) 0 4px, transparent 4px 7px); }
.legend .ln.dot { background: repeating-linear-gradient(90deg, var(--s2) 0 2px, transparent 2px 5px); }
.legend .band { display: inline-block; width: 12px; height: 10px; border-radius: 2px; margin-right: 5px; vertical-align: -1px; }
.band.ok, .bandok { fill: var(--ok); background: var(--ok); opacity: .14; }
.band.bad, .bandbad { fill: var(--tired); background: var(--tired); opacity: .14; }
.legend .band.ok, .legend .band.bad { opacity: .35; }
.today { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 2 3; }
.grid line { stroke: var(--line); stroke-width: 1; }
.axis text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.zero { stroke: var(--muted); stroke-width: 1; }
.brk { stroke: var(--ink); stroke-width: 1.5; }
.legend .ln.brk { background: var(--ink); }
.guide { stroke: var(--muted); stroke-width: 1; }
.lbl { fill: var(--ink); font-size: 11px; font-weight: 600; }
.hit { fill: transparent; cursor: crosshair; }
.hl { fill: var(--ink); opacity: .06; }
#tip { position: fixed; z-index: 20; pointer-events: none; background: var(--card); color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font-size: 12px; line-height: 1.45;
  box-shadow: 0 4px 14px rgba(0,0,0,.18); display: none; max-width: 240px; font-variant-numeric: tabular-nums; }
#tip b { font-weight: 600; }
#tip i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 24px; }
table.t { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
table.t td, table.t th { padding: 3px 14px 3px 0; text-align: left; }
table.t td:last-child { text-align: right; padding-right: 0; }
table.t.full td, table.t.full th { text-align: right; padding: 3px 8px; }
table.t.full td:first-child, table.t.full th:first-child { text-align: left; padding-left: 0; }
table.t th { color: var(--muted); font-weight: 500; }
details { margin-top: 10px; }
summary { cursor: pointer; color: var(--muted); font-size: 12px; }
.scroll { overflow-x: auto; }
.z1 { background: var(--z1); fill: var(--z1); } .z2 { background: var(--z2); fill: var(--z2); }
.z3 { background: var(--z3); fill: var(--z3); } .z4 { background: var(--z4); fill: var(--z4); }
.z5 { background: var(--z5); fill: var(--z5); }
.next .kv td:first-child { color: var(--muted); padding-right: 18px; } .next .kv td:last-child { text-align: left; }
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px 24px; margin-top: 8px; }
.panel h3 { margin: 0 0 2px; } .panel .big { font-size: 24px; font-weight: 600; margin: 2px 0 4px; } .panel .big .dim { font-size: 13px; font-weight: 400; }
.panel .kv td { padding: 2px 12px 2px 0; } .panel .note { margin: 6px 0 0; }
.est { display: flex; gap: 28px; align-items: flex-start; } .est small { font-size: 13px; font-weight: 400; color: var(--muted); margin-left: 2px; }
.next .note b { color: var(--ink); } .dim { color: var(--muted); font-size: 12px; }
.about p { font-size: 13px; color: var(--muted); margin: 6px 0; }
.about b { color: var(--ink); font-weight: 600; }
@media (max-width: 760px) { .chart { height: 200px; } .tv { font-size: 22px; } }
</style>
</head>
<body>
<main>
<header>
  %%TABS%%
  <h1>Running Fitness</h1>
  <p class="goal">Built from completed runs only.</p>
</header>
<div class="tiles">%%TILES%%</div>
%%NEXT%%

<div class="range" role="group" aria-label="Chart time range">
  <div class="seg" id="rSpan"><button data-span="30">30 days</button><button data-span="90">90 days</button><button data-span="0">All</button></div>
  <button id="rPrev" aria-label="Earlier">‹</button><span id="rLabel"></span><button id="rNext" aria-label="Later">›</button>
  <button id="rLatest">Latest</button>
  <span class="dim">Drag a chart to scroll</span>
</div>
<section class="card"><h2>Weekly mileage</h2>
<p class="note">Mon–Sun totals, with the 28-day rolling average (mi/wk).</p>
<div class="chart" id="cMiles"></div>
<div class="legend"><span><i style="background:var(--s1)"></i>Weekly miles</span><span><span class="ln" style="background:var(--s2)"></span>28-day average</span></div>
</section>

<section class="card"><h2>Training load</h2>
<p class="note">Long-term load is the %%CTL%%-day average daily load and recent load the %%ATL%%-day average. Form = long-term − recent. These track how much you train; fitness gains show up in pace at AeT and VDOT below.</p>
<div class="chart" id="cLoad"></div>
<div class="legend"><span><span class="ln" style="background:var(--s1)"></span>Long-term load (%%CTL%%-day)</span><span><span class="ln" style="background:var(--s2)"></span>Recent load (%%ATL%%-day)</span></div>
</section>

<section class="card"><h2>Aerobic efficiency: pace at %%AET%% bpm, at %%HOME_ELEV%% ft</h2>
<p class="note">Estimated from grade-adjusted easy running near AeT, after the warm-up and before any hard effort. Faster is up. Dots are single runs, colored by form going into that day, so a slow dot on a tired day isn't a sign of lost fitness. Open circles are runs in the days after a race or on trails, left out of the trend.</p>
<div class="chart" id="cEff"></div>
<div class="legend"><span><span class="ln" style="background:var(--s7)"></span>%%TREND_DAYS%%-day trend</span><span><span class="dt" style="background:var(--fresh)"></span>Fresh (form &gt; +%%FRESH%%%)</span><span><span class="dt" style="background:var(--neutral)"></span>Neutral</span><span><span class="dt" style="background:var(--tired)"></span>Tired (form &lt; %%TIRED%%%)</span><span><span class="dt" style="background:var(--card);box-shadow:inset 0 0 0 1.5px var(--muted)"></span>Race recovery or trail</span></div>
</section>

<section class="card"><h2>Aerobic durability: decoupling on steady runs</h2>
<p class="note">How much grade-adjusted pace per heartbeat falls from the first half of a steady run to the second. Under %%DEC_LIMIT%%% (below the line) means your aerobic endurance holds for that long; watch it on the long runs as they grow. Open circles are trail runs.</p>
<div class="chart" id="cDec"></div>
<div class="legend"><span><span class="dt" style="background:var(--s1)"></span>Steady run (dot size = minutes analyzed)</span><span><span class="ln" style="background:var(--muted)"></span>%%DEC_LIMIT%%% line</span></div>
</section>

<section class="card"><h2>Intensity by week</h2>
<p class="note">Share of time in each HR zone. Wider breaks mark LT1 (AeT) and LT2 (LT).</p>
<div class="chart" id="cZones"></div>
<div class="legend">%%ZLEG%%</div>
</section>

%%AHEAD%%
%%WORKOUTS%%
%%GOAL%%
%%EFFORTS%%
%%RACE%%

<section class="card"><details><summary>Weekly data table</summary><div class="scroll"><table class="t full">
<thead><tr><th>Week of</th><th>Miles</th><th>Runs</th><th>Load</th><th>Long-term load</th><th>Recent load</th><th>Cross-training (h)</th><th>Z1</th><th>Z2</th><th>Z3</th><th>Z4</th><th>Z5</th></tr></thead>
<tbody>%%WEEK_ROWS%%</tbody></table></div></details></section>

<section class="card about"><h2>How these are calculated</h2>
%%ABOUT%%
</section>
<footer>Updated %%BUILT%%.</footer>
</main>
<div id="tip"></div>
<script>
(function () {
  var D = %%DATA%%;
  var NS = "http://www.w3.org/2000/svg";
  var tip = document.getElementById("tip");
  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function el(tag, a, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in a) e.setAttribute(k, a[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function day(iso) { var p = iso.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]) / 864e5; }
  function fmtDate(iso) { var p = iso.split("-"); return MON[+p[1] - 1] + " " + (+p[2]); }
  function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
  function nice(v) { var p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }
  function showTip(html, ev) {
    tip.innerHTML = html; tip.style.display = "block";
    var w = tip.offsetWidth, h = tip.offsetHeight, x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.style.display = "none"; }
  function sw(v) { return '<i style="background:var(' + v + ')"></i>'; }

  // Frame: time x-axis, linear y-axis with hairline grid
  function frame(id, x0, x1, y0, y1, yTicks, yFmt, invert) {
    var box = document.getElementById(id); box.innerHTML = "";
    var W = box.clientWidth, H = box.clientHeight, m = { l: 40, r: 14, t: 10, b: 22 };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H }, box);
    var X = function (d) { return m.l + (d - x0) / (x1 - x0) * (W - m.l - m.r); };
    var Y = function (v) { var f = (v - y0) / (y1 - y0); if (invert) f = 1 - f; return H - m.b - f * (H - m.t - m.b); };
    var g = el("g", { "class": "grid" }, svg), ax = el("g", { "class": "axis" }, svg);
    yTicks.forEach(function (v) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
      el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, ax).textContent = yFmt(v);
    });
    var cp = el("clipPath", { id: "clip-" + id }, el("defs", {}, svg));
    el("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H - m.b }, cp);
    var plot = el("g", { "clip-path": "url(#clip-" + id + ")" }, svg);
    return { svg: plot, root: svg, X: X, Y: Y, W: W, H: H, m: m, ax: ax };
  }
  function monthTicks(f, x0, x1) {
    if (x1 - x0 <= 70) {  // weekly ticks on Mondays (day 0 = Thu Jan 1 1970, so Monday = 4 mod 7)
      var every = (f.X(7) - f.X(0)) < 56 ? 14 : 7;
      for (var d0 = Math.ceil((x0 - 4) / 7) * 7 + 4; d0 <= x1; d0 += every) {
        var t = new Date(d0 * 864e5);
        el("text", { x: f.X(d0), y: f.H - 6, "text-anchor": "middle" }, f.ax).textContent = MON[t.getUTCMonth()] + " " + t.getUTCDate();
      }
      return;
    }
    var d = new Date(x0 * 864e5); d = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) / 864e5;
    while (d <= x1) {
      var dt = new Date(d * 864e5);
      el("text", { x: f.X(d), y: f.H - 6, "text-anchor": "middle" }, f.ax).textContent = MON[dt.getUTCMonth()];
      dt = Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 1) / 864e5; d = dt;
    }
  }
  function linePath(f, pts) { return pts.map(function (p, i) { return (i ? "L" : "M") + f.X(p[0]).toFixed(1) + "," + f.Y(p[1]).toFixed(1); }).join(""); }
  function colPath(x, w, yTop, yBase) { // 4px rounded top, square base
    var r = Math.min(4, w / 2, Math.max(0, yBase - yTop));
    return "M" + x + "," + yBase + "V" + (yTop + r) + "Q" + x + "," + yTop + " " + (x + r) + "," + yTop +
      "H" + (x + w - r) + "Q" + (x + w) + "," + yTop + " " + (x + w) + "," + (yTop + r) + "V" + yBase + "Z";
  }

  var FIRST = day(D.daily[0].d), LAST = day(D.daily[D.daily.length - 1].d) + 1;
  var span = 30, vEnd = LAST, x0, x1;  // visible window [x0, x1) in days
  function setWindow() {
    var sp = span === 0 ? LAST - FIRST : Math.min(span, LAST - FIRST);
    vEnd = Math.max(FIRST + sp, Math.min(LAST, vEnd));
    x0 = vEnd - sp; x1 = vEnd;
    document.getElementById("rLabel").textContent = fmtDay(x0) + " – " + fmtDay(x1 - 1);
    document.getElementById("rPrev").disabled = x0 <= FIRST;
    document.getElementById("rNext").disabled = x1 >= LAST;
    document.getElementById("rLatest").disabled = x1 >= LAST;
    Array.prototype.forEach.call(document.querySelectorAll("#rSpan button"), function (b) {
      b.setAttribute("aria-pressed", String(+b.dataset.span === span)); });
  }
  function fmtDay(d) { var t = new Date(d * 864e5); return MON[t.getUTCMonth()] + " " + t.getUTCDate(); }

  function miles() {
    var mx = Math.max.apply(null, D.weeks.map(function (w) { return w.mi; }).concat(D.daily.map(function (x) { return x.r28 || 0; })));
    var top = Math.ceil(mx / 10) * 10, ticks = []; for (var v = 0; v <= top; v += 10) ticks.push(v);
    // at the latest date, pad right so the current (partial) week bar, drawn mid-week, stays visible
    var f = frame("cMiles", x0, x1 + (x1 >= LAST ? 4 : 0), 0, top, ticks, function (v) { return v; });
    monthTicks(f, x0, x1);
    var band = f.X(7) - f.X(0), bw = Math.min(24, band - 6);
    D.weeks.forEach(function (w) {
      var cx = f.X(day(w.w) + 3.5);
      el("path", { d: colPath(cx - bw / 2, bw, f.Y(w.mi), f.Y(0)), fill: "var(--s1)", opacity: w.partial ? .45 : 1 }, f.svg);
    });
    var pts = D.daily.filter(function (x) { return x.r28 != null; }).map(function (x) { return [day(x.d) + .5, x.r28]; });
    el("path", { d: linePath(f, pts), fill: "none", stroke: "var(--s2)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
    var last = pts[pts.length - 1];
    if (last) el("circle", { cx: f.X(last[0]), cy: f.Y(last[1]), r: 4, fill: "var(--s2)", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
    var hl = el("rect", { "class": "hl", y: f.m.t, height: f.H - f.m.t - f.m.b, width: band, display: "none" }, f.svg);
    D.weeks.forEach(function (w) {
      var xs = f.X(day(w.w)), end = D.daily.filter(function (x) { return day(x.d) <= day(w.w) + 6; }).pop();
      var hit = el("rect", { "class": "hit", x: xs, y: f.m.t, width: band, height: f.H - f.m.t - f.m.b }, f.svg);
      hit.addEventListener("pointermove", function (ev) {
        hl.setAttribute("x", xs); hl.removeAttribute("display");
        showTip("<b>Week of " + fmtDate(w.w) + (w.partial ? " (so far)" : "") + "</b><br>" + sw("--s1") + w.mi + " mi · " + w.runs + " runs" +
          (end && end.r28 != null ? "<br>" + sw("--s2") + "28-day avg " + end.r28 + " mi/wk" : ""), ev);
      });
      hit.addEventListener("pointerleave", function () { hl.setAttribute("display", "none"); hideTip(); });
    });
  }

  function load() {
    // y-axis fits the data (all dates, so it stays fixed while panning); lines don't need a zero baseline
    var vals = D.daily.map(function (x) { return Math.max(x.ctl, x.atl); }), lows = D.daily.map(function (x) { return Math.min(x.ctl, x.atl); });
    var mx = Math.max.apply(null, vals), mn = Math.min.apply(null, lows);
    var step = nice((mx - mn) / 4), bot = Math.floor(mn / step) * step, top = Math.ceil(mx / step) * step, ticks = [];
    for (var v = bot; v <= top; v += step) ticks.push(v);
    var f = frame("cLoad", x0, x1, bot, top, ticks, function (v) { return v; });
    monthTicks(f, x0, x1);
    [["atl", "--s2"], ["ctl", "--s1"]].forEach(function (s) {
      var pts = D.daily.map(function (x) { return [day(x.d) + .5, x[s[0]]]; });
      el("path", { d: linePath(f, pts), fill: "none", stroke: "var(" + s[1] + ")", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
      var l = pts[pts.length - 1];
      el("circle", { cx: f.X(l[0]), cy: f.Y(l[1]), r: 4, fill: "var(" + s[1] + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
    });
    var guide = el("line", { "class": "guide", y1: f.m.t, y2: f.H - f.m.b, display: "none" }, f.svg);
    var dots = ["--s1", "--s2"].map(function (c) { return el("circle", { r: 4, fill: "var(" + c + ")", stroke: "var(--card)", "stroke-width": 2, display: "none" }, f.svg); });
    var hit = el("rect", { "class": "hit", x: f.m.l, y: f.m.t, width: f.W - f.m.l - f.m.r, height: f.H - f.m.t - f.m.b }, f.svg);
    hit.addEventListener("pointermove", function (ev) {
      var r = f.svg.getBoundingClientRect(), px = (ev.clientX - r.left) * f.W / r.width;
      var dd = x0 + (px - f.m.l) / (f.W - f.m.l - f.m.r) * (x1 - x0);
      var i = Math.max(0, Math.min(D.daily.length - 1, Math.floor(dd - FIRST))), x = D.daily[i], cx = f.X(day(x.d) + .5);
      guide.setAttribute("x1", cx); guide.setAttribute("x2", cx); guide.removeAttribute("display");
      dots[0].setAttribute("cx", cx); dots[0].setAttribute("cy", f.Y(x.ctl)); dots[0].removeAttribute("display");
      dots[1].setAttribute("cx", cx); dots[1].setAttribute("cy", f.Y(x.atl)); dots[1].removeAttribute("display");
      showTip("<b>" + fmtDate(x.d) + "</b>" + (x.load ? " · load " + x.load + (x.xload ? " (cross-training " + x.xload + ")" : "") : "") + "<br>" + sw("--s1") + "Long-term load " + x.ctl.toFixed(0) +
        "<br>" + sw("--s2") + "Recent load " + x.atl.toFixed(0) + "<br>Form " + (x.form > 0 ? "+" : "") + x.form.toFixed(0) +
        " (" + (x.formPct > 0 ? "+" : "") + x.formPct.toFixed(0) + "% going in)", ev);
    });
    hit.addEventListener("pointerleave", function () { guide.setAttribute("display", "none"); dots.forEach(function (d) { d.setAttribute("display", "none"); }); hideTip(); });
  }

  function eff() {
    if (!D.eff.length) return;
    var ps = D.eff.map(function (p) { return p.p; }).concat(D.trend.map(function (t) { return t.p; }));
    var lo = Math.floor((Math.min.apply(null, ps) - 5) / 30) * 30, hi = Math.ceil((Math.max.apply(null, ps) + 5) / 30) * 30, ticks = [];
    for (var v = lo; v <= hi; v += 30) ticks.push(v);
    var f = frame("cEff", x0, x1, lo, hi, ticks, mmss, true);
    monthTicks(f, x0, x1);
    var tp = D.trend.map(function (t) { return [day(t.d) + 1 - D.trendDays / 2, t.p]; });  // plot each value at the center of its window
    if (tp.length) el("path", { d: linePath(f, tp), fill: "none", stroke: "var(--s7)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
    D.eff.forEach(function (p) {
      var cx = f.X(day(p.d) + .5), cy = f.Y(p.p);
      var c = p.why ? el("circle", { cx: cx, cy: cy, r: 4, fill: "var(--card)", stroke: "var(--muted)", "stroke-width": 1.5 }, f.svg)
                    : el("circle", { cx: cx, cy: cy, r: 4.5, fill: "var(--" + p.cls + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
      var hit = el("circle", { "class": "hit", cx: cx, cy: cy, r: 11 }, f.svg);
      hit.addEventListener("pointermove", function (ev) {
        c.setAttribute("r", 6);
        showTip("<b>" + fmtDate(p.d) + "</b> · " + p.mi.toFixed(1) + " mi<br>" + mmss(p.p) + "/mi at " + D.aet + " bpm<br>" +
          Math.round(p.n / 60) + " min of qualifying running<br>" + sw("--" + p.cls) + "Form going in " + (p.formPct > 0 ? "+" : "") + p.formPct.toFixed(0) + "%" +
          (p.why ? "<br>" + p.why.charAt(0).toUpperCase() + p.why.slice(1) + " (not in trend)" : ""), ev);
      });
      hit.addEventListener("pointerleave", function () { c.setAttribute("r", p.why ? 4 : 4.5); hideTip(); });
    });
    if (tp.length) {
      var l = tp[tp.length - 1];
      el("circle", { cx: f.X(l[0]), cy: f.Y(l[1]), r: 4, fill: "var(--s7)", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
      var t = el("text", { "class": "lbl", x: f.X(l[0]) - 8, y: f.Y(l[1]) - 10, "text-anchor": "end" }, f.svg);
      t.textContent = mmss(l[1]) + "/mi";
    }
  }

  function zones() {
    var ws = D.weeks.filter(function (w) { return w.hrH > 0 && day(w.w) + 7 > x0 && day(w.w) < x1; });
    if (!ws.length) { document.getElementById("cZones").innerHTML = ""; return; }
    var box = document.getElementById("cZones"); box.innerHTML = "";
    var W = box.clientWidth, H = box.clientHeight, m = { l: 40, r: 14, t: 10, b: 22 };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H }, box);
    var Y = function (v) { return H - m.b - v / 100 * (H - m.t - m.b); };
    var g = el("g", { "class": "grid" }, svg), ax = el("g", { "class": "axis" }, svg);
    [0, 50, 100].forEach(function (v) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
      el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, ax).textContent = v + "%";
    });
    var band = (W - m.l - m.r) / ws.length, bw = Math.min(24, band - 6);
    ws.forEach(function (w, i) {
      var cx = m.l + band * (i + .5), acc = 0, top = 0, prev = -1;
      w.z.forEach(function (z, k) { if (z > 0) top = k; });
      w.z.forEach(function (z, k) {
        if (z <= 0) return;
        var yb = Y(acc), yt = Y(acc + z);
        // 2px surface gap between segments; a wider gap and a rule where a threshold (LT1/LT2) is crossed
        var isBreak = prev >= 0 && D.breaks.some(function (b) { return prev <= b && b < k; });
        var gap = prev < 0 ? 0 : isBreak ? 6 : 2;
        if (isBreak) el("line", { "class": "brk", x1: cx - bw / 2 - 5, x2: cx + bw / 2 + 5, y1: yb - gap / 2, y2: yb - gap / 2 }, svg);
        if (yb - gap - yt >= 1) {
          var d = k === top ? colPath(cx - bw / 2, bw, yt, yb - gap) : "M" + (cx - bw / 2) + "," + (yb - gap) + "V" + yt + "H" + (cx + bw / 2) + "V" + (yb - gap) + "Z";
          el("path", { d: d, "class": "z" + (k + 1) }, svg);
        }
        acc += z; prev = k;
      });
      el("text", { x: cx, y: H - 6, "text-anchor": "middle" }, ax).textContent = fmtDate(w.w);
      var hl = el("rect", { "class": "hl", x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b, display: "none" }, svg);
      var hit = el("rect", { "class": "hit", x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b }, svg);
      hit.addEventListener("pointermove", function (ev) {
        hl.removeAttribute("display");
        var b1 = D.breaks[0], b2 = D.breaks[1], sum = function (a, b) { return w.z.slice(a, b).reduce(function (s, x) { return s + x; }, 0); };
        showTip("<b>Week of " + fmtDate(w.w) + (w.partial ? " (so far)" : "") + "</b> · " + w.hrH + " h<br>" +
          "Below LT1 " + sum(0, b1 + 1) + "% · LT1–LT2 " + sum(b1 + 1, b2 + 1) + "% · Above LT2 " + sum(b2 + 1) + "%<br>" +
          D.zones.map(function (n, k) { return sw("--z" + (k + 1)) + n + ": " + w.z[k] + "%"; }).join("<br>"), ev);
      });
      hit.addEventListener("pointerleave", function () { hl.setAttribute("display", "none"); hideTip(); });
    });
    // thin labels on narrow screens
    if (band < 44) Array.prototype.forEach.call(ax.querySelectorAll("text"), function (t, i) { if (i >= 3 && (i - 3) % 2) t.textContent = ""; });
  }

  function dot(f, cx, cy, r, filled, color) {
    return filled ? el("circle", { cx: cx, cy: cy, r: r, fill: "var(" + color + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg)
                  : el("circle", { cx: cx, cy: cy, r: r - .5, fill: "var(--card)", stroke: "var(--muted)", "stroke-width": 1.5 }, f.svg);
  }
  function hover(f, mark, html, r) {  // tooltip on a dot, with a hit target bigger than the mark
    var hit = el("circle", { "class": "hit", cx: mark.getAttribute("cx"), cy: mark.getAttribute("cy"), r: 11 }, f.svg);
    hit.addEventListener("pointermove", function (ev) { mark.setAttribute("r", r + 2); showTip(html, ev); });
    hit.addEventListener("pointerleave", function () { mark.setAttribute("r", r); hideTip(); });
  }
  function hline(f, v, cls) { return el("line", { "class": cls, x1: f.m.l, x2: f.W - f.m.r, y1: f.Y(v), y2: f.Y(v) }, f.svg); }
  function vline(f, d, cls) { return el("line", { "class": cls, x1: f.X(d), x2: f.X(d), y1: f.m.t, y2: f.H - f.m.b }, f.svg); }

  function dec() {
    var box = document.getElementById("cDec");
    if (!D.dec.length) { box.innerHTML = '<p class="note">No steady run with enough qualifying running yet.</p>'; return; }
    var vs = D.dec.map(function (p) { return p.pct; }).concat([0, D.decLimit]);
    var lo = Math.floor(Math.min.apply(null, vs) / 5) * 5, hi = Math.ceil((Math.max.apply(null, vs) + 1) / 5) * 5, ticks = [];
    for (var v = lo; v <= hi; v += 5) ticks.push(v);
    var f = frame("cDec", x0, x1, lo, hi, ticks, function (v) { return v + "%"; });
    monthTicks(f, x0, x1);
    hline(f, 0, "zero");
    hline(f, D.decLimit, "brk");
    D.dec.forEach(function (p) {
      var r = 4 + Math.min(4, Math.max(0, (p.min - 45) / 20));
      var c = dot(f, f.X(day(p.d) + .5), f.Y(p.pct), r, !p.trail, "--s1");
      hover(f, c, "<b>" + fmtDate(p.d) + "</b> · " + p.mi.toFixed(1) + " mi" + (p.trail ? " · trail" : "") + "<br>Decoupling " +
        (p.pct > 0 ? "+" : "") + p.pct.toFixed(1) + "%<br>" + p.min + " min analyzed", r);
    });
  }

  // projection to race day: its own fixed window, from 6 weeks back through race day
  function projWindow() {
    var a = Math.max(day(D.proj[0].d), day(D.today) - 42), b = day(D.proj[D.proj.length - 1].d) + 1;
    return [a, b, D.proj.filter(function (x) { return day(x.d) >= a; })];
  }
  function isFuture(x) { return x.plan || x.d > D.today; }
  function splitLine(f, pts, key, color) {  // solid through today, dashed after
    var k = pts.findIndex(isFuture); if (k < 0) k = pts.length;
    var past = pts.slice(0, k), fut = pts.slice(Math.max(0, k - 1));
    var xy = function (x) { return [day(x.d) + .5, x[key]]; };
    if (past.length) el("path", { d: linePath(f, past.map(xy)), fill: "none", stroke: "var(" + color + ")", "stroke-width": 2, "stroke-linejoin": "round" }, f.svg);
    if (fut.length > 1) el("path", { d: linePath(f, fut.map(xy)), fill: "none", stroke: "var(" + color + ")", "stroke-width": 2, "stroke-dasharray": "5 4" }, f.svg);
  }
  function projHover(f, pts, html, keys) {
    var guide = el("line", { "class": "guide", y1: f.m.t, y2: f.H - f.m.b, display: "none" }, f.svg);
    var dots = keys.map(function (k) { return el("circle", { r: 4, fill: "var(" + k[1] + ")", stroke: "var(--card)", "stroke-width": 2, display: "none" }, f.svg); });
    var hit = el("rect", { "class": "hit", x: f.m.l, y: f.m.t, width: f.W - f.m.l - f.m.r, height: f.H - f.m.t - f.m.b }, f.svg);
    hit.addEventListener("pointermove", function (ev) {
      var r = f.svg.getBoundingClientRect(), px = (ev.clientX - r.left) * f.W / r.width;
      var a = day(pts[0].d), dd = Math.floor(a + (px - f.m.l) / (f.W - f.m.l - f.m.r) * (day(pts[pts.length - 1].d) + 1 - a));
      var x = pts[Math.max(0, Math.min(pts.length - 1, dd - a))], cx = f.X(day(x.d) + .5);
      guide.setAttribute("x1", cx); guide.setAttribute("x2", cx); guide.removeAttribute("display");
      keys.forEach(function (k, i) {
        if (x[k[0]] == null) { dots[i].setAttribute("display", "none"); return; }
        dots[i].setAttribute("cx", cx); dots[i].setAttribute("cy", f.Y(x[k[0]])); dots[i].removeAttribute("display");
      });
      showTip(html(x), ev);
    });
    hit.addEventListener("pointerleave", function () { guide.setAttribute("display", "none"); dots.forEach(function (d) { d.setAttribute("display", "none"); }); hideTip(); });
  }
  function projTip(x) {
    return "<b>" + fmtDate(x.d) + "</b>" + (isFuture(x) ? " · planned" : "") + (x.s ? "<br>" + x.s : "") +
      (x.xload ? "<br>Cross-training load " + x.xload : "") +
      "<br>" + sw("--s1") + "Long-term load " + x.ctl.toFixed(0) + "<br>" + sw("--s2") + "Recent load " + x.atl.toFixed(0) +
      "<br>Form going in " + (x.formPct > 0 ? "+" : "") + x.formPct.toFixed(0) + "%" +
      (x.acwr != null ? "<br>Load ramp " + x.acwr.toFixed(2) : "");
  }
  function proj() {
    if (!D.proj.length) return;
    var w = projWindow(), a = w[0], b = w[1], pts = w[2];
    var vals = [].concat.apply([], pts.map(function (x) { return [x.ctl, x.atl]; }));
    var mx = Math.max.apply(null, vals), mn = Math.min.apply(null, vals);
    var step = nice((mx - mn) / 4), bot = Math.floor(mn / step) * step, top = Math.ceil(mx / step) * step, ticks = [];
    for (var v = bot; v <= top; v += step) ticks.push(v);
    var f = frame("cProj", a, b, bot, top, ticks, function (v) { return v; });
    monthTicks(f, a, b);
    vline(f, day(D.today) + 1, "today");
    splitLine(f, pts, "atl", "--s2"); splitLine(f, pts, "ctl", "--s1");
    var l = pts[pts.length - 1];
    el("text", { "class": "lbl", x: f.X(day(l.d) + .5) - 6, y: f.m.t + 10, "text-anchor": "end" }, f.svg).textContent = "Race";
    vline(f, day(l.d) + .5, "guide");
    projHover(f, pts, projTip, [["ctl", "--s1"], ["atl", "--s2"]]);
  }
  function acwr() {
    if (!D.proj.length) return;
    var w = projWindow(), a = w[0], b = w[1], pts = w[2].filter(function (x) { return x.acwr != null; });
    if (!pts.length) return;
    var vs = pts.map(function (x) { return x.acwr; });
    var lo = Math.min(0.5, Math.floor(Math.min.apply(null, vs) * 4) / 4), hi = Math.max(1.75, Math.ceil(Math.max.apply(null, vs) * 4) / 4), ticks = [];
    for (var v = lo; v <= hi + 1e-9; v += 0.25) if (Math.abs(v * 2 - Math.round(v * 2)) < 1e-9) ticks.push(v);
    var f = frame("cAcwr", a, b, lo, hi, ticks, function (v) { return v.toFixed(1); });
    monthTicks(f, a, b);
    el("rect", { "class": "bandok", x: f.m.l, width: f.W - f.m.l - f.m.r, y: f.Y(D.acwrSweet[1]), height: f.Y(D.acwrSweet[0]) - f.Y(D.acwrSweet[1]) }, f.svg);
    el("rect", { "class": "bandbad", x: f.m.l, width: f.W - f.m.l - f.m.r, y: f.Y(hi), height: f.Y(D.acwrDanger) - f.Y(hi) }, f.svg);
    vline(f, day(D.today) + 1, "today");
    splitLine(f, pts, "acwr", "--s7");
    projHover(f, pts, projTip, [["acwr", "--s7"]]);
  }
  function goalChart() {
    var G = D.goal; if (!G) return;
    var all = G.races.concat(G.efforts), ds = all.map(function (p) { return day(p.d); }).concat([day(D.today)]);
    var a = Math.min.apply(null, ds) - 4, b = day(G.race) + 5;
    var vs = all.map(function (p) { return p.v; }).concat([G.v]);
    var lo = Math.floor(Math.min.apply(null, vs)) - 1, hi = Math.ceil(Math.max.apply(null, vs)) + 1, step = hi - lo > 10 ? 2 : 1, ticks = [];
    lo = Math.floor(lo / step) * step;
    for (var v = lo; v <= hi; v += step) ticks.push(v);
    var f = frame("cGoal", a, b, lo, hi, ticks, function (v) { return v; });
    monthTicks(f, a, b);
    vline(f, day(D.today) + 1, "today");
    el("line", { x1: f.m.l, x2: f.W - f.m.r, y1: f.Y(G.v), y2: f.Y(G.v), stroke: "var(--s2)", "stroke-width": 2 }, f.svg);
    el("text", { "class": "lbl", x: f.m.l + 6, y: f.Y(G.v) - 6 }, f.svg).textContent = "Goal " + G.t + " · " + G.v.toFixed(1);
    if (G.basis) {
      var bx = day(G.basis.d) + .5, rx = day(G.race) + .5;
      el("line", { x1: f.X(bx), y1: f.Y(G.basis.v), x2: f.X(rx), y2: f.Y(G.v), stroke: "var(--s2)", "stroke-width": 2, "stroke-dasharray": "2 4", "stroke-linecap": "round" }, f.svg);
      if (G.tt) {
        var tx = day(G.tt.d) + .5, tv = G.basis.v + (G.v - G.basis.v) * (tx - bx) / (rx - bx);
        var c = el("circle", { cx: f.X(tx), cy: f.Y(tv), r: 4, fill: "var(--card)", stroke: "var(--s2)", "stroke-width": 2 }, f.svg);
        el("text", { "class": "lbl", x: f.X(tx), y: f.Y(tv) + 18, "text-anchor": "middle" }, f.svg).textContent = "Checkpoint";
        hover(f, c, "<b>" + fmtDate(G.tt.d) + "</b> · " + G.tt.name + "<br>On track: VDOT " + tv.toFixed(1), 4);
      }
    }
    G.efforts.forEach(function (p) {
      var c = dot(f, f.X(day(p.d) + .5), f.Y(p.v), 4, false);
      hover(f, c, "<b>" + fmtDate(p.d) + "</b> · best " + p.name + " in training<br>" + p.t + " · VDOT " + p.v.toFixed(1) +
        "<br>" + (p.why ? "Doesn't count for race fitness: " + p.why : "Qualifies to stand in for a race"), 4);
    });
    G.races.forEach(function (p) {
      var c = dot(f, f.X(day(p.d) + .5), f.Y(p.v), 5.5, true, "--s1");
      hover(f, c, "<b>" + fmtDate(p.d) + "</b> · " + p.name + "<br>" + p.t + " · VDOT " + p.v.toFixed(1) + " (sea level)", 5.5);
    });
    var r = el("text", { "class": "lbl", x: f.X(day(G.race) + .5), y: f.H - f.m.b - 6, "text-anchor": "end" }, f.svg);
    r.textContent = G.name;
  }
  function renderStatic() { proj(); acwr(); goalChart(); }

  function render() { setWindow(); miles(); load(); eff(); dec(); zones(); }
  function shift(days) { vEnd += days; render(); }
  document.getElementById("rPrev").onclick = function () { shift(-7); };
  document.getElementById("rNext").onclick = function () { shift(7); };
  document.getElementById("rLatest").onclick = function () { vEnd = LAST; render(); };
  Array.prototype.forEach.call(document.querySelectorAll("#rSpan button"), function (b) {
    b.onclick = function () { span = +b.dataset.span; render(); };
  });
  // drag (mouse) or swipe (touch) on a time chart to pan
  ["cMiles", "cLoad", "cEff", "cDec", "cZones"].forEach(function (id) {
    var box = document.getElementById(id), startX = null, startEnd, raf = 0;
    box.addEventListener("pointerdown", function (ev) { startX = ev.clientX; startEnd = vEnd; box.setPointerCapture(ev.pointerId); });
    box.addEventListener("pointermove", function (ev) {
      if (startX === null) return;
      var plotW = box.clientWidth - 54, dd = Math.round((startX - ev.clientX) / plotW * (x1 - x0));
      if (dd !== vEnd - startEnd && !raf) raf = requestAnimationFrame(function () { raf = 0; hideTip(); vEnd = startEnd + dd; render(); });
    });
    ["pointerup", "pointercancel"].forEach(function (t) { box.addEventListener(t, function () { startX = null; }); });
  });
  render(); renderStatic();
  var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { render(); renderStatic(); }, 120); });
})();
</script>
</body>
</html>
"""
