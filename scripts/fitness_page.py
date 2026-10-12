"""Fitness tab (docs/fitness.html): volume, training load, aerobic efficiency, zones, race fitness.

Built from data/runs.csv by scripts/build_site.py.
"""
import csv, html, json, math
from datetime import date, timedelta
from pathlib import Path

from analyze import EF_HR_BAND, EF_MAX_GRADE, EF_MIN_SECS
from build_site import BASE_CSS, tabs
from config import (AET, ALT_FLOOR_FT, ALT_PCT_PER_1000, GOAL_ELEV_FT, GOAL_METERS, GOAL_NAME, LT,
                    MANUAL_LOAD_PER_MIN, ROOT, ZONE_NAMES as ZONE_COLS, ZONES, altitude_factor_range,
                    zone_range)

CTL_DAYS, ATL_DAYS = 42, 7
TREND_DAYS = 28
FRESH, TIRED = 5, -10  # form % thresholds
M_PER_MI = 1609.344
DISTANCES = [(5000, "5K"), (10000, "10K"), (15000, "15K"), (21097.5, "Half marathon"), (42195, "Marathon")]


def dist_label(meters):
    m, name = min(DISTANCES, key=lambda x: abs(x[0] - meters))
    return name if abs(m - meters) / m < 0.04 else f"{meters / M_PER_MI:.1f} mi"


# ---------- Daniels VDOT ----------

def vo2(v):  # v in m/min
    return -4.60 + 0.182258 * v + 0.000104 * v * v


def pct_max(t):  # t in minutes
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)


def vdot(meters, secs):
    t = secs / 60
    return vo2(meters / t) / pct_max(t)


def predict(v, meters):
    lo, hi = 60.0, 36000.0
    for _ in range(60):  # bisection on time
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if vdot(meters, mid) > v else (lo, mid)
    return (lo + hi) / 2


def pace_at(v, frac):
    """sec/mi at the velocity where VO2 = frac * VDOT."""
    a, b, c = 0.000104, 0.182258, -4.60 - frac * v
    vel = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)
    return M_PER_MI / vel * 60


def hms(s):
    s = round(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# ---------- data ----------

def load_runs():
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def series(runs, today):
    first = min(date.fromisoformat(r["date"]) for r in runs)
    days = [first + timedelta(i) for i in range((today - first).days + 1)]
    miles = {d: 0.0 for d in days}
    load = {d: 0.0 for d in days}
    for r in runs:
        d = date.fromisoformat(r["date"])
        if d in miles:
            miles[d] += float(r["miles"])
            load[d] += float(r["load"] or 0)
    # seed both averages with the first 4 weeks' mean so history doesn't start from zero
    seed = sum(load[d] for d in days[:28]) / min(28, len(days))
    ctl = atl = seed
    out = []
    for i, d in enumerate(days):
        form_pct = 100 * (ctl - atl) / ctl if ctl else 0  # going into the day
        ctl += (load[d] - ctl) / CTL_DAYS
        atl += (load[d] - atl) / ATL_DAYS
        r7 = sum(miles[days[j]] for j in range(max(0, i - 6), i + 1))
        r28 = sum(miles[days[j]] for j in range(max(0, i - 27), i + 1)) / 4
        out.append({"d": d.isoformat(), "mi": round(miles[d], 2), "load": round(load[d]),
                    "ctl": round(ctl, 1), "atl": round(atl, 1), "form": round(ctl - atl, 1),
                    "formPct": round(form_pct, 1), "r7": round(r7, 1),
                    "r28": round(r28, 1) if i >= 27 else None})
    return out


def efficiency(runs, daily):
    by_day = {x["d"]: x for x in daily}
    pts = []
    for r in runs:
        if r["aet_pace_secs"] and r["date"] in by_day:
            f = by_day[r["date"]]["formPct"]
            pts.append({"d": r["date"], "p": int(r["aet_pace_secs"]), "n": int(r["aet_secs"]),
                        "mi": float(r["miles"]), "formPct": f,
                        "cls": "fresh" if f > FRESH else "tired" if f < TIRED else "neutral"})
    trend = []
    for x in daily:
        d = date.fromisoformat(x["d"])
        win = [p for p in pts if 0 <= (d - date.fromisoformat(p["d"])).days < TREND_DAYS]
        if len(win) >= 4:
            trend.append({"d": x["d"], "p": round(sum(p["p"] * p["n"] for p in win) / sum(p["n"] for p in win), 1)})
    return pts, trend


def weekly(runs, daily):
    weeks = {}
    for r in runs:
        d = date.fromisoformat(r["date"])
        w = weeks.setdefault(d - timedelta(d.weekday()), {"mi": 0.0, "runs": 0, "hr": 0, "z": [0.0] * 5, "load": 0})
        w["mi"] += float(r["miles"])
        w["runs"] += 1
        w["load"] += float(r["load"] or 0)
        if r["hr_secs"]:
            n = int(r["hr_secs"])
            w["hr"] += n
            w["z"] = [z + float(r[c]) * n / 100 for z, c in zip(w["z"], ZONE_COLS)]
    by_day = {x["d"]: x for x in daily}
    out = []
    for wk0 in sorted(weeks):
        w = weeks[wk0]
        end = min(wk0 + timedelta(6), date.fromisoformat(daily[-1]["d"]))
        x = by_day.get(end.isoformat(), {})
        out.append({"w": wk0.isoformat(), "mi": round(w["mi"], 1), "runs": w["runs"], "load": round(w["load"]),
                    "hrH": round(w["hr"] / 3600, 1),
                    "z": [round(100 * z / w["hr"]) if w["hr"] else 0 for z in w["z"]],
                    "ctl": x.get("ctl"), "atl": x.get("atl"), "partial": end < wk0 + timedelta(6)})
    return out


def races(runs, plan):
    out = []
    for r in runs:
        d = date.fromisoformat(r["date"])
        p = plan.get(d)
        if not p or not (p["type"] == "race" or "time trial" in p["session"].lower()):
            continue
        m, s = r["pace"].split(":")
        secs = (int(m) * 60 + int(s)) * float(r["miles"])
        meters = float(r["miles"]) * M_PER_MI
        kind = "race" if p["type"] == "race" else "time trial"
        out.append({"d": d, "name": f"{dist_label(meters)} {kind}", "meters": meters, "secs": secs,
                    "elev": int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else None,
                    "vdot": vdot(meters, secs)})
    return out


# ---------- page ----------

def tile(label, value, sub="", delta=""):
    return (f'<div class="tile"><div class="tl">{label}</div><div class="tv">{value}</div>'
            f'{f"<div class=td>{delta}</div>" if delta else ""}'
            f'{f"<div class=ts>{sub}</div>" if sub else ""}</div>')


def signed(x, fmt="{:.1f}", good_up=True):
    if x is None:
        return ""
    cls = "flat" if abs(x) < 1e-9 else ("up" if (x > 0) == good_up else "down")
    sign = "+" if x > 0 else "−" if x < 0 else "±"
    return f'<span class="{cls}">{sign}{fmt.format(abs(x))}</span>'


def ft(x):
    return f"{x:,} ft" if x is not None else ""


def mmss(secs):
    secs = round(secs)
    return f"{secs // 60}:{secs % 60:02d}"


def build(plan, built):
    runs = load_runs()
    today = date.today()
    daily = series(runs, today)
    pts, trend = efficiency(runs, daily)
    weeks = weekly(runs, daily)
    rc = races(runs, plan)
    now, ago = daily[-1], daily[-29] if len(daily) > 28 else None

    # tiles
    prev28 = sum(x["mi"] for x in daily[-56:-28]) / 4 if len(daily) >= 56 else None
    t_now = trend[-1]["p"] if trend else None
    t_ago = next((t["p"] for t in trend if t["d"] == ago["d"]), None) if ago and trend else None
    fp = now["formPct"]
    form_word = "fresh" if fp > FRESH else "carrying fatigue" if fp < TIRED else "neutral"
    tiles = [
        tile("Last 7 days", f"{now['r7']:.1f} mi"),
        tile("28-day average", f"{now['r28']:.1f} mi/wk",
             delta=(signed(now["r28"] - prev28) + " vs. previous 28 days") if prev28 else ""),
        tile("Fitness", f"{now['ctl']:.0f}", "42-day training load",
             (signed(now["ctl"] - ago["ctl"], "{:.0f}") + " vs. 4 weeks ago") if ago else ""),
        tile("Form", f"{now['form']:+.0f}".replace("-", "−"), f"{fp:+.0f}% · {form_word}".replace("-", "−")),
    ]
    if t_now:
        tiles.append(tile(f"Pace at AeT ({AET} bpm)", f"{mmss(t_now)}/mi", "28-day trend, flat steady running",
                          (signed((t_now - t_ago) / 1, "{:.0f}", good_up=False) + " s/mi vs. 4 weeks ago") if t_ago else ""))
    best = rc[-1] if rc else None
    if best:
        tiles.append(tile("VDOT", f"{best['vdot']:.1f}", f"{best['name']} {best['d']:%b} {best['d'].day}"))

    # race fitness block
    race_html = ""
    if best:
        v = best["vdot"]
        elev = best["elev"]
        at = f"at {elev:,} ft" if elev is not None else "at race elevation"
        equiv = [(m, n) for m, n in DISTANCES[:3]]
        pgoal = predict(v, GOAL_METERS)
        rows = "".join(f"<tr><td>{r['d']:%b} {r['d'].day}</td><td>{html.escape(r['name'])}</td>"
                       f"<td>{r['meters'] / 1000:.2f} km</td><td>{hms(r['secs'])}</td>"
                       f"<td>{ft(r['elev'])}</td><td>{r['vdot']:.1f}</td></tr>"
                       for r in reversed(rc))
        goal_row = ""
        if elev is not None:
            flo, fhi = altitude_factor_range(elev, GOAL_ELEV_FT)
            goal_row = (f"<tr><td>{html.escape(GOAL_NAME)} at {GOAL_ELEV_FT:,} ft (est.)</td>"
                        f"<td>{hms(pgoal * flo)}–{hms(pgoal * fhi)}</td></tr>")
        equiv_rows = "".join(f"<tr><td>{n} {at}</td><td>{hms(predict(v, m))}</td></tr>" for m, n in equiv)
        race_html = f"""
<section class="card"><h2>Race fitness</h2>
<p class="note">From your most recent race or time trial: {html.escape(best['name'])}, {best['d']:%b} {best['d'].day}, {at}.</p>
<div class="grid2">
<div><h3>Equivalent times</h3><table class="t">
{equiv_rows}{goal_row}</table></div>
<div><h3>Training paces ({at})</h3><table class="t">
<tr><td>Easy</td><td>{mmss(pace_at(v, 0.70))}–{mmss(pace_at(v, 0.62))}/mi</td></tr>
<tr><td>Threshold</td><td>{mmss(pace_at(v, 0.88))}/mi</td></tr>
<tr><td>Interval</td><td>{mmss(pace_at(v, 0.98))}/mi</td></tr></table></div>
</div>
<details><summary>Races and time trials</summary><table class="t full"><thead><tr><th>Date</th><th>Event</th><th>Distance</th><th>Time</th><th>Elevation</th><th>VDOT</th></tr></thead><tbody>{rows}</tbody></table></details>
</section>"""

    wk_rows = "".join(
        f"<tr><td>{date.fromisoformat(w['w']):%b} {date.fromisoformat(w['w']).day}{' (so far)' if w['partial'] else ''}</td>"
        f"<td>{w['mi']}</td><td>{w['runs']}</td><td>{w['load']}</td><td>{w['ctl'] if w['ctl'] is not None else ''}</td>"
        f"<td>{w['atl'] if w['atl'] is not None else ''}</td>" + "".join(f"<td>{z}%</td>" for z in w["z"]) + "</tr>"
        for w in reversed(weeks))

    data = {"daily": daily, "weeks": weeks, "eff": pts, "trend": trend, "today": today.isoformat(),
            "aet": AET, "zones": [f"{n} {zone_range(lo, hi)}" for n, lo, hi in ZONES],
            "breaks": [1, 3]}  # zone indexes below the LT1 (AeT) and LT2 (LT) boundaries
    first = date.fromisoformat(daily[0]["d"])
    alt_lo, alt_hi = ALT_PCT_PER_1000
    about = f"""<p><b>Load</b> is HR-zone-weighted minutes: each minute in Z1 counts 1, Z2 counts 2, and so on up to Z5 = 5. Runs without real HR count {MANUAL_LOAD_PER_MIN:g} per minute.</p>
<p><b>Fitness / fatigue</b> are exponentially weighted averages of daily load over {CTL_DAYS} and {ATL_DAYS} days. History begins {first:%b} {first.day}, {first.year}, so both start at the average load of the first four weeks.</p>
<p><b>Form</b> measures training fatigue only. Sleep, stress, heat, and illness aren't included, so read single runs with that in mind.</p>
<p><b>Pace at AeT</b> uses only seconds with HR {EF_HR_BAND[0]}–{EF_HR_BAND[1]} bpm, moving faster than walking, on grades under {EF_MAX_GRADE:.0%}, scaled to exactly {AET} bpm. A run needs at least {EF_MIN_SECS // 60} minutes of those seconds. The trend weights each run by how many seconds qualified over the past {TREND_DAYS} days.</p>
<p><b>Zones</b> come from config.json: LT1 (AeT) {AET} bpm and LT2 (LT) {LT} bpm. The intensity chart puts wider breaks at those two thresholds.</p>
<p><b>VDOT</b> is Jack Daniels' formula applied to your most recent race or time trial (any planned race, or a session named "time trial"). The goal-race estimate assumes altitude costs {alt_lo:g}–{alt_hi:g}% per 1,000 ft above {ALT_FLOOR_FT:,} ft, using the race's average elevation from its own file.</p>"""
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("fitness.html"), "TILES": "".join(tiles), "RACE": race_html,
            "WEEK_ROWS": wk_rows, "DATA": json.dumps(data, separators=(",", ":")),
            "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}",
            "FRESH": str(FRESH), "TIRED": str(TIRED).replace("-", "−"), "AET": str(AET), "ABOUT": about,
            "ZLEG": "".join(f'<span><i class="z{i + 1}"></i>{n} {zone_range(lo, hi)}</span>' for i, (n, lo, hi) in enumerate(ZONES))
                    + '<span><span class="ln brk"></span>LT1 / LT2 break</span>'}
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs" / "fitness.html").write_text(page, encoding="utf-8")
    print("Wrote docs/fitness.html")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Running Fitness</title>
<style>
%%BASE_CSS%%
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; margin: 4px 0 20px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.tl { font-size: 12px; color: var(--muted); }
.tv { font-size: 26px; font-weight: 600; margin-top: 2px; letter-spacing: -0.01em; }
.td, .ts { font-size: 12px; color: var(--muted); margin-top: 2px; }
.td .up { color: var(--ok); } .td .down { color: var(--tired); } .td .flat { color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.card h2 { font-size: 16px; margin: 0 0 2px; }
.card h3 { font-size: 13px; margin: 8px 0 4px; color: var(--muted); font-weight: 600; }
.note { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.legend { margin: 6px 0 0; }
.legend .ln { display: inline-block; width: 16px; height: 2px; vertical-align: 3px; margin-right: 5px; border-radius: 1px; }
.legend .dt { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
.chart { position: relative; height: 240px; margin-top: 6px; }
.chart svg { display: block; width: 100%; height: 100%; overflow: visible; }
.grid line { stroke: var(--line); stroke-width: 1; }
.axis text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.zero { stroke: var(--muted); stroke-width: 1; }
.brk { stroke: var(--ink); stroke-width: 1.5; }
.legend .ln.brk { background: var(--ink); }
.guide { stroke: var(--muted); stroke-width: 1; }
.lbl { fill: var(--ink); font-size: 11px; font-weight: 600; }
.hit { fill: transparent; cursor: crosshair; }
.hl { fill: var(--ink); opacity: .06; }
#tip { position: fixed; z-index: 20; pointer-events: none; background: var(--card); color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font-size: 12px; line-height: 1.45;
  box-shadow: 0 4px 14px rgba(0,0,0,.18); display: none; max-width: 240px; font-variant-numeric: tabular-nums; }
#tip b { font-weight: 600; }
#tip i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 24px; }
table.t { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
table.t td, table.t th { padding: 3px 14px 3px 0; text-align: left; }
table.t td:last-child { text-align: right; padding-right: 0; }
table.t.full td, table.t.full th { text-align: right; padding: 3px 8px; }
table.t.full td:first-child, table.t.full th:first-child { text-align: left; padding-left: 0; }
table.t th { color: var(--muted); font-weight: 500; }
details { margin-top: 10px; }
summary { cursor: pointer; color: var(--muted); font-size: 12px; }
.scroll { overflow-x: auto; }
.z1 { background: var(--z1); fill: var(--z1); } .z2 { background: var(--z2); fill: var(--z2); }
.z3 { background: var(--z3); fill: var(--z3); } .z4 { background: var(--z4); fill: var(--z4); }
.z5 { background: var(--z5); fill: var(--z5); }
.about p { font-size: 13px; color: var(--muted); margin: 6px 0; }
.about b { color: var(--ink); font-weight: 600; }
@media (max-width: 760px) { .chart { height: 200px; } .tv { font-size: 22px; } }
</style>
</head>
<body>
<main>
<header>
  %%TABS%%
  <h1>Running Fitness</h1>
  <p class="goal">Built from completed runs only.</p>
</header>
<div class="tiles">%%TILES%%</div>

<section class="card"><h2>Weekly mileage</h2>
<p class="note">Mon–Sun totals, with the 28-day rolling average (mi/wk).</p>
<div class="chart" id="cMiles"></div>
<div class="legend"><span><i style="background:var(--s1)"></i>Weekly miles</span><span><span class="ln" style="background:var(--s2)"></span>28-day average</span></div>
</section>

<section class="card"><h2>Training load</h2>
<p class="note">Fitness is the 42-day average daily load and fatigue is the 7-day average. Form = fitness − fatigue.</p>
<div class="chart" id="cLoad"></div>
<div class="legend"><span><span class="ln" style="background:var(--s1)"></span>Fitness (42-day)</span><span><span class="ln" style="background:var(--s2)"></span>Fatigue (7-day)</span></div>
</section>

<section class="card"><h2>Aerobic efficiency: pace at %%AET%% bpm</h2>
<p class="note">Estimated from steady, flat running near AeT. Faster is up. Dots are single runs, colored by form going into that day, so a slow dot on a tired day isn't a sign of lost fitness.</p>
<div class="chart" id="cEff"></div>
<div class="legend"><span><span class="ln" style="background:var(--s7)"></span>28-day trend</span><span><span class="dt" style="background:var(--fresh)"></span>Fresh (form &gt; +%%FRESH%%%)</span><span><span class="dt" style="background:var(--neutral)"></span>Neutral</span><span><span class="dt" style="background:var(--tired)"></span>Tired (form &lt; %%TIRED%%%)</span></div>
</section>

<section class="card"><h2>Intensity by week</h2>
<p class="note">Share of time in each HR zone. Wider breaks mark LT1 (AeT) and LT2 (LT).</p>
<div class="chart" id="cZones"></div>
<div class="legend">%%ZLEG%%</div>
</section>

%%RACE%%

<section class="card"><details><summary>Weekly data table</summary><div class="scroll"><table class="t full">
<thead><tr><th>Week of</th><th>Miles</th><th>Runs</th><th>Load</th><th>Fitness</th><th>Fatigue</th><th>Z1</th><th>Z2</th><th>Z3</th><th>Z4</th><th>Z5</th></tr></thead>
<tbody>%%WEEK_ROWS%%</tbody></table></div></details></section>

<section class="card about"><h2>How these are calculated</h2>
%%ABOUT%%
</section>
<footer>Updated %%BUILT%%.</footer>
</main>
<div id="tip"></div>
<script>
(function () {
  var D = %%DATA%%;
  var NS = "http://www.w3.org/2000/svg";
  var tip = document.getElementById("tip");
  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  function el(tag, a, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in a) e.setAttribute(k, a[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function day(iso) { var p = iso.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]) / 864e5; }
  function fmtDate(iso) { var p = iso.split("-"); return MON[+p[1] - 1] + " " + (+p[2]); }
  function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
  function nice(v) { var p = Math.pow(10, Math.floor(Math.log10(v))), m = v / p; return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p; }
  function showTip(html, ev) {
    tip.innerHTML = html; tip.style.display = "block";
    var w = tip.offsetWidth, h = tip.offsetHeight, x = ev.clientX + 14, y = ev.clientY + 14;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.style.display = "none"; }
  function sw(v) { return '<i style="background:var(' + v + ')"></i>'; }

  // Frame: time x-axis, linear y-axis with hairline grid
  function frame(id, x0, x1, y0, y1, yTicks, yFmt, invert) {
    var box = document.getElementById(id); box.innerHTML = "";
    var W = box.clientWidth, H = box.clientHeight, m = { l: 40, r: 14, t: 10, b: 22 };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H }, box);
    var X = function (d) { return m.l + (d - x0) / (x1 - x0) * (W - m.l - m.r); };
    var Y = function (v) { var f = (v - y0) / (y1 - y0); if (invert) f = 1 - f; return H - m.b - f * (H - m.t - m.b); };
    var g = el("g", { "class": "grid" }, svg), ax = el("g", { "class": "axis" }, svg);
    yTicks.forEach(function (v) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
      el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, ax).textContent = yFmt(v);
    });
    return { svg: svg, X: X, Y: Y, W: W, H: H, m: m, ax: ax };
  }
  function monthTicks(f, x0, x1) {
    var d = new Date(x0 * 864e5); d = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1) / 864e5;
    while (d <= x1) {
      var dt = new Date(d * 864e5);
      el("text", { x: f.X(d), y: f.H - 6, "text-anchor": "middle" }, f.ax).textContent = MON[dt.getUTCMonth()];
      dt = Date.UTC(dt.getUTCFullYear(), dt.getUTCMonth() + 1, 1) / 864e5; d = dt;
    }
  }
  function linePath(f, pts) { return pts.map(function (p, i) { return (i ? "L" : "M") + f.X(p[0]).toFixed(1) + "," + f.Y(p[1]).toFixed(1); }).join(""); }
  function colPath(x, w, yTop, yBase) { // 4px rounded top, square base
    var r = Math.min(4, w / 2, Math.max(0, yBase - yTop));
    return "M" + x + "," + yBase + "V" + (yTop + r) + "Q" + x + "," + yTop + " " + (x + r) + "," + yTop +
      "H" + (x + w - r) + "Q" + (x + w) + "," + yTop + " " + (x + w) + "," + (yTop + r) + "V" + yBase + "Z";
  }

  var x0 = day(D.daily[0].d), x1 = day(D.daily[D.daily.length - 1].d) + 1;

  function miles() {
    var mx = Math.max.apply(null, D.weeks.map(function (w) { return w.mi; }).concat(D.daily.map(function (x) { return x.r28 || 0; })));
    var top = Math.ceil(mx / 10) * 10, ticks = []; for (var v = 0; v <= top; v += 10) ticks.push(v);
    var f = frame("cMiles", x0 - 3, x1 + 3, 0, top, ticks, function (v) { return v; });
    monthTicks(f, x0, x1);
    var band = f.X(7) - f.X(0), bw = Math.min(24, band - 6);
    D.weeks.forEach(function (w) {
      var cx = f.X(day(w.w) + 3.5);
      el("path", { d: colPath(cx - bw / 2, bw, f.Y(w.mi), f.Y(0)), fill: "var(--s1)", opacity: w.partial ? .45 : 1 }, f.svg);
    });
    var pts = D.daily.filter(function (x) { return x.r28 != null; }).map(function (x) { return [day(x.d) + .5, x.r28]; });
    el("path", { d: linePath(f, pts), fill: "none", stroke: "var(--s2)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
    var last = pts[pts.length - 1];
    if (last) el("circle", { cx: f.X(last[0]), cy: f.Y(last[1]), r: 4, fill: "var(--s2)", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
    var hl = el("rect", { "class": "hl", y: f.m.t, height: f.H - f.m.t - f.m.b, width: band, display: "none" }, f.svg);
    D.weeks.forEach(function (w) {
      var xs = f.X(day(w.w)), end = D.daily.filter(function (x) { return day(x.d) <= day(w.w) + 6; }).pop();
      var hit = el("rect", { "class": "hit", x: xs, y: f.m.t, width: band, height: f.H - f.m.t - f.m.b }, f.svg);
      hit.addEventListener("pointermove", function (ev) {
        hl.setAttribute("x", xs); hl.removeAttribute("display");
        showTip("<b>Week of " + fmtDate(w.w) + (w.partial ? " (so far)" : "") + "</b><br>" + sw("--s1") + w.mi + " mi · " + w.runs + " runs" +
          (end && end.r28 != null ? "<br>" + sw("--s2") + "28-day avg " + end.r28 + " mi/wk" : ""), ev);
      });
      hit.addEventListener("pointerleave", function () { hl.setAttribute("display", "none"); hideTip(); });
    });
  }

  function load() {
    var mx = Math.max.apply(null, D.daily.map(function (x) { return Math.max(x.ctl, x.atl); }));
    var step = nice(mx / 4), top = Math.ceil(mx / step) * step, ticks = [];
    for (var v = 0; v <= top; v += step) ticks.push(v);
    var f = frame("cLoad", x0, x1, 0, top, ticks, function (v) { return v; });
    monthTicks(f, x0, x1);
    [["atl", "--s2"], ["ctl", "--s1"]].forEach(function (s) {
      var pts = D.daily.map(function (x) { return [day(x.d) + .5, x[s[0]]]; });
      el("path", { d: linePath(f, pts), fill: "none", stroke: "var(" + s[1] + ")", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
      var l = pts[pts.length - 1];
      el("circle", { cx: f.X(l[0]), cy: f.Y(l[1]), r: 4, fill: "var(" + s[1] + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
    });
    var guide = el("line", { "class": "guide", y1: f.m.t, y2: f.H - f.m.b, display: "none" }, f.svg);
    var dots = ["--s1", "--s2"].map(function (c) { return el("circle", { r: 4, fill: "var(" + c + ")", stroke: "var(--card)", "stroke-width": 2, display: "none" }, f.svg); });
    var hit = el("rect", { "class": "hit", x: f.m.l, y: f.m.t, width: f.W - f.m.l - f.m.r, height: f.H - f.m.t - f.m.b }, f.svg);
    hit.addEventListener("pointermove", function (ev) {
      var r = f.svg.getBoundingClientRect(), px = (ev.clientX - r.left) * f.W / r.width;
      var dd = x0 + (px - f.m.l) / (f.W - f.m.l - f.m.r) * (x1 - x0);
      var i = Math.max(0, Math.min(D.daily.length - 1, Math.floor(dd - x0))), x = D.daily[i], cx = f.X(day(x.d) + .5);
      guide.setAttribute("x1", cx); guide.setAttribute("x2", cx); guide.removeAttribute("display");
      dots[0].setAttribute("cx", cx); dots[0].setAttribute("cy", f.Y(x.ctl)); dots[0].removeAttribute("display");
      dots[1].setAttribute("cx", cx); dots[1].setAttribute("cy", f.Y(x.atl)); dots[1].removeAttribute("display");
      showTip("<b>" + fmtDate(x.d) + "</b>" + (x.load ? " · load " + x.load : "") + "<br>" + sw("--s1") + "Fitness " + x.ctl.toFixed(0) +
        "<br>" + sw("--s2") + "Fatigue " + x.atl.toFixed(0) + "<br>Form " + (x.form > 0 ? "+" : "") + x.form.toFixed(0) +
        " (" + (x.formPct > 0 ? "+" : "") + x.formPct.toFixed(0) + "% going in)", ev);
    });
    hit.addEventListener("pointerleave", function () { guide.setAttribute("display", "none"); dots.forEach(function (d) { d.setAttribute("display", "none"); }); hideTip(); });
  }

  function eff() {
    if (!D.eff.length) return;
    var ps = D.eff.map(function (p) { return p.p; }).concat(D.trend.map(function (t) { return t.p; }));
    var lo = Math.floor((Math.min.apply(null, ps) - 5) / 30) * 30, hi = Math.ceil((Math.max.apply(null, ps) + 5) / 30) * 30, ticks = [];
    for (var v = lo; v <= hi; v += 30) ticks.push(v);
    var f = frame("cEff", x0, x1, lo, hi, ticks, mmss, true);
    monthTicks(f, x0, x1);
    var tp = D.trend.map(function (t) { return [day(t.d) + .5, t.p]; });
    if (tp.length) el("path", { d: linePath(f, tp), fill: "none", stroke: "var(--s7)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, f.svg);
    D.eff.forEach(function (p) {
      var cx = f.X(day(p.d) + .5), cy = f.Y(p.p);
      var c = el("circle", { cx: cx, cy: cy, r: 4.5, fill: "var(--" + p.cls + ")", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
      var hit = el("circle", { "class": "hit", cx: cx, cy: cy, r: 11 }, f.svg);
      hit.addEventListener("pointermove", function (ev) {
        c.setAttribute("r", 6);
        showTip("<b>" + fmtDate(p.d) + "</b> · " + p.mi.toFixed(1) + " mi<br>" + mmss(p.p) + "/mi at " + D.aet + " bpm<br>" +
          Math.round(p.n / 60) + " min of steady flat running<br>" + sw("--" + p.cls) + "Form going in " + (p.formPct > 0 ? "+" : "") + p.formPct.toFixed(0) + "%", ev);
      });
      hit.addEventListener("pointerleave", function () { c.setAttribute("r", 4.5); hideTip(); });
    });
    if (tp.length) {
      var l = tp[tp.length - 1];
      el("circle", { cx: f.X(l[0]), cy: f.Y(l[1]), r: 4, fill: "var(--s7)", stroke: "var(--card)", "stroke-width": 2 }, f.svg);
      var t = el("text", { "class": "lbl", x: f.X(l[0]) - 8, y: f.Y(l[1]) - 10, "text-anchor": "end" }, f.svg);
      t.textContent = mmss(l[1]) + "/mi";
    }
  }

  function zones() {
    var ws = D.weeks.filter(function (w) { return w.hrH > 0; }).slice(-10);
    var box = document.getElementById("cZones"); box.innerHTML = "";
    var W = box.clientWidth, H = box.clientHeight, m = { l: 40, r: 14, t: 10, b: 22 };
    var svg = el("svg", { viewBox: "0 0 " + W + " " + H }, box);
    var Y = function (v) { return H - m.b - v / 100 * (H - m.t - m.b); };
    var g = el("g", { "class": "grid" }, svg), ax = el("g", { "class": "axis" }, svg);
    [0, 50, 100].forEach(function (v) {
      el("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v) }, g);
      el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, ax).textContent = v + "%";
    });
    var band = (W - m.l - m.r) / ws.length, bw = Math.min(24, band - 6);
    ws.forEach(function (w, i) {
      var cx = m.l + band * (i + .5), acc = 0, top = 0, prev = -1;
      w.z.forEach(function (z, k) { if (z > 0) top = k; });
      w.z.forEach(function (z, k) {
        if (z <= 0) return;
        var yb = Y(acc), yt = Y(acc + z);
        // 2px surface gap between segments; a wider gap and a rule where a threshold (LT1/LT2) is crossed
        var isBreak = prev >= 0 && D.breaks.some(function (b) { return prev <= b && b < k; });
        var gap = prev < 0 ? 0 : isBreak ? 6 : 2;
        if (isBreak) el("line", { "class": "brk", x1: cx - bw / 2 - 5, x2: cx + bw / 2 + 5, y1: yb - gap / 2, y2: yb - gap / 2 }, svg);
        if (yb - gap - yt >= 1) {
          var d = k === top ? colPath(cx - bw / 2, bw, yt, yb - gap) : "M" + (cx - bw / 2) + "," + (yb - gap) + "V" + yt + "H" + (cx + bw / 2) + "V" + (yb - gap) + "Z";
          el("path", { d: d, "class": "z" + (k + 1) }, svg);
        }
        acc += z; prev = k;
      });
      el("text", { x: cx, y: H - 6, "text-anchor": "middle" }, ax).textContent = fmtDate(w.w);
      var hl = el("rect", { "class": "hl", x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b, display: "none" }, svg);
      var hit = el("rect", { "class": "hit", x: cx - band / 2, y: m.t, width: band, height: H - m.t - m.b }, svg);
      hit.addEventListener("pointermove", function (ev) {
        hl.removeAttribute("display");
        var b1 = D.breaks[0], b2 = D.breaks[1], sum = function (a, b) { return w.z.slice(a, b).reduce(function (s, x) { return s + x; }, 0); };
        showTip("<b>Week of " + fmtDate(w.w) + (w.partial ? " (so far)" : "") + "</b> · " + w.hrH + " h<br>" +
          "Below LT1 " + sum(0, b1 + 1) + "% · LT1–LT2 " + sum(b1 + 1, b2 + 1) + "% · Above LT2 " + sum(b2 + 1) + "%<br>" +
          D.zones.map(function (n, k) { return sw("--z" + (k + 1)) + n + ": " + w.z[k] + "%"; }).join("<br>"), ev);
      });
      hit.addEventListener("pointerleave", function () { hl.setAttribute("display", "none"); hideTip(); });
    });
    // thin labels on narrow screens
    if (band < 44) Array.prototype.forEach.call(ax.querySelectorAll("text"), function (t, i) { if (i >= 3 && (i - 3) % 2) t.textContent = ""; });
  }

  function render() { miles(); load(); eff(); zones(); }
  render();
  var rt; window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(render, 120); });
})();
</script>
</body>
</html>
"""
