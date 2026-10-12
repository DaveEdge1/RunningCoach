# Running Coach

This folder is David's running log; Claude acts as his running coach.

## Data
- Runs are recorded on COROS (upper-arm optical HR strap) and auto-sync to intervals.icu.
- `python scripts/sync.py [oldest-date]` pulls activities from the intervals.icu API
  (key in `.env`) into `data/activities.json` (raw) and `log.csv` (summary, miles / min per mi).
- Run the sync at the start of any coaching conversation to get the latest runs.
- `scripts/analyze.py` reads every FIT file in `data/fit/` (intervals.icu) and `data/coros_export/` (COROS bulk export zips, unzipped), de-duplicates by start time, and writes `data/laps.csv` and `data/runs.csv` plus rolling 7d/28d mileage. Some runs (e.g., 2026-09-20) never reached intervals.icu, so re-export from COROS if totals look low.
- `profile.md`: goals, PRs, HR thresholds and zones, injury history. `notes.md`: subjective notes per run.

## Context
- Lives in Flagstaff, AZ (~7,000 ft). Factor altitude into pace and HR interpretation.
- Uses imperial units.
- Hand-made FIT files (e.g., when he forgets his watch) go in `data/manual_runs.txt`. They count toward mileage but are excluded from HR and pace analysis.
- `training-plan.md`: current block (Phoenix 10K, Dec 26 2026). Compare actual runs against it and adjust week by week.

## Publishing
- GitHub repo DaveEdge1/RunningCoach (public), with Pages served from `docs/`.
- `scripts/build_site.py` builds `docs/index.html` (the calendar) from `plan.csv` and `data/runs.csv`.
- `bash scripts/update.sh` runs the sync, analysis, and site build, then commits and pushes.
- When the plan changes, update both `plan.csv` (which drives the calendar) and `training-plan.md`.
- Never commit `.env`, FIT files, `data/coros_export/`, or `data/activities.json` (they hold the API key and GPS tracks). `.gitignore` covers them.
