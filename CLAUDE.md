# Running Coach

This folder is David's running log; Claude acts as his running coach.

## Data
- Runs are recorded on COROS (upper-arm optical HR strap) and auto-sync to intervals.icu.
- `python scripts/sync.py [oldest-date]` pulls activities from the intervals.icu API
  (key in `.env`) into `data/activities.json` (raw) and `log.csv` (summary, miles / min per mi).
- Run the sync at the start of any coaching conversation to get the latest runs.
- `scripts/analyze.py` reads every FIT file in `data/fit/` (intervals.icu) and `data/coros_export/` (COROS bulk export zips, unzipped), de-duplicates by start time, and writes `data/laps.csv`, `data/runs.csv`, and `data/hr_hist.csv` (seconds at each bpm, drawn as the month view HR curve), `data/trace.csv` (HR, grade-adjusted pace via Minetti et al. 2002, and pace in 5 s bins, drawn in the week view), `data/efforts.csv` (fastest grade-adjusted 1 mi / 2 mi / 5K / 10K / half within each run, with clock time, net elevation and HR), and `data/cross.csv` (hikes and XC skis: HR-zone load, never mileage). `laps.csv` carries grade-adjusted lap pace; `runs.csv` carries aerobic decoupling (Friel's Pa:HR, first vs. second half after the warm-up) and whole-run grade-adjusted time. Grade comes from USGS 3DEP map elevation along the GPS track (`scripts/elevation.py`, cached per run in `data/elev.csv` as elevation vs distance, no coordinates), falling back to the barometer plus rolling 7d/28d mileage. Some runs (e.g., 2026-09-20) never reached intervals.icu, so re-export from COROS if totals look low.
- `config.json` holds the athlete settings the scripts use: time zone, home (name, elevation), HR thresholds (AeT/LT/max), altitude methods (Daniels s/mi per 1,000 ft above a floor; path to the NCAA table), load for manual runs, load ramp (acute:chronic windows and Gabbett's thresholds), decoupling (minimum minutes, 5% limit), best efforts (distances, minimum distance to stand in for a race), workout rep matching tolerances, and cross-training (which FIT sports count, plan keywords, default load per minute before any recorded sessions). When a threshold changes, edit config.json only; zones, labels, and method notes are derived from it.
- `races.csv` lists races (date, name, location, distance, elevation, course notes). The next upcoming row is the "next race" on both pages. Add new races here.
- Altitude conversions use two published methods, shown as a range (`scripts/config.py` `convert_time`): Daniels and the NCAA 2009 tables. Sources are in `data/ref/README.md`. Don't invent altitude factors.
- `profile.md`: narrative profile (goals, races, injury history). `notes.md`: subjective notes per run (`- YYYY-MM-DD: note`); a note saying "trail" keeps that run out of the pace-at-AeT trend.

## Context
- Lives in Flagstaff, AZ (~7,000 ft). Factor altitude into pace and HR interpretation.
- Uses imperial units.
- Hand-made FIT files (e.g., when he forgets his watch) go in `data/manual_runs.txt`. They count toward mileage but are excluded from HR and pace analysis.
- `training-plan.md`: current block (Phoenix 10K, Dec 26 2026). Compare actual runs against it and adjust week by week.

## Publishing
- GitHub repo DaveEdge1/RunningCoach (public), with Pages served from `docs/`.
- `scripts/build_site.py` builds `docs/index.html` (calendar) and, via `scripts/fitness_page.py`, `docs/fitness.html` (Fitness tab: mileage, training load, pace at AeT, decoupling, zones, load projection to race day with the acute:chronic load ramp, workouts vs. targets, fitness vs. goal, best efforts, VDOT) from `plan.csv` and `data/`.
- Workout reps are matched to laps from the plan session's "N × D" (e.g., "4 × 1 mi threshold"), so keep that wording in `plan.csv` and remind David to lap each rep.
- Current fitness is the latest race or time trial, unless a later training best effort beats it, is ≥ 1 mi, has a net grade under 5%, and reaches LT. The comparison uses grade-adjusted (Minetti) time for both the effort and the race; race results still display clock time. David chose this on 2026-10-15 (the World Athletics 1 m/km net-drop rule was dropped: net drop ignores climbing inside the span).
- Cross-training: record hikes/backpacking and XC skis on COROS with the arm strap; they add load, not miles. Planned ones need minutes in `plan.csv`'s `cross_min` column and a keyword (hike, backpack, ski) in the session.
- A GitHub Action (`.github/workflows/update.yml`) does the same daily at 12:00 Arizona (19:00 UTC), using the `INTERVALS_API_KEY` / `INTERVALS_ATHLETE_ID` repo secrets. It downloads FIT files fresh and never commits them.
- `bash scripts/update.sh` does it locally (pulls first, since the Action also commits). Use it after a COROS re-export or a manual FIT file, since those exist only on this PC.
- `data/runs.csv` and `data/laps.csv` are the permanent record: `analyze.py` refreshes runs whose FIT it can see and keeps the other rows as they are.
- When the plan changes, update both `plan.csv` (which drives the calendar) and `training-plan.md`.
- Never commit `.env`, FIT files, `data/coros_export/`, or `data/activities.json` (they hold the API key and GPS tracks). `.gitignore` covers them.
- Races and time trials for VDOT: any `races.csv` date or `plan.csv` row of type `race`, or a plan session that contains "time trial", on a day with a recorded run. Elevation comes from the run's FIT file (falling back to races.csv, then home). The result is the fastest window at the race distance (races.csv, or the distance in the session, e.g. "5K time trial"), so a warm-up and cool-down recorded in the same activity don't count; a run within `race_whole_run_tolerance_pct` of the distance is scored whole, and shorter runs that day (a separate warm-up) are ignored. Keep the distance in time-trial session names.
