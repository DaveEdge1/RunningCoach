"""Build the GitHub Pages calendar (docs/index.html) from plan.csv and data/runs.csv.

Usage: python scripts/build_site.py   (run scripts/analyze.py first)
"""
import calendar, csv, html
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RACE = (date(2026, 12, 26), "Phoenix 10K")
ZONE_COLS = ["Z1 Recovery", "Z2 Aerobic", "Z3 Tempo", "Z4 Threshold", "Z5 >LT"]
TYPES = {"easy": "Easy", "long": "Long", "workout": "Workout", "race": "Race", "off": "Off"}


def load_plan():
    with open(ROOT / "plan.csv", encoding="utf-8") as f:
        return {date.fromisoformat(r["date"]): r for r in csv.DictReader(f)}


def load_actuals():
    """Per-day totals; HR and zones are weighted by seconds of HR data."""
    days = defaultdict(lambda: {"miles": 0.0, "secs": 0, "hr_secs": 0, "hr_sum": 0,
                                "zones": [0.0] * len(ZONE_COLS), "runs": 0, "manual": False})
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = days[date.fromisoformat(r["date"])]
            mi = float(r["miles"])
            d["miles"] += mi
            d["runs"] += 1
            m, s = r["pace"].split(":") if r["pace"] else (0, 0)
            d["secs"] += (int(m) * 60 + int(s)) * mi
            if r["fit"].endswith("(manual)"):
                d["manual"] = True
            elif r["hr_secs"]:
                n = int(r["hr_secs"])
                d["hr_secs"] += n
                d["hr_sum"] += int(r["avg_hr"]) * n
                d["zones"] = [z + float(r[c]) * n / 100 for z, c in zip(d["zones"], ZONE_COLS)]
    return days


def fmt_pace(secs, miles):
    if not miles:
        return ""
    p = secs / miles
    return f"{int(p // 60)}:{int(p % 60):02d}"


def fmt_mi(x):
    return f"{x:.1f}".rstrip("0").rstrip(".") if x else "0"


def zone_bar(zones, total):
    if not total:
        return ""
    segs = "".join(f'<i class="z{i + 1}" style="width:{100 * z / total:.1f}%"></i>'
                   for i, z in enumerate(zones) if z)
    title = " · ".join(f"Z{i + 1} {100 * z / total:.0f}%" for i, z in enumerate(zones))
    return f'<span class="zbar" title="{title}">{segs}</span>'


def day_cell(d, plan, act, month):
    p, a = plan.get(d), act.get(d)
    cls = ["day"]
    if d.month != month:
        cls.append("other")
    if p:
        cls.append(f"t-{p['type']}")
    parts = [f'<div class="dnum"><span class="wd">{d:%a}</span> {d.day}</div>']
    if p:
        mi = float(p["miles"])
        label = TYPES.get(p["type"], p["type"])
        parts.append(f'<div class="plan"><span class="tag">{label}</span>'
                     f'{f" <b>{fmt_mi(mi)}</b> mi" if mi else ""}'
                     + (f'<div class="sess">{html.escape(p["session"])}</div>' if p["session"] != label else "")
                     + '</div>')
    if a:
        hr = round(a["hr_sum"] / a["hr_secs"]) if a["hr_secs"] else None
        bits = [f'<b>{fmt_mi(round(a["miles"], 1))}</b> mi', fmt_pace(a["secs"], a["miles"])]
        if hr:
            bits.append(f"{hr} bpm")
        if a["manual"]:
            bits.append("no HR")
        parts.append(f'<div class="act">✓ {" · ".join(b for b in bits if b)}'
                     f'{zone_bar(a["zones"], a["hr_secs"])}</div>')
    elif p and float(p["miles"]) and d < date.today():
        parts.append('<div class="act missed">not recorded</div>')
    return f'<div class="{" ".join(cls)}" data-date="{d}">{"".join(parts)}</div>'


def month_html(year, month, plan, act):
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    rows = []
    for wk in weeks:
        cells = "".join(day_cell(d, plan, act, month) for d in wk)
        planned = [d for d in wk if d in plan]
        pm = sum(float(plan[d]["miles"]) for d in planned) if len(planned) >= 3 else 0  # skip one-off entries
        am = sum(act[d]["miles"] for d in wk if d in act)
        if pm and wk[0] > date.today():  # future week: plan only
            tot = f'<div class="wk"><span class="wd">Week</span><b>{fmt_mi(pm)}</b><small>planned</small></div>'
        else:
            tot = f'<div class="wk"><span class="wd">Week</span><b>{fmt_mi(round(am, 1))}</b>'
            tot += f'<small>of {fmt_mi(pm)} planned</small></div>' if pm else "<small>mi</small></div>"
        empty = "" if any(d in plan or d in act for d in wk if d.month == month) else " empty"
        rows.append(f'<div class="week{empty}">{cells}{tot}</div>')
    head = "".join(f"<div>{n}</div>" for n in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Week"])
    return (f'<section class="month" id="m{year}-{month:02d}"><h2>{calendar.month_name[month]} {year}</h2>'
            f'<div class="head">{head}</div>{"".join(rows)}</section>')


def main():
    plan, act = load_plan(), load_actuals()
    first = min(min(plan), min(act))
    last = max(max(plan), max(act))
    months, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        months.append(month_html(y, m, plan, act))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    built = datetime.now(timezone(timedelta(hours=-7)))
    legend = "".join(f'<span class="tag t-{k}">{v}</span>' for k, v in TYPES.items() if k != "off")
    zleg = "".join(f'<span><i class="z{i + 1}"></i>{c}</span>' for i, c in enumerate(ZONE_COLS))
    page = TEMPLATE.format(
        race_date=RACE[0].isoformat(), race_name=html.escape(RACE[1]),
        race_long=f"{RACE[0]:%a %b} {RACE[0].day}, {RACE[0].year}",
        legend=legend, zleg=zleg, months="".join(months),
        built=f"{built:%b} {built.day}, {built:%Y %H:%M} MST")
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(page, encoding="utf-8")
    (ROOT / "docs" / ".nojekyll").touch()
    print(f"Wrote docs/index.html ({len(months)} months)")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Training Calendar</title>
<style>
:root {{
  --bg: #f7f7f5; --card: #ffffff; --ink: #1d1d1b; --muted: #6b6b66; --line: #e4e3df;
  --easy: #3b82c4; --long: #7a5cc4; --workout: #d9822b; --race: #d43d3d;
  --ok: #2e8b57; --miss: #b8b6ae; --today: #f2c94c;
  --z1: #a9b4c2; --z2: #5b9bd5; --z3: #6bbf7a; --z4: #f0a640; --z5: #e0534a;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #161615; --card: #20201e; --ink: #ecebe6; --muted: #9a998f; --line: #34332f;
    --easy: #6aa9e0; --long: #a48be6; --workout: #f0a24f; --race: #f06b6b;
    --ok: #5cc48a; --miss: #5d5c56; --today: #d9b23a;
    --z1: #6f7a88; --z2: #4f8cc4; --z3: #57a866; --z4: #d99236; --z5: #cc4a42;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 1240px; margin: 0 auto; padding: 24px 16px 48px; }}
header h1 {{ margin: 0 0 4px; font-size: 26px; letter-spacing: -0.01em; }}
.goal {{ color: var(--muted); margin: 0 0 16px; }}
.goal b {{ color: var(--race); }}
.legend {{ display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; color: var(--muted);
  font-size: 12px; margin-bottom: 8px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px;
  vertical-align: -1px; }}
.month {{ margin-top: 28px; }}
.month h2 {{ font-size: 18px; margin: 0 0 8px; }}
.head, .week {{ display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)) 84px; gap: 4px; }}
.head div {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
  padding: 0 6px 4px; }}
.week {{ margin-bottom: 4px; }}
.day, .wk {{ background: var(--card); border: 1px solid var(--line); border-radius: 6px;
  padding: 6px; min-height: 92px; }}
.day {{ border-top: 3px solid var(--line); }}
.day.other {{ opacity: .45; }}
.day.t-easy {{ border-top-color: var(--easy); }}
.day.t-long {{ border-top-color: var(--long); }}
.day.t-workout {{ border-top-color: var(--workout); }}
.day.t-race {{ border-top-color: var(--race); }}
.day.today {{ outline: 2px solid var(--today); outline-offset: 1px; }}
.dnum {{ font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }}
.wd {{ display: none; }}
.tag {{ display: inline-block; font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: .05em; padding: 1px 5px; border-radius: 3px; color: #fff; background: var(--muted); }}
.tag.t-easy, .t-easy .tag {{ background: var(--easy); }}
.tag.t-long, .t-long .tag {{ background: var(--long); }}
.tag.t-workout, .t-workout .tag {{ background: var(--workout); }}
.tag.t-race, .t-race .tag {{ background: var(--race); }}
.t-off .tag {{ background: transparent; color: var(--muted); border: 1px solid var(--line); }}
.plan {{ margin-top: 3px; }}
.sess {{ font-size: 12px; color: var(--muted); margin-top: 2px; }}
.act {{ margin-top: 6px; font-size: 12px; color: var(--ok); font-variant-numeric: tabular-nums; }}
.act.missed {{ color: var(--miss); font-style: italic; }}
.zbar {{ display: flex; height: 5px; border-radius: 3px; overflow: hidden; margin-top: 4px;
  background: var(--line); }}
.zbar i {{ display: block; height: 100%; }}
.z1 {{ background: var(--z1); }} .z2 {{ background: var(--z2); }} .z3 {{ background: var(--z3); }}
.z4 {{ background: var(--z4); }} .z5 {{ background: var(--z5); }}
.wk {{ display: flex; flex-direction: column; justify-content: center; align-items: flex-end;
  text-align: right; font-variant-numeric: tabular-nums; }}
.wk b {{ font-size: 18px; }}
.wk small {{ color: var(--muted); font-size: 11px; }}
footer {{ margin-top: 32px; color: var(--muted); font-size: 12px; }}
@media (max-width: 760px) {{
  .head, .week.empty {{ display: none; }}
  .week {{ grid-template-columns: 1fr; gap: 3px; margin-bottom: 14px; }}
  .day, .wk {{ min-height: 0; }}
  .day {{ border-top-width: 1px; border-left: 4px solid var(--line); }}
  .day.t-easy {{ border-left-color: var(--easy); }} .day.t-long {{ border-left-color: var(--long); }}
  .day.t-workout {{ border-left-color: var(--workout); }} .day.t-race {{ border-left-color: var(--race); }}
  .day.other {{ display: none; }}
  .day:not(.t-easy):not(.t-long):not(.t-workout):not(.t-race):not(:has(.act)) {{ display: none; }}
  .wd {{ display: inline; }}
  .wk {{ flex-direction: row; justify-content: space-between; align-items: baseline; gap: 8px;
    background: transparent; border: 0; padding: 2px 6px; }}
  .wk b {{ font-size: 15px; margin-left: auto; }}
}}
</style>
</head>
<body>
<main>
<header>
  <h1>Training Calendar</h1>
  <p class="goal">Goal: <b>{race_name}</b> · {race_long} · <span id="countdown"></span></p>
  <div class="legend">{legend}</div>
  <div class="legend">HR zones: {zleg}</div>
</header>
{months}
<footer>Plan vs. completed runs (COROS). Updated {built}.</footer>
</main>
<script>
(function () {{
  var t = new Date(), iso = t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" +
    String(t.getDate()).padStart(2, "0");
  var el = document.querySelector('.day[data-date="' + iso + '"]:not(.other)');
  if (el) {{ el.classList.add("today"); el.scrollIntoView({{block: "center"}}); }}
  var days = Math.round((new Date("{race_date}T00:00:00") - new Date(iso + "T00:00:00")) / 864e5);
  document.getElementById("countdown").textContent =
    days > 0 ? days + " days to go" : days === 0 ? "race day!" : "done";
}})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
