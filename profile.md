# Athlete Profile

## Heart rate and thresholds (COROS upper-arm optical; thresholds are COROS app estimates)
_The scripts read thresholds from `config.json`. Update both when they change._
- Aerobic threshold (AeT): ~145 bpm
- Lactate threshold (LT): 168 bpm, 7:43/mi (COROS estimate, updated after the 2026-10-10 10K; was 166)
- Max HR: ~188 bpm
- Zones used in analysis (5-zone, anchored to AeT/LT):
  Z1 Recovery <135 · Z2 Aerobic 135–144 · Z3 Tempo 145–157 · Z4 Threshold 158–167 · Z5 Above LT ≥168

## Goals
- **2026-12-26 — 5K in Phoenix** (~1,100 ft, close to sea level). Goal: 21:00 (6:46/mi), set 2026-10-15; it lives in races.csv (goal_time).
  Switched from the 10K (goal was 44:00, set 2026-10-12) on 2026-10-15: the course is a 0.78 mi out-and-back, so the 10K meant 4 laps and 7 turnarounds.
- **2027-02-06 — RunSedona 5K** (~4,450 ft, rolling: ~290 ft of climbing). Goal: under 21:00 (set 2026-10-30). With altitude and hills that's about a 20:00 at sea level (VDOT ~49.7), as hard as the sub-20 goal.
- **Sub-20 5K** on a flat course near sea level (VDOT ~49.9) before moving up to longer races (set 2026-10-30).
- **2,000 miles in 2027** (key goal, set 2026-10-30): ~38.4 mi/wk average including cut-backs and time off. Holding ~45 mi/wk with cut-back weeks gives ~2,150, so it needs consistency, not higher peaks. Don't chase a deficit late in the year (injury pattern below).
- Later (after sub-20): 10K in 40:00 (VDOT ~51.9, a stretch) and a half in 1:30 (VDOT ~51.0); same fitness level, so treat them as one target.

## PRs / recent races
- 2026-10-10: 10K in 47:55 by watch (GPS 9.99 km). Flat dirt road with some rocky sections, ~7,400 ft.
  Avg HR 172, max 187. Miles 7:43 / 7:36 / 7:34 / 7:53 / 7:59 / 7:44, then about a 6:39 pace to the finish.
  Miles 4–5 were slower because of rocky climbs and a headwind, not a fade. Effort was even (HR kept rising through them).
  HR dipped to ~149 at 5.9 mi while pace held at ~7:00–7:30, so that dip is probably an optical sensor dropout.

## Running history (from `data/Running log - 2024-25.csv` and `Running log - 2026.csv`, his spreadsheets)
- **2024 (Apr 29–Nov):** started from some biking. Built from ~5 to 50–57 mi/wk by Sep–Oct (876 mi in 28 weeks; long runs up to 20 on the AZT). Strained his left calf on a goal-pace run Nov 4, then tore it in a race and DNF'd at mile 6.
  Strava export (`data/Strava_export_10-21-2026/`, gitignored; no HR before Jun 2025): Nov 9, 2024 was 12.1 mi flat at ~7,100 ft, miles 1–9 at 8:26–9:02, then 8:04 / 7:58 / 7:38. The Nov 16, 2024 DNF was a marathon at ~5,100 ft, about 8:07/mi through 6 mi (goal ~8:00, so ~3:30). Strava moving time is wrong for Jun 20–Jul 24 2025 (GPS auto-pause; use elapsed time).
- **Dec 2024–Jan 2025:** physical therapy, aqua jogging, run/walk, then back to running by mid-January.
- **2025 (Jan–Oct):** about 1,020 mi, mostly 25–40 mi/wk, peaking at 41–46 in late June and July. Schultz Creek 5.8 mi loop went from 53:17 to 46:54 between January and April. Right knee pain in mid-July (started strength training), then a left meniscus problem. Restarted with MAF/HR training in August, did threshold work in September, and the knees got sore again. In October an MRI showed mild arthritis and a minor meniscus tear in the left knee; the doctor advised running every other day. **Oct 12, 2025: 18K at 8:29/mi (~1:34:53)**, same venue as the 2026 10K (~7,400 ft), "slow start, strong finish", on reduced volume (VDOT 39.4 at altitude vs. 42.1 for the 2026 10K).
- 2025 aerobic pace (`Running log - MAF pace.csv`): best 2 km on Karen Cooper (1 km up, 1 km down, no net change) at MAF HR ~143: 10:52/mi (Aug 12) → 9:39 (Sep 19). His own adjustment is ~0.5% per bpm.
- Sep 2025 threshold (his notes; the weeks of Sep 1–15 had 3 × 10 min and 30 min threshold sessions): 7:41–7:42 grade-adjusted at HR 170–174.
- **Nov 2025–Jun 14, 2026:** no running (about 7.5 months).
- **2026:** restarted Jun 15 at 6–11 mi/wk; first workouts mid-August. The recorded data starts Aug 17.
- Pattern: both injuries came at or just after the highest volume he had reached quickly (57 mi/wk in 2024, 46 in 2025).

## Injury history / constraints
- Left calf tear (Nov 2024, in a race). Left knee: mild arthritis and a minor meniscus tear (MRI, Oct 2025); right knee pain Jul 2025. Hill sprints 2×/week and strength work appear to have fixed the knees, so keep them in every week.
- Currently runs 5 days/week and wants to move to 6. Target volume is 45 mi/wk (raised from 40 on 2026-10-17).
- All training is in Flagstaff.
