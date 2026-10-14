"""Lap splits, HR time-in-zone, and rolling mileage from FIT files.

Reads every .fit in data/fit/ (intervals.icu sync) and data/coros_export/ (COROS bulk
export), de-duplicating by start time, so runs that never reached intervals.icu still count.
The existing data/runs.csv and data/laps.csv are the permanent record: runs with no FIT file
present (e.g., COROS-export-only runs when this runs on GitHub Actions) keep their old rows.

Usage: python scripts/analyze.py   (run scripts/sync.py first)
Writes data/laps.csv, data/runs.csv, data/hr_hist.csv (seconds at each bpm), and data/trace.csv
(HR, grade-adjusted pace, and pace in 15 s bins; no location), prints rolling mileage.
"""
import csv, warnings
from datetime import date, timedelta

import fitdecode

from config import AET, MANUAL_LOAD_PER_MIN, ROOT, TZ, ZONES

warnings.filterwarnings("ignore", module="fitdecode")  # COROS files trip a harmless size warning

FIT_DIRS = [ROOT / "data" / "fit", ROOT / "data" / "coros_export"]
MANUAL_LIST = ROOT / "data" / "manual_runs.txt"  # FIT ids of hand-made files (no real HR/GPS)
M_PER_MI = 1609.344
FT_PER_M = 3.281

# Aerobic efficiency: pace at AeT from steady, flat running in a narrow HR band around it
EF_HR_BAND = (AET - 7, AET + 7)
EF_WINDOW = 30         # seconds over which grade is measured
EF_MAX_GRADE = 0.02
EF_MIN_SECS = 240


def pace(sec, meters):
    if not meters:
        return ""
    s = sec / (meters / M_PER_MI)
    return f"{int(s // 60)}:{int(s % 60):02d}"


def run_load(zone_secs):
    """Zone-weighted TRIMP (Edwards-style): minutes in zone N count N times."""
    return sum((i + 1) * z for i, z in enumerate(zone_secs)) / 60


def aet_pace(recs):
    """Pace (sec/mi) at AeT HR, scaled from speed/HR on steady flat seconds; None if too little data."""
    n = v_sum = hr_sum = 0
    lo, hi = EF_HR_BAND
    for (hr, v, d, a), (_, _, d0, a0) in zip(recs[EF_WINDOW:], recs):
        if None in (hr, v, d, a, d0, a0) or v < 2.0 or not lo <= hr <= hi:
            continue
        if d - d0 < 40 or abs((a - a0) / (d - d0)) > EF_MAX_GRADE:
            continue
        n, v_sum, hr_sum = n + 1, v_sum + v, hr_sum + hr
    if n < EF_MIN_SECS:
        return None, n
    speed_at_aet = (v_sum / n) / (hr_sum / n) * AET
    return M_PER_MI / speed_at_aet, n


TRACE_BIN = 5          # seconds per point in data/trace.csv (fine enough for 8 s sprints)
TRACE_ALT_SMOOTH = 5   # +/- seconds of altitude averaging (barometer noise)
TRACE_GRADE_SPAN = 10  # grade at each second is taken over +/- this many seconds


def minetti_cost(g):
    """Energy cost of running (J/kg/m) at gradient g (rise/run), Minetti et al. 2002, J Appl Physiol 93:1039."""
    g = max(-0.45, min(0.45, g))  # range the polynomial was fitted on
    return 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6


def trace(recs):
    """Per TRACE_BIN seconds: mean HR, grade-adjusted pace and pace (sec/mi).
    GAP is computed second by second so short hills keep their grade. Minetti's model is for running,
    so GAP during walking is only an estimate. A stopped bin gets neither pace."""
    n = len(recs)
    alt, dist = [r[3] for r in recs], [r[2] for r in recs]
    sm = []
    for k in range(n):
        w = [a for a in alt[max(0, k - TRACE_ALT_SMOOTH):k + TRACE_ALT_SMOOTH + 1] if a is not None]
        sm.append(sum(w) / len(w) if w else None)
    c0 = minetti_cost(0)
    adj = []  # per second: grade-adjusted speed
    for k in range(n):
        v = recs[k][1] or 0
        a, b = max(0, k - TRACE_GRADE_SPAN), min(n - 1, k + TRACE_GRADE_SPAN)
        ok = None not in (sm[a], sm[b], dist[a], dist[b]) and dist[b] - dist[a] > 8
        g = (sm[b] - sm[a]) / (dist[b] - dist[a]) if ok else 0
        adj.append(v * minetti_cost(g) / c0)
    out = []
    for i in range(0, n, TRACE_BIN):
        j = min(n - 1, i + TRACE_BIN)
        hr = [r[0] for r in recs[i:i + TRACE_BIN] if r[0]]
        gap = pace_s = None
        if None not in (dist[i], dist[j]) and j > i:
            v = (dist[j] - dist[i]) / (j - i)
            if v >= 1.0:  # slower than ~27 min/mi counts as stopped
                pace_s = M_PER_MI / v
                gs = sum(adj[i:j]) / (j - i)
                gap = M_PER_MI / gs if gs > 0 else None
        out.append((round(sum(hr) / len(hr)) if hr else None, gap and round(gap), pace_s and round(pace_s)))
    return out


def parse_fit(path):
    session, laps, recs = {}, [], []
    with fitdecode.FitReader(path) as fr:
        for frame in fr:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            g = lambda k: frame.get_value(k, fallback=None)
            if frame.name == "session":
                session = {"start": g("start_time"), "sport": g("sport"),
                           "dist": g("total_distance") or 0, "time": g("total_timer_time") or 0,
                           "ascent": g("total_ascent") or 0}
            elif frame.name == "lap":
                laps.append({"dist": g("total_distance") or 0,
                             "time": g("total_timer_time") or g("total_elapsed_time") or 0,
                             "avg_hr": g("avg_heart_rate"), "max_hr": g("max_heart_rate"),
                             "ascent": g("total_ascent") or 0, "descent": g("total_descent") or 0,
                             "cadence": g("avg_running_cadence") or g("avg_cadence")})
            elif frame.name == "record":
                recs.append((g("heart_rate"), g("enhanced_speed") or g("speed"), g("distance"),
                             g("enhanced_altitude") or g("altitude")))
    return session, laps, recs  # COROS records are 1 Hz, so record count ~ seconds


def load_runs():
    runs = {}
    for d in FIT_DIRS:
        for p in sorted(d.glob("*.fit")):
            session, laps, recs = parse_fit(p)
            if session.get("sport") != "running" or not session.get("start"):
                continue
            runs.setdefault(session["start"], (p.stem, session, laps, recs))
    return [runs[k] for k in sorted(runs)]


def main():
    lap_rows, run_rows, hist_rows, trace_rows, daily = [], [], [], [], {}
    zone_secs = [0] * len(ZONES)
    manual = {l.split("#")[0].strip() for l in MANUAL_LIST.read_text().splitlines()} - {""}
    for fid, s, laps, recs in load_runs():
        hr = [r[0] for r in recs if r[0]]
        alts = [r[3] for r in recs if r[3] is not None]
        elev_avg = round(sum(alts) / len(alts) * FT_PER_M) if alts else ""
        local = s["start"].astimezone(TZ)
        d = local.date()
        daily[d] = daily.get(d, 0) + s["dist"] / M_PER_MI
        if fid in manual:  # hand-made file: counts toward mileage, not HR/pace analysis
            run_rows.append([d, local.strftime("%H:%M"), f"{s['dist'] / M_PER_MI:.2f}",
                             pace(s["time"], s["dist"]), "", *[""] * (len(ZONES) + 3),
                             round(s["time"] / 60 * MANUAL_LOAD_PER_MIN), "", "", "", fid + " (manual)"])
            continue
        for i, l in enumerate(laps, 1):
            lap_rows.append([d, local.strftime("%H:%M"), i, f"{l['dist'] / M_PER_MI:.2f}",
                             int(l["time"]), pace(l["time"], l["dist"]), l["avg_hr"], l["max_hr"],
                             round(l["ascent"] * FT_PER_M), round(l["descent"] * FT_PER_M),
                             l["cadence"] and l["cadence"] * 2])
        counts = {}
        for h in hr:
            counts[h] = counts.get(h, 0) + 1
        hist_rows.append([d, local.strftime("%H:%M"), " ".join(f"{b}:{c}" for b, c in sorted(counts.items()))])
        tr = trace(recs)
        trace_rows.append([d, local.strftime("%H:%M"), TRACE_BIN,
                           *(" ".join("-" if x is None else str(x) for x in col) for col in zip(*tr))])
        n = len(hr) or 1
        zs = [sum(lo <= h < hi for h in hr) for _, lo, hi in ZONES]
        zone_secs = [a + b for a, b in zip(zone_secs, zs)]
        zpct = [100 * z / n for z in zs]
        run_rows.append([d, local.strftime("%H:%M"), f"{s['dist'] / M_PER_MI:.2f}",
                         pace(s["time"], s["dist"]), round(sum(hr) / n) if hr else "", len(laps),
                         *[f"{z:.0f}" for z in zpct],
                         round(s["ascent"] * FT_PER_M), len(hr), round(run_load(zs)),
                         *(lambda p, n: (round(p) if p else "", n))(*aet_pace(recs)), elev_avg, fid])

    # The CSVs are the permanent record. Keep a run's old rows when the FIT file they came from
    # isn't here (COROS-export-only runs, or a hand-added file intervals.icu also has a copy of),
    # so the same run never flips between versions depending on which machine ran this.
    present = {p.stem for d in FIT_DIRS for p in d.glob("*.fit")}
    old_runs = []
    if (ROOT / "data" / "runs.csv").exists():
        with open(ROOT / "data" / "runs.csv", newline="") as f:
            old_runs = list(csv.reader(f))[1:]
    stale = {(r[0], r[1]) for r in old_runs if r[-1].split(" ")[0] not in present}
    run_rows[:] = [r for r in run_rows if (str(r[0]), r[1]) not in stale]
    lap_rows[:] = [r for r in lap_rows if (str(r[0]), r[1]) not in stale]
    hist_rows[:] = [r for r in hist_rows if (str(r[0]), r[1]) not in stale]
    trace_rows[:] = [r for r in trace_rows if (str(r[0]), r[1]) not in stale]
    have = {(str(r[0]), r[1]) for r in run_rows}
    for name, rows in (("runs.csv", run_rows), ("laps.csv", lap_rows), ("hr_hist.csv", hist_rows),
                       ("trace.csv", trace_rows)):
        path = ROOT / "data" / name
        if path.exists():
            with open(path, newline="") as f:
                kept = [r for r in list(csv.reader(f))[1:] if (r[0], r[1]) not in have]
            rows += kept
            if name == "runs.csv":
                for r in kept:
                    d = date.fromisoformat(r[0])
                    daily[d] = daily.get(d, 0) + float(r[2])
    run_rows.sort(key=lambda r: (str(r[0]), r[1]))
    lap_rows.sort(key=lambda r: (str(r[0]), r[1], int(r[2])))
    hist_rows.sort(key=lambda r: (str(r[0]), r[1]))
    with open(ROOT / "data" / "hr_hist.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "bpm_secs"])  # "bpm:seconds" pairs
        w.writerows(hist_rows)
    trace_rows.sort(key=lambda r: (str(r[0]), r[1]))
    with open(ROOT / "data" / "trace.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "bin_secs", "hr", "gap_secs", "pace_secs"])  # space-separated, "-" = none
        w.writerows(trace_rows)

    with open(ROOT / "data" / "laps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "lap", "miles", "secs", "pace", "avg_hr", "max_hr",
                    "ascent_ft", "descent_ft", "spm"])
        w.writerows(lap_rows)
    with open(ROOT / "data" / "runs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "miles", "pace", "avg_hr", "laps", *[z[0] for z in ZONES],
                    "elev_ft", "hr_secs", "load", "aet_pace_secs", "aet_secs", "elev_avg_ft", "fit"])
        w.writerows(run_rows)

    total = sum(zone_secs) or 1
    print(f"Time in zone, all runs ({total / 3600:.1f} h with HR):")
    for (name, lo, hi), z in zip(ZONES, zone_secs):
        rng = f"{lo}-{hi - 1}" if hi < 999 else f"{lo}+"
        print(f"  {name:13} {rng:>7}  {100 * z / total:4.0f}%  {z / 3600:5.1f} h")
    print()
    start, end = min(daily), date.today()
    print("date        7d_mi  28d_avg_wk")
    d = start
    while d <= end:
        r7 = sum(daily.get(d - timedelta(i), 0) for i in range(7))
        r28 = sum(daily.get(d - timedelta(i), 0) for i in range(28)) / 4
        print(f"{d}  {r7:5.1f}  {r28:5.1f}{'' if (d - start).days >= 27 else ' (partial)'}")
        d += timedelta(1)


if __name__ == "__main__":
    main()
