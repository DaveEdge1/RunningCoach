"""Fitness tab (docs/fitness.html): volume, training load, aerobic efficiency, zones, race fitness.

Built from data/runs.csv by scripts/build_site.py.
"""
import csv, html, json, math, re, statistics
from datetime import date, timedelta
from pathlib import Path

from analyze import EF_HR_BAND, EF_MIN_SECS
from build_site import BASE_CSS, tabs
from config import (_cfg, ACUTE_DAYS, ACWR_DANGER, ACWR_SWEET, AET, AET_MAX_GRADE, AET_MAX_S, AET_WARMUP_S, CHRONIC_DAYS,
                    CROSS_DEFAULT_RATE, CROSS_NAMES, DANIELS_FLOOR_FT, DANIELS_SPM, HARD_BOUT,
                    HRFIT_DAYS, HRFIT_HR_MAX, HRFIT_MAX_CV, HRFIT_MIN_S, HRFIT_SETTLE_S, MAX_HR, RACE_WHOLE_RUN_TOL, REST_HR,
                    BENCH_HR_SKIP_S, BENCH_TOL, HOME_ELEV_FT, HOME_NAME, LT, MANUAL_LOAD_PER_MIN, RECOVERY_KM_PER_DAY, REP_MIN_S, REP_PACE_TOL, ROOT,
                    HR_CEIL, HR_CEIL_PCT, THRESH_HR,
                    WORKOUT_Z5_PCT, ZONE_NAMES as ZONE_COLS, ZONES, convert_range, convert_time, load_races, next_race,
                    cross_kind, recovery_days, trail_days, zone_anchors, zone_range)

CTL_DAYS, ATL_DAYS = 42, 7
TREND_DAYS = 28
TREND_MIN_RUNS = 3  # runs that must qualify in the window
EF_ELEV_TOL_FT = 1000  # pace-at-AeT uses only runs within this of home elevation
FRESH, TIRED = 5, -10  # form % thresholds
RATE_DAYS = 42  # load per mile for the projection comes from this many recent days
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


def load_bench():
    """Passes through the trail benchmarks (data/benchmarks.csv, from analyze.py), each with its time scaled to AeT
    HR: at a fixed grade the energy cost per metre is constant (Minetti et al. 2002), so net VO2 is proportional to
    speed, and %VO2 reserve tracks %HR reserve (Swain & Leutholtz 1997); so time x (HR - rest) / (AeT - rest)."""
    path = ROOT / "data" / "benchmarks.csv"
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            secs, hr = int(r["secs"]), int(r["avg_hr"]) if r["avg_hr"] else None
            out.append({"d": r["date"], "n": r["name"], "t": secs, "mi": float(r["miles"]), "hr": hr,
                        "adj": round(secs * (hr - REST_HR) / (AET - REST_HR)) if hr and hr > REST_HR else None,
                        "climb": int(r["climb_ft"] or 0), "net": int(r["net_elev_ft"]) if r["net_elev_ft"] else None})
    return out


def signed_ft(x):
    return "" if x is None else f"{x:+,}".replace("-", "−")


def bench_html(bench):
    if not bench:
        return ""
    names = list(dict.fromkeys(b["n"] for b in sorted(bench, key=lambda b: b["n"], reverse=True)))  # "up" before "down"
    rows = "".join(
        f"<tr><td>{date.fromisoformat(b['d']):%b} {date.fromisoformat(b['d']).day}</td><td>{html.escape(b['n'])}</td>"
        f"<td>{mmss(b['t'])}</td><td class=opt>{mmss(b['t'] / b['mi'])}/mi</td><td>{b['hr'] or '–'}</td>"
        f"<td>{mmss(b['adj']) if b['adj'] else '–'}</td><td class=opt>{signed_ft(b['net'])}</td></tr>"
        for b in sorted(bench, key=lambda b: (b["d"], b["n"]), reverse=True))
    leg = "".join(f'<span><span class="dt" style="background:var(--s{1 if i == 0 else 2})"></span>{html.escape(n)}</span>'
                  for i, n in enumerate(names))
    return (
        '<section class="card"><h2>Trail benchmark: Schultz</h2>\n'
        f'<p class="note">Each time a run passes through a benchmark section, its time scaled to {AET} bpm. Faster is up.</p>\n'
        '<div class="chart" id="cBench"></div>\n'
        f'<div class="legend">{leg}</div>\n'
        '<div class="scroll"><table class="t full"><thead><tr><th>Date</th><th>Section</th><th>Time</th>'
        f'<th class=opt>Pace</th><th>Avg HR</th><th>At {AET} bpm</th><th class=opt>Net elev. (ft)</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>\n'
        + how(f"<p>The sections are fixed GPS start and end points in config.json (benchmarks): about {bench[0]['mi']:.2f} mi of the "
              "upper Schultz trail, timed in each direction separately, so a run can include either one. A pass counts from "
              "the start point to the next arrival at the end point, if its distance is within "
              f"{round(BENCH_TOL * 100)}% of the section's (no detours). Average HR leaves out the first {BENCH_HR_SKIP_S} s, "
              "while HR catches up with the change of grade.</p>"
              f"<p><b>At {AET} bpm</b> scales each time to the same effort, your AeT: on a fixed grade the energy cost per "
              "metre is constant (Minetti et al. 2002), so the oxygen used above rest rises in proportion to speed, and the "
              "share of oxygen reserve tracks the share of HR reserve (Swain &amp; Leutholtz 1997). So time × (HR − "
              f"{REST_HR}) ÷ ({AET} − {REST_HR}): a pass 5 bpm above AeT is credited about 5%. It works best on passes "
              "run steadily near AeT; it reads slow when you're tired (the day after a workout), hot, or on snow or mud, and "
              "fast when HR reads low (the arm strap). Same trail each time, so footing and grade cancel out, which "
              "grade-adjusted pace can't do for a trail.</p>") + '\n</section>')


def load_hr_hist():
    """{(date, start): {bpm: seconds}} from data/hr_hist.csv."""
    path = ROOT / "data" / "hr_hist.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {(r["date"], r["start"]): {int(b): int(n) for b, n in (x.split(":") for x in r["bpm_secs"].split())}
                for r in csv.DictReader(f)}


def load_cross():
    """Recorded hikes and XC skis (data/cross.csv): load counts, miles don't."""
    path = ROOT / "data" / "cross.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def prior_miles(first):
    """{date: miles} from data/prior_weeks.csv (Mon-Sun totals David reported for weeks before the recorded data),
    for the 12-week average only. A week's miles go evenly on its days before `first`; a week that overlaps the
    recorded runs adds only what they don't already cover."""
    path = ROOT / "data" / "prior_weeks.csv"
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for w in csv.DictReader(f):
            wk0 = date.fromisoformat(w["week"])
            days = [wk0 + timedelta(i) for i in range(7) if wk0 + timedelta(i) < first]
            for d in days:
                out[d] = float(w["miles"]) / len(days)
    return out


def run_secs(r):
    """Moving time from distance x pace, so runs without HR (manual runs) count too."""
    m, sec = r["pace"].split(":")
    return float(r["miles"]) * (int(m) * 60 + int(sec))


def recorded_week_miles(runs, wk0):
    return sum(float(r["miles"]) for r in runs if 0 <= (date.fromisoformat(r["date"]) - wk0).days < 7)


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
    mins = {d: 0.0 for d in days}  # running minutes and climbing (ft), recorded runs only
    climb = {d: 0.0 for d in days}
    for r in runs:
        d = date.fromisoformat(r["date"])
        if d in miles:
            miles[d] += float(r["miles"])
            load[d] += float(r["load"] or 0)
            mins[d] += run_secs(r) / 60
            climb[d] += float(r["elev_ft"] or 0)
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
    # weeks before the recorded data (data/prior_weeks.csv) count toward the 12-week average only
    prior = prior_miles(first)
    for wk0 in {d - timedelta(d.weekday()) for d in prior}:
        days_ = [d for d in prior if d - timedelta(d.weekday()) == wk0]
        left = max(0.0, sum(prior[d] for d in days_) - recorded_week_miles(runs, wk0))
        for d in days_:
            prior[d] = left / len(days_)
    covered = min(prior, default=first)
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
        r84 = (sum(miles[days[j]] for j in range(max(0, i - 83), i + 1))
               + sum(v for k, v in prior.items() if (d - k).days < 84)) / 12
        roll = lambda v, n: sum(v[days[j]] for j in range(max(0, i - n + 1), i + 1)) * 7 / n
        acute = sum(load[days[j]] for j in range(max(0, i - ACUTE_DAYS + 1), i + 1))
        chronic = sum(load[days[j]] for j in range(max(0, i - CHRONIC_DAYS + 1), i + 1)) * ACUTE_DAYS / CHRONIC_DAYS
        out.append({"d": d.isoformat(), "mi": round(miles[d], 2), "load": round(load[d]), "xload": round(xload[d]),
                    "ctl": round(ctl, 1), "atl": round(atl, 1), "form": round(ctl - atl, 1),
                    "formPct": round(form_pct, 1), "r7": round(r7, 1),
                    "r28": round(r28, 1) if i >= 27 else None,
                    "r84": round(r84, 1) if (d - covered).days >= 83 else None,
                    "acwr": round(acute / chronic, 2) if i >= CHRONIC_DAYS - 1 and chronic else None,
                    "min": round(mins[d]), "ft": round(climb[d]),
                    "m28": round(roll(mins, 28)) if i >= 27 else None, "m84": round(roll(mins, 84)) if i >= 83 else None,
                    "f28": round(roll(climb, 28)) if i >= 27 else None, "f84": round(roll(climb, 84)) if i >= 83 else None,
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
    are kept as points but marked and left out of the trend. Trail runs (notes.md) are left out entirely."""
    by_day = {x["d"]: x for x in daily}
    recovering = {}
    for r in rc:
        for i in range(recovery_days(r["meters"]) + 1):
            recovering[(r["d"] + timedelta(i)).isoformat()] = (r["name"], i)
    trails = trail_days()
    pts = []
    for r in runs:
        near_home = not r.get("elev_avg_ft") or abs(int(r["elev_avg_ft"]) - HOME_ELEV_FT) <= EF_ELEV_TOL_FT
        if r["aet_pace_secs"] and r["date"] in by_day and near_home and r["date"] not in trails:
            f = by_day[r["date"]]["formPct"]
            rec = recovering.get(r["date"])
            why = f"{rec[1]} day{'s' * (rec[1] != 1)} after {rec[0]}" if rec else None
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
            trend.append({"d": x["d"], "p": round(statistics.median(p["p"] for p in win), 1)})
    return pts, trend


def weekly(runs, daily, cross=()):
    weeks = {}
    new = lambda: {"mi": 0.0, "runs": 0, "hr": 0, "z": [0.0] * 5, "load": 0, "xh": 0.0, "hh": {}, "secs": 0.0, "ft": 0.0}
    hists = load_hr_hist()
    for c in cross:
        d = date.fromisoformat(c["date"])
        w = weeks.setdefault(d - timedelta(d.weekday()), new())
        w["load"] += float(c["load"] or 0)
        w["xh"] += float(c["secs"] or 0) / 3600
    for r in runs:
        d = date.fromisoformat(r["date"])
        w = weeks.setdefault(d - timedelta(d.weekday()), new())
        w["mi"] += float(r["miles"])
        w["secs"] += run_secs(r)
        w["ft"] += float(r["elev_ft"] or 0)
        w["runs"] += 1
        w["load"] += float(r["load"] or 0)
        if r["hr_secs"]:
            n = int(r["hr_secs"])
            w["hr"] += n
            w["z"] = [z + float(r[c]) * n / 100 for z, c in zip(w["z"], ZONE_COLS)]
        for b, n in hists.get((r["date"], r["start"]), {}).items():
            w["hh"][b] = w["hh"].get(b, 0) + n
    by_day = {x["d"]: x for x in daily}
    out = []
    for wk0 in sorted(weeks):
        w = weeks[wk0]
        end = min(wk0 + timedelta(6), date.fromisoformat(daily[-1]["d"]))
        x = by_day.get(end.isoformat(), {})
        out.append({"w": wk0.isoformat(), "mi": round(w["mi"], 1), "runs": w["runs"], "load": round(w["load"]),
                    "xh": round(w["xh"], 1),
                    "hrH": round(w["hr"] / 3600, 1), "runH": round(w["secs"] / 3600, 1),
                    "min": round(w["secs"] / 60), "ft": round(w["ft"]),
                    "z": [round(100 * z / w["hr"]) if w["hr"] else 0 for z in w["z"]],
                    "hh": [[b, w["hh"][b]] for b in sorted(w["hh"])],  # seconds at each bpm, runs only
                    "ctl": x.get("ctl"), "atl": x.get("atl"), "partial": end < wk0 + timedelta(6)})
    return out


def session_meters(sess):
    """Distance named in a plan session ("5K time trial" -> 5000, "1 mi time trial" -> 1609), or None."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(km|k|mi|mile)\b", sess, re.I)
    if not m:
        return None
    return float(m.group(1)) * (M_PER_MI if m.group(2).lower().startswith("mi") else 1000)


def effort_windows():
    """Fastest window per (date, start, meters) from data/efforts.csv: (clock secs, grade-adjusted secs)."""
    path = ROOT / "data" / "efforts.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return {(e["date"], e["start"], round(float(e["meters"]))):
                (float(e["secs"]), float(e["gap_secs"]) if e.get("gap_secs") else float(e["secs"]))
                for e in csv.DictReader(f)}


def races(runs, plan):
    """Completed races (races.csv or plan type race) and time trials (plan session named so), with results.

    The result is the fastest window at the race distance (races.csv, or the distance in the plan session) when
    the recorded run is longer than that, so a warm-up and cool-down recorded in the same activity don't count.
    A run within RACE_WHOLE_RUN_TOL of the distance (GPS runs a little short or long) is scored whole. With
    several runs that day, the one with the fastest window wins. No distance known: the longest run, whole."""
    listed = {r["date"]: r for r in load_races()}
    windows = effort_windows()
    best = {}
    for r in runs:
        d = date.fromisoformat(r["date"])
        p, rc = plan.get(d), listed.get(d)
        is_race = rc is not None or (p is not None and p["type"] == "race")
        if not (is_race or (p is not None and "time trial" in p["session"].lower())):
            continue
        target = rc["meters"] if rc else session_meters(p["session"])
        run_m = float(r["miles"]) * M_PER_MI
        m, s = r["pace"].split(":")
        clock = (int(m) * 60 + int(s)) * float(r["miles"])
        gap = float(r["gap_secs"]) if r.get("gap_secs") else clock
        meters, whole = run_m, True
        if target and run_m > target * (1 + RACE_WHOLE_RUN_TOL):
            w = windows.get((r["date"], r["start"], round(target)))
            if w is None:
                print(f"warning: {r['date']} {r['start']}: no {dist_label(target)} window in efforts.csv "
                      f"(add {target:g} m to best_efforts.distances_m); scoring the whole {run_m / M_PER_MI:.2f} mi")
            else:
                (clock, gap), meters, whole = w, target, False
        elif target and run_m < target * (1 - RACE_WHOLE_RUN_TOL):
            continue  # a warm-up or cool-down recorded separately
        elev = int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else (rc or {}).get("elev")
        name = rc["name"] if rc else f"{dist_label(target or meters)} {'race' if is_race else 'time trial'}"
        cand = {"d": d, "start": r["start"], "name": name, "meters": meters, "secs": clock, "elev": elev, "gap": gap,
                "vdot": vdot(meters, clock), "whole": whole, "run_mi": float(r["miles"])}
        old = best.get(d)
        faster = target and cand["secs"] / cand["meters"] < old["secs"] / old["meters"] if old else True
        if faster or (old and not target and cand["run_mi"] > old["run_mi"]):
            best[d] = cand
    return [best[d] for d in sorted(best)]


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
    """Best efforts (data/efforts.csv), scored on grade-adjusted time: "secs" is the grade-adjusted time, so the
    effort's VDOT is a flat-ground equivalent; "clock" is the time as run. A record of training, not of race fitness:
    training efforts are submaximal by design (see fitness_basis)."""
    path = ROOT / "data" / "efforts.csv"
    if not path.exists():
        return []
    elev = {(r["date"], r["start"]): int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else None for r in runs}
    out = []
    with open(path, encoding="utf-8") as f:
        for e in csv.DictReader(f):
            d, m, clock = date.fromisoformat(e["date"]), float(e["meters"]), float(e["secs"])
            secs = float(e["gap_secs"]) if e.get("gap_secs") else clock
            el = elev.get((e["date"], e["start"]))
            net = int(e["net_elev_ft"]) if e["net_elev_ft"] else None
            mhr = int(e["max_hr"]) if e["max_hr"] else None
            out.append({"d": d, "start": e["start"], "meters": m, "secs": secs, "clock": clock, "elev": el, "net": net,
                        "hr": int(e["avg_hr"]) if e["avg_hr"] else None, "maxhr": mhr,
                        "vdot": vdot(m, secs), "sl": sl_vdot(m, secs, el if el is not None else HOME_ELEV_FT),
                        "name": f"Best {effort_label(m)} in training"})
    return out


def fitness_basis(rc, before=None):
    """Current fitness: the latest race or time trial before `before`. Training efforts are submaximal by design,
    so none stands in for one; the HR-based estimate (hr_fitness) shows the trend between races."""
    races_ = [r for r in rc if before is None or r["d"] < before]
    return races_[-1] if races_ else None


def hr_vo2max(gap_mps, hr):
    """Effective VO2max (ml/kg/min) from a steady stretch: the oxygen cost of its grade-adjusted speed (Daniels'
    equation, as in vdot) over the fraction of VO2 reserve it used, taken as equal to the fraction of HR reserve
    (Swain & Leutholtz 1997). None outside 30-100% of HR reserve."""
    frac = (hr - REST_HR) / (MAX_HR - REST_HR)
    if not 0.3 <= frac <= 1:
        return None
    return 3.5 + (vo2(gap_mps * 60) - 3.5) / frac


def hr_fitness(runs, rc, meters):
    """HR-based fitness per run from data/segments.csv (steady laps, see analyze.hr_segments), calibrated to races.

    A run's raw estimate is the seconds-weighted mean of hr_vo2max over its steady laps averaging at most
    HRFIT_HR_MAX (AeT + aet_margin_bpm); trail runs are left out. Above AeT HR and VO2 keep rising for 10-20 minutes
    (the slow component), so a rep's average HR understates its cost and reads high, and a race's drifts up and
    reads low. The population HR-VO2 relation doesn't
    fit any one runner exactly, so every estimate is scaled by k, the factor that puts the trend line (hr_slope over the
    last 2 x HRFIT_DAYS days, the line the chart draws) on the latest race or time trial's sea-level VDOT (clock time,
    as plotted) on its date. David's choice (2026-11-04): the latest race is the best measure of current fitness, so the
    trend should pass through it. Races on trail days don't count (rough footing reads slow). Until 2026-11-04 k matched
    the median of the runs within HRFIT_DAYS / 2 of each of the last 3 races, which rested on few runs and left the
    trend well above the time trial. Without a trend (under 4 runs), it falls back to that median for the latest race.
    Each run goes on the sea-level scale the way a race does: VDOT -> time at `meters` at the run's elevation ->
    sea-level VDOT.
    Returns (points, calibration or None)."""
    path = ROOT / "data" / "segments.csv"
    if not path.exists():
        return [], [], None
    trails = trail_days()
    info = {(r["date"], r["start"]): r for r in runs}
    per_run = {}
    with open(path, encoding="utf-8") as f:
        for s in csv.DictReader(f):
            k = (s["date"], s["start"])
            est = hr_vo2max(float(s["gap_mps"]), float(s["avg_hr"]))
            if est is not None and float(s["avg_hr"]) <= HRFIT_HR_MAX and s["date"] not in trails and k in info:
                per_run.setdefault(k, []).append((int(s["secs"]), est, float(s["gap_mps"]), float(s["avg_hr"])))
    pts = []
    for (d, start), ss in sorted(per_run.items()):
        n = sum(x[0] for x in ss)
        el = info[(d, start)].get("elev_avg_ft")
        pts.append({"d": date.fromisoformat(d), "start": start, "secs": n, "laps": len(ss),
                    "raw": sum(x[0] * x[1] for x in ss) / n, "elev": int(el) if el else HOME_ELEV_FT,
                    "gap": M_PER_MI / (sum(x[0] * x[2] for x in ss) / n), "hr": sum(x[0] * x[3] for x in ss) / n})
    half = timedelta(HRFIT_DAYS / 2)
    sl_at = lambda p, k: sl_vdot(meters, predict(k * p["raw"], meters), p["elev"])

    def trend_at(r, k):  # the drawn trend line's value on the race date, with every estimate scaled by k
        for p in pts:
            p["sl"] = sl_at(p, k)
        t = hr_slope(pts, 2 * HRFIT_DAYS)
        return None if t is None else t[2] + t[3] * (r["d"] - t[0]).days

    def window_median(r, k):
        win = [p for p in pts if abs(p["d"] - r["d"]) <= half and (p["d"], p["start"]) != (r["d"], r["start"])]
        return statistics.median(sl_at(p, k) for p in win) if len(win) >= 3 else None

    def race_k(r):  # bisection: the estimates (and so the trend) rise with k
        level = trend_at if trend_at(r, 1.0) is not None else window_median
        if level(r, 1.0) is None:
            return None
        target, lo, hi = sl_vdot(r["meters"], r["secs"], src_elev(r)), 0.5, 2.0  # clock time, as every page shows
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if level(r, mid) < target else (lo, mid)
        return (lo + hi) / 2

    # the latest race or time trial, not on rough footing (a "trail" note reads slow for its fitness)
    latest = [r for r in rc if r["d"].isoformat() not in trails][-1:]
    cal = [(r, c) for r in latest if (c := race_k(r)) is not None]
    k = cal[0][1] if cal else 1.0
    for p in pts:
        p["v"] = k * p["raw"]
        p["sl"] = sl_at(p, k)
        p["race"] = False
    return pts, ({"k": k, "races": [r for r, _ in cal]} if cal else None)


DAYS_PER_MO = 365.25 / 12


def hr_slope(pts, days):
    """Theil-Sen line (Sen 1968: median of pairwise slopes, so one odd run can't tip it) through the sea-level
    HR-based estimates of the last `days` days: (first day, last day, value at first day, VDOT per day), or None."""
    end = date.today()
    win = [(p["d"], p["sl"]) for p in pts if (end - p["d"]).days <= days]
    pairs = [(v2 - v1) / (d2 - d1).days for i, (d1, v1) in enumerate(win) for d2, v2 in win[i + 1:] if d2 != d1]
    if len(win) < 4 or not pairs:
        return None
    m = statistics.median(pairs)
    d0 = win[0][0]
    return d0, win[-1][0], statistics.median(v - m * (d - d0).days for d, v in win), m



REP_RE = re.compile(r"(\d+)\s*[×x]\s*(\d+(?:\.\d+)?)\s*(km|mi|min|k|m)\b", re.I)


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
    """(label, (fast, slow) sec/mi at elev, HR (low, high, ceiling) or None) for a workout session; None if it names
    no pace. Only threshold reps have an HR target (config.json workouts)."""
    s = session.lower()
    cap = None
    if "goal" in s and nr and nr["goal_secs"] and nr["elev"] is not None:
        lo, hi = convert_range(nr["goal_secs"], nr["meters"], nr["elev"], elev)
        mi = nr["meters"] / M_PER_MI
        return f"goal {dist_label(nr['meters'])} pace", (lo / mi, hi / mi), cap
    if not basis:
        return None
    vlo, vhi = vdot_range(basis, elev)
    if "threshold" in s:  # HR band and ceiling: config.json workouts
        return "threshold", (pace_at(vhi, 0.88), pace_at(vlo, 0.88)), (*THRESH_HR, HR_CEIL)
    if "interval" in s and "10k" not in s:  # Daniels I pace
        return "interval", (pace_at(vhi, 0.98), pace_at(vlo, 0.98)), cap
    for meters, word in ((5000, "5k"), (10000, "10k")):
        if word in s:
            race = (predict(vhi, meters) / (meters / M_PER_MI), predict(vlo, meters) / (meters / M_PER_MI))
            if "interval" in s:
                return f"{word.upper()} to interval", (pace_at(vhi, 0.98), race[1]), cap
            return word.upper(), race, cap
    return None


def workouts(runs, plan, rc, nr):
    """Planned workouts with reps ("N × D"), matched to laps by effort, not distance (reps may be run by time):
    the N laps of at least REP_MIN_S seconds with average HR at or above AeT and the fastest grade-adjusted pace.
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
        n = int(m.group(1))
        best = None
        for r in by_day[d]:
            ls = laps.get((r["date"], r["start"]), [])
            cand = [l for l in ls if int(l["secs"]) >= REP_MIN_S and (l["gap"] or l["pace"])
                    and l["avg_hr"] and int(l["avg_hr"]) >= AET]
            reps = sorted(sorted(cand, key=lambda l: clock_secs(l["gap"] or l["pace"]))[:n], key=lambda l: int(l["lap"]))
            if best is None or len(reps) > len(best[1]):
                best = (r, reps)
        r, reps = best
        elev = int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else HOME_ELEV_FT
        tgt = rep_target(p["session"], fitness_basis(rc, d), elev, nr)
        rows = []
        for i, l in enumerate(reps):
            gap = clock_secs(l["gap"]) if l.get("gap") else clock_secs(l["pace"])
            pctl = int(l["hr_pctl"]) if l.get("hr_pctl") else None
            pace_ok = hr_ok = None
            if tgt:
                fast, slow = tgt[1]
                pace_ok = "fast" if gap < fast - REP_PACE_TOL else "slow" if gap > slow + REP_PACE_TOL else "on"
                if tgt[2] and l["avg_hr"]:
                    lo, hi, ceil = tgt[2]
                    avg = int(l["avg_hr"])
                    hr_ok = ("over" if (pctl and pctl > ceil) or avg > hi else
                             ("settling" if i == 0 else "low") if avg < lo else "in")
            rows.append({"lap": l["lap"], "mi": float(l["miles"]), "secs": int(l["secs"]), "pace": l["pace"],
                         "gap": gap, "hr": l["avg_hr"], "pctl": pctl, "paceOk": pace_ok, "hrOk": hr_ok})
        out.append({"d": d, "session": p["session"], "n": n, "elev": elev, "target": tgt, "reps": rows})
    return out

# ---------- page ----------

def acwr_word(x):
    lo, hi = ACWR_SWEET
    return ("below the sweet spot" if x < lo else "sweet spot" if x <= hi else
            "above the sweet spot" if x < ACWR_DANGER else "danger zone")


def basis_phrase(b):
    when = f'{b["d"]:%b} {b["d"].day}, {ft(b["elev"]) or "elevation unknown"}'
    return f'your most recent race or time trial ({html.escape(b["name"])}, {when})'


def hmm(minutes):
    m = round(minutes)
    return f"{m // 60}h {m % 60:02d}m"


def year_goal(daily, today):
    """Tile for the year's running-time goal (config.json goals.annual_minutes): before the year, the weekly
    average it takes against the last 28 days; during it, minutes so far against an even pace."""
    goals = {int(y): g for y, g in _cfg.get("goals", {}).get("annual_minutes", {}).items()}
    year = min((y for y in goals if y >= today.year), default=None)
    if year is None:
        return None
    goal, jan1, dec31 = goals[year], date(year, 1, 1), date(year, 12, 31)
    per_wk = goal * 7 / ((dec31 - jan1).days + 1)
    r28 = sum(x["min"] for x in daily[-28:]) / 4
    if today < jan1:
        return (f"{goal:,} min in {year}", f"{per_wk:.0f} min/wk",
                f"{hmm(per_wk)} a week on average; last 28 days: {r28:.0f} min/wk")
    done = sum(x["min"] for x in daily if x["d"] >= jan1.isoformat())
    even = goal * ((today - jan1).days + 1) / ((dec31 - jan1).days + 1)
    return (f"{year} running time", f"{done:,.0f} min", f"{100 * done / goal:.0f}% of {goal:,}",
            signed(done - even, "{:,.0f}") + " min vs. even pace")


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
        if today <= d < before and "time trial" in sess.lower() and session_meters(sess):
            return d, session_meters(sess), sess
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
    bench = load_bench()
    bench_card = bench_html(bench)
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
    t_now = trend[-1]["p"] if trend else None
    # latest trend value on or before that day: the window leaves gaps where fewer than TREND_MIN_RUNS qualify
    t_ago = next((t["p"] for t in reversed(trend) if t["d"] <= ago["d"]), None) if ago and trend else None
    fp = now["formPct"]
    form_word = "fresh" if fp > FRESH else "carrying fatigue" if fp < TIRED else "neutral"
    last = lambda k, n: sum(x[k] for x in daily[-n:])
    prev28m = last("min", 56) - last("min", 28) if len(daily) >= 56 else None
    volume = [
        tile("Last 7 days", hmm(last("min", 7)), f"{now['r7']:.1f} mi · {last('ft', 7):,} ft climbing"),
        tile("28-day average", f"{hmm(last('min', 28) / 4)}/wk",
             f"{now['r28']:.1f} mi · {last('ft', 28) / 4:,.0f} ft a week" if now["r28"] is not None else "",
             (signed((last("min", 28) - prev28m) / 4, "{:.0f}") + " min/wk vs. previous 28 days") if prev28m is not None else ""),
        tile("Days run", f"{sum(1 for x in daily[-28:] if x['min'] > 0)} of 28", "in the last 28 days"),
    ]
    yg = year_goal(daily, today)
    if yg:
        volume.append(tile(*yg))
    load_t = [
        tile("Long-term load", f"{now['ctl']:.0f}", f"{CTL_DAYS}-day average",
             (signed(now["ctl"] - ago["ctl"], "{:.0f}") + " vs. 4 weeks ago") if ago else ""),
        tile("Form", f"{now['form']:+.0f}".replace("-", "−"), f"{fp:+.0f}% · {form_word}".replace("-", "−")),
    ]
    if now["acwr"] is not None:
        load_t.append(tile("Load ramp", f"{now['acwr']:.2f}", f"{ACUTE_DAYS}-day ÷ {CHRONIC_DAYS}-day · {acwr_word(now['acwr'])}"))
    fit_t = []
    if t_now:
        fit_t.append(tile(f"Pace at AeT ({AET} bpm)", f"{mmss(t_now)}/mi",
                          f"{TREND_DAYS}-day median at {HOME_ELEV_FT:,} ft",
                          (signed((t_now - t_ago) / 1, "{:.0f}", good_up=False) + " s/mi vs. 4 weeks ago") if t_ago else ""))
    best = fitness_basis(rc)

    def vrange(lo, hi):
        return f"{lo:.1f}" if f"{lo:.1f}" == f"{hi:.1f}" else f"{lo:.1f}–{hi:.1f}"

    if best:
        fit_t.append(tile("VDOT (sea level)", f"{sl_vdot(best['meters'], best['secs'], src_elev(best)):.1f}",
                          f"{html.escape(best['name'])}, {best['d']:%b} {best['d'].day}, at {ft(src_elev(best))}"))
    tiles = [f'<div class="tgroup"><div class="tgh">{name}</div><div class="tiles">{"".join(ts)}</div></div>'
             for name, ts in (("Time on feet", volume), ("Load", load_t), ("Fitness", fit_t)) if ts]

    spm = "–".join(f"{k:g}" for k in DANIELS_SPM)
    first = date.fromisoformat(daily[0]["d"])
    iso_today = today.isoformat()

    # methods, shown in collapsed "How it's calculated" blocks under the chart or table they explain
    p_load = (f"<p><b>Load</b> is HR-zone-weighted minutes: each minute in Z1 counts 1, Z2 counts 2, and so on up to Z5 = 5. "
              f"Runs without real HR count {MANUAL_LOAD_PER_MIN:g} per minute.</p>")
    p_ctl = (f"<p><b>Long-term / recent load</b> are exponentially weighted averages of daily load over {CTL_DAYS} and {ATL_DAYS} days "
             "(often called fitness and fatigue, CTL and ATL). They measure how much you train, not how fit you are: steady training "
             "keeps them flat even while you improve. Fitness gains show up in pace at AeT and VDOT (Fitness tab). "
             f"History begins {first:%b} {first.day}, {first.year}, so both start at the average load of the first four weeks.</p>")
    p_form = ("<p><b>Form</b> is long-term minus recent load, as a % of long-term load going into the day: above "
              f"+{FRESH}% reads fresh, below {TIRED}% carrying fatigue. It measures training fatigue only. Sleep, stress, heat, "
              "and illness aren't included, so read single runs with that in mind.</p>").replace("-1", "−1")
    p_acwr = (f"<p><b>Load ramp</b> is the acute:chronic workload ratio: load over the last {ACUTE_DAYS} days divided by the weekly "
              f"average over the last {CHRONIC_DAYS}. "
              '<a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC4789704">Gabbett (2016, Br J Sports Med 50:273)</a> calls '
              f"{ACWR_SWEET[0]:g}–{ACWR_SWEET[1]:g} the sweet spot and ≥ {ACWR_DANGER:g} the danger zone for injury. The ratio has been "
              "criticized as a predictor (Impellizzeri et al. 2020, Int J Sports Physiol Perform 15:907), so read it as a guide "
              "to how fast load is rising, not a risk score. It needs four weeks of history.</p>")
    p_cross = ("<p><b>Cross-training</b> (hikes and XC skis recorded on the watch) adds HR-zone load the same way runs do, but no "
               "miles. HR load misses the muscle damage from long loaded descents, so expect to feel more tired after the Grand "
               "Canyon than form shows.</p>")
    p_aet = (f"<p><b>Pace at AeT</b> uses grade-adjusted pace (Minetti et al. 2002) from seconds with HR {EF_HR_BAND[0]}–{EF_HR_BAND[1]} bpm, "
             f"moving faster than walking, on grades under {AET_MAX_GRADE:.0%}, scaled to exactly {AET} bpm. It skips the first "
             f"{AET_WARMUP_S // 60} minutes (warm-up), everything after {AET_MAX_S // 60} minutes (cardiac drift), and everything after a hard bout, "
             f"when HR stays high for the rest of the run. A hard bout is {HARD_BOUT[0][1] // 60} minutes at {HARD_BOUT[0][0]} bpm or "
             f"{HARD_BOUT[1][1] // 60} at {HARD_BOUT[1][0]}, on an exponential curve through those points "
             f"(shorter the higher the HR), counted after the warm-up. Runs within one day per {RECOVERY_KM_PER_DAY:g} km of a race (Daniels' easy days "
             f"after racing: 3 days for a 10K) "
             f"show as open circles and stay out of the trend. Trail runs (marked in notes.md) are left out: grade adjustment "
             f"doesn't account for rough footing. A run needs at least {EF_MIN_SECS // 60} minutes "
             f"of those seconds. The trend is the median of the runs in the past {TREND_DAYS} days (at least {TREND_MIN_RUNS} runs), so one odd day doesn't move it, and each "
             f"point is drawn at the center of its window, so the line stops about {TREND_DAYS // 2} days short of today. "
             f"Only runs within {EF_ELEV_TOL_FT:,} ft of {HOME_NAME} ({HOME_ELEV_FT:,} ft) count, so the number is a "
             f"{HOME_NAME}-altitude pace. Dots are colored by form going into that day, so a slow dot on a tired day isn't a "
             "sign of lost fitness.</p>")
    p_zones = ("<p>Each week is a sideways histogram of running time at each heart rate (1 bpm bins): the farther right, the "
               "more minutes, so a bigger week draws a bigger shape. Hours above each week are all running time, including runs "
               "without HR (manual ones), which the histogram can't show.</p>"
               f"<p><b>Zones</b> come from config.json: LT1 (AeT) {AET} bpm and LT2 (LT) {LT} bpm, the dashed lines. "
               "The zones put wider breaks at those two thresholds.</p>")
    p_vdot = ("<p><b>VDOT</b> is Jack Daniels' formula applied to your most recent race (races.csv, or a planned race) or time trial "
              "(a planned session named \"time trial\"). Training efforts never replace it: they are run below race effort by design, "
              "and a fastest window can include recoveries. "
              "The next-race estimate uses that current fitness with no projected gains. "
              "Elevation comes from the run's own file.</p>")
    p_alt = ("<p><b>Altitude</b> is converted two published ways and shown as a range. "
             '<a href="https://news.vdoto2.com/2011/03/ask-a-coach-how-do-you-adjust-threshold-pace-at-altitude/">Daniels</a>: '
             f"paces slow {spm} s/mi per 1,000 ft above {DANIELS_FLOOR_FT:,} ft (15–18 s/mi at 7,000 ft). "
             '<a href="https://www.ustfccca.org/assets/ncaa-info/2009-outdoor-alt-adjust.pdf">NCAA 2009 altitude tables</a>: '
             "% added by venue altitude (0–7,703 ft) for the mile, 5K and 10K, interpolated by altitude and distance, and held at "
             "the nearest of those distances outside that span. Neither accounts for acclimatization.</p>")
    p_hrfit = ("<p><b>HR-based estimate</b> estimates fitness from pace and HR together, so any steady easy "
               "running counts. Each steady lap gives an effective VO2max: the oxygen cost of its grade-adjusted pace (Daniels' equation, "
               "the one inside VDOT) divided by the share of VO2 reserve it used, which Swain &amp; Leutholtz (1997, Med Sci Sports "
               f"Exerc 29:410) found equal to the share of HR reserve (resting {REST_HR}, max {MAX_HR} bpm, from config.json). "
               f"A steady lap leaves out its first {HRFIT_SETTLE_S // 60} minutes (HR still catching up with pace), the run's first "
               f"{AET_WARMUP_S // 60} minutes, everything after {AET_MAX_S // 60} minutes (cardiac drift), grades over "
               f"{AET_MAX_GRADE:.0%}, and laps after a hard bout that aren't hard themselves (HR stays raised on jogs and "
               f"cool-downs). It needs {HRFIT_MIN_S // 60} minutes left and grade-adjusted speed that varies less than "
               f"{HRFIT_MAX_CV:.0%} (no strides or surges). Trail runs are left out, and so is any lap averaging over {HRFIT_HR_MAX} bpm "
               f"(AeT {AET} plus a little sensor noise): above AeT HR and VO2 keep rising for 10–20 minutes (the slow component; Poole &amp; Jones 2012, "
               "Compr Physiol 2:933), so a rep's average HR understates its cost and reads high, while a race's drifts up and "
               "reads low. A population formula doesn't fit one runner exactly, so every estimate is scaled by one factor, "
               f"chosen so the trend line (last {2 * HRFIT_DAYS} days) passes through your latest race or time trial: the "
               "most direct measure of current fitness (Runalyze likewise scales its effective VO2max to race results; a race "
               "marked trail in notes.md doesn't count, since rough footing makes it read slow). Estimates are converted to "
               "sea level like a race. Single runs scatter by a point "
               "or two (sleep, fatigue, a few beats of sensor error), so read the trend, not one dot.</p>")

    # Today: what's coming up, each line linking to its day on the calendar
    coming = []  # (label, html)

    def cal(d):
        return f'<a href="index.html#{d.isoformat()}">{d:%a %b} {d.day}</a>'

    wo = workouts(runs, plan, rc, nr)
    nxt = next(((d, plan[d]) for d in sorted(plan) if d >= today and plan[d]["type"] == "workout"
                and REP_RE.search(plan[d]["session"]) and d not in {w["d"] for w in wo}), None)
    if nxt:
        d, p = nxt
        el = 0 if "sea level" in p["session"].lower() else HOME_ELEV_FT
        coming.append(("Next workout", f'{cal(d)} · {html.escape(p["session"])}<div class="dim">Target: '
                                       f'{target_txt(rep_target(p["session"], best, el, nr), el, best)}</div>'))
    if wo:
        w = wo[-1]
        coming.append(("Last workout", f'{cal(w["d"])} · {html.escape(w["session"])}<div class="dim">{wo_summary(w)}</div>'))

    # Race: next race (target, current fitness, goal, checkpoint)
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
        how_next = []
        if best and nr["elev"] is not None:
            t = predict(best["vdot"], nr["meters"])
            by = convert_time(t, nr["meters"], src_elev(best), nr["elev"])
            cur = (min(by.values()), max(by.values()))
            panels.append(panel("Current fitness", hms_range(*cur),
                                [("Pace", f"{mmss_range(cur[0] / mi, cur[1] / mi)}/mi"),
                                 ("Basis", f"{html.escape(best['name'])}, {best['d']:%b} {best['d'].day}")]))
            age = (today - best["d"]).days
            parts = " · ".join(f"{k}: {hms(v)}" for k, v in by.items())
            how_next.append(
                f'<p><b>Current fitness</b> (no projected training gains): {html.escape(best["name"])} on '
                f'{best["d"]:%b} {best["d"].day} ({age} day{"s" * (age != 1)} ago), {hms(best["secs"])} at '
                f'{ft(best["elev"]) or "unknown elevation"}, VDOT {best["vdot"]:.1f}. Derived as: that result → VDOT → '
                f'equivalent {dist_label(nr["meters"])} at {ft(src_elev(best))} ({hms(t)}) → converted to {ft(nr["elev"])}: {parts}.</p>')
        else:
            reason = "this race has no elevation in races.csv" if best else "no completed race or time trial yet"
            how_next.append(f"<p>No current-fitness estimate: {reason}.</p>")

        g = nr["goal_secs"]
        race_line = f'{html.escape(nr["name"])}, {cal(nr["date"])} · {days} days'
        if g:
            items = [("Pace", f"{mmss(g / mi)}/mi" + (' <span class="dim">(from goal time)</span>' if nr["goal_from"] == "time" else "")),
                     ("Needs", f"VDOT {vdot(nr['meters'], g):.1f} at {ft(nr['elev']) or 'race elevation'}")]
            if nr["goal_from"] == "pace":
                items.insert(0, ("Time", f'{hms(g)} <span class="dim">(from goal pace)</span>'))
            if cur:
                lo, hi = cur
                vs_now = ("within current range" if lo <= g <= hi else
                          f"{lo - g:.0f}–{hi - g:.0f} s faster" if g < lo else f"{g - hi:.0f}–{g - lo:.0f} s slower")
                items.append(("Vs. now", vs_now))
                race_line += f'<div class="dim">Goal {hms(g)} · now {hms_range(*cur)} ({vs_now.replace("faster", "to find")})</div>'
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
                short = f"{dist_label(m_tt)} time trial"
                rest = label.split("(", 1)[1].rstrip(")") if "(" in label else ""
                panels.append(panel(
                    f"Checkpoint: {short}",
                    f'{hms_range(min(on_track), max(on_track))} <span class="dim">on track</span>',
                    [("Date", f"{d_tt:%a %b} {d_tt.day} · {tdays} days · at {HOME_NAME}"),
                     ("On-track pace", f"{mmss_range(min(on_track) / tt_mi, max(on_track) / tt_mi)}/mi"),
                     ("Goal-equivalent", f"{hms_range(min(full), max(full))} · "
                                         f"{mmss_range(min(full) / tt_mi, max(full) / tt_mi)}/mi")],
                    html.escape(rest)))
                coming.append(("Checkpoint", f"{cal(d_tt)} · {short} · {tdays} days"
                                             f'<div class="dim">On track: {hms_range(min(on_track), max(on_track))} at {HOME_NAME}</div>'))
                how_next.append(
                    f"<p><b>On track</b>: steady progress (linear in VDOT) from current fitness ({best['d']:%b} {best['d'].day}) to "
                    f"{hms(g)} on race day; {d_tt:%b} {d_tt.day} is {frac:.0%} of the way. "
                    f"<b>Goal-equivalent</b>: the {dist_label(m_tt)} you'd run at {HOME_NAME} if already at goal fitness.</p>")
        coming.append(("Race", race_line))
        next_html = (f'<section class="card next"><h2>Next race: {html.escape(nr["name"])}</h2>\n'
                     f'<div class="panels">{"".join(panels)}</div>\n{how("".join(how_next) + p_vdot + p_alt)}\n</section>')

    race_html, races_html = "", ""
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
        race_html = (
            '<section class="card"><h2>Race fitness</h2>\n'
            f'<p class="note">From {basis_phrase(best)}.</p>\n'
            '<div class="grid2">\n'
            f'<div><h3>Equivalent times</h3><table class="t full"><thead>{head}</thead><tbody>{time_rows}</tbody></table></div>\n'
            f'<div><h3>Training paces (per mile)</h3><table class="t full"><thead>{head}</thead><tbody>{pace_rows}{vdot_row}</tbody></table></div>\n'
            f'</div>\n{how(p_vdot + p_alt)}\n</section>')
        rows = "".join(f"<tr><td>{r['d']:%b} {r['d'].day}</td><td>{html.escape(r['name'])}</td>"
                       f"<td class=opt>{r['meters'] / 1000:.2f} km</td><td>{hms(r['secs'])}</td>"
                       f"<td class=opt>{ft(r['elev'])}</td><td class=opt>{r['vdot']:.1f}</td><td>{vrange(*vdot_range(r, 0))}</td></tr>"
                       for r in reversed(rc))
        races_html = (
            '<section class="card"><h2>Races and time trials</h2>\n'
            '<div class="scroll"><table class="t full"><thead><tr><th>Date</th>'
            '<th>Event</th><th class=opt>Distance</th><th>Time</th><th class=opt>Elevation</th><th class=opt>VDOT</th>'
            '<th>VDOT (sea level)</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>\n'
            + how("<p>Each result is the fastest window at the race distance inside that day's run, so a warm-up and "
                  "cool-down recorded in the same activity don't count. <b>VDOT</b> is at the elevation run; "
                  "<b>VDOT (sea level)</b> converts it by both altitude methods.</p>" + p_alt) + '\n</section>')

    # training load: history plus the projection to race day, in one chart
    kpis, proj_how, proj_how_spans = "", "", ""
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
        pd_ = date.fromisoformat(peak["d"])
        kp = [("Race-day form", f"{rp:+.0f}%".replace("-", "−"),
               "fresh" if rp > FRESH else "carrying fatigue" if rp < TIRED else "neutral"),
              ("Peak long-term load", f"{peak['ctl']:.0f}", f"{pd_:%b} {pd_.day} (today {now['ctl']:.0f})")]
        if mx:
            md_ = date.fromisoformat(mx["d"])
            kp.append(("Highest load ramp ahead", f"{mx['acwr']:.2f}", f"{md_:%b} {md_.day} · {acwr_word(mx['acwr'])}"))
            if spans:
                proj_how_spans = (f"<p>Days ahead above {ACWR_SWEET[1]:g}: " + "; ".join(span_txt(s) for s in spans) + ".</p>")
        kpis = '<div class="kpis">' + "".join(
            f'<div><div class="tl">{a}</div><div class="kv1">{b}</div><div class="ts">{c}</div></div>' for a, b, c in kp) + "</div>"
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
        proj_how = (
            "<p><b>Projection</b> (dashed, after today): plan.csv, with each planned mile worth your load per mile over the "
            f"last {RATE_DAYS} days for that kind of run: {rate_txt}. Planned workout days, and before the plan, runs with "
            f"≥ {WORKOUT_Z5_PCT}% of the time in Z5, count as workouts. {xrate_txt}{missing_txt}Planned hikes and XC skis "
            "(named in the session, with minutes in plan.csv's cross_min) count at your load per minute from recorded "
            "sessions of that kind or, until you have any, a default from config.json: "
            + ", ".join(f"{CROSS_NAMES[k].lower()} {v:g}" for k, v in CROSS_DEFAULT_RATE.items()) + " per minute. The hike "
            "default is the Z1 weight: the Pandolf load-carriage equation and Swain's %VO2max-to-%HRmax relation put a "
            "35-lb-pack hike on 0 to 10% grades at about 105 to 130 bpm (data/ref/README.md). Sea-level days and steady "
            "finishes aren't modeled. The chart shows the projection when the time range reaches today.</p>" + proj_how_spans)
    load_html = (
        '<section class="card"><h2>Training load</h2>\n'
        f'<p class="note">Long-term ({CTL_DAYS}-day) and recent ({ATL_DAYS}-day) load'
        + (", planned through race day (dashed)" if proj else "") + '.</p>\n'
        + kpis + '<div class="chart" id="cLoad"></div>\n'
        '<div class="legend"><span><span class="ln" style="background:var(--s1)"></span>Long-term load</span>'
        '<span><span class="ln" style="background:var(--s2)"></span>Recent load</span>'
        + ('<span><span class="ln dash"></span>Planned</span>' if proj else "") + '</div>\n'
        f'<h3>Load ramp: {ACUTE_DAYS}-day ÷ {CHRONIC_DAYS}-day load</h3>\n'
        '<div class="chart short" id="cAcwr"></div>\n'
        f'<div class="legend"><span><i class="band ok"></i>Sweet spot {ACWR_SWEET[0]:g}–{ACWR_SWEET[1]:g}</span>'
        f'<span><i class="band bad"></i>Danger zone ≥ {ACWR_DANGER:g}</span></div>\n'
        + how(p_load + p_ctl + p_form + p_acwr + proj_how + p_cross) + '\n</section>')

    # best efforts
    eff_html = ""
    if efforts:
        cut = today - timedelta(28)
        rows = []
        # races and time trials at a table distance compete too: a race GPS measured a little short of the
        # distance (scored whole) has no window of that length in efforts.csv
        maxhr = {}
        for e in efforts:
            if e["maxhr"] is not None:
                maxhr[e["d"]] = max(maxhr.get(e["d"], 0), e["maxhr"])
        race_eff = []
        for r in rc:
            el = src_elev(r)
            race_eff.append({"d": r["d"], "meters": r["meters"], "secs": r["gap"], "clock": r["secs"], "elev": r["elev"],
                             "net": None, "maxhr": maxhr.get(r["d"]), "sl": sl_vdot(r["meters"], r["gap"], el),
                             "race": r["name"]})
        for m in sorted({e["meters"] for e in efforts}):
            at = [e for e in efforts if e["meters"] == m] + [
                r for r in race_eff if abs(r["meters"] - m) <= m * RACE_WHOLE_RUN_TOL]
            recent = [e for e in at if e["d"] > cut]
            top_all = max(at, key=lambda e: e["sl"])
            top_28 = max(recent, key=lambda e: e["sl"]) if recent else None
            shown = ([("28 days", top_28)] if top_28 else []) + ([("All", top_all)] if top_all is not top_28 else [])
            if top_28 is top_all:
                shown = [("Both", top_28)]  # best of the last 28 days is also the best overall
            for i, (period, e) in enumerate(shown):
                net = "" if e["net"] is None else f"{e['net']:+,}".replace("-", "−")
                rows.append(
                    f"<tr><td>{effort_label(m) if i == 0 else ''}</td><td>{period}</td>"
                    f"<td class=opt>{hms(e['clock'])}</td><td>{hms(e['secs'])}</td><td class=opt>{mmss(e['secs'] / (m / M_PER_MI))}</td>"
                    f"<td>{e['d']:%b} {e['d'].day}</td><td class=opt>{ft(e['elev'])}</td><td class=opt>{net}</td>"
                    f"<td class=opt>{e['maxhr'] or ''}</td><td>{e['sl']:.1f}</td>"
                    f"<td class=opt>{'race: ' + html.escape(e['race']) if e.get('race') else 'training'}</td></tr>")
        eff_html = (
            '<section class="card"><h2>Best efforts</h2>\n'
            '<p class="note">Fastest grade-adjusted time over each distance inside any run: a record, not race fitness.</p>\n'
            '<div class="scroll"><table class="t full"><thead><tr><th>Distance</th><th>Period</th><th class=opt>Time</th>'
            '<th>Grade-adj.</th><th class=opt>Pace (adj.)</th>'
            '<th>Date</th><th class=opt>Elevation</th><th class=opt>Net elev. (ft)</th><th class=opt>Max HR</th><th>VDOT (sea level)</th>'
            f'<th class=opt>Source</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>\n'
            + how('<p>The fastest grade-adjusted time over each distance inside any run, in the last 28 days and in all '
                  'data ("Both" when one effort is best in both). Grade adjustment (Minetti) turns each stretch into its flat-ground '
                  'equivalent, so a downhill stretch is slowed and a hilly one credited; the VDOT is on that time, at sea level, so '
                  'efforts at different elevations compare fairly. A training window can hold recoveries (a whole interval '
                  'session inside a 5K window) and is run below race effort by design, so it is a record, not race fitness: '
                  'only races and time trials set current fitness. On a phone, the table drops the clock time, pace, elevation, '
                  'HR and source columns; turn the phone sideways to see them.</p>')
            + '\n</section>')

    # fitness vs. goal (sea-level VDOT)
    goal = None
    if nr and nr["goal_secs"] and nr["elev"] is not None:
        hpts, hcal = hr_fitness(runs, rc, nr["meters"])
        cal_names = ", ".join(f"{html.escape(r['name'])} ({r['d']:%b} {r['d'].day})" for r in hcal["races"]) if hcal else ""
        goal = {"v": round(sl_vdot(nr["meters"], nr["goal_secs"], nr["elev"]), 2), "race": nr["date"].isoformat(),
                "name": nr["name"], "t": hms(nr["goal_secs"]),
                "races": [{"d": r["d"].isoformat(), "v": round(sl_vdot(r["meters"], r["secs"], src_elev(r)), 2),
                           "name": r["name"], "t": hms(r["secs"])} for r in rc],
                "hr": [{"d": p["d"].isoformat(), "v": round(p["sl"], 2), "gap": mmss(p["gap"]), "hr": round(p["hr"]),
                        "min": round(p["secs"] / 60), "laps": p["laps"], "race": p["race"]} for p in hpts],
                # drawn on to the calibrating race, which it passes through, when that comes after the last estimate
                "slope": {"d0": sl_[0].isoformat(), "d1": max([sl_[1]] + [r["d"] for r in hcal["races"] if hcal]).isoformat(), "v0": round(sl_[2], 2),
                          "perMo": round(sl_[3] * DAYS_PER_MO, 2), "days": 2 * HRFIT_DAYS}
                if (sl_ := hr_slope(hpts, 2 * HRFIT_DAYS)) else None,
                "basis": {"d": best["d"].isoformat(), "v": round(sl_vdot(best["meters"], best["secs"], src_elev(best)), 2)}
                if best else None}
        tt = next_time_trial(plan, today, nr["date"])
        if tt:
            goal["tt"] = {"d": tt[0].isoformat(), "name": tt[2]}
    goal_html = "" if not goal else (
        '<section class="card"><h2>Fitness vs. goal</h2>\n'
        f'<p class="note">Sea-level VDOT. The goal ({goal["t"]} at {ft(nr["elev"])}) needs {goal["v"]:.1f}.</p>\n'
        '<div class="chart" id="cGoal"></div>\n'
        '<div class="legend"><span><span class="dt" style="background:var(--s1)"></span>Race or time trial</span>'
        '<span><span class="dt" style="background:var(--card);box-shadow:inset 0 0 0 1.5px var(--muted)"></span>HR-based estimate (one run)</span>'
        f'<span><span class="ln" style="background:repeating-linear-gradient(90deg,var(--s7) 0 6px,transparent 6px 10px)"></span>Trend, last {2 * HRFIT_DAYS} days'
        + (f' ({goal["slope"]["perMo"]:+.1f} VDOT/mo)' if goal["slope"] else '') + '</span>'
        '<span><span class="dt" style="background:var(--s2)"></span>Goal race</span>'
        '<span><span class="ln" style="background:var(--s2)"></span>Goal</span>'
        '<span><span class="ln dot"></span>On-track path</span></div>\n'
        + how('<p>Sea-level VDOT over time. The dotted line is steady progress from current fitness to the goal on race day, '
              'the same path the checkpoint uses. Races and time trials are measured results. Between them, each small dot '
              'is an HR-based estimate from one run\'s steady laps at or near AeT (pace and HR together, so it needs no '
              'all-out effort; reps and races are left out, since HR lags pace above AeT). '
              + (f'Estimates are scaled (× {hcal["k"]:.3f}) so the trend line passes through {cal_names}. '
                 if hcal else 'No race or time trial with steady laps yet, so estimates are uncalibrated. ')
              + f'The dashed line is the trend over the last {2 * HRFIT_DAYS} days: a Theil–Sen line (the median of pairwise slopes, '
              'Sen 1968, so one odd run can\'t tip it) through the estimates; its rate is in the legend.</p>' + p_hrfit + p_vdot)
        + '\n</section>')

    pfrom = min(prior_miles(date.max), default=first)
    miles_html = (
        '<section class="card"><h2>Volume</h2>\n'
        '<div class="seg vol" id="volUnit"><button data-u="time">Time</button><button data-u="miles">Miles</button>'
        '<button data-u="climb">Climbing</button></div>\n'
        '<p class="note">Mon–Sun totals with 28-day and 12-week averages per week.</p>\n'
        '<div class="chart" id="cMiles"></div>\n'
        '<div class="legend"><span><i style="background:var(--s1)"></i>Weekly total</span>'
        '<span><span class="ln" style="background:var(--s2)"></span>28-day average</span>'
        '<span><span class="ln" style="background:var(--s7)"></span>12-week average</span></div>\n'
        + how("<p>Bars are Monday–Sunday totals of running only (hikes and skis add load, not volume); the current week "
              "is lighter until it ends. The lines are rolling averages per week over the last 28 days and 12 weeks. "
              "The 12-week average is the slower layer: aerobic base built over months.</p>"
              "<p><b>Time</b> is moving time (distance × average pace), so it counts runs without HR, and a trail mile "
              "counts for the time it takes. <b>Climbing</b> is the watch's total ascent (barometer). "
              "The 12-week mileage average includes the weekly totals from before the recorded data "
              f"(data/prior_weeks.csv, from {pfrom:%b} {pfrom.day}); time and climbing weren't reported for those weeks, "
              "so their 12-week averages start 12 weeks into the recorded data.</p>") + '\n</section>')
    eff_chart_html = (
        f'<section class="card"><h2>Aerobic efficiency: pace at {AET} bpm, at {HOME_ELEV_FT:,} ft</h2>\n'
        '<p class="note">Grade-adjusted easy running near AeT. Faster is up.</p>\n'
        '<div class="chart" id="cEff"></div>\n'
        f'<div class="legend"><span><span class="ln" style="background:var(--s7)"></span>{TREND_DAYS}-day median</span>'
        f'<span><span class="dt" style="background:var(--fresh)"></span>Fresh (form &gt; +{FRESH}%)</span>'
        '<span><span class="dt" style="background:var(--neutral)"></span>Neutral</span>'
        f'<span><span class="dt" style="background:var(--tired)"></span>Tired (form &lt; {str(TIRED).replace("-", "−")}%)</span>'
        '<span><span class="dt" style="background:var(--card);box-shadow:inset 0 0 0 1.5px var(--muted)"></span>Race recovery</span></div>\n'
        + how(p_aet) + '\n</section>')
    zleg = ("".join(f'<span><i class="z{i + 1}"></i>{n} {zone_range(lo, hi)}</span>' for i, (n, lo, hi) in enumerate(ZONES))
            + '<span><span class="ln dash"></span>LT1 (AeT) / LT2 (LT)</span>')
    zones_html = (
        '<section class="card"><h2>Intensity by week</h2>\n'
        '<p class="note">Running time at each heart rate, one sideways histogram per week.</p>\n'
        '<div class="chart tall" id="cZones"></div>\n'
        f'<div class="legend">{zleg}</div>\n' + how(p_zones + p_load) + '\n</section>')
    coming_html = ""
    if coming:
        coming_html = ('<section class="card"><h2>Coming up</h2><ul class="up">' + "".join(
            f'<li><span class="k">{k}</span><span class="v">{v}</span></li>' for k, v in coming) + "</ul></section>")
    tiles_html = (f'<div class="tgroups">{"".join(tiles)}</div>'
                  + how(p_ctl + p_form + p_acwr + p_aet + p_vdot, "What these numbers mean"))

    data = {"daily": daily, "weeks": weeks, "eff": pts, "trend": trend, "trendDays": TREND_DAYS, "today": iso_today,
            "aet": AET, "lt": LT, "zanc": [round(a, 1) for a in zone_anchors()], "zones": [f"{n} {zone_range(lo, hi)}" for n, lo, hi in ZONES],
            "breaks": [1, 3],  # zone indexes below the LT1 (AeT) and LT2 (LT) boundaries
            "proj": [{k: x[k] for k in ("d", "ctl", "atl", "formPct", "acwr", "plan", "load", "xload", "s")} for x in proj],
            "acwrSweet": ACWR_SWEET, "acwrDanger": ACWR_DANGER, "goal": goal, "bench": bench}
    sections = {
        "today": tiles_html + coming_html,
        "training": miles_html + load_html + zones_html,
        "fitness": bench_card + goal_html + eff_chart_html + eff_html + races_html,
        "race": next_html + race_html,
    }
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("fitness.html"), "DATA": json.dumps(data, separators=(",", ":")),
            "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}"}
    subs.update({k.upper(): v for k, v in sections.items()})
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs" / "fitness.html").write_text(page, encoding="utf-8")
    print("Wrote docs/fitness.html")


def how(body, label="How it's calculated"):
    """A collapsed methods block."""
    return f'<details class="how"><summary>{label}</summary><div class="howb">{body}</div></details>'


def target_txt(t, elev, basis):
    if not t:
        return "no pace target for this session"
    label, (fast, slow), cap = t
    src = ("from the goal time" if label.startswith("goal") else
           f"from {html.escape(basis['name'])}, {basis['d']:%b} {basis['d'].day}" if basis else "")
    return (f"{label} {mmss_range(fast, slow)}/mi grade-adjusted at {f'{elev:,} ft' if elev else 'sea level'} ({src})"
            + (f" · HR {cap[0]}–{cap[1]} average, {HR_CEIL_PCT}th percentile ≤ {cap[2]}" if cap else ""))


def wo_summary(w):
    reps = w["reps"]
    n_on = sum(r["paceOk"] == "on" for r in reps)
    n_over = sum(r["hrOk"] == "over" for r in reps)
    return (f"Found {len(reps)} of {w['n']} reps" + ("" if len(reps) == w["n"] else
            " (lap each rep with the lap button so they can be matched)")
            + (f". {n_on} on target pace" if w["target"] and reps else "")
            + (f", {n_over} over the HR target" if w["target"] and w["target"][2] and reps else "") + ".")


def workout_blocks(plan):
    """{date: html} for the calendar's day view: each completed rep workout against its targets, and the target for
    each planned one from today on."""
    runs = load_runs()
    today = date.today()
    rc = races(runs, plan)
    nr = next_race(today)
    best = fitness_basis(rc)
    method = how(
        f"<p>Reps are found by effort, not distance (so reps run by time count): the laps of at least {REP_MIN_S} s with "
        f"average HR at or above {AET} bpm and the fastest grade-adjusted pace, so press lap at the start and end of each "
        f"rep. Threshold reps aim for an average HR of {THRESH_HR[0]}–{THRESH_HR[1]} with the {HR_CEIL_PCT}th-percentile "
        f"HR at or below {HR_CEIL} (a ceiling that ignores brief spikes); the first rep may sit a little low while HR "
        f"catches up. Pace is judged on grade-adjusted pace (Minetti), within {REP_PACE_TOL} s/mi of the target range, "
        "which comes from current fitness at the time (Daniels threshold, 5K, 10K and interval paces from the latest race "
        "or time trial) or the goal time, converted to the run's elevation.</p>", "How reps are matched")
    out = {}
    pace_word = {"on": ("on", "ok"), "fast": ("fast", "warn"), "slow": ("slow", "warn"), None: ("", "")}
    hr_word = {"in": ("in", "ok"), "low": ("low", "warn"), "settling": ("low (1st)", ""), "over": ("over", "bad"), None: ("", "")}
    for w in workouts(runs, plan, rc, nr):
        b = fitness_basis(rc, w["d"])
        cell = lambda wd: f'<td class="{wd[1]}">{wd[0]}</td>'
        body = "".join(
            f"<tr><td>{i} <small>lap {r['lap']}</small></td><td>{hms(r['secs'])}</td><td><b>{mmss(r['gap'])}</b></td>"
            f"<td><b>{r['hr'] or ''}</b></td><td>{r['pctl'] or ''}</td>{cell(pace_word[r['paceOk']])}{cell(hr_word[r['hrOk']])}</tr>"
            for i, r in enumerate(w["reps"], 1))
        table = ('<table class="laps reps"><thead><tr><th>Rep</th><th>Time</th><th>GAP</th><th>HR</th>'
                 f'<th>{HR_CEIL_PCT}% HR</th><th>Pace</th><th>HR</th></tr></thead><tbody>{body}</tbody></table>') if w["reps"] else ""
        out[w["d"]] = (f'<div class="wo"><h3>Workout vs. target</h3><p class="wot">Target: {target_txt(w["target"], w["elev"], b)}.</p>'
                       f'<p class="wot">{wo_summary(w)}</p>{table}{method}</div>')
    for d in sorted(plan):
        p = plan[d]
        if d < today or d in out or p["type"] != "workout" or not REP_RE.search(p["session"]):
            continue
        el = 0 if "sea level" in p["session"].lower() else HOME_ELEV_FT
        out[d] = (f'<div class="wo"><h3>Target</h3><p class="wot">{target_txt(rep_target(p["session"], best, el, nr), el, best)}. '
                  'Lap each rep.</p>' + method + '</div>')
    return out


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Running Fitness</title>
<style>
%%BASE_CSS%%
.tgroups { display: grid; grid-template-columns: 3fr 2fr; gap: 10px 14px; margin: 0 0 18px; }
.tgroup:first-child { grid-column: 1 / -1; }  /* time on feet: its own row, first */
.tgh { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 0 0 5px 2px; }
.tiles { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 8px; height: calc(100% - 22px); }
@media (max-width: 960px) { .tgroups { grid-template-columns: 1fr; } .tiles { height: auto; } }
@media (max-width: 600px) { .tiles { grid-auto-flow: row; grid-template-columns: 1fr 1fr; } }
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
/* section tabs: stick under the site bar */
.stick { position: sticky; top: var(--topbar); z-index: 6; background: var(--bg); padding: 4px 0 10px; margin-top: -8px; }
.subtabs { display: flex; max-width: 560px; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.subtabs button { flex: 1; font: inherit; font-size: 14px; font-weight: 500; color: var(--muted); background: var(--card);
  border: 0; padding: 0 8px; min-height: 40px; cursor: pointer; }
.subtabs button + button { border-left: 1px solid var(--line); }
.subtabs button[aria-selected="true"] { background: var(--ink); color: var(--bg); }
.range { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; padding: 0 0 10px; }
.range[hidden], .sec[hidden] { display: none; }
@media (min-width: 761px) { .range { position: sticky; top: calc(var(--topbar) + 50px); z-index: 5; background: var(--bg); } }
.range button { font: inherit; font-size: 13px; color: var(--ink); background: var(--card); border: 1px solid var(--line);
  border-radius: 6px; padding: 0 12px; min-height: 36px; min-width: 40px; cursor: pointer; }
.range .narrow { display: none; }
.range button:disabled { opacity: .4; cursor: default; }
.range .seg { display: inline-flex; } .range .seg button { border-radius: 0; margin-left: -1px; }
.range .seg button:first-child { border-radius: 6px 0 0 6px; margin-left: 0; } .range .seg button:last-child { border-radius: 0 6px 6px 0; }
.range .seg button[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
.seg.vol { display: inline-flex; margin: 4px 0 6px; }
.seg.vol button { font: inherit; font-size: 13px; color: var(--ink); background: var(--card); border: 1px solid var(--line);
  border-radius: 0; margin-left: -1px; padding: 0 12px; min-height: 32px; cursor: pointer; }
.seg.vol button:first-child { border-radius: 6px 0 0 6px; margin-left: 0; } .seg.vol button:last-child { border-radius: 0 6px 6px 0; }
.seg.vol button[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
#rLabel { min-width: 120px; text-align: center; font-variant-numeric: tabular-nums; font-size: 13px; }
.chart svg { display: block; width: 100%; height: 100%; overflow: visible; }
.chart.short { height: 150px; } .chart.tall { height: 320px; }
.zh text.hrs { fill: var(--muted); font-size: 11px; } .zh .thr { stroke: var(--ink); stroke-width: 1; stroke-dasharray: 3 3; opacity: .6; }
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
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px 24px; margin-top: 8px; }
.panel h3 { margin: 0 0 2px; } .panel .big { font-size: 24px; font-weight: 600; margin: 2px 0 4px; } .panel .big .dim { font-size: 13px; font-weight: 400; }
.panel .kv td { padding: 2px 12px 2px 0; } .panel .note { margin: 6px 0 0; }
.est { display: flex; gap: 28px; align-items: flex-start; } .est small { font-size: 13px; font-weight: 400; color: var(--muted); margin-left: 2px; }
.next .note b { color: var(--ink); } .dim { color: var(--muted); font-size: 12px; }
details.how { margin-top: 8px; }
details.how > summary { display: inline-block; padding: 6px 0; font-size: 12px; color: var(--muted); }
details.how[open] > summary { color: var(--ink); }
.howb p { font-size: 13px; color: var(--muted); margin: 4px 0 8px; max-width: 80ch; }
.howb b { color: var(--ink); font-weight: 600; }
.tgroups + details.how { margin: -10px 0 14px 2px; }
table.t.full td { white-space: nowrap; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 8px 20px; margin: 8px 0 4px; }
.kpis .kv1 { font-size: 20px; font-weight: 600; } .kpis .ts, .kpis .tl { font-size: 12px; color: var(--muted); }
ul.up { list-style: none; margin: 6px 0 0; padding: 0; }
ul.up li { display: grid; grid-template-columns: 120px 1fr; gap: 2px 12px; padding: 8px 0; border-top: 1px solid var(--line); }
ul.up li:first-child { border-top: 0; }
ul.up .k { color: var(--muted); font-size: 13px; } ul.up .v .dim { display: block; margin-top: 2px; }
ul.up a, .next a { color: inherit; text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 3px; }
footer { color: var(--muted); font-size: 12px; margin-top: 18px; }
@media (max-width: 760px) {
  main { padding: 0 12px 40px; }
  header h1 { font-size: 22px; }
  .card { padding: 12px; }
  .chart { height: 210px; } .chart.tall { height: 280px; } .chart.short { height: 130px; }
  .tv { font-size: 22px; }
  #rLabel, .range .hint { display: none; }
  .range .wide { display: none; } .range .narrow { display: inline; }
  .range .seg button { padding: 0 10px; }
  .opt { display: none; }
  ul.up li { grid-template-columns: 1fr; }
  .panels { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<main>
%%TABS%%
<header>
  <h1>Running Fitness</h1>
  <p class="sub">Built from completed runs only · updated %%BUILT%%</p>
</header>
<div class="stick"><nav class="subtabs" role="tablist" aria-label="Sections">
<button role="tab" data-sec="today">Today</button><button role="tab" data-sec="training">Training</button><button role="tab" data-sec="fitness">Fitness</button><button role="tab" data-sec="race">Race</button>
</nav></div>
<div class="range" role="group" aria-label="Chart time range" hidden>
  <div class="seg" id="rSpan"><button data-span="28">4 wk</button><button data-span="56">8 wk</button><button data-span="84">12 wk</button><button data-span="0">All</button></div>
  <button id="rPrev" aria-label="Earlier">‹</button><span id="rLabel"></span><button id="rNext" aria-label="Later">›</button>
  <button id="rLatest" aria-label="Latest"><span class="wide">Latest</span><span class="narrow">»</span></button>
  <span class="dim hint">Drag a chart to scroll · pinch or Ctrl + scroll to zoom · tap or hover for values</span>
</div>
<section class="sec" id="s-today" role="tabpanel">
%%TODAY%%
</section>
<section class="sec" id="s-training" role="tabpanel" hidden>
%%TRAINING%%
</section>
<section class="sec" id="s-fitness" role="tabpanel" hidden>
%%FITNESS%%
</section>
<section class="sec" id="s-race" role="tabpanel" hidden>
%%RACE%%
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
    var w = tip.offsetWidth, h = tip.offsetHeight, x, y;
    if (ev.pointerType === "touch") {  // above the finger, so the thumb doesn't cover it
      x = Math.max(8, Math.min(window.innerWidth - w - 8, ev.clientX - w / 2));
      y = ev.clientY - h - 32; if (y < 8) y = ev.clientY + 32;
    } else {
      x = ev.clientX + 14; y = ev.clientY + 14;
      if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
      if (y + h > window.innerHeight - 8) y = ev.clientY - h - 14;
    }
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  var unpin = null;  // undoes the highlight of the tooltip pinned by a tap
  function hideTip() { tip.style.display = "none"; if (unpin) { var u = unpin; unpin = null; u(); } }
  // hover with a mouse; a tap pins the tooltip until the next tap, a swipe or a scroll
  function hov(target, show, leave) {
    target.addEventListener("pointermove", function (ev) { if (ev.pointerType !== "touch") show(ev); });
    target.addEventListener("pointerleave", function (ev) { if (ev.pointerType !== "touch") { leave(); hideTip(); } });
    target.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType !== "touch") return;
      hideTip(); show(ev); unpin = leave; ev.hitTip = true;
    });
  }
  document.addEventListener("pointerdown", function (ev) { if (ev.pointerType === "touch" && !ev.hitTip) hideTip(); });
  window.addEventListener("scroll", function () { if (unpin) hideTip(); }, { passive: true });
  function sw(v) { return '<i style="background:var(' + v + ')"></i>'; }

  // Frame: time x-axis, linear y-axis with hairline grid
  function frame(id, x0, x1, y0, y1, yTicks, yFmt, invert) {
    var box = document.getElementById(id); box.innerHTML = "";
    var W = box.clientWidth, H = box.clientHeight, m = { l: W < 500 ? 34 : 40, r: W < 500 ? 8 : 14, t: 10, b: 22 };
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
  var span = window.innerWidth < 600 ? 56 : 84, vEnd = LAST, x0, x1;  // visible window [x0, x1) in days; 12 weeks (8 on a phone)
  var MIN_SPAN = 14;  // narrowest zoom
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

  // volume chart: time (default; a trail mile takes longer), miles or climbing
  var VOL = {
    time: { w: "min", a: "m28", b: "m84", sc: 1 / 60, step: 1, fmt: function (v) { return v + " h"; },
            tip: function (v) { return Math.floor(v / 60) + ":" + String(Math.round(v % 60)).padStart(2, "0"); }, u: " h:mm" },
    miles: { w: "mi", a: "r28", b: "r84", sc: 1, step: 10, fmt: function (v) { return v; }, tip: function (v) { return v; }, u: " mi" },
    climb: { w: "ft", a: "f28", b: "f84", sc: 1, step: 500, fmt: function (v) { return v ? (v / 1000) + "k" : 0; },
             tip: function (v) { return Math.round(v).toLocaleString("en-US"); }, u: " ft" }
  };
  var unit = "time";
  try { unit = localStorage.getItem("volUnit") || unit; } catch (e) {}
  function setUnit() {
    Array.prototype.forEach.call(document.querySelectorAll("#volUnit button"), function (b) {
      b.setAttribute("aria-pressed", b.dataset.u === unit ? "true" : "false");
      b.onclick = function () { unit = b.dataset.u; try { localStorage.setItem("volUnit", unit); } catch (e) {} setUnit(); if (vis("cMiles")) miles(); };
    });
  }
  setUnit();
  function miles() {
    var U = VOL[unit] || VOL.time, sc = U.sc;
    var mx = Math.max.apply(null, D.weeks.map(function (w) { return w[U.w] * sc; }).concat(D.daily.map(function (x) { return Math.max(x[U.a] || 0, x[U.b] || 0) * sc; })));
    var st = U.step; while (mx / st > 8) st *= 2;
    var top = Math.max(st, Math.ceil(mx / st) * st), ticks = []; for (var v = 0; v <= top; v += st) ticks.push(v);
    // at the latest date, pad right so the current (partial) week bar, drawn mid-week, stays visible
    var f = frame("cMiles", x0, x1 + (x1 >= LAST ? 4 : 0), 0, top, ticks, U.fmt);
    monthTicks(f, x0, x1);
    var band = f.X(7) - f.X(0), bw = Math.min(24, band - 6);
    D.weeks.forEach(function (w) {
      var cx = f.X(day(w.w) + 3.5);
      el("path", { d: colPath(cx - bw / 2, bw, f.Y(w[U.w] * sc), f.Y(0)), fill: "var(--s1)", opacity: w.partial ? .45 : 1 }, f.svg);
    });
    [[U.b, "--s7"], [U.a, "--s2"]].forEach(function (s) {
      var pts = D.daily.filter(function (x) { return x[s[0]] != null; }).map(function (x) { return [day(x.d) + .5, x[s[0]] * sc]; });
      el("path", { d: linePath(f, pts), fill: "none", stroke: "var(" + s[1] + ")", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
      var last = pts[pts.length - 1];
      if (last) el("circle", { cx: f.X(last[0]), cy: f.Y(last[1]), r: 4, fill: "var(" + s[1] + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
    });
    var hl = el("rect", { "class": "hl", y: f.m.t, height: f.H - f.m.t - f.m.b, width: band, display: "none" }, f.svg);
    D.weeks.forEach(function (w) {
      var xs = f.X(day(w.w)), end = D.daily.filter(function (x) { return day(x.d) <= day(w.w) + 6; }).pop();
      var hit = el("rect", { "class": "hit", x: xs, y: f.m.t, width: band, height: f.H - f.m.t - f.m.b }, f.svg);
      hov(hit, function (ev) {
        hl.setAttribute("x", xs); hl.removeAttribute("display");
        showTip("<b>Week of " + fmtDate(w.w) + (w.partial ? " (so far)" : "") + "</b><br>" + sw("--s1") + VOL.time.tip(w.min) + " · " + w.mi + " mi · " +
          VOL.climb.tip(w.ft) + " ft · " + w.runs + " runs" +
          (end && end[U.a] != null ? "<br>" + sw("--s2") + "28-day avg " + U.tip(end[U.a]) + U.u + "/wk" : "") +
          (end && end[U.b] != null ? "<br>" + sw("--s7") + "12-week avg " + U.tip(end[U.b]) + U.u + "/wk" : ""), ev);
      }, function () { hl.setAttribute("display", "none"); });
    });
  }

  // load and load ramp: history, then (when the window reaches today) the projection through race day
  var LD = D.proj.length ? D.proj : D.daily;
  function loadEnd() { return x1 >= LAST && D.proj.length ? Math.max(x1, day(D.proj[D.proj.length - 1].d) + 1) : x1; }
  function isFuture(x) { return x.plan || x.d > D.today; }
  function splitLine(f, pts, key, color) {  // solid through today, dashed after
    var k = pts.findIndex(isFuture); if (k < 0) k = pts.length;
    var past = pts.slice(0, k), fut = pts.slice(Math.max(0, k - 1));
    var xy = function (x) { return [day(x.d) + .5, x[key]]; };
    if (past.length) el("path", { d: linePath(f, past.map(xy)), fill: "none", stroke: "var(" + color + ")", "stroke-width": 2, "stroke-linejoin": "round" }, f.svg);
    if (fut.length > 1) el("path", { d: linePath(f, fut.map(xy)), fill: "none", stroke: "var(" + color + ")", "stroke-width": 2, "stroke-dasharray": "5 4" }, f.svg);
  }
  function lineHover(f, a, b, pts, html, keys) {  // guide line and dots at the day under the pointer
    var guide = el("line", { "class": "guide", y1: f.m.t, y2: f.H - f.m.b, display: "none" }, f.svg);
    var dots = keys.map(function (k) { return el("circle", { r: 4, fill: "var(" + k[1] + ")", stroke: "var(--card)", "stroke-width": 2, display: "none" }, f.svg); });
    var hit = el("rect", { "class": "hit", x: f.m.l, y: f.m.t, width: f.W - f.m.l - f.m.r, height: f.H - f.m.t - f.m.b }, f.svg);
    var p0 = day(pts[0].d);
    hov(hit, function (ev) {
      var r = f.root.getBoundingClientRect(), px = (ev.clientX - r.left) * f.W / r.width;
      var dd = Math.floor(a + (px - f.m.l) / (f.W - f.m.l - f.m.r) * (b - a));
      var x = pts[Math.max(0, Math.min(pts.length - 1, dd - p0))], cx = f.X(day(x.d) + .5);
      guide.setAttribute("x1", cx); guide.setAttribute("x2", cx); guide.removeAttribute("display");
      keys.forEach(function (k, i) {
        if (x[k[0]] == null) { dots[i].setAttribute("display", "none"); return; }
        dots[i].setAttribute("cx", cx); dots[i].setAttribute("cy", f.Y(x[k[0]])); dots[i].removeAttribute("display");
      });
      showTip(html(x), ev);
    }, function () { guide.setAttribute("display", "none"); dots.forEach(function (d) { d.setAttribute("display", "none"); }); });
  }
  function loadTip(x) {
    return "<b>" + fmtDate(x.d) + "</b>" + (isFuture(x) ? " · planned" : x.load ? " · load " + x.load : "") + (x.s ? "<br>" + x.s : "") +
      (x.xload ? "<br>Cross-training load " + x.xload : "") +
      "<br>" + sw("--s1") + "Long-term load " + x.ctl.toFixed(0) + "<br>" + sw("--s2") + "Recent load " + x.atl.toFixed(0) +
      "<br>Form going in " + (x.formPct > 0 ? "+" : "") + x.formPct.toFixed(0) + "%" +
      (x.acwr != null ? "<br>Load ramp " + x.acwr.toFixed(2) : "");
  }
  function markToday(f, b) {
    if (b <= LAST) return;
    vline(f, LAST, "today");
    var l = LD[LD.length - 1];
    vline(f, day(l.d) + .5, "guide");
    el("text", { "class": "lbl", x: f.X(day(l.d) + .5) - 6, y: f.m.t + 10, "text-anchor": "end" }, f.svg).textContent = "Race";
  }
  function load() {
    // y-axis fits all dates, so it stays fixed while panning; lines don't need a zero baseline
    var b = loadEnd(), vals = [].concat.apply([], LD.map(function (x) { return [x.ctl, x.atl]; }));
    var mx = Math.max.apply(null, vals), mn = Math.min.apply(null, vals);
    var step = nice((mx - mn) / 4), bot = Math.floor(mn / step) * step, top = Math.ceil(mx / step) * step, ticks = [];
    for (var v = bot; v <= top; v += step) ticks.push(v);
    var f = frame("cLoad", x0, b, bot, top, ticks, function (v) { return v; });
    monthTicks(f, x0, b);
    markToday(f, b);
    splitLine(f, LD, "atl", "--s2"); splitLine(f, LD, "ctl", "--s1");
    lineHover(f, x0, b, LD, loadTip, [["ctl", "--s1"], ["atl", "--s2"]]);
  }
  function acwr() {
    var b = loadEnd(), pts = LD.filter(function (x) { return x.acwr != null; });
    if (!pts.length) return;
    var vs = pts.map(function (x) { return x.acwr; });
    var lo = Math.min(0.5, Math.floor(Math.min.apply(null, vs) * 4) / 4), hi = Math.max(1.75, Math.ceil(Math.max.apply(null, vs) * 4) / 4), ticks = [];
    for (var v = lo; v <= hi + 1e-9; v += 0.25) if (Math.abs(v * 2 - Math.round(v * 2)) < 1e-9) ticks.push(v);
    var f = frame("cAcwr", x0, b, lo, hi, ticks, function (v) { return v.toFixed(1); });
    monthTicks(f, x0, b);
    el("rect", { "class": "bandok", x: f.m.l, width: f.W - f.m.l - f.m.r, y: f.Y(D.acwrSweet[1]), height: f.Y(D.acwrSweet[0]) - f.Y(D.acwrSweet[1]) }, f.svg);
    el("rect", { "class": "bandbad", x: f.m.l, width: f.W - f.m.l - f.m.r, y: f.Y(hi), height: f.Y(D.acwrDanger) - f.Y(hi) }, f.svg);
    markToday(f, b);
    splitLine(f, pts, "acwr", "--s7");
    lineHover(f, x0, b, LD, loadTip, [["acwr", "--s7"]]);
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
      hov(hit, function (ev) {
        c.setAttribute("r", 6);
        showTip("<b>" + fmtDate(p.d) + "</b> · " + p.mi.toFixed(1) + " mi<br>" + mmss(p.p) + "/mi at " + D.aet + " bpm<br>" +
          Math.round(p.n / 60) + " min of qualifying running<br>" + sw("--" + p.cls) + "Form going in " + (p.formPct > 0 ? "+" : "") + p.formPct.toFixed(0) + "%" +
          (p.why ? "<br>" + p.why.charAt(0).toUpperCase() + p.why.slice(1) + " (not in trend)" : ""), ev);
      }, function () { c.setAttribute("r", p.why ? 4 : 4.5); });
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
    var W = box.clientWidth, H = box.clientHeight, m = { l: W < 500 ? 34 : 40, r: 50, t: 22, b: 22 };
    var svg = el("svg", { "class": "zh", viewBox: "0 0 " + W + " " + H }, box);
    // HR range: all visible weeks together, trimmed of the 0.5% tails at each end, to whole 10s
    var tot = {}, all = 0;
    ws.forEach(function (w) { w.hh.forEach(function (p) { tot[p[0]] = (tot[p[0]] || 0) + p[1]; all += p[1]; }); });
    var bs = Object.keys(tot).map(Number).sort(function (a, b) { return a - b; }), cum = 0, lo = bs[0], hi = bs[bs.length - 1], i;
    for (i = 0; i < bs.length; i++) { cum += tot[bs[i]]; if (cum > all * .005) { lo = bs[i]; break; } }
    cum = 0; for (i = bs.length - 1; i >= 0; i--) { cum += tot[bs[i]]; if (cum > all * .005) { hi = bs[i]; break; } }
    lo = Math.floor(Math.min(lo, D.aet - 5) / 10) * 10; hi = Math.ceil(Math.max(hi, D.lt + 3) / 10) * 10;
    var Y = function (b) { return H - m.b - (b - lo) / (hi - lo + 1) * (H - m.t - m.b); };  // bottom edge of bin b
    var g = el("g", { "class": "grid" }, svg), ax = el("g", { "class": "axis" }, svg);
    for (var v = lo; v <= hi; v += 10) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v + .5), y2: Y(v + .5) }, g);
      el("text", { x: m.l - 6, y: Y(v + .5) + 4, "text-anchor": "end" }, ax).textContent = v;
    }
    var band = (W - m.l - m.r) / ws.length, len = band - 10;
    var peak = Math.max.apply(null, ws.map(function (w) { return Math.max.apply(null, w.hh.map(function (p) { return p[0] >= lo && p[0] <= hi ? p[1] : 0; })); }));
    var A = D.zanc, grad = el("linearGradient", { id: "zramp", gradientUnits: "userSpaceOnUse", x1: 0, x2: 0,
      y1: Y(A[0] + .5), y2: Y(A[A.length - 1] + .5) }, el("defs", {}, svg));  // same continuous ramp as the calendar
    A.forEach(function (b, j) { el("stop", { offset: (b - A[0]) / (A[A.length - 1] - A[0]), style: "stop-color:var(--z" + (j + 1) + ")" }, grad); });
    [[D.aet, "LT1 " + D.aet], [D.lt, "LT2 " + D.lt]].forEach(function (t) {
      el("line", { "class": "thr", x1: m.l, x2: W - m.r, y1: Y(t[0]), y2: Y(t[0]) }, svg);
      el("text", { x: W - m.r + 4, y: Y(t[0]) + 4 }, ax).textContent = t[1];
    });
    ws.forEach(function (w, i) {
      var cx = m.l + band * (i + .5), x = cx - band / 2 + 5, d = "";
      w.hh.forEach(function (p) {
        if (p[0] < lo || p[0] > hi) return;
        d += "M" + x.toFixed(1) + "," + Y(p[0] + 1).toFixed(1) + "h" + (p[1] / peak * len).toFixed(1) +
          "V" + Y(p[0]).toFixed(1) + "H" + x.toFixed(1) + "Z";
      });
      el("path", { d: d, fill: "url(#zramp)", opacity: w.partial ? .55 : 1 }, svg);
      el("text", { "class": "hrs", "data-i": i, x: x, y: m.t - 8 }, svg).textContent = w.runH + " h" + (w.partial ? " so far" : "");
      el("text", { "class": "wk", "data-i": i, x: cx, y: H - 6, "text-anchor": "middle" }, ax).textContent = fmtDate(w.w);
      var hl = el("rect", { "class": "hl", x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b, display: "none" }, svg);
      var hit = el("rect", { "class": "hit", x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b }, svg);
      var at = {}; w.hh.forEach(function (p) { at[p[0]] = p[1]; });
      hov(hit, function (ev) {
        hl.removeAttribute("display");
        var r = svg.getBoundingClientRect(), b = Math.floor(lo + (H - m.b - (ev.clientY - r.top) * H / r.height) / (H - m.t - m.b) * (hi - lo + 1));
        var b1 = D.breaks[0], b2 = D.breaks[1], sum = function (a, b) { return w.z.slice(a, b).reduce(function (s, x) { return s + x; }, 0); };
        showTip("<b>Week of " + fmtDate(w.w) + (w.partial ? " (so far)" : "") + "</b> · " + w.runH + " h · " + w.mi + " mi<br>" +
          (w.runH - w.hrH >= .1 ? "Histogram: the " + w.hrH + " h with HR (runs without HR, like manual ones, aren't in it)<br>" : "") +
          (b >= lo && b <= hi ? b + " bpm: " + ((at[b] || 0) / 60).toFixed(1) + " min<br>" : "") +
          "Below LT1 " + sum(0, b1 + 1) + "% · LT1–LT2 " + sum(b1 + 1, b2 + 1) + "% · Above LT2 " + sum(b2 + 1) + "%<br>" +
          D.zones.map(function (n, k) { return sw("--z" + (k + 1)) + n + ": " + w.z[k] + "%"; }).join("<br>"), ev);
      }, function () { hl.setAttribute("display", "none"); });
    });
    // thin week labels and hours on narrow screens
    if (band < 44) Array.prototype.forEach.call(svg.querySelectorAll("text.wk, text.hrs"), function (t) {
      if (+t.getAttribute("data-i") % 2) t.textContent = ""; });
  }

  function dot(f, cx, cy, r, filled, color) {
    return filled ? el("circle", { cx: cx, cy: cy, r: r, fill: "var(" + color + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg)
                  : el("circle", { cx: cx, cy: cy, r: r - .5, fill: "var(--card)", stroke: "var(--muted)", "stroke-width": 1.5 }, f.svg);
  }
  function hover(f, mark, html, r) {  // tooltip on a dot, with a hit target bigger than the mark
    var hit = el("circle", { "class": "hit", cx: mark.getAttribute("cx"), cy: mark.getAttribute("cy"), r: 11 }, f.svg);
    hov(hit, function (ev) { mark.setAttribute("r", r + 2); showTip(html, ev); }, function () { mark.setAttribute("r", r); });
  }
  function hline(f, v, cls) { return el("line", { "class": cls, x1: f.m.l, x2: f.W - f.m.r, y1: f.Y(v), y2: f.Y(v) }, f.svg); }
  function vline(f, d, cls) { return el("line", { "class": cls, x1: f.X(d), x2: f.X(d), y1: f.m.t, y2: f.H - f.m.b }, f.svg); }

  function goalChart() {
    var G = D.goal; if (!G) return;
    var all = G.races.concat(G.hr), ds = all.map(function (p) { return day(p.d); }).concat([day(D.today)]);
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
    if (G.slope) {  // Theil-Sen line through the recent estimates
      var S = G.slope, x0 = day(S.d0) + .5, x1 = day(S.d1) + .5, v1 = S.v0 + S.perMo * 12 / 365.25 * (x1 - x0);
      var sl = el("line", { x1: f.X(x0), y1: f.Y(S.v0), x2: f.X(x1), y2: f.Y(v1), stroke: "var(--s7)", "stroke-width": 2, "stroke-dasharray": "6 4" }, f.svg);
      hover(f, sl, "<b>Trend</b>, last " + S.days + " days<br>" + (S.perMo >= 0 ? "+" : "") + S.perMo.toFixed(2) +
        " VDOT per month (Theil–Sen line through the HR-based estimates)", 2);
    }
    G.hr.forEach(function (p) {
      var c = dot(f, f.X(day(p.d) + .5), f.Y(p.v), 3.5, false);
      c.setAttribute("opacity", .55);
      hover(f, c, "<b>" + fmtDate(p.d) + "</b> · HR-based estimate<br>" + p.min + " min in " + p.laps + " steady lap" +
        (p.laps > 1 ? "s" : "") + " · " + p.gap + "/mi grade-adjusted at " + p.hr + " bpm<br>VDOT " + p.v.toFixed(1) +
        " (sea level)" + (p.race ? " · race, sets the calibration" : ""), 3.5);
    });
    G.races.forEach(function (p) {
      var c = dot(f, f.X(day(p.d) + .5), f.Y(p.v), 5.5, true, "--s1");
      hover(f, c, "<b>" + fmtDate(p.d) + "</b> · " + p.name + "<br>" + p.t + " · VDOT " + p.v.toFixed(1) + " (sea level)", 5.5);
    });
    var gx = f.X(day(G.race) + .5), gy = f.Y(G.v);  // the goal race: where the on-track path ends
    var g = dot(f, gx, gy, 6, true, "--s2");
    el("text", { "class": "lbl", x: gx - 10, y: gy + 18, "text-anchor": "end" }, f.svg).textContent = G.name;
    hover(f, g, "<b>" + fmtDate(G.race) + "</b> · " + G.name + "<br>Goal " + G.t + " · VDOT " + G.v.toFixed(1) + " (sea level)", 6);
  }
  function vis(id) { var e = document.getElementById(id); return e && e.offsetParent !== null; }
  function benchChart() {
    var B = D.bench.filter(function (p) { return p.adj; }); if (!B.length) return;
    var names = []; B.forEach(function (p) { if (names.indexOf(p.n) < 0) names.push(p.n); }); names.sort().reverse();
    var ds = B.map(function (p) { return day(p.d); }), a = Math.min.apply(null, ds) - 7, b = Math.max(Math.max.apply(null, ds), day(D.today)) + 7;
    var vs = B.map(function (p) { return p.adj; }), lo = Math.floor(Math.min.apply(null, vs) / 30) * 30 - 30, hi = Math.ceil(Math.max.apply(null, vs) / 30) * 30 + 30;
    var step = hi - lo > 360 ? 120 : 60, ticks = []; for (var v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);
    var f = frame("cBench", a, b, lo, hi, ticks, mmss, true);
    monthTicks(f, a, b);
    names.forEach(function (n, k) {
      var col = k ? "--s2" : "--s1", P = B.filter(function (p) { return p.n === n; });
      if (P.length > 1) el("path", { d: linePath(f, P.map(function (p) { return [day(p.d) + .5, p.adj]; })), fill: "none", stroke: "var(" + col + ")",
                                     "stroke-width": 1.5, opacity: .6 }, f.svg);
      P.forEach(function (p) {
        var c = dot(f, f.X(day(p.d) + .5), f.Y(p.adj), 5, true, col);
        hover(f, c, "<b>" + fmtDate(p.d) + "</b> · " + p.n + "<br>" + mmss(p.t) + " (" + mmss(p.t / p.mi) + "/mi) at " + p.hr + " bpm<br>" +
          sw(col) + mmss(p.adj) + " at " + D.aet + " bpm", 5);
      });
    });
  }
  function renderStatic() { if (vis("cGoal")) goalChart(); if (vis("cBench")) benchChart(); }
  function render() {
    hideTip(); setWindow();
    [["cMiles", miles], ["cLoad", load], ["cAcwr", acwr], ["cEff", eff], ["cZones", zones]].forEach(function (c) { if (vis(c[0])) c[1](); });
  }
  function shift(days) { vEnd += days; render(); }
  document.getElementById("rPrev").onclick = function () { shift(-7); };
  document.getElementById("rNext").onclick = function () { shift(7); };
  document.getElementById("rLatest").onclick = function () { vEnd = LAST; render(); };
  Array.prototype.forEach.call(document.querySelectorAll("#rSpan button"), function (b) {
    b.onclick = function () { span = +b.dataset.span; render(); };
  });
  // drag (mouse) or swipe (touch) on a time chart to pan
  var PAN = ["cMiles", "cLoad", "cAcwr", "cEff", "cZones"].filter(function (id) { return document.getElementById(id); });
  PAN.forEach(function (id) {
    var box = document.getElementById(id), startX = null, startEnd, raf = 0;
    box.addEventListener("pointerdown", function (ev) { startX = ev.clientX; startEnd = vEnd; box.setPointerCapture(ev.pointerId); });
    box.addEventListener("pointermove", function (ev) {
      if (startX === null || pinching) return;
      var plotW = box.clientWidth - 54, dd = Math.round((startX - ev.clientX) / plotW * (x1 - x0));
      if (dd !== vEnd - startEnd && !raf) raf = requestAnimationFrame(function () { raf = 0; hideTip(); vEnd = startEnd + dd; render(); });
    });
    ["pointerup", "pointercancel"].forEach(function (t) { box.addEventListener(t, function () { startX = null; }); });
  });
  var pinching = false;  // two fingers down: zoom, not pan
  // zoom: Ctrl + wheel (a trackpad pinch arrives as one) or a two-finger pinch, keeping the day under the pointer in place
  function zoomAt(factor, box, clientX) {
    var all = LAST - FIRST, sp = x1 - x0, r = box.getBoundingClientRect();
    var frac = Math.max(0, Math.min(1, (clientX - r.left - 40) / (r.width - 54)));
    var nsp = Math.max(Math.min(MIN_SPAN, all), Math.min(all, Math.round(sp * factor)));
    if (nsp === sp) return;
    var a = x0 + frac * sp;
    span = nsp >= all ? 0 : nsp;
    vEnd = Math.round(a - frac * nsp) + nsp;
    hideTip(); render();
  }
  PAN.forEach(function (id) {
    var box = document.getElementById(id), touches = {}, pinch0 = null;
    box.addEventListener("wheel", function (ev) {
      if (!ev.ctrlKey) return;  // a plain wheel scrolls the page
      ev.preventDefault();
      zoomAt(Math.exp(ev.deltaY * 0.004), box, ev.clientX);  // a mouse notch (100) is about 1.5×
    }, { passive: false });
    function gap() { var p = Object.values(touches); return Math.abs(p[0].x - p[1].x); }
    box.addEventListener("pointerdown", function (ev) {
      if (ev.pointerType !== "touch") return;
      touches[ev.pointerId] = { x: ev.clientX };
      if (Object.keys(touches).length === 2) { pinch0 = gap(); pinching = true; }
    });
    box.addEventListener("pointermove", function (ev) {
      if (!(ev.pointerId in touches)) return;
      touches[ev.pointerId].x = ev.clientX;
      if (pinch0 && Object.keys(touches).length === 2) {
        var g = gap(), p = Object.values(touches);
        if (g > 10 && Math.abs(g / pinch0 - 1) > 0.08) { zoomAt(pinch0 / g, box, (p[0].x + p[1].x) / 2); pinch0 = g; }
      }
    });
    ["pointerup", "pointercancel"].forEach(function (t) { box.addEventListener(t, function (ev) {
      delete touches[ev.pointerId];
      if (!Object.keys(touches).length) pinching = false;  // stays set until every finger lifts, so the last one doesn't pan
      if (Object.keys(touches).length < 2) pinch0 = null; }); });
  });
  // sections: one shown at a time, named in the URL hash so links and the back button work
  var SECS = ["today", "training", "fitness", "race"], rangeIn = { training: 1, fitness: 1 };
  function showSec() {
    var h = location.hash.slice(1), cur = SECS.indexOf(h) >= 0 ? h : "today";
    SECS.forEach(function (s) { document.getElementById("s-" + s).hidden = s !== cur; });
    Array.prototype.forEach.call(document.querySelectorAll(".subtabs button"), function (b) {
      b.setAttribute("aria-selected", String(b.dataset.sec === cur)); });
    document.querySelector(".range").hidden = !rangeIn[cur];
    render(); renderStatic();
  }
  Array.prototype.forEach.call(document.querySelectorAll(".subtabs button"), function (b) {
    b.onclick = function () {
      if (location.hash.slice(1) !== b.dataset.sec) location.hash = b.dataset.sec;
      window.scrollTo(0, 0);
    };
  });
  window.addEventListener("hashchange", showSec);
  showSec();
  var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(function () { render(); renderStatic(); }, 120); });
})();
</script>
</body>
</html>
"""
