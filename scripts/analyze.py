"""Lap splits, HR time-in-zone, and rolling mileage from FIT files.

Reads every .fit in data/fit/ (intervals.icu sync) and data/coros_export/ (COROS bulk
export), de-duplicating by start time, so runs that never reached intervals.icu still count.

Usage: python scripts/analyze.py   (run scripts/sync.py first)
Writes data/laps.csv and data/runs.csv, prints rolling mileage.
"""
import csv, warnings
from datetime import date, timedelta, timezone
from pathlib import Path

import fitdecode

warnings.filterwarnings("ignore", module="fitdecode")  # COROS files trip a harmless size warning

ROOT = Path(__file__).resolve().parent.parent
FIT_DIRS = [ROOT / "data" / "fit", ROOT / "data" / "coros_export"]
MANUAL_LIST = ROOT / "data" / "manual_runs.txt"  # FIT ids of hand-made files (no real HR/GPS)
LOCAL_TZ = timezone(timedelta(hours=-7))  # Arizona: MST year-round
M_PER_MI = 1609.344
FT_PER_M = 3.281

# Keep in sync with profile.md
AET, LT, MAX_HR = 145, 168, 188
# 5 zones anchored to AeT/LT: Z2 tops out at AeT, Z4 is the 10 bpm below LT
ZONES = [("Z1 Recovery", 0, AET - 10), ("Z2 Aerobic", AET - 10, AET),
         ("Z3 Tempo", AET, LT - 10), ("Z4 Threshold", LT - 10, LT), ("Z5 >LT", LT, 999)]
MANUAL_LOAD_PER_MIN = 2.5  # hand-made runs have no real HR; assume a Z2/Z3 easy effort

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
    lap_rows, run_rows, daily = [], [], {}
    zone_secs = [0] * len(ZONES)
    manual = {l.split("#")[0].strip() for l in MANUAL_LIST.read_text().splitlines()} - {""}
    for fid, s, laps, recs in load_runs():
        hr = [r[0] for r in recs if r[0]]
        local = s["start"].astimezone(LOCAL_TZ)
        d = local.date()
        daily[d] = daily.get(d, 0) + s["dist"] / M_PER_MI
        if fid in manual:  # hand-made file: counts toward mileage, not HR/pace analysis
            run_rows.append([d, local.strftime("%H:%M"), f"{s['dist'] / M_PER_MI:.2f}",
                             pace(s["time"], s["dist"]), "", *[""] * (len(ZONES) + 3),
                             round(s["time"] / 60 * MANUAL_LOAD_PER_MIN), "", "", fid + " (manual)"])
            continue
        for i, l in enumerate(laps, 1):
            lap_rows.append([d, local.strftime("%H:%M"), i, f"{l['dist'] / M_PER_MI:.2f}",
                             int(l["time"]), pace(l["time"], l["dist"]), l["avg_hr"], l["max_hr"],
                             round(l["ascent"] * FT_PER_M), round(l["descent"] * FT_PER_M),
                             l["cadence"] and l["cadence"] * 2])
        n = len(hr) or 1
        zs = [sum(lo <= h < hi for h in hr) for _, lo, hi in ZONES]
        zone_secs = [a + b for a, b in zip(zone_secs, zs)]
        zpct = [100 * z / n for z in zs]
        run_rows.append([d, local.strftime("%H:%M"), f"{s['dist'] / M_PER_MI:.2f}",
                         pace(s["time"], s["dist"]), round(sum(hr) / n) if hr else "", len(laps),
                         *[f"{z:.0f}" for z in zpct],
                         round(s["ascent"] * FT_PER_M), len(hr), round(run_load(zs)),
                         *(lambda p, n: (round(p) if p else "", n))(*aet_pace(recs)), fid])

    with open(ROOT / "data" / "laps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "lap", "miles", "secs", "pace", "avg_hr", "max_hr",
                    "ascent_ft", "descent_ft", "spm"])
        w.writerows(lap_rows)
    with open(ROOT / "data" / "runs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "start", "miles", "pace", "avg_hr", "laps", *[z[0] for z in ZONES],
                    "elev_ft", "hr_secs", "load", "aet_pace_secs", "aet_secs", "fit"])
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
