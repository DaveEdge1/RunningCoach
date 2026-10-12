"""Athlete settings from config.json, shared by every script.

Edit config.json (not the scripts) when thresholds, the goal race, or the time zone change.
"""
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
_cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))

TZ = ZoneInfo(_cfg["timezone"])

AET = _cfg["heart_rate"]["aerobic_threshold"]
LT = _cfg["heart_rate"]["lactate_threshold"]
MAX_HR = _cfg["heart_rate"]["max"]
# 5 zones anchored to AeT/LT: Z2 tops out at AeT, Z4 is the 10 bpm below LT
ZONES = [("Z1 Recovery", 0, AET - 10), ("Z2 Aerobic", AET - 10, AET),
         ("Z3 Tempo", AET, LT - 10), ("Z4 Threshold", LT - 10, LT), ("Z5 >LT", LT, 999)]
ZONE_NAMES = [z[0] for z in ZONES]


def zone_range(lo, hi):
    return f"<{hi}" if lo == 0 else f"{lo}+" if hi >= 999 else f"{lo}–{hi - 1}"


GOAL = _cfg["goal_race"]
GOAL_NAME = GOAL["name"]
GOAL_DATE = date.fromisoformat(GOAL["date"])
GOAL_METERS = GOAL["distance_km"] * 1000
GOAL_ELEV_FT = GOAL["elevation_ft"]

ALT_FLOOR_FT = _cfg["altitude"]["no_effect_below_ft"]
ALT_PCT_PER_1000 = tuple(_cfg["altitude"]["slowdown_pct_per_1000ft"])

MANUAL_LOAD_PER_MIN = _cfg["manual_run_load_per_min"]


def altitude_factor_range(from_ft, to_ft):
    """Multipliers on race time when moving a performance from one elevation to another.

    Altitude is assumed to cost a fixed % per 1,000 ft above a floor; returns (low, high)
    multipliers, which are < 1 when the target is lower.
    """
    def cost(ft, pct):
        return max(0.0, ft - ALT_FLOOR_FT) / 1000 * pct / 100
    lo, hi = ALT_PCT_PER_1000
    return tuple(sorted(((1 + cost(to_ft, p)) / (1 + cost(from_ft, p)) for p in (lo, hi))))
