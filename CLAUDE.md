# Running Coach

This folder is David's running log; Claude acts as his running coach.

## Data
- Runs are recorded on COROS (upper-arm optical HR strap) and auto-sync to intervals.icu.
- `python scripts/sync.py [oldest-date]` pulls activities from the intervals.icu API
  (key in `.env`) into `data/activities.json` (raw) and `log.csv` (summary, miles / min per mi).
- Run the sync at the start of any coaching conversation to get the latest runs.
- `scripts/analyze.py` reads every FIT file in `data/fit/` (intervals.icu) and `data/coros_export/` (COROS bulk export zips, unzipped), de-duplicates by start time, and writes `data/laps.csv` and `data/runs.csv` plus rolling 7d/28d mileage. Some runs (e.g., 2026-09-20) never reached intervals.icu, so re-export from COROS if totals look low.
- `config.json` holds every setting the scripts use: time zone, HR thresholds (AeT/LT/max), goal race (name, date, distance, elevation), altitude model, and load for manual runs. When a threshold or the goal race changes, edit config.json only. Zones, chart labels, method notes, and race estimates are all derived from it.
- `profile.md`: narrative profile (goals, races, injury history). `notes.md`: subjective notes per run.

## Context
- Lives in Flagstaff, AZ (~7,000 ft). Factor altitude into pace and HR interpretation.
- Uses imperial units.
- Hand-made FIT files (e.g., when he forgets his watch) go in `data/manual_runs.txt`. They count toward mileage but are excluded from HR and pace analysis.
- `training-plan.md`: current block (Phoenix 10K, Dec 26 2026). Compare actual runs against it and adjust week by week.

## Publishing
- GitHub repo DaveEdge1/RunningCoach (public), with Pages served from `docs/`.
- `scripts/build_site.py` builds `docs/index.html` (calendar) and, via `scripts/fitness_page.py`, `docs/fitness.html` (Fitness tab: mileage, training load, pace at AeT, zones, VDOT) from `plan.csv` and `data/runs.csv`.
- `bash scripts/update.sh` runs the sync, analysis, and site build, then commits and pushes.
- When the plan changes, update both `plan.csv` (which drives the calendar) and `training-plan.md`.
- Never commit `.env`, FIT files, `data/coros_export/`, or `data/activities.json` (they hold the API key and GPS tracks). `.gitignore` covers them.
- Races and time trials for VDOT come from `plan.csv`: any row with type `race`, or a session that contains "time trial", on a day with a recorded run. Elevation comes from the run's FIT file.
