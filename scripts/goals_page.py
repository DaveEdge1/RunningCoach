"""Goals tab (docs/goals.html): time goals against current fitness, and the year's mileage goal.

Built by scripts/build_site.py. Time goals are the races in races.csv with a goal_time (or goal_pace) plus the
standalone goals in config.json ("goals"."times"); each is scored as the sea-level VDOT it needs, against the latest
race or time trial (the same current fitness as the Fitness tab). A race with a course profile in
data/courses/<date>.csv (km, elev_m) is also adjusted for its hills with Minetti et al. (2002). The mileage goal
("goals"."annual_miles") is tracked day by day against an even pace through the year.
"""
import csv, json
from datetime import date, timedelta

from analyze import minetti_cost
from build_site import BASE_CSS, tabs
from config import ROOT, _cfg, convert_range, load_races, parse_clock
from fitness_page import M_PER_MI, dist_label, fitness_basis, hms, predict, races, series, sl_vdot, src_elev, vdot

GOALS = _cfg.get("goals", {})


def course_factor(d):
    """Energy cost of a race course relative to flat ground (Minetti et al. 2002, as analyze.grade_adjust uses), from
    data/courses/<date>.csv; None without a profile."""
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


def time_goals(rc, today):
    """One row per goal: what it needs (sea-level VDOT) and what current fitness predicts there."""
    basis = fitness_basis(rc)
    cur = sl_vdot(basis["meters"], basis["secs"], src_elev(basis)) if basis else None
    done = {r["d"]: r for r in rc}
    rows = []
    goals = [dict(r, kind="race") for r in load_races() if r["goal_secs"]]
    goals += [{"date": None, "name": g["name"], "location": g.get("note", ""), "meters": g["distance_km"] * 1000,
               "elev": g.get("elevation_ft", 0), "goal_secs": parse_clock(g["time"]), "kind": "goal"}
              for g in GOALS.get("times", [])]
    for g in goals:
        if g["date"] and g["date"] < today and g["date"] not in done:
            continue  # a past race with no recorded result
        elev = g["elev"] if g["elev"] is not None else 0
        factor, gain = course_factor(g["date"]) if g["date"] else (None, None)
        flat = g["goal_secs"] / (factor or 1)  # the goal as a flat-course time at that altitude
        need = sl_vdot(g["meters"], flat, elev)
        row = {"name": g["name"], "date": g["date"].isoformat() if g["date"] else None, "where": g["location"],
               "dist": dist_label(g["meters"]), "elev": elev, "goal": hms(g["goal_secs"]), "need": round(need, 1),
               "hills": f"+{(factor - 1) * 100:.1f}% for ~{gain * 3.281:,.0f} ft of climbing" if factor else None}
        r = done.get(g["date"]) if g["date"] else None
        if r:
            row["result"] = hms(r["secs"])
            row["hit"] = r["secs"] <= g["goal_secs"] + 0.5
        elif cur:
            lo, hi = convert_range(predict(cur, g["meters"]), g["meters"], 0, elev)
            row["now"] = [hms(lo * (factor or 1)), hms(hi * (factor or 1))]
            row["gap"] = round(need - cur, 1)
        rows.append(row)
    rows.sort(key=lambda x: (x["date"] is None, x["date"] or ""))
    return rows, cur, basis


def mileage(runs, plan, today):
    """The year's mileage goal: daily cumulative miles, the plan for the rest of the year, and the 28-day pace."""
    years = sorted(int(y) for y in GOALS.get("annual_miles", {}))
    year = next((y for y in years if date(y, 12, 31) >= today), years[-1] if years else None)
    if year is None:
        return None
    goal = GOALS["annual_miles"][str(year)]
    jan1, dec31 = date(year, 1, 1), date(year, 12, 31)
    daily = series(runs, today) if runs else []
    mi = {date.fromisoformat(x["d"]): x["mi"] for x in daily}
    days, cum, total = [], 0.0, 0.0
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
            pc += m
            planned.append([pd.isoformat(), round(pc, 1)])
            last_plan = pd
    r28 = daily[-1]["r28"] if daily and daily[-1]["r28"] is not None else None
    months = []
    for mo in range(1, 13):
        m0 = date(year, mo, 1)
        m1 = date(year + (mo == 12), mo % 12 + 1, 1) - timedelta(1)
        n = (m1 - m0).days + 1
        months.append({"m": m0.strftime("%b"), "mi": round(sum(mi.get(m0 + timedelta(i), 0.0) for i in range(n)), 1),
                       "target": round(goal * n / ((dec31 - jan1).days + 1), 1), "started": m0 <= today})
    return {"year": year, "goal": goal, "jan1": jan1.isoformat(), "dec31": dec31.isoformat(), "days": days,
            "total": round(total, 1), "plan": planned, "planEnd": last_plan.isoformat() if last_plan else None,
            "r28": r28, "months": months, "today": today.isoformat()}


def build(plan, built):
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        runs = list(csv.DictReader(f))
    today = date.today()
    rc = races(runs, plan)
    rows, cur, basis = time_goals(rc, today)
    data = {"goals": rows, "miles": mileage(runs, plan, today)}
    cur_txt = (f"Current fitness: sea-level VDOT <b>{cur:.1f}</b>, from the {basis['name']} on {basis['d']:%b} "
               f"{basis['d'].day}." if cur else "No race or time trial yet.")
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("goals.html"), "DATA": json.dumps(data, separators=(",", ":")),
            "CURRENT": cur_txt, "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}"}
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
.note { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.note b { color: var(--ink); }
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
table.t .sub { display: block; color: var(--muted); font-size: 11px; white-space: normal; max-width: 260px; }
.gbar { position: relative; display: inline-block; width: 110px; height: 8px; border-radius: 4px; background: var(--line);
  vertical-align: 0; overflow: hidden; }
.gbar i { position: absolute; left: 0; top: 0; bottom: 0; background: var(--s1); border-radius: 4px; }
.pill { display: inline-block; padding: 1px 7px; border-radius: 9px; font-size: 12px; font-weight: 600; }
.pill.hit { background: color-mix(in srgb, var(--ok) 18%, transparent); color: var(--ok); }
.pill.miss { background: color-mix(in srgb, var(--tired) 18%, transparent); color: var(--tired); }
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
  <p class="sub">Time goals against current fitness, and the year's mileage · updated %%BUILT%%</p>
</header>

<section class="card">
<h2>Time goals</h2>
<p class="note">%%CURRENT%% Each goal is converted to the sea-level VDOT it needs (altitude by Daniels and the NCAA tables, and hills by Minetti et al. 2002 where the course profile is known), so goals at different altitudes compare on one scale.</p>
<div class="scroll"><table class="t" id="goals"></table></div>
</section>

<section class="card" id="mi"></section>

<section class="card about">
<h2>About</h2>
<p>Current fitness is the latest race or time trial, as on the Fitness tab. "Now" is what that fitness predicts at the goal's altitude (Daniels' VDOT tables, converted with both altitude methods, so it's a range), slowed by the course's hills where a profile is in data/courses. The bar fills as current VDOT approaches the VDOT the goal needs, from 5 points below it.</p>
<p>The mileage goal is tracked against an even pace through the year (goal × days so far ÷ days in the year). "Needed" is the weekly average the rest of the year takes; the projections add the planned miles in plan.csv, or the last 28 days' average, to what's done. Mileage counts runs only (hand-made FIT files too), never hikes or skis.</p>
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

  // ---- time goals ----
  var t = document.getElementById("goals");
  var h = "<tr><th>Goal</th><th>Date</th><th>Elevation</th><th>Goal time</th><th>VDOT needed</th><th>Now</th><th>Progress</th></tr>";
  D.goals.forEach(function (g) {
    var sub = [g.dist + (g.where ? " · " + g.where : "")];
    if (g.hills) sub.push("hills " + g.hills);
    var now, prog;
    if (g.result) {
      now = g.result; prog = '<span class="pill ' + (g.hit ? "hit" : "miss") + '">' + (g.hit ? "done" : "missed") + "</span>";
    } else if (g.now) {
      now = g.now[0] === g.now[1] ? g.now[0] : g.now[0] + "–" + g.now[1];
      var f = Math.max(0, Math.min(1, 1 - g.gap / 5));
      prog = '<span class="gbar"><i style="width:' + (f * 100).toFixed(0) + '%"></i></span> ' +
             (g.gap > 0 ? "+" + g.gap.toFixed(1) + " to go" : '<span class="up">there</span>');
    } else { now = "–"; prog = ""; }
    h += "<tr><td><b>" + g.name + "</b><span class=sub>" + sub.join(" · ") + "</span></td><td>" + (g.date ? fmtLong(g.date) : "–") +
         "</td><td>" + n0(g.elev) + " ft</td><td>" + g.goal + "</td><td>" + g.need.toFixed(1) + "</td><td>" + now + "</td><td>" + prog + "</td></tr>";
  });
  t.innerHTML = h;

  // ---- mileage goal ----
  var M = D.miles, box = document.getElementById("mi");
  if (!M) { box.hidden = true; return; }
  var x0 = day(M.jan1), x1 = day(M.dec31), nDays = x1 - x0 + 1, td = day(M.today);
  var started = td >= x0, elapsed = Math.max(0, Math.min(nDays, td - x0 + 1)), left = nDays - elapsed;
  var target = M.goal * elapsed / nDays, diff = M.total - target;
  var need = left > 0 ? (M.goal - M.total) / (left / 7) : 0;
  var planTot = M.plan.length ? M.plan[M.plan.length - 1][1] : M.total;
  var planEnd = M.planEnd ? day(M.planEnd) : Math.max(td, x0 - 1);
  var proj = M.r28 == null ? null : planTot + M.r28 / 7 * Math.max(0, x1 - Math.max(planEnd, x0 - 1));
  function tile(l, v, s) { return '<div class="tile"><div class="tl">' + l + '</div><div class="tv">' + v + '</div><div class="ts">' + s + "</div></div>"; }
  var tiles = started
    ? tile(M.year + " so far", n0(M.total) + " mi", n0(M.total / M.goal * 100) + "% of " + n0(M.goal)) +
      tile("vs. even pace", '<span class="' + (diff >= 0 ? "up" : "down") + '">' + (diff >= 0 ? "+" : "−") + n0(Math.abs(diff)) + " mi</span>",
           (diff >= 0 ? "ahead of " : "behind ") + n0(target) + " by today") +
      tile("Needed", (need).toFixed(1) + " mi/wk", "average for the " + Math.round(left / 7) + " weeks left")
    : tile("Starts", fmtLong(M.jan1), Math.round(x0 - td) + " days away") +
      tile("Needed", (M.goal / (nDays / 7)).toFixed(1) + " mi/wk", "average, every week of " + M.year);
  if (M.r28 != null) tiles += tile("Last 28 days", M.r28.toFixed(1) + " mi/wk", (M.r28 >= need || !started && M.r28 * nDays / 7 >= M.goal ? "on pace" : "below") + " the goal pace");
  if (proj != null) tiles += tile("Projected", n0(proj) + " mi", (M.plan.length ? "plan, then " : "") + "28-day average to Dec 31");
  box.innerHTML = "<h2>" + n0(M.goal) + " miles in " + M.year + "</h2>" +
    '<p class="note">' + n0(M.goal) + " miles is " + (M.goal / (nDays / 7)).toFixed(1) + " mi a week on average, cut-back weeks, tapers, trips and time off included." + "</p>" +
    '<div class="tiles">' + tiles + "</div>" +
    '<div class="chart" id="cum"></div>' +
    '<div class="legend note"><span><i class="ln" style="background:var(--s1)"></i>Miles run</span> &nbsp; <span><i class="ln dash"></i>Even pace to ' + n0(M.goal) +
    "</span>" + (M.plan.length ? ' &nbsp; <span><i class="ln" style="background:var(--s7)"></i>Plan</span>' : "") +
    (proj != null ? ' &nbsp; <span><i class="ln dot"></i>28-day average</span>' : "") + "</div>" +
    '<div class="scroll"><table class="t" id="months"></table></div>';

  var mt = "<tr><th>Month</th><th>Miles</th><th>Even pace</th><th>vs. pace</th><th>Running total</th></tr>", run = 0, tgt = 0;
  M.months.forEach(function (m) {
    run += m.mi; tgt += m.target;
    var dv = m.mi - m.target;
    mt += "<tr><td>" + m.m + "</td><td>" + (m.started ? m.mi.toFixed(1) : "–") + "</td><td>" + m.target.toFixed(0) + "</td><td>" +
          (m.started ? '<span class="' + (dv >= 0 ? "up" : "down") + '">' + (dv >= 0 ? "+" : "−") + Math.abs(dv).toFixed(0) + "</span>" : "–") +
          "</td><td>" + (m.started ? n0(run) + " / " + n0(tgt) : "–") + "</td></tr>";
  });
  document.getElementById("months").innerHTML = mt;

  function draw() {
    var c = document.getElementById("cum"); c.innerHTML = "";
    var W = c.clientWidth, H = c.clientHeight, m = { l: 46, r: 14, t: 10, b: 22 };
    var ymax = Math.max(M.goal, proj || 0, planTot) * 1.06, step = ymax > 1500 ? 500 : 250;
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
      if (byDay[d] != null) s += "<br>" + sw("--s1") + n0(byDay[d]) + " mi run (" + (byDay[d] >= tg ? "+" : "−") + n0(Math.abs(byDay[d] - tg)) + ")";
      else { var pk = Object.keys(planBy).filter(function (k) { return +k <= d; }); if (pk.length) s += "<br>" + sw("--s7") + n0(planBy[Math.max.apply(null, pk)]) + " mi planned"; }
      s += "<br>" + sw("--muted") + n0(tg) + " mi even pace";
      showTip(s, ev);
    });
    hit.addEventListener("pointerleave", function () { gd.setAttribute("display", "none"); tip.style.display = "none"; });
  }
  draw();
  window.addEventListener("resize", draw);
})();
</script>
</body>
</html>
"""
