"""Goals tab (docs/goals.html): the year's running-time goal, speed milestones, and the year's mileage.

Built by scripts/build_site.py. The time goal (config.json "goals"."annual_minutes", the headline since 2026-11-05,
when David made trails his default: a trail mile takes longer, so time is neutral where miles aren't) and the
mileage goal ("annual_miles") are tracked against an even pace through the year.
The milestones ("goals"."milestones", gates that unlocked a first marathon until 2026-11-05) are race times on a
flat course at sea level; any race or time trial anywhere reaches one by beating the time there: converted to the
race's altitude by the least generous of the altitude methods (Daniels at 4 and 5 s/mi per 1,000 ft, the NCAA
tables), with no credit for hills. That's deliberately conservative: a milestone reached at altitude is one he'd
certainly have reached at sea level. VDOTs shown are the same as the Fitness tab's (clock time, the altitude methods
averaged). A course profile in data/courses/<date>.csv (km, elev_m) slows an upcoming race's prediction for its
hills (Minetti et al. 2002).
"""
import csv, html, json
from datetime import date, timedelta

from analyze import minetti_cost
from build_site import BASE_CSS, tabs
from config import HOME_ELEV_FT, HOME_NAME, ROOT, _cfg, convert_range, convert_time, load_races, parse_clock
from fitness_page import dist_label, hms, predict, races, series, sl_vdot, src_elev, vdot

GOALS = _cfg.get("goals", {})
GATE_BAR = 10  # progress bars fill from this many VDOT points below the gate
EPS = 0.005  # VDOT rounding slack when checking a gate


def course_factor(d):
    """Energy cost of a race course relative to flat ground (Minetti et al. 2002, as analyze.grade_adjust uses), and
    its climbing in m, from data/courses/<date>.csv; (None, None) without a profile."""
    path = ROOT / "data" / "courses" / f"{d.isoformat()}.csv"
    if not path.exists():
        return None, None
    with open(path, encoding="utf-8") as f:
        pts = [(float(r["km"]) * 1000, float(r["elev_m"])) for r in csv.DictReader(l for l in f if not l.startswith("#"))]
    c0, cost, gain = minetti_cost(0), 0.0, 0.0
    for (a, e), (b, g) in zip(pts, pts[1:]):
        cost += (b - a) * minetti_cost((g - e) / (b - a)) / c0
        gain += max(0.0, g - e)
    return cost / (pts[-1][0] - pts[0][0]), gain


def gate_vdot(meters, secs, elev):
    """Sea-level VDOT of a clock time at `elev`, by the altitude method that gives the least credit."""
    return min(vdot(meters, t) for t in convert_time(secs, meters, elev, 0).values())


def time_for(need, meters, elev):
    """Clock time at `elev` that scores `need` on the gate scale."""
    lo, hi = 60.0, 36000.0
    for _ in range(50):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if gate_vdot(meters, mid, elev) > need else (lo, mid)
    return (lo + hi) / 2


def gate_score(r):
    """A finished race or time trial on the gate scale (passes a gate when it reaches the gate's VDOT): its clock time,
    no hill credit, at its altitude by the least generous method. Equivalent to beating time_for() there."""
    return gate_vdot(r["meters"], r["secs"], src_elev(r))


def race_vdot(r):
    """The VDOT shown for a race, the same as the Fitness tab's."""
    return sl_vdot(r["meters"], r["secs"], src_elev(r))


def gates(rc):
    out = []
    for g in GOALS.get("milestones", []):
        opts = [{"m": o["distance_km"] * 1000, "secs": parse_clock(o["time"])} for o in g["any"]]
        for o in opts:
            o["need"] = vdot(o["m"], o["secs"])
        need = min(o["need"] for o in opts)
        out.append({"name": g["name"], "opts": opts, "need": need,
                    "passed": next((r for r in rc if gate_score(r) >= need - EPS), None)})
    return out


def under(secs):
    """The round time a goal like 19:59 or 1:29:59 is 'under'."""
    return hms(secs + 1).removesuffix(":00") if secs + 1 >= 3600 else hms(secs + 1)


def bar(frac):
    return f'<span class="gbar"><i style="width:{max(0, min(1, frac)) * 100:.0f}%"></i></span>'


def when(d):
    return f"{d:%b} {d.day}, {d.year}"


def path_html(gs, cur):
    rows = []
    for g in gs:
        opts = " or ".join(f"{under(o['secs'])} {dist_label(o['m'])}" for o in g["opts"])
        if g["passed"]:
            r = g["passed"]
            st = f'<span class="pill hit">done</span> {html.escape(r["name"])}, {when(r["d"])}: {hms(r["secs"])}'
        elif cur:
            pred = " · ".join(f"{hms(predict(cur, o['m']))} {dist_label(o['m'])}" for o in g["opts"])
            st = f"{bar(1 - (g['need'] - cur) / GATE_BAR)} your latest race predicts {pred} at sea level"
        else:
            st = "No race yet."
        rows.append(f'<li><div class="step">{html.escape(g["name"])}</div><div><b>Under {opts}</b> '
                    f'<span class="dim">· on the clock, at sea level</span><div class="st">{st}</div></div></li>')
    return '<ol class="path">' + "".join(rows) + "</ol>"


def equiv_html(gs, today):
    """Each gate time as it reads at home, at the upcoming race venues and at sea level."""
    upcoming = [r for r in load_races() if r["date"] >= today]
    places = {0: "Sea level", HOME_ELEV_FT: HOME_NAME}
    for r in upcoming:
        if r["elev"] is not None:
            places.setdefault(r["elev"], r["location"])
    opts = [o for g in gs for o in g["opts"]]
    head = "".join(f"<th>{under(o['secs'])} {dist_label(o['m'])}</th>" for o in opts)
    rows = []
    for elev in sorted(places):
        cells = "".join(f"<td>{hms(time_for(o['need'], o['m'], elev))}</td>" for o in opts)
        rows.append(f"<tr><td>{html.escape(places[elev])} <span class=dim>{elev:,} ft</span></td>{cells}</tr>")
    return f'<div class="scroll"><table class="t"><tr><th>Where</th>{head}</tr>{"".join(rows)}</table></div>'


def races_html(rc, gs, cur, today):
    up = []
    for g in load_races():
        if g["date"] < today or not g["goal_secs"]:
            continue
        f, gain = course_factor(g["date"])
        f = f or 1.0
        elev = g["elev"] if g["elev"] is not None else HOME_ELEV_FT
        v = sl_vdot(g["meters"], g["goal_secs"], elev)
        if cur:
            lo, hi = convert_range(predict(cur, g["meters"]), g["meters"], 0, elev)
            now = f"{hms(lo * f)}–{hms(hi * f)}"
        else:
            now = "–"
        up.append(f"<tr><td><b>{html.escape(g['name'])}</b> <span class=dim>{elev:,} ft{f', ~{gain * 3.281:,.0f} ft of climbing' if gain else ''}"
                  f"</span></td><td>{when(g['date'])}</td><td>{hms(g['goal_secs'])}</td><td>{v:.1f}</td>"
                  f"<td>{hms(predict(v, 5000))}</td><td>{now}</td></tr>")
    res = []
    for r in reversed(rc):
        v = race_vdot(r)
        n = sum(1 for g in gs if gate_score(r) >= g["need"] - EPS)  # milestones are in order, so the first n
        res.append(f"<tr><td><b>{html.escape(r['name'])}</b> <span class=dim>{src_elev(r):,} ft</span></td>"
                   f"<td>{when(r['d'])}</td><td>{hms(r['secs'])}</td><td>{v:.1f}</td>"
                   f"<td>{hms(predict(v, 5000))}</td><td>{', '.join(html.escape(g['name']) for g in gs[:n]) if n else '–'}</td></tr>")
    out = ""
    if up:
        out += ("<h3>Coming up</h3><div class=scroll><table class=t><tr><th>Race</th><th>Date</th><th>Goal</th>"
                "<th>Sea-level VDOT</th><th>= 5K at sea level</th><th>Now</th></tr>" + "".join(up) + "</table></div>")
    if res:
        out += ("<h3>Results</h3><div class=scroll><table class=t><tr><th>Race</th><th>Date</th><th>Time</th>"
                "<th>Sea-level VDOT</th><th>= 5K at sea level</th><th>Reaches</th></tr>"
                + "".join(res) + "</table></div>")
    return out


def annual(runs, plan, today, unit):
    """A yearly goal, in running minutes ("min", goals.annual_minutes) or miles ("mi", goals.annual_miles): daily
    cumulative totals, the plan for the rest of the year, and the 28-day pace. Planned minutes are plan.csv's miles
    at the last 28 days' average pace."""
    key, col = {"min": ("annual_minutes", "min"), "mi": ("annual_miles", "mi")}[unit]
    years = sorted(int(y) for y in GOALS.get(key, {}))
    year = next((y for y in years if date(y, 12, 31) >= today), years[-1] if years else None)
    if year is None:
        return None
    goal = GOALS[key][str(year)]
    jan1, dec31 = date(year, 1, 1), date(year, 12, 31)
    daily = series(runs, today) if runs else []
    mi = {date.fromisoformat(x["d"]): x[col] for x in daily}
    m28, mi28 = sum(x["min"] for x in daily[-28:]), sum(x["mi"] for x in daily[-28:])
    per_mile = m28 / mi28 if unit == "min" and mi28 else 1.0
    days, cum = [], 0.0
    d = jan1
    while d <= min(today, dec31):
        cum += mi.get(d, 0.0)
        days.append([d.isoformat(), round(cum, 1)])
        d += timedelta(1)
    total = cum
    planned, pc, last_plan = [], total, None
    for pd in sorted(p for p in plan if max(today + timedelta(1), jan1) <= p <= dec31):
        try:
            m = float(plan[pd]["miles"] or 0)
        except ValueError:
            m = 0.0
        if m:
            pc += m * per_mile
            planned.append([pd.isoformat(), round(pc, 1)])
            last_plan = pd
    r28 = (round(m28 / 4) if unit == "min" else daily[-1]["r28"]) if len(daily) >= 28 else None
    months = []
    for mo in range(1, 13):
        m0 = date(year, mo, 1)
        m1 = date(year + (mo == 12), mo % 12 + 1, 1) - timedelta(1)
        n = (m1 - m0).days + 1
        months.append({"m": m0.strftime("%b"), "mi": round(sum(mi.get(m0 + timedelta(i), 0.0) for i in range(n)), 1),
                       "target": round(goal * n / ((dec31 - jan1).days + 1), 1), "started": m0 <= today})
    return {"unit": unit, "year": year, "goal": goal, "jan1": jan1.isoformat(), "dec31": dec31.isoformat(), "days": days,
            "total": round(total, 1), "plan": planned, "planEnd": last_plan.isoformat() if last_plan else None,
            "r28": r28, "months": months, "today": today.isoformat()}


def build(plan, built):
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f))
    today = date.today()
    rc = races(runs, plan)
    latest = rc[-1] if rc else None
    cur = race_vdot(latest) if latest else None
    gs = gates(rc)
    yearly = [g for g in (annual(runs, plan, today, "min"), annual(runs, plan, today, "mi")) if g]
    cur_txt = (f"Your latest race, the {html.escape(latest['name'])} on {latest['d']:%b} {latest['d'].day}, scores a "
               f"sea-level VDOT of <b>{cur:.1f}</b>." if latest else "No race or time trial yet.")
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("goals.html"), "DATA": json.dumps({"yearly": yearly}, separators=(",", ":")),
            "CURRENT": cur_txt, "PATH": path_html(gs, cur), "EQUIV": equiv_html(gs, today),
            "RACES": races_html(rc, gs, cur, today), "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}"}
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs" / "goals.html").write_text(page, encoding="utf-8")
    print("Wrote docs/goals.html")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Goals</title>
<style>
%%BASE_CSS%%
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.card h2 { font-size: 16px; margin: 0 0 2px; }
.card h3 { font-size: 13px; margin: 12px 0 4px; color: var(--muted); font-weight: 600; }
.note { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.note b { color: var(--ink); }
.dim { color: var(--muted); font-size: 12px; font-weight: 400; }
.path { list-style: none; margin: 10px 0 0; padding: 0; }
.path li { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 10px; padding: 9px 0; border-top: 1px solid var(--line);
  font-size: 14px; font-variant-numeric: tabular-nums; }
.path li:first-child { border-top: none; }
.path .step { font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); padding-top: 2px; }
.path .st { font-size: 13px; color: var(--muted); margin-top: 3px; }
.path li.end b { color: var(--race); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin: 10px 0 4px; }
.tile { background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; }
.tl { font-size: 12px; color: var(--muted); }
.tv { font-size: 24px; font-weight: 600; margin-top: 2px; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.ts { font-size: 12px; color: var(--muted); margin-top: 2px; }
.up { color: var(--ok); } .down { color: var(--tired); }
.scroll { overflow-x: auto; }
table.t { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; width: 100%; }
table.t td, table.t th { padding: 5px 8px; text-align: right; white-space: nowrap; border-top: 1px solid var(--line); }
table.t td:first-child, table.t th:first-child { text-align: left; padding-left: 0; }
table.t th { color: var(--muted); font-weight: 500; border-top: none; }
.gbar { position: relative; display: inline-block; width: 110px; height: 8px; border-radius: 4px; background: var(--line);
  vertical-align: 0; overflow: hidden; margin-right: 4px; }
.gbar i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--s1); border-radius: 4px; }
.pill { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 12px; font-weight: 600; margin-right: 4px; }
.pill.hit { background: color-mix(in srgb, var(--ok) 18%, transparent); color: var(--ok); }
.chart { position: relative; height: 280px; margin-top: 8px; touch-action: pan-y; }
.chart svg { display: block; width: 100%; height: 100%; overflow: visible; }
.grid line { stroke: var(--line); stroke-width: 1; }
.axis text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.guide { stroke: var(--muted); stroke-width: 1; }
.hit { fill: transparent; cursor: crosshair; }
.legend { margin: 6px 0 0; }
.legend .ln { display: inline-block; width: 16px; height: 2px; vertical-align: 3px; margin-right: 5px; border-radius: 1px; }
.legend .ln.dash { background: repeating-linear-gradient(90deg, var(--muted) 0 4px, transparent 4px 7px); }
.legend .ln.dot { background: repeating-linear-gradient(90deg, var(--s2) 0 2px, transparent 2px 5px); }
#tip { position: fixed; z-index: 20; pointer-events: none; background: var(--card); color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font-size: 12px; line-height: 1.45;
  box-shadow: 0 4px 14px rgba(0,0,0,.18); display: none; max-width: 240px; font-variant-numeric: tabular-nums; }
#tip i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; }
.about p { font-size: 13px; color: var(--muted); margin: 6px 0; }
footer { margin-top: 32px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<main>
%%TABS%%
<header>
  <h1>Goals</h1>
  <p class="sub">Time on feet for the year, and speed milestones along the way · updated %%BUILT%%</p>
</header>

<section class="card" id="y0"></section>

<section class="card">
<h2>Speed milestones</h2>
<p class="note">Good to reach, not requirements. Any race or time trial, anywhere, reaches one by beating its time there (next card). %%CURRENT%%</p>
%%PATH%%
</section>

<section class="card">
<h2>What the milestones mean where you race</h2>
<p class="note">The clock time that reaches each milestone at each place, on purpose conservative: it gets the smallest of the published altitude adjustments, and hills earn no credit, so these are the times to beat on any course there.</p>
%%EQUIV%%
</section>

<section class="card">
<h2>Races on the same scale</h2>
<p class="note">Goals and results as sea-level VDOT (as on the Fitness tab) and the 5K at sea level that VDOT predicts. "Now" is what your latest race predicts there (both altitude methods, and the course's hills where its profile is known). "Reaches" uses the milestone times above.</p>
%%RACES%%
</section>

<section class="card" id="y1"></section>

<section class="card about">
<h2>About</h2>
<p>The yearly goals are tracked against an even pace through the year (goal × days so far ÷ days in the year). "Needed" is the weekly average the rest of the year takes; the projection adds the planned miles in plan.csv (as time, at the last 28 days' average pace), then the last 28 days' average, to what's done. Both count runs only (hand-made FIT files too), never hikes or skis. Time is moving time, distance × average pace. Time is the headline because a trail mile takes longer than a road mile, so a mileage goal quietly pulls toward roads.</p>
<p>Each milestone is a finish time on a flat course at sea level, judged conservatively: a race reaches it when its clock time beats the milestone time at its altitude, converted by whichever published method gives the least adjustment (Daniels' 4 or 5 s/mi per 1,000 ft above 3,000 ft, or the NCAA 2009 tables), and hills earn nothing. Predictions and VDOTs on this page are the Fitness tab's (clock time, the methods averaged). The bars fill as that VDOT approaches the one the milestone time works out to, from 10 points below.</p>
</section>
<footer>Updated %%BUILT%%.</footer>
</main>
<div id="tip"></div>
<script>
(function () {
  var D = %%DATA%%;
  var NS = "http://www.w3.org/2000/svg", tip = document.getElementById("tip");
  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function el(tag, a, parent) { var e = document.createElementNS(NS, tag); for (var k in a) e.setAttribute(k, a[k]); if (parent) parent.appendChild(e); return e; }
  function day(iso) { var p = iso.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]) / 864e5; }
  function fmtDate(iso) { var p = iso.split("-"); return MON[+p[1] - 1] + " " + (+p[2]); }
  function fmtLong(iso) { return fmtDate(iso) + ", " + iso.slice(0, 4); }
  function n0(x) { return Math.round(x).toLocaleString("en-US"); }
  function showTip(html, ev) {
    tip.innerHTML = html; tip.style.display = "block";
    var w = tip.offsetWidth, h = tip.offsetHeight, x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function sw(v) { return '<i style="background:var(' + v + ')"></i>'; }

  // ---- yearly goals: running time (first), then miles ----
  function hmm(m) { m = Math.round(m); return Math.floor(m / 60) + "h " + String(m % 60).padStart(2, "0") + "m"; }
  D.yearly.forEach(function (M, k) { yearGoal(M, document.getElementById("y" + k)); });
  ["y0", "y1"].slice(D.yearly.length).forEach(function (id) { document.getElementById(id).hidden = true; });

  function yearGoal(M, box) {
  var isMin = M.unit === "min", U = isMin ? " min" : " mi", UN = isMin ? "minutes" : "miles";
  var wk = function (v) { return isMin ? n0(v) : v.toFixed(1); };
  var wkSub = function (v) { return isMin ? " (" + hmm(v) + ")" : ""; };
  var x0 = day(M.jan1), x1 = day(M.dec31), nDays = x1 - x0 + 1, td = day(M.today);
  var started = td >= x0, elapsed = Math.max(0, Math.min(nDays, td - x0 + 1)), left = nDays - elapsed;
  var target = M.goal * elapsed / nDays, diff = M.total - target;
  var need = left > 0 ? (M.goal - M.total) / (left / 7) : 0;
  var planTot = M.plan.length ? M.plan[M.plan.length - 1][1] : M.total;
  var planEnd = M.planEnd ? day(M.planEnd) : Math.max(td, x0 - 1);
  var proj = M.r28 == null ? null : planTot + M.r28 / 7 * Math.max(0, x1 - Math.max(planEnd, x0 - 1));
  function tile(l, v, s) { return '<div class="tile"><div class="tl">' + l + '</div><div class="tv">' + v + '</div><div class="ts">' + s + "</div></div>"; }
  var tiles = started
    ? tile(M.year + " so far", n0(M.total) + U, n0(M.total / M.goal * 100) + "% of " + n0(M.goal)) +
      tile("vs. even pace", '<span class="' + (diff >= 0 ? "up" : "down") + '">' + (diff >= 0 ? "+" : "−") + n0(Math.abs(diff)) + U + "</span>",
           (diff >= 0 ? "ahead of " : "behind ") + n0(target) + " by today") +
      tile("Needed", wk(need) + U + "/wk", "average for the " + Math.round(left / 7) + " weeks left" + wkSub(need))
    : tile("Starts", fmtLong(M.jan1), Math.round(x0 - td) + " days away") +
      tile("Needed", wk(M.goal / (nDays / 7)) + U + "/wk", "average, every week of " + M.year + wkSub(M.goal / (nDays / 7)));
  var pace = started ? need : M.goal / (nDays / 7);
  if (M.r28 != null) tiles += tile("Last 28 days", wk(M.r28) + U + "/wk", (M.r28 >= pace ? "at or above" : "below") + " the needed pace" + wkSub(M.r28));
  if (proj != null) tiles += tile("Projected", n0(proj) + U, (M.plan.length ? "plan, then " : "") + "28-day average to Dec 31");
  var per = M.goal / (nDays / 7);
  box.innerHTML = "<h2>" + n0(M.goal) + " " + UN + " of running in " + M.year + "</h2>" +
    '<p class="note">' + (isMin
      ? n0(M.goal) + " minutes is " + n0(per) + " min (" + hmm(per) + ") a week on average, cut-back weeks, tapers, trips and time off included. Time counts a slow trail mile for what it takes."
      : n0(M.goal) + " miles is " + per.toFixed(1) + " mi a week on average. Kept alongside the time goal; time is the headline.") + "</p>" +
    '<div class="tiles">' + tiles + "</div>" +
    '<div class="chart" id="cum' + M.unit + '"></div>' +
    '<div class="legend note"><span><i class="ln" style="background:var(--s1)"></i>' + (isMin ? "Minutes" : "Miles") + " run</span> &nbsp; <span><i class='ln dash'></i>Even pace to " + n0(M.goal) +
    "</span>" + (M.plan.length ? ' &nbsp; <span><i class="ln" style="background:var(--s7)"></i>Plan</span>' : "") +
    (proj != null ? ' &nbsp; <span><i class="ln dot"></i>28-day average</span>' : "") + "</div>" +
    '<div class="scroll"><table class="t" id="months' + M.unit + '"></table></div>';

  var mt = "<tr><th>Month</th><th>" + (isMin ? "Minutes" : "Miles") + "</th><th>Even pace</th><th>vs. pace</th><th>Running total</th></tr>", run = 0, tgt = 0;
  M.months.forEach(function (m) {
    run += m.mi; tgt += m.target;
    var dv = m.mi - m.target;
    mt += "<tr><td>" + m.m + "</td><td>" + (m.started ? (isMin ? n0(m.mi) : m.mi.toFixed(1)) : "–") + "</td><td>" + n0(m.target) + "</td><td>" +
          (m.started ? '<span class="' + (dv >= 0 ? "up" : "down") + '">' + (dv >= 0 ? "+" : "−") + n0(Math.abs(dv)) + "</span>" : "–") +
          "</td><td>" + (m.started ? n0(run) + " / " + n0(tgt) : "–") + "</td></tr>";
  });
  document.getElementById("months" + M.unit).innerHTML = mt;

  function draw() {
    var c = document.getElementById("cum" + M.unit); c.innerHTML = "";
    var W = c.clientWidth, H = c.clientHeight, m = { l: 52, r: 14, t: 10, b: 22 };
    var ymax = Math.max(M.goal, proj || 0, planTot) * 1.06, step = Math.pow(10, Math.floor(Math.log10(ymax)));
    step = ymax / step > 5 ? step : ymax / step > 2.5 ? step / 2 : step / 4;
    var X = function (d) { return m.l + (d - x0) / (x1 + 1 - x0) * (W - m.l - m.r); };
    var Y = function (v) { return H - m.b - v / ymax * (H - m.t - m.b); };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H }, c), g = el("g", { "class": "grid" }, svg), ax = el("g", { "class": "axis" }, svg);
    for (var v = 0; v <= ymax; v += step) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
      el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, ax).textContent = n0(v);
    }
    for (var mo = 0; mo < 12; mo++) {
      var d0 = Date.UTC(+M.year, mo, 1) / 864e5, d1 = Date.UTC(+M.year, mo + 1, 1) / 864e5;
      el("text", { x: (X(d0) + X(d1)) / 2, y: H - 6, "text-anchor": "middle" }, ax).textContent = MON[mo];
      if (mo) el("line", { x1: X(d0), x2: X(d0), y1: m.t, y2: H - m.b }, g);
    }
    function line(pts, color, dash, w) {
      if (pts.length < 2) return;
      el("path", { d: pts.map(function (p, i) { return (i ? "L" : "M") + X(p[0]).toFixed(1) + "," + Y(p[1]).toFixed(1); }).join(""),
                   fill: "none", stroke: "var(" + color + ")", "stroke-width": w || 2, "stroke-dasharray": dash || "",
                   "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    }
    line([[x0, 0], [x1 + 1, M.goal]], "--muted", "4 3", 1.5);
    var act = [[x0, 0]].concat(M.days.map(function (p) { return [day(p[0]) + 1, p[1]]; }));
    var pl = M.plan.length ? [[Math.max(td, x0 - 1) + 1, M.total]].concat(M.plan.map(function (p) { return [day(p[0]) + 1, p[1]]; })) : [];
    line(pl, "--s7", "", 2);
    if (proj != null) line([[Math.max(planEnd, x0 - 1) + 1, planTot], [x1 + 1, proj]], "--s2", "2 3", 2);
    line(act, "--s1", "", 2.5);
    var gd = el("line", { "class": "guide", y1: m.t, y2: H - m.b, display: "none" }, svg);
    var hit = el("rect", { "class": "hit", x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b }, svg);
    var byDay = {}; M.days.forEach(function (p) { byDay[day(p[0])] = p[1]; });
    var planBy = {}; M.plan.forEach(function (p) { planBy[day(p[0])] = p[1]; });
    hit.addEventListener("pointermove", function (ev) {
      var r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width;
      var d = Math.max(x0, Math.min(x1, Math.floor(x0 + (px - m.l) / (W - m.l - m.r) * (x1 + 1 - x0))));
      gd.setAttribute("x1", X(d + 1)); gd.setAttribute("x2", X(d + 1)); gd.removeAttribute("display");
      var iso = new Date(d * 864e5).toISOString().slice(0, 10), tg = M.goal * (d - x0 + 1) / nDays, s = "<b>" + fmtLong(iso) + "</b>";
      if (byDay[d] != null) s += "<br>" + sw("--s1") + n0(byDay[d]) + U + " run (" + (byDay[d] >= tg ? "+" : "−") + n0(Math.abs(byDay[d] - tg)) + ")";
      else { var pk = Object.keys(planBy).filter(function (k) { return +k <= d; }); if (pk.length) s += "<br>" + sw("--s7") + n0(planBy[Math.max.apply(null, pk)]) + U + " planned"; }
      s += "<br>" + sw("--muted") + n0(tg) + U + " even pace";
      showTip(s, ev);
    });
    hit.addEventListener("pointerleave", function () { gd.setAttribute("display", "none"); tip.style.display = "none"; });
  }
  draw();
  window.addEventListener("resize", draw);
  }
})();
</script>
</body>
</html>
"""
