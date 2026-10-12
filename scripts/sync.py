"""Pull activities from intervals.icu into data/activities.json, log.csv, and data/fit/.

Usage: python scripts/sync.py [oldest YYYY-MM-DD]
"""
import base64, csv, json, sys, urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
env = dict(l.split("=", 1) for l in (ROOT / ".env").read_text().split() if "=" in l)
KEY, ATHLETE = env["INTERVALS_API_KEY"], env.get("INTERVALS_ATHLETE_ID", "0")
HEADERS = {"Authorization": "Basic " + base64.b64encode(f"API_KEY:{KEY}".encode()).decode(),
           "User-Agent": "RunningCoach-sync/1.0"}  # intervals.icu rejects urllib's default UA
API = "https://intervals.icu/api/v1"
M_PER_MI = 1609.344


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS)) as r:
        return r.read()


def fmt_time(s):
    s = int(s or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_pace(sec_per_mi):
    return f"{int(sec_per_mi // 60)}:{int(sec_per_mi % 60):02d}" if sec_per_mi else ""


def main():
    oldest = sys.argv[1] if len(sys.argv) > 1 else (date.today() - timedelta(days=365)).isoformat()
    newest = (date.today() + timedelta(days=1)).isoformat()
    acts = json.loads(fetch(f"{API}/athlete/{ATHLETE}/activities?oldest={oldest}&newest={newest}"))
    acts.sort(key=lambda a: a["start_date_local"])
    fit_dir = ROOT / "data" / "fit"
    fit_dir.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "activities.json").write_text(json.dumps(acts, indent=1))

    new_fits = 0
    for a in acts:
        path = fit_dir / f"{a['id']}.fit"
        if not path.exists():
            path.write_bytes(fetch(f"{API}/activity/{a['id']}/file"))
            new_fits += 1

    with open(ROOT / "log.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "type", "name", "miles", "moving_time", "pace_per_mi",
                    "avg_hr", "max_hr", "elev_ft", "load", "rpe", "id"])
        for a in acts:
            mi = (a.get("distance") or 0) / M_PER_MI
            t = a.get("moving_time") or 0
            w.writerow([a["start_date_local"][:10], a.get("type"), a.get("name"),
                        f"{mi:.2f}", fmt_time(t), fmt_pace(t / mi) if mi else "",
                        a.get("average_heartrate"), a.get("max_heartrate"),
                        round((a.get("total_elevation_gain") or 0) * 3.281),
                        a.get("icu_training_load"), a.get("icu_rpe"), a.get("id")])
    print(f"Synced {len(acts)} activities ({oldest} to today), {new_fits} new FIT files")


if __name__ == "__main__":
    main()
