"""Build the GitHub Pages calendar (docs/index.html) from plan.csv, data/runs.csv and data/laps.csv.

Usage: python scripts/build_site.py   (run scripts/analyze.py first)
"""
import calendar, csv, html
from collections import defaultdict
from datetime import date, datetime, timedelta

from config import ROOT, TZ, ZONE_NAMES as ZONE_COLS, ZONES, next_race, zone_range
TYPES = {"easy": "Easy", "long": "Long", "workout": "Workout", "race": "Race",
         "cross": "Cross-train", "off": "Off"}


def load_plan():
    with open(ROOT / "plan.csv", encoding="utf-8") as f:
        return {date.fromisoformat(r["date"]): r for r in csv.DictReader(f)}


def plan_start(plan):
    """Monday of the first week with at least 3 planned days (one-off entries don't start the plan)."""
    weeks = defaultdict(int)
    for d in plan:
        weeks[d - timedelta(d.weekday())] += 1
    return min((w for w, n in weeks.items() if n >= 3), default=None)


def secs(p):
    m, s = p.split(":") if p else (0, 0)
    return int(m) * 60 + int(s)


def load_actuals():
    """Per-day totals plus each run's laps; HR and zones are weighted by seconds of HR data."""
    laps = defaultdict(list)
    with open(ROOT / "data" / "laps.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            laps[(r["date"], r["start"])].append(r)
    days = defaultdict(lambda: {"miles": 0.0, "secs": 0, "hr_secs": 0, "hr_sum": 0, "elev": 0,
                                "zones": [0.0] * len(ZONE_COLS), "runs": [], "manual": False})
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = days[date.fromisoformat(r["date"])]
            mi = float(r["miles"])
            d["miles"] += mi
            d["secs"] += secs(r["pace"]) * mi
            d["elev"] += int(r["elev_ft"] or 0)
            d["runs"].append({**r, "laps": laps.get((r["date"], r["start"]), [])})
            if r["fit"].endswith("(manual)"):
                d["manual"] = True
            elif r["hr_secs"]:
                n = int(r["hr_secs"])
                d["hr_secs"] += n
                d["hr_sum"] += int(r["avg_hr"]) * n
                d["zones"] = [z + float(r[c]) * n / 100 for z, c in zip(d["zones"], ZONE_COLS)]
    return days


def fmt_pace(total_secs, miles):
    if not miles:
        return ""
    p = total_secs / miles
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


def zone_text(zones, total):
    return " · ".join(f'<span class="zt"><i class="z{i + 1}"></i>{100 * z / total:.0f}%</span>'
                      for i, z in enumerate(zones)) if total else ""


def plan_html(p):
    mi = float(p["miles"])
    label = TYPES.get(p["type"], p["type"])
    sess = f'<div class="sess">{html.escape(p["session"])}</div>' if p["session"] != label else ""
    return (f'<div class="plan"><span class="tag">{label}</span>'
            f'{f" <b>{fmt_mi(mi)}</b> mi" if mi else ""}{sess}</div>')


def act_summary(a):
    hr = round(a["hr_sum"] / a["hr_secs"]) if a["hr_secs"] else None
    bits = [f'<b>{fmt_mi(round(a["miles"], 1))}</b> mi', fmt_pace(a["secs"], a["miles"])]
    if hr:
        bits.append(f"{hr} bpm")
    if a["manual"]:
        bits.append("no HR")
    return "✓ " + " · ".join(b for b in bits if b)


def day_classes(d, plan, month=None):
    cls = ["day"]
    if month and d.month != month:
        cls.append("other")
    if d in plan:
        cls.append(f"t-{plan[d]['type']}")
    return " ".join(cls)


def missed(d, plan):
    return d in plan and plan[d]["type"] != "cross" and float(plan[d]["miles"]) and d < date.today()


def week_totals(wk, plan, act):
    planned = [d for d in wk if d in plan]
    pm = sum(float(plan[d]["miles"]) for d in planned) if len(planned) >= 3 else 0  # skip one-offs
    am = sum(act[d]["miles"] for d in wk if d in act)
    return pm, am


def total_html(pm, am, wk0):
    if wk0 > date.today():  # future week: plan only
        if not pm:
            return ""
        return f'<b>{fmt_mi(pm)}</b><small>planned</small>'
    return f'<b>{fmt_mi(round(am, 1))}</b>' + (f'<small>of {fmt_mi(pm)} planned</small>' if pm else "<small>mi</small>")


# ---------- month view ----------

def month_cell(d, plan, act, month):
    p, a = plan.get(d), act.get(d)
    parts = [f'<div class="dnum"><span class="wd">{d:%a}</span> {d.day}</div>']
    if p:
        parts.append(plan_html(p))
    if a:
        parts.append(f'<div class="act">{act_summary(a)}{zone_bar(a["zones"], a["hr_secs"])}</div>')
    elif missed(d, plan):
        parts.append('<div class="act missed">not recorded</div>')
    return f'<div class="{day_classes(d, plan, month)}" data-date="{d}">{"".join(parts)}</div>'


def month_html(year, month, plan, act):
    rows = []
    for wk in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = "".join(month_cell(d, plan, act, month) for d in wk)
        pm, am = week_totals(wk, plan, act)
        empty = "" if any(d in plan or d in act for d in wk if d.month == month) else " empty"
        rows.append(f'<div class="week{empty}" data-week="{wk[0]}">{cells}'
                    f'<div class="wk" data-week="{wk[0]}"><span class="wd">Week</span>{total_html(pm, am, wk[0])}</div></div>')
    head = "".join(f"<div>{n}</div>" for n in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Week"])
    return (f'<section class="month"><h2>{calendar.month_name[month]} {year}</h2>'
            f'<div class="head">{head}</div>{"".join(rows)}</section>')


# ---------- week view ----------

def laps_table(run):
    laps = run["laps"]
    if len(laps) < 2:
        return ""
    rows = "".join(f'<tr><td>{l["lap"]}</td><td>{l["miles"]}</td><td>{l["pace"]}</td>'
                   f'<td>{l["avg_hr"]}</td><td>{l["max_hr"]}</td>'
                   f'<td>+{l["ascent_ft"]}/−{l["descent_ft"]}</td></tr>' for l in laps)
    return (f'<details><summary>{len(laps)} laps</summary><table class="laps"><thead><tr>'
            f'<th>Lap</th><th>Mi</th><th>Pace</th><th>HR</th><th>Max</th><th>Ft</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></details>')


def week_day(d, plan, act):
    p, a = plan.get(d), act.get(d)
    body = plan_html(p) if p else '<div class="plan none">—</div>'
    if a:
        body += f'<div class="act">{act_summary(a)} · {a["elev"]} ft'
        body += zone_bar(a["zones"], a["hr_secs"])
        if a["hr_secs"]:
            body += f'<div class="zts">{zone_text(a["zones"], a["hr_secs"])}</div>'
        body += "".join(laps_table(r) for r in a["runs"]) + "</div>"
    elif missed(d, plan):
        body += '<div class="act missed">not recorded</div>'
    return (f'<div class="wday {day_classes(d, plan)}" data-date="{d}">'
            f'<div class="wdate"><b>{d:%a}</b><span>{d:%b} {d.day}</span></div>'
            f'<div class="wbody">{body}</div></div>')


def week_html(wk0, plan, act, start):
    wk = [wk0 + timedelta(i) for i in range(7)]
    pm, am = week_totals(wk, plan, act)
    zones = [sum(act[d]["zones"][i] for d in wk if d in act) for i in range(len(ZONE_COLS))]
    hr_total = sum(act[d]["hr_secs"] for d in wk if d in act)
    n = (wk0 - start).days // 7 + 1 if start else 0
    label = f"Plan week {n} · " if start and start <= wk0 <= max(plan) else ""
    end = wk[-1]
    zsum = f'<div class="zts">{zone_text(zones, hr_total)}</div>' if hr_total else ""
    head = (f'<div class="whead"><h2>{label}{wk0:%b} {wk0.day} – {end:%b} {end.day}</h2>'
            f'<div class="wsum">{total_html(pm, am, wk0)}{zsum}</div></div>')
    return (f'<section class="wview" data-week="{wk0}" hidden>{head}'
            f'{"".join(week_day(d, plan, act) for d in wk)}</section>')


def main():
    plan, act = load_plan(), load_actuals()
    first, last = min(min(plan), min(act)), max(max(plan), max(act))
    months, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        months.append(month_html(y, m, plan, act))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    weeks, wk0 = [], first - timedelta(first.weekday())
    while wk0 <= last:
        weeks.append(week_html(wk0, plan, act, plan_start(plan)))
        wk0 += timedelta(7)

    built = datetime.now(TZ)
    legend = "".join(f'<span class="tag t-{k}">{v}</span>' for k, v in TYPES.items() if k != "off")
    zleg = "".join(f'<span><i class="z{i + 1}"></i>{n} {zone_range(lo, hi)}</span>' for i, (n, lo, hi) in enumerate(ZONES))
    nr = next_race()
    goal = (f'Next race: <b>{html.escape(nr["name"])}</b> · {nr["date"]:%a %b} {nr["date"].day}, {nr["date"].year}'
            ' · <span id="countdown"></span>') if nr else ""
    subs = {"RACE_DATE": nr["date"].isoformat() if nr else "", "GOAL": goal,
            "LEGEND": legend, "ZLEG": zleg, "MONTHS": "".join(months), "WEEKS": "".join(weeks),
            "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}",
            "BASE_CSS": BASE_CSS, "TABS": tabs("index.html")}
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(page, encoding="utf-8")
    (ROOT / "docs" / ".nojekyll").touch()
    print(f"Wrote docs/index.html ({len(months)} months, {len(weeks)} weeks)")

    import fitness_page
    fitness_page.build(plan, built)


BASE_CSS = """:root {
  --bg: #f7f7f5; --card: #ffffff; --ink: #1d1d1b; --muted: #6b6b66; --line: #e4e3df;
  --easy: #3b82c4; --long: #7a5cc4; --workout: #d9822b; --race: #d43d3d; --cross: #2a9d8f;
  --ok: #2e8b57; --miss: #b8b6ae; --today: #e0b100; --curwk: rgba(224, 177, 0, .16);
  --z1: #3a9d5d; --z2: #9cc43a; --z3: #f0c330; --z4: #ee8a2c; --z5: #d63c35;
  --s1: #2a78d6; --s2: #eb6834; --s7: #4a3aa7; --fresh: #2a78d6; --neutral: #9b9a94; --tired: #e34948;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #161615; --card: #20201e; --ink: #ecebe6; --muted: #9a998f; --line: #34332f;
    --easy: #6aa9e0; --long: #a48be6; --workout: #f0a24f; --race: #f06b6b; --cross: #4cc2b3;
    --ok: #5cc48a; --miss: #5d5c56; --today: #d9b23a; --curwk: rgba(217, 178, 58, .18);
    --z1: #3fa862; --z2: #94bd3f; --z3: #e3b93a; --z4: #e2883a; --z5: #d4473f;
    --s1: #3987e5; --s2: #d95926; --s7: #9085e9; --fresh: #3987e5; --neutral: #77766f; --tired: #e66767;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1240px; margin: 0 auto; padding: 24px 16px 48px; }
header h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: -0.01em; }
.goal { color: var(--muted); margin: 0 0 16px; }
.goal b { color: var(--race); }
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; color: var(--muted);
  font-size: 12px; margin-bottom: 8px; }
.legend i, .zt i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px;
  vertical-align: -1px; }
nav.tabs { display: flex; gap: 4px; margin: 0 0 14px; }
nav.tabs a { padding: 6px 14px; border-radius: 6px; color: var(--muted); text-decoration: none;
  border: 1px solid var(--line); background: var(--card); font-size: 13px; }
nav.tabs a[aria-current="page"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }
"""


def tabs(current):
    links = [("index.html", "Calendar"), ("fitness.html", "Fitness")]
    return '<nav class="tabs">' + "".join(
        f'<a href="{h}"' + (' aria-current="page"' if h == current else "") + f">{t}</a>"
        for h, t in links) + "</nav>"


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Training Calendar</title>
<style>
%%BASE_CSS%%.bar { position: sticky; top: 0; z-index: 5; background: var(--bg); padding: 10px 0;
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center; border-bottom: 1px solid var(--line); }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
button { font: inherit; font-size: 13px; color: var(--ink); background: var(--card); border: 0;
  padding: 6px 12px; cursor: pointer; }
button:disabled { opacity: .4; cursor: default; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button[aria-pressed="true"] { background: var(--ink); color: var(--bg); }
.nav { display: none; gap: 4px; }
.nav button { border: 1px solid var(--line); border-radius: 6px; }
body.weekly .nav { display: inline-flex; }
body.weekly #monthly, body:not(.weekly) #weekly { display: none; }
.month { margin-top: 28px; }
.month h2, .whead h2 { font-size: 18px; margin: 0 0 8px; }
.head, .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)) 84px; gap: 4px; }
.head div { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted);
  padding: 0 6px 4px; }
.week { margin-bottom: 4px; border-radius: 8px; }
.week.current { background: var(--curwk); box-shadow: 0 0 0 4px var(--curwk); }
.week.current .wk { border-color: var(--today); }
.day, .wk { background: var(--card); border: 1px solid var(--line); border-radius: 6px;
  padding: 6px; min-height: 92px; }
.day { border-top: 3px solid var(--line); }
.day.other { opacity: .45; }
.t-easy { --tc: var(--easy); } .t-long { --tc: var(--long); } .t-workout { --tc: var(--workout); }
.t-race { --tc: var(--race); } .t-cross { --tc: var(--cross); }
.day[class*="t-"] { border-top-color: var(--tc, var(--line)); }
.day.today { outline: 2px solid var(--today); outline-offset: 1px; }
.dnum { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
.wd { display: none; }
.tag { display: inline-block; font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: .05em; padding: 1px 5px; border-radius: 3px; color: #fff; background: var(--tc, var(--muted)); }
.t-off .tag, .tag.t-off { background: transparent; color: var(--muted); border: 1px solid var(--line); }
.plan { margin-top: 3px; }
.plan.none { color: var(--muted); }
.sess { font-size: 12px; color: var(--muted); margin-top: 2px; }
.act { margin-top: 6px; font-size: 12px; color: var(--ok); font-variant-numeric: tabular-nums; }
.act.missed { color: var(--miss); font-style: italic; }
.zbar { display: flex; height: 5px; border-radius: 3px; overflow: hidden; margin-top: 4px;
  background: var(--line); }
.zbar i { display: block; height: 100%; }
.z1 { background: var(--z1); } .z2 { background: var(--z2); } .z3 { background: var(--z3); }
.z4 { background: var(--z4); } .z5 { background: var(--z5); }
.wk { display: flex; flex-direction: column; justify-content: center; align-items: flex-end;
  text-align: right; font-variant-numeric: tabular-nums; cursor: pointer; }
.wk b { font-size: 18px; }
.wk small, .wsum small { color: var(--muted); font-size: 11px; margin-left: 4px; }
/* week view */
#weekly { margin-top: 20px; }
.whead { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 8px;
  margin-bottom: 10px; }
.wsum { text-align: right; font-variant-numeric: tabular-nums; }
.wsum b { font-size: 20px; }
.zts { color: var(--muted); font-size: 12px; margin-top: 4px; }
.zt { white-space: nowrap; }
.wday { display: grid; grid-template-columns: 90px 1fr; gap: 12px; background: var(--card);
  border: 1px solid var(--line); border-left: 4px solid var(--tc, var(--line)); border-radius: 6px;
  padding: 10px 12px; margin-bottom: 6px; }
.wday.today { outline: 2px solid var(--today); outline-offset: 1px; }
.wdate b { display: block; }
.wdate span { color: var(--muted); font-size: 12px; }
.wday .sess { font-size: 13px; }
.wday .act { font-size: 13px; }
.wday .zbar { max-width: 420px; height: 6px; }
details { margin-top: 6px; color: var(--ink); }
summary { cursor: pointer; color: var(--muted); font-size: 12px; }
table.laps { border-collapse: collapse; margin-top: 4px; font-size: 12px; font-variant-numeric: tabular-nums; }
.laps th, .laps td { padding: 2px 10px 2px 0; text-align: right; }
.laps th { color: var(--muted); font-weight: 500; }
footer { margin-top: 32px; color: var(--muted); font-size: 12px; }
@media (max-width: 760px) {
  .head, .week.empty { display: none; }
  .week { grid-template-columns: 1fr; gap: 3px; margin-bottom: 14px; }
  .week.current { box-shadow: 0 0 0 6px var(--curwk); }
  .day, .wk { min-height: 0; }
  .day { border-top-width: 1px; border-left: 4px solid var(--tc, var(--line)); }
  #monthly .day.other { display: none; }
  #monthly .day:not([class*="t-"]):not(:has(.act)), #monthly .day.t-off:not(:has(.act)) { display: none; }
  .wd { display: inline; }
  .wk { flex-direction: row; justify-content: space-between; align-items: baseline; gap: 8px;
    background: transparent; border: 0; padding: 2px 6px; }
  .wk b { font-size: 15px; margin-left: auto; }
  .wday { grid-template-columns: 1fr; gap: 4px; }
  .wdate b { display: inline; margin-right: 6px; }
}
</style>
</head>
<body>
<main>
<header>
  %%TABS%%
  <h1>Training Calendar</h1>
  <p class="goal">%%GOAL%%</p>
  <div class="legend">%%LEGEND%%</div>
  <div class="legend">HR zones: %%ZLEG%%</div>
</header>
<div class="bar">
  <div class="seg" role="group" aria-label="View">
    <button id="vMonth" aria-pressed="true">Month</button><button id="vWeek" aria-pressed="false">Week</button>
  </div>
  <div class="nav"><button id="prev">‹ Prev</button><button id="thisWk">This week</button><button id="next">Next ›</button></div>
</div>
<div id="monthly">%%MONTHS%%</div>
<div id="weekly">%%WEEKS%%</div>
<footer>Plan vs. completed runs. Click a week total to open that week. Updated %%BUILT%%.</footer>
</main>
<script>
(function () {
  function iso(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  var now = new Date(), today = iso(now);
  var mon = new Date(now); mon.setDate(now.getDate() - ((now.getDay() + 6) % 7));
  var curWeek = iso(mon);

  document.querySelectorAll('[data-date="' + today + '"]').forEach(function (el) {
    if (!el.classList.contains("other")) el.classList.add("today");
  });
  document.querySelectorAll('.week[data-week="' + curWeek + '"]').forEach(function (el) { el.classList.add("current"); });

  var cd = document.getElementById("countdown");
  if (cd) {
    var days = Math.round((new Date("%%RACE_DATE%%T00:00:00") - new Date(today + "T00:00:00")) / 864e5);
    cd.textContent = days > 0 ? days + " days to go" : days === 0 ? "race day!" : "done";
  }

  var weeks = Array.prototype.slice.call(document.querySelectorAll(".wview"));
  var home = weeks.findIndex(function (w) { return w.dataset.week === curWeek; });
  if (home < 0) home = weeks.length - 1;
  var idx = home;
  function showWeek(i) {
    idx = Math.max(0, Math.min(weeks.length - 1, i));
    weeks.forEach(function (w, j) { w.hidden = j !== idx; });
    document.getElementById("prev").disabled = idx === 0;
    document.getElementById("next").disabled = idx === weeks.length - 1;
  }
  function setView(v) {
    document.body.classList.toggle("weekly", v === "week");
    document.getElementById("vMonth").setAttribute("aria-pressed", String(v !== "week"));
    document.getElementById("vWeek").setAttribute("aria-pressed", String(v === "week"));
    try { localStorage.setItem("view", v); } catch (e) {}
    if (v !== "week") {
      var el = document.querySelector(".week.current") || document.querySelector(".day.today");
      if (el) el.scrollIntoView({ block: "center" });
    }
  }
  document.getElementById("vMonth").onclick = function () { setView("month"); };
  document.getElementById("vWeek").onclick = function () { setView("week"); };
  document.getElementById("prev").onclick = function () { showWeek(idx - 1); };
  document.getElementById("next").onclick = function () { showWeek(idx + 1); };
  document.getElementById("thisWk").onclick = function () { showWeek(home); };
  document.addEventListener("keydown", function (e) {
    if (!document.body.classList.contains("weekly")) return;
    if (e.key === "ArrowLeft") showWeek(idx - 1);
    if (e.key === "ArrowRight") showWeek(idx + 1);
  });
  document.querySelectorAll(".wk[data-week]").forEach(function (el) {
    el.onclick = function () {
      var i = weeks.findIndex(function (w) { return w.dataset.week === el.dataset.week; });
      if (i >= 0) { showWeek(i); setView("week"); window.scrollTo(0, 0); }
    };
  });

  showWeek(idx);
  var saved = null;
  try { saved = localStorage.getItem("view"); } catch (e) {}
  setView(saved === "week" ? "week" : "month");
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
