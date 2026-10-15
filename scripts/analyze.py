"""Lap splits, HR time-in-zone, and rolling mileage from FIT files.

Reads every .fit in data/fit/ (intervals.icu sync) and data/coros_export/ (COROS bulk
export), de-duplicating by start time, so runs that never reached intervals.icu still count.
The existing data/runs.csv and data/laps.csv are the permanent record: runs with no FIT file
present (e.g., COROS-export-only runs when this runs on GitHub Actions) keep their old rows.

Usage: python scripts/analyze.py   (run scripts/sync.py first)
Writes data/laps.csv, data/runs.csv, data/hr_hist.csv (seconds at each bpm), data/trace.csv
(HR, grade-adjusted pace, and pace in 5 s bins; no location), data/efforts.csv (fastest grade-adjusted time
over each standard distance within each run), and data/cross.csv (hikes and XC skis: HR-zone load, not mileage),
prints rolling mileage.
"""
import bisect, csv, warnings
from datetime import date, timedelta

import fitdecode

import elevation
from config import (AET, AET_MAX_GRADE, AET_MAX_S, AET_WARMUP_S, CROSS_DEFAULT_RATE, CROSS_FIT_SPORTS, DECOUPLE_MIN_S,
                    EFFORT_DISTANCES, MANUAL_LOAD_PER_MIN, ROOT, TZ, ZONES, hard_bout_secs)

warnings.filterwarnings("ignore", module="fitdecode")  # COROS files trip a harmless size warning

FIT_DIRS = [ROOT / "data" / "fit", ROOT / "data" / "coros_export"]
MANUAL_LIST = ROOT / "data" / "manual_runs.txt"  # FIT ids of hand-made files (no real HR/GPS)
M_PER_MI = 1609.344
SEMICIRCLE = 180 / 2**31  # FIT position units to degrees
FT_PER_M = 3.281

# Aerobic efficiency: pace at AeT from grade-adjusted easy running in a narrow HR band around it
EF_HR_BAND = (AET - 7, AET + 7)
EF_WINDOW = 30         # seconds over which grade is averaged
EF_MIN_SECS = 240


def pace(sec, meters):
    if not meters:
        return ""
    s = sec / (meters / M_PER_MI)
    return f"{int(s // 60)}:{int(s % 60):02d}"


def run_load(zone_secs):
    """Zone-weighted TRIMP (Edwards-style): minutes in zone N count N times."""
    return sum((i + 1) * z for i, z in enumerate(zone_secs)) / 60


TRACE_BIN = 5          # seconds per point in data/trace.csv (fine enough for 8 s sprints)
TRACE_ALT_SMOOTH = 5   # +/- seconds of altitude averaging (barometer noise)
TRACE_GRADE_SPAN = 10  # barometer fallback: grade at each second is taken over +/- this many seconds
GRADE_M = 10           # map elevation: grade at each point is taken over +/- this many meters of distance


def minetti_cost(g):
    """Energy cost of running (J/kg/m) at gradient g (rise/run), Minetti et al. 2002, J Appl Physiol 93:1039."""
    g = max(-0.45, min(0.45, g))  # range the polynomial was fitted on
    return 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6


def elev_at(prof):
    """Linear interpolation of elevation (m) at a distance along the run, from [(dist, elev), ...]."""
    ds = [d for d, _ in prof]
    def f(x):
        x = min(max(x, ds[0]), ds[-1])
        i = bisect.bisect_right(ds, x)
        if i >= len(ds):
            return prof[-1][1]
        (d0, e0), (d1, e1) = prof[i - 1], prof[i]
        return e0 if d1 == d0 else e0 + (e1 - e0) * (x - d0) / (d1 - d0)
    return f


def grade_adjust(recs, prof=None):
    """Per second: (grade, grade-adjusted speed m/s) via Minetti's cost curve.
    Grade comes from map elevation along the track (prof, see elevation.py) when available, else from the barometer.
    Speed comes from distance: the FIT speed field lags distance (and so altitude) by ~6 s on this watch,
    which pairs a sprint's top speed with the flat ground after the hill."""
    n = len(recs)
    alt, dist = [r[3] for r in recs], [r[2] for r in recs]
    E = elev_at(prof) if prof else None
    sm = []
    for k in range(n):
        w = [a for a in alt[max(0, k - TRACE_ALT_SMOOTH):k + TRACE_ALT_SMOOTH + 1] if a is not None]
        sm.append(sum(w) / len(w) if w else None)
    c0 = minetti_cost(0)
    out = []
    for k in range(n):
        p, q = max(0, k - 1), min(n - 1, k + 1)
        v = (dist[q] - dist[p]) / (q - p) if None not in (dist[p], dist[q]) and q > p else 0
        if E and dist[k] is not None:
            g = (E(dist[k] + GRADE_M) - E(dist[k] - GRADE_M)) / (2 * GRADE_M)
        else:
            a, b = max(0, k - TRACE_GRADE_SPAN), min(n - 1, k + TRACE_GRADE_SPAN)
            ok = None not in (sm[a], sm[b], dist[a], dist[b]) and dist[b] - dist[a] > 8
            g = (sm[b] - sm[a]) / (dist[b] - dist[a]) if ok else 0
        out.append((g, v * minetti_cost(g) / c0))
    return out


def hard_bout_start(recs):
    """Second at which a hard bout is complete, counted after the warm-up: each second at HR h adds
    1 / hard_bout_secs(h) (config: e.g., 4 min at 150 or 2 min at 160) until the sum reaches 1. None if never."""
    dose = 0
    for k in range(AET_WARMUP_S, len(recs)):
        t = recs[k][0] and hard_bout_secs(recs[k][0])
        dose += 1 / t if t else 0
        if dose >= 1:
            return k
    return None


def aet_pace(recs, ga):
    """Pace (sec/mi) at AeT HR, scaled from grade-adjusted speed / HR; None if too little data.
    Counts seconds after the warm-up, before any hard bout, within the first AET_MAX_S (cardiac drift),
    in the HR band, running (>= 2 m/s grade-adjusted), on grades under AET_MAX_GRADE averaged over EF_WINDOW s."""
    n = v_sum = hr_sum = 0
    lo, hi = EF_HR_BAND
    end = min(len(recs), AET_MAX_S, hard_bout_start(recs) or len(recs))
    for k in range(max(AET_WARMUP_S, EF_WINDOW), end):
        hr, v = recs[k][0], ga[k][1]
        if not hr or not lo <= hr <= hi or v < 2.0:
            continue
        if abs(sum(g for g, _ in ga[k - EF_WINDOW:k]) / EF_WINDOW) > AET_MAX_GRADE:
            continue
        n, v_sum, hr_sum = n + 1, v_sum + v, hr_sum + hr
    if n < EF_MIN_SECS:
        return None, n
    speed_at_aet = (v_sum / n) / (hr_sum / n) * AET
    return M_PER_MI / speed_at_aet, n


def steady_seconds(recs, ga, end):
    """Indexes of seconds that count as steady running: after the warm-up and before `end`, with HR,
    running (>= 2 m/s grade-adjusted), on grades under AET_MAX_GRADE averaged over EF_WINDOW s."""
    out = []
    for k in range(max(AET_WARMUP_S, EF_WINDOW), end):
        if not recs[k][0] or ga[k][1] < 2.0:
            continue
        if abs(sum(g for g, _ in ga[k - EF_WINDOW:k]) / EF_WINDOW) > AET_MAX_GRADE:
            continue
        out.append(k)
    return out


def decoupling(recs, ga):
    """Aerobic decoupling (Friel's Pa:HR) in %: how much grade-adjusted speed per heartbeat falls from the first
    to the second half of a run's steady seconds (the whole run after the warm-up, so drift late in the run counts).
    Returns (pct, minutes analyzed), with pct "" under DECOUPLE_MIN_S. Whether the run was steady enough to
    read it (an easy day, not a workout) is decided on the Fitness page."""
    ks = steady_seconds(recs, ga, len(recs))
    if len(ks) < DECOUPLE_MIN_S:
        return "", round(len(ks) / 60)
    half = len(ks) // 2
    ef = lambda part: sum(ga[k][1] for k in part) / sum(recs[k][0] for k in part)
    first, second = ef(ks[:half]), ef(ks[half:])
    return round(100 * (first - second) / first, 1), round(len(ks) / 60)


def gap_ratio(recs, ga):
    """Prefix sums for grade adjustment: (distance run, grade-adjusted distance) up to each record, so a span's
    grade-adjusted time is its time x run / adjusted over the span (as in lap_gaps)."""
    raw, adj = [0.0], [0.0]
    for k in range(1, len(recs)):
        d0, d1 = recs[k - 1][2], recs[k][2]
        step = d1 - d0 if None not in (d0, d1) else 0.0
        raw.append(raw[-1] + step)
        adj.append(adj[-1] + (ga[k][1] if step else 0.0))
    return raw, adj


def best_efforts(recs, ga, prof):
    """Fastest grade-adjusted time (Minetti, as in grade_adjust) over each of EFFORT_DISTANCES within the run, so
    a downhill span is slowed to its flat equivalent and a hilly one credited. Elapsed time comes from record
    timestamps (a pause inside the span counts), with the start interpolated so each span is exactly the distance.
    Returns [(meters, secs, grade-adjusted secs, net elevation change m, avg HR, max HR)]."""
    pts = [(r[4], r[2], r[0], r[3], k) for k, r in enumerate(recs) if r[2] is not None and r[4] is not None]
    raw, adj = gap_ratio(recs, ga)
    E = elev_at(prof) if prof else None
    out = []
    for D in EFFORT_DISTANCES:
        if not pts or pts[-1][1] - pts[0][1] < D:
            break
        best, i = None, 0
        for j in range(1, len(pts)):
            while i + 1 < j and pts[j][1] - pts[i + 1][1] >= D:
                i += 1
            if pts[j][1] - pts[i][1] < D:
                continue
            (t0, d0), (t1, d1) = pts[i][:2], pts[i + 1][:2]
            start = pts[j][1] - D
            secs = pts[j][0] - (t0 + (t1 - t0) * (start - d0) / (d1 - d0) if d1 > d0 else t0)
            a, b = pts[i][4], pts[j][4]
            if adj[b] - adj[a] <= 0:
                continue
            gap = secs * (raw[b] - raw[a]) / (adj[b] - adj[a])
            if best is None or gap < best[1]:
                best = (secs, gap, i, j)
        if best is None:
            break
        secs, gap, i, j = best
        hr = [p[2] for p in pts[i:j + 1] if p[2]]
        if E:
            drop = E(pts[j][1]) - E(pts[j][1] - D)
        else:
            drop = (pts[j][3] - pts[i][3]) if None not in (pts[i][3], pts[j][3]) else ""
        out.append((D, round(secs, 1), round(gap, 1), drop if drop == "" else round(drop, 1),
                    round(sum(hr) / len(hr)) if hr else "", max(hr) if hr else ""))
    return out


def lap_gaps(laps, recs, ga):
    """Grade-adjusted pace (sec/mi) per lap: the lap's pace times its distance run over its grade-adjusted distance.
    Laps are matched to records by cumulative distance."""
    out, start = [], 0.0
    for l in laps:
        end = start + l["dist"]
        raw = adj = 0.0
        for k in range(1, len(recs)):
            d0, d1 = recs[k - 1][2], recs[k][2]
            if d0 is None or d1 is None or not start <= d1 < end:
                continue
            raw += d1 - d0
            adj += ga[k][1]
        out.append(round(l["time"] / (l["dist"] / M_PER_MI) * raw / adj) if adj > 0 and l["dist"] else "")
        start = end
    return out


def trace(recs, ga):
    """Per TRACE_BIN seconds: mean HR, grade-adjusted pace and pace (sec/mi), from grade_adjust's ga.
    GAP is computed second by second so short hills keep their grade. Minetti's model is
    for running, so GAP during walking is only an estimate. A stopped bin gets neither pace."""
    n = len(recs)
    dist = [r[2] for r in recs]
    adj = [v for _, v in ga]
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
                session.update({"start": g("start_time"), "sport": g("sport"),
                                "dist": g("total_distance") or 0, "time": g("total_timer_time") or 0,
                                "ascent": g("total_ascent") or 0})
            elif frame.name == "lap":
                laps.append({"dist": g("total_distance") or 0,
                             "time": g("total_timer_time") or g("total_elapsed_time") or 0,
                             "avg_hr": g("avg_heart_rate"), "max_hr": g("max_heart_rate"),
                             "ascent": g("total_ascent") or 0, "descent": g("total_descent") or 0,
                             "cadence": g("avg_running_cadence") or g("avg_cadence")})
            elif frame.name == "record":
                ts = g("timestamp")
                recs.append((g("heart_rate"), g("enhanced_speed") or g("speed"), g("distance"),
                             g("enhanced_altitude") or g("altitude"), ts.timestamp() if ts else None))
                lat, lon = g("position_lat"), g("position_long")  # semicircles; used only for map elevation
                session.setdefault("pos", []).append((lat * SEMICIRCLE, lon * SEMICIRCLE)
                                                     if None not in (lat, lon) else None)
    return session, laps, recs  # COROS records are 1 Hz, so record count ~ seconds


def load_runs():
    """Runs and the cross-training in CROSS_FIT_SPORTS, de-duplicated by start time."""
    runs = {}
    for d in FIT_DIRS:
        for p in sorted(d.glob("*.fit")):
            session, laps, recs = parse_fit(p)
            if session.get("sport") not in ("running", *CROSS_FIT_SPORTS) or not session.get("start"):
                continue
            runs.setdefault(session["start"], (p.stem, session, laps, recs))
    return [runs[k] for k in sorted(runs)]


def main():
    lap_rows, run_rows, hist_rows, trace_rows, effort_rows, cross_rows, daily = [], [], [], [], [], [], {}
    zone_secs = [0] * len(ZONES)
    manual = {l.split("#")[0].strip() for l in MANUAL_LIST.read_text().splitlines()} - {""}
    for fid, s, laps, recs in load_runs():
        hr = [r[0] for r in recs if r[0]]
        alts = [r[3] for r in recs if r[3] is not None]
        elev_avg = round(sum(alts) / len(alts) * FT_PER_M) if alts else ""
        local = s["start"].astimezone(TZ)
        d = local.date()
        kind = CROSS_FIT_SPORTS.get(s["sport"])
        if kind:  # hike or XC ski: HR-zone load like a run (default rate per minute without HR), no mileage
            zs = [sum(lo <= h < hi for h in hr) for _, lo, hi in ZONES]
            cross_rows.append([d, local.strftime("%H:%M"), kind, f"{s['dist'] / M_PER_MI:.2f}", round(s["time"]),
                               round(s["ascent"] * FT_PER_M), len(hr), round(sum(hr) / len(hr)) if hr else "",
                               round(run_load(zs) if hr else s["time"] / 60 * CROSS_DEFAULT_RATE[kind]), fid])
            continue
        daily[d] = daily.get(d, 0) + s["dist"] / M_PER_MI
        if fid in manual:  # hand-made file: counts toward mileage, not HR/pace analysis
            run_rows.append([d, local.strftime("%H:%M"), f"{s['dist'] / M_PER_MI:.2f}",
                             pace(s["time"], s["dist"]), "", *[""] * (len(ZONES) + 3),
                             round(s["time"] / 60 * MANUAL_LOAD_PER_MIN), "", "", "", "", "", "", fid + " (manual)"])
            continue
        prof = elevation.profile(fid, [r[2] for r in recs], s.get("pos", []))
        ga = grade_adjust(recs, prof)
        for i, (l, gap) in enumerate(zip(laps, lap_gaps(laps, recs, ga)), 1):
            lap_rows.append([d, local.strftime("%H:%M"), i, f"{l['dist'] / M_PER_MI:.2f}",
                             int(l["time"]), pace(l["time"], l["dist"]), l["avg_hr"], l["max_hr"],
                             round(l["ascent"] * FT_PER_M), round(l["descent"] * FT_PER_M),
                             l["cadence"] and l["cadence"] * 2, gap and f"{gap // 60}:{gap % 60:02d}"])
        for m, secs, gap, drop, ahr, mhr in best_efforts(recs, ga, prof):
            effort_rows.append([d, local.strftime("%H:%M"), m, secs, gap,
                                drop if drop == "" else round(drop * FT_PER_M), ahr, mhr])
        raw, adj = gap_ratio(recs, ga)
        counts = {}
        for h in hr:
            counts[h] = counts.get(h, 0) + 1
        hist_rows.append([d, local.strftime("%H:%M"), " ".join(f"{b}:{c}" for b, c in sorted(counts.items()))])
        tr = trace(recs, ga)
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
                         *(lambda p, n: (round(p) if p else "", n))(*aet_pace(recs, ga)), elev_avg,
                         *decoupling(recs, ga), round(s["time"] * raw[-1] / adj[-1]) if adj[-1] > 0 else "", fid])

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
    effort_rows[:] = [r for r in effort_rows if (str(r[0]), r[1]) not in stale]
    old_cross = []
    if (ROOT / "data" / "cross.csv").exists():
        with open(ROOT / "data" / "cross.csv", newline="") as f:
            old_cross = list(csv.reader(f))[1:]
    stale_x = {(r[0], r[1]) for r in old_cross if r[-1] not in present}
    cross_rows[:] = [r for r in cross_rows if (str(r[0]), r[1]) not in stale_x]
    cross_rows += [r for r in old_cross if (r[0], r[1]) in stale_x]
    cross_rows.sort(key=lambda r: (str(r[0]), r[1]))
    with open(ROOT / "data" / "cross.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "kind", "miles", "secs", "elev_ft", "hr_secs", "avg_hr", "load", "fit"])
        w.writerows(cross_rows)
    have = {(str(r[0]), r[1]) for r in run_rows}
    for name, rows in (("runs.csv", run_rows), ("laps.csv", lap_rows), ("hr_hist.csv", hist_rows),
                       ("trace.csv", trace_rows), ("efforts.csv", effort_rows)):
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
    effort_rows.sort(key=lambda r: (str(r[0]), r[1], float(r[2])))
    with open(ROOT / "data" / "efforts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "meters", "secs", "gap_secs", "net_elev_ft", "avg_hr", "max_hr"])
        w.writerows(effort_rows)
    elevation.save()
    with open(ROOT / "data" / "trace.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "bin_secs", "hr", "gap_secs", "pace_secs"])  # space-separated, "-" = none
        w.writerows(trace_rows)

    with open(ROOT / "data" / "laps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "lap", "miles", "secs", "pace", "avg_hr", "max_hr",
                    "ascent_ft", "descent_ft", "spm", "gap"])
        w.writerows(lap_rows)
    with open(ROOT / "data" / "runs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "miles", "pace", "avg_hr", "laps", *[z[0] for z in ZONES],
                    "elev_ft", "hr_secs", "load", "aet_pace_secs", "aet_secs", "elev_avg_ft",
                    "decouple_pct", "decouple_min", "gap_secs", "fit"])
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
