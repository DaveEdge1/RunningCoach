"""Athlete settings from config.json, shared by every script.

Edit config.json (not the scripts) when thresholds, home, or the time zone change; races go in races.csv.
"""
import csv, json, math, re
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
_cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

TZ = ZoneInfo(_cfg["timezone"])
HOME_NAME = _cfg["home"]["name"]
HOME_ELEV_FT = _cfg["home"]["elevation_ft"]

AET = _cfg["heart_rate"]["aerobic_threshold"]
LT = _cfg["heart_rate"]["lactate_threshold"]
MAX_HR = _cfg["heart_rate"]["max"]
# 5 zones anchored to AeT/LT: Z2 tops out at AeT, Z4 is the 10 bpm below LT
ZONES = [("Z1 Recovery", 0, AET - 10), ("Z2 Aerobic", AET - 10, AET),
         ("Z3 Tempo", AET, LT - 10), ("Z4 Threshold", LT - 10, LT), ("Z5 >LT", LT, 999)]
ZONE_NAMES = [z[0] for z in ZONES]

# Pace at AeT: which seconds of a run count (see analyze.aet_pace)
AET_WARMUP_S = _cfg["aet_pace"]["warmup_min"] * 60
AET_MAX_S = _cfg["aet_pace"]["max_min"] * 60
AET_MAX_GRADE = _cfg["aet_pace"]["max_grade_pct"] / 100
RECOVERY_KM_PER_DAY = _cfg["aet_pace"]["recovery_km_per_day"]  # Daniels: one easy day per 3 km raced
HARD_BOUT = [(bpm, mins * 60) for bpm, mins in _cfg["aet_pace"]["hard_bout_minutes_at_bpm"]]

# Load ramp: acute:chronic workload ratio (Gabbett 2016)
ACUTE_DAYS = _cfg["load_ramp"]["acute_days"]
CHRONIC_DAYS = _cfg["load_ramp"]["chronic_days"]
ACWR_SWEET = tuple(_cfg["load_ramp"]["sweet_spot"])
ACWR_DANGER = _cfg["load_ramp"]["danger"]
WORKOUT_Z5_PCT = _cfg["load_ramp"]["workout_z5_pct"]  # % of time in Z5 that makes an unplanned run a workout (load projection)

# Aerobic decoupling (Friel): first vs. second half of a run's steady part
DECOUPLE_MIN_S = _cfg["decoupling"]["min_minutes"] * 60
DECOUPLE_LIMIT = _cfg["decoupling"]["limit_pct"]

# Best efforts
EFFORT_DISTANCES = _cfg["best_efforts"]["distances_m"]
EFFORT_MIN_M = _cfg["best_efforts"]["min_m_for_fitness"]

# Cross-training that counts toward load (HR-zone load, like runs; not toward mileage)
_ct = _cfg["cross_training"]
CROSS_FIT_SPORTS = _ct["fit_sports"]          # FIT sport -> kind ("hike", "ski")
CROSS_NAMES = _ct["names"]                    # kind -> display name
CROSS_PLAN_WORDS = _ct["plan_keywords"]       # kind -> whole words (plus -s, -ing) that mark it in a plan.csv session
CROSS_DEFAULT_RATE = _ct["default_load_per_min"]  # kind -> load/min with no HR and no history


def cross_kind(session):
    """Cross-training kind named in a plan session ("Grand Canyon: backpack ..." -> "hike"), or None."""
    s = session.lower()
    return next((k for k, words in CROSS_PLAN_WORDS.items() if any(re.search(rf"\b{w}(?:s|ing)?\b", s) for w in words)), None)

# Workout reps
REP_PACE_TOL = _cfg["workouts"]["pace_tolerance_s_per_mi"]
REP_DIST_TOL = _cfg["workouts"]["rep_distance_tolerance_pct"] / 100


def recovery_days(meters):
    """Easy days after a race of this distance (Daniels, rounded: 15K -> 5, half marathon -> 7)."""
    return round(meters / 1000 / RECOVERY_KM_PER_DAY)


def hard_bout_secs(hr):
    """Seconds at this HR that make a hard bout: exponential through the two config points, none below the first."""
    (h1, t1), (h2, t2) = HARD_BOUT
    return None if hr < h1 else t1 * (t2 / t1) ** ((hr - h1) / (h2 - h1))


def zone_range(lo, hi):
    return f"<{hi}" if lo == 0 else f"{lo}+" if hi >= 999 else f"{lo}–{hi - 1}"




def parse_clock(s):
    """'45:00', '0:45:00' or '7:15' -> seconds."""
    secs = 0.0
    for part in s.split(":"):
        secs = secs * 60 + float(part)
    return secs


def _race(r):
    meters = float(r["distance_km"]) * 1000
    gt, gp = (r.get("goal_time") or "").strip(), (r.get("goal_pace") or "").strip()
    if gt and gp:
        raise ValueError(f"races.csv {r['date']}: fill in goal_time or goal_pace, not both")
    goal = parse_clock(gt) if gt else parse_clock(gp) * meters / M_PER_MI if gp else None
    return {"date": date.fromisoformat(r["date"]), "name": r["name"], "location": r["location"],
            "meters": meters, "elev": int(r["elevation_ft"]) if r["elevation_ft"] else None,
            "notes": r["course_notes"], "goal_secs": goal, "goal_from": "time" if gt else "pace" if gp else None}


def trail_days():
    """Dates (ISO strings) whose line in notes.md mentions "trail"."""
    path = ROOT / "notes.md"
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"-\s*(\d{4}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?\s*:(.*)", line.strip())
        if m and re.search(r"\btrail\b", m.group(2), re.I):
            out.add(m.group(1))
    return out


def load_races():
    """Race calendar from races.csv (past and upcoming), sorted by date.

    Each race takes goal_time or goal_pace (per mile); the other is derived from it.
    """
    with open(ROOT / "races.csv", encoding="utf-8", newline="") as f:
        return sorted((_race(r) for r in csv.DictReader(f)), key=lambda r: r["date"])


def next_race(today=None):
    today = today or date.today()
    return next((r for r in load_races() if r["date"] >= today), None)

MANUAL_LOAD_PER_MIN = _cfg["manual_run_load_per_min"]


# ---------- altitude: two published methods (see data/ref/README.md) ----------

M_PER_MI = 1609.344
DANIELS_FLOOR_FT = _cfg["altitude"]["daniels_floor_ft"]
DANIELS_SPM = tuple(_cfg["altitude"]["daniels_sec_per_mile_per_1000ft"])


def _load_ncaa():
    with open(ROOT / _cfg["altitude"]["ncaa_table"], encoding="utf-8", newline="") as f:
        rows = [(int(r["altitude_ft"]), [(M_PER_MI, float(r["pct_mile"])), (5000, float(r["pct_5000m"])),
                                        (10000, float(r["pct_10000m"]))]) for r in csv.DictReader(f)]
    return sorted(rows)


_NCAA = _load_ncaa()


def _interp(x, pts):
    """Linear interpolation over sorted (x, y) points, clamped at both ends."""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def ncaa_pct(alt_ft, meters):
    """% the NCAA table adds to a race time at alt_ft: interpolated by altitude (linear, incl. its
    sea-level row) and by log-distance between mile, 5K and 10K, clamped outside that span."""
    by_alt = [(a, _interp(math.log(meters), [(math.log(m), p) for m, p in dists])) for a, dists in _NCAA]
    return _interp(alt_ft, by_alt)


def daniels_spm(alt_ft, k):
    """Seconds per mile slower than sea level: k s/mi per 1,000 ft above the floor."""
    return k * max(0.0, alt_ft - DANIELS_FLOOR_FT) / 1000


def convert_time(secs, meters, from_ft, to_ft):
    """Equivalent time at to_ft for a performance at from_ft, by each method."""
    mi = meters / M_PER_MI
    out = {f"Daniels {k:g} s/mi": secs + (daniels_spm(to_ft, k) - daniels_spm(from_ft, k)) * mi
           for k in DANIELS_SPM}
    out["NCAA"] = secs / (1 + ncaa_pct(from_ft, meters) / 100) * (1 + ncaa_pct(to_ft, meters) / 100)
    return out


def convert_range(secs, meters, from_ft, to_ft):
    v = convert_time(secs, meters, from_ft, to_ft).values()
    return min(v), max(v)
