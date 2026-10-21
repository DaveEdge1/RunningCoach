"""Calculator tab (docs/calculator.html): race predictions with 80% ranges from whatever you enter.

Built by scripts/build_site.py. The math runs in the browser, so any races or paces can be entered. Inputs are all
optional: recent races, LT2 (threshold) pace, LT1 (aerobic threshold) pace, weekly mileage. The page is seeded with
the races in races.csv / the plan, pace at AeT, the 28-day mileage and the next race. Each input gives a
prediction and an 80% range per distance, measured on real runners (data/ref/race_errors.csv, from
scripts/race_errors.py), and the inputs are pooled by how tight their ranges are.
"""
import csv, json
from datetime import date, timedelta

from build_site import BASE_CSS, tabs
from config import DANIELS_FLOOR_FT, DANIELS_SPM, HOME_ELEV_FT, HOME_NAME, ROOT, _NCAA, next_race
from fitness_page import efficiency, load_runs, races, series, src_elev

RECENT_DAYS = 183  # races this recent start ticked (Vickers & Vertosick asked for races within six months)


def load_errors():
    with open(ROOT / "data" / "ref" / "race_errors.csv", encoding="utf-8", newline="") as f:
        return {f"{r['band']}|{r['from']}|{r['to']}": [float(r["p10_pct"]), float(r["p50_pct"]), float(r["p90_pct"])]
                for r in csv.DictReader(f)}


def build(plan, built):
    runs = load_runs()
    today = date.today()
    rc = races(runs, plan)
    nr = next_race(today)
    daily = series(runs, today) if runs else []
    _, trend = efficiency(runs, daily, rc) if runs else (None, [])
    data = {
        "ncaa": [[a, d] for a, d in _NCAA], "floor": DANIELS_FLOOR_FT, "spm": list(DANIELS_SPM), "err": load_errors(),
        "races": [{"date": r["d"].isoformat(), "m": r["meters"], "secs": round(r["secs"]), "elev": src_elev(r),
                   "use": (today - r["d"]).days <= RECENT_DAYS} for r in reversed(rc)],
        "lt1": round(trend[-1]["p"]) if trend else None,
        "mileage": round(daily[-1]["r28"]) if daily else None,
        "home": HOME_ELEV_FT,
        "target": nr["elev"] if nr and nr["elev"] is not None else HOME_ELEV_FT,
        "focus": nr["meters"] if nr else 5000,
    }
    seed = ("Seeded with your races from the last six months, your 28-day median pace at AeT as LT1 (grade-adjusted, "
            f"at {HOME_NAME}), your 28-day average mileage" + (f", and {nr['name']}." if nr else "."))
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("calculator.html"), "DATA": json.dumps(data, separators=(",", ":")),
            "SEED": seed, "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}",
            "SPM": "–".join(f"{k:g}" for k in DANIELS_SPM), "FLOOR": f"{DANIELS_FLOOR_FT:,}"}
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs" / "calculator.html").write_text(page, encoding="utf-8")
    print("Wrote docs/calculator.html")


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Race Calculator</title>
<style>
%%BASE_CSS%%
a { color: var(--s1); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin-bottom: 14px; }
.card h2 { font-size: 16px; margin: 0 0 2px; }
.card h3 { font-size: 13px; margin: 12px 0 4px; color: var(--muted); font-weight: 600; }
.note { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
input, select { font: inherit; font-size: 14px; color: var(--ink); background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 5px 8px; min-width: 0; font-variant-numeric: tabular-nums; }
input:focus, select:focus { outline: 2px solid var(--s1); outline-offset: -1px; }
input.bad { border-color: var(--race); }
.form { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px 16px; }
.form label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--muted); min-width: 0; }
.form input, .form select { width: 100%; }
.rhead, .race { display: grid; grid-template-columns: 28px 140px minmax(90px, 1fr) minmax(90px, 1fr) minmax(80px, 1fr) 28px;
  gap: 6px; align-items: center; }
.rhead { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.race { margin-bottom: 6px; } .race input, .race select { width: 100%; }
.race .dist { display: flex; gap: 4px; } .race .dist select { flex: 1; } .race .dist input { width: 64px; flex: none; }
.race input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--ink); }
.race.off > :not(:first-child):not(:last-child) { opacity: .45; }
button.x, button.add { font: inherit; font-size: 13px; color: var(--muted); background: none; border: 1px solid var(--line);
  border-radius: 6px; cursor: pointer; padding: 4px 0; }
button.add { padding: 5px 12px; color: var(--ink); background: var(--card); margin-top: 2px; }
@media (max-width: 640px) {
  .rhead { display: none; }
  .race { grid-template-columns: 28px 1fr 1fr 28px; border-bottom: 1px solid var(--line); padding-bottom: 6px; }
  .race .date { grid-column: 2 / 4; grid-row: 1; } .race .dist { grid-column: 2 / 4; grid-row: 2; }
  .race .t { grid-column: 2; grid-row: 3; } .race .e { grid-column: 3; grid-row: 3; }
  .race .x { grid-row: 1; grid-column: 4; } .race .use { grid-row: 1; }
}
[hidden] { display: none !important; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 8px; margin: 0 0 14px; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.tl { font-size: 12px; color: var(--muted); }
.tv { font-size: 26px; font-weight: 600; margin-top: 2px; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.ts { font-size: 12px; color: var(--muted); margin-top: 2px; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 4px 28px; }
.scroll { overflow-x: auto; }
table.t { border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; width: 100%; }
table.t td, table.t th { padding: 4px 8px; text-align: right; white-space: nowrap; }
table.t td:first-child, table.t th:first-child { text-align: left; padding-left: 0; }
table.t th { color: var(--muted); font-weight: 500; }
table.t tr.hl td { font-weight: 600; }
table.t td.dim { color: var(--muted); }
td .bar { position: relative; display: inline-block; width: 120px; height: 10px; vertical-align: -1px; }
td .bar i { position: absolute; top: 3px; height: 4px; border-radius: 2px; background: var(--s1); opacity: .35; }
td .bar b { position: absolute; top: 0; width: 2px; height: 10px; background: var(--s1); }
.err { color: var(--race); font-size: 13px; margin: 8px 0 0; }
.about p { font-size: 13px; color: var(--muted); margin: 6px 0; }
.about b { color: var(--ink); font-weight: 600; }
footer { margin-top: 32px; color: var(--muted); font-size: 12px; }
</style>
</head>
<body>
<main>
%%TABS%%
<header>
  <h1>Race Calculator</h1>
  <p class="sub">Race predictions with 80% ranges from whatever you know · updated %%BUILT%%</p>
</header>

<section class="card">
<h2>What you know</h2>
<p class="note">Everything is optional; one race or one pace is enough, and more inputs pull the prediction toward the ones that say the most about each distance. %%SEED%%</p>
<h3>Recent races</h3>
<div class="rhead"><span>Use</span><span>Date</span><span>Distance</span><span>Time</span><span>Elevation (ft)</span><span></span></div>
<div id="races"></div>
<button class="add" id="add">+ Add a race</button>
<h3>Paces (per mile, flat ground)</h3>
<div class="form">
  <label>LT2: threshold pace<input id="lt2" placeholder="m:ss" inputmode="numeric"></label>
  <label>LT2 means<select id="lt2def"><option value="hrv">All-out pace for 10–15 km (HRV4Training)</option><option value="daniels">1-hour race pace (Daniels T)</option></select></label>
  <label>LT1: aerobic threshold pace<input id="lt1" placeholder="m:ss" inputmode="numeric"></label>
  <label>Paces run at elevation (ft)<input id="pe" inputmode="numeric"></label>
</div>
<h3>Training and target</h3>
<div class="form">
  <label>Typical weekly miles<input id="mi" inputmode="decimal" placeholder="for the marathon"></label>
  <label>Predict at elevation (ft)<input id="tg" inputmode="numeric"></label>
  <label>Focus distance<select id="focus"></select></label>
</div>
<p class="err" id="err" hidden></p>
</section>

<div class="tiles" id="tiles"></div>

<section class="card">
<h2 id="eqH">Predictions</h2>
<p class="note">80% of club runners at your level (by VDOT) finished inside the range when they raced both distances within 90 days. The formula column is plain VDOT, before the correction for how real runners differ from it.</p>
<div class="scroll"><table class="t" id="eq"></table></div>
</section>

<section class="card">
<h2 id="byH">What each input says</h2>
<p class="note">Weight is each input's share in the pooled prediction: the tighter its range for this distance, the more it counts.</p>
<div class="scroll"><table class="t" id="by"></table></div>
</section>

<section class="card">
<h2 id="paceH">Training paces</h2>
<div class="grid2">
<div><h3 id="danH">Daniels</h3><table class="t" id="dan"></table></div>
<div><h3 id="hrvH">HRV4Training zones (% of LT2 pace)</h3><table class="t" id="hrv"></table></div>
</div>
</section>

<section class="card about"><h2>How these are calculated</h2>
<p><b>Ranges from real runners.</b> Blythe &amp; Király (<a href="https://doi.org/10.1371/journal.pone.0157257">"Prediction and quantification of individual athletic performance of runners"</a>, <i>PLOS ONE</i> 2016) published the <a href="https://figshare.com/articles/thepowerof10/3408202">thepowerof10</a> results database: official times for 165,000 British club runners from 1500 m to the marathon. For every pair of distances, scripts/race_errors.py takes each runner's two races closest in time (within 90 days) and compares the second time with VDOT's prediction from the first (data/ref/race_errors.csv). At the same distance it's two different races, so a race predicts its own distance with the spread of ordinary race-to-race variation. The prediction is VDOT shifted by the median miss, and the range is the 10th to 90th percentile, separately for VDOT under 40, 40–50, 50–60 and over 60 (the input race's VDOT at sea level), since slower runners are less predictable. For example, at VDOT 40–50 a 10K predicts a 5K 1.2% faster than VDOT, with a range of 6.2% faster to 3.0% slower. A half predicts a marathon 9.3% slower. Distances in between are interpolated on log distance. Checks: built from half the runners, 71–83% of the other half land inside (80% for most pairs). In Vickers &amp; Vertosick's survey of 2,303 recreational runners (self-reported times, races up to six months apart), 76% land inside. Survey runners vary more between short races (5K → 10K: 65%) and slightly less in the marathon (83–86%).</p>
<p><b>VDOT</b> (Daniels &amp; Gilbert, <i>Oxygen Power</i>, 1979) is the same formula as the Fitness tab. Riegel's formula (time × distance ratio<sup>1.06</sup>) misses by about the same amounts, so it isn't shown separately.</p>
<p><b>Marathon with weekly mileage</b> uses Vickers &amp; Vertosick's models (<a href="https://doi.org/10.1186/s13102-016-0052-y">"An empirical study of race times in recreational endurance runners"</a>, <i>BMC Sports Sci Med Rehabil</i> 2016;8:26), the ones FiveThirtyEight's marathon calculator is built on. Model 1, one race: marathon speed (m/s) = 0.1602 + 0.8308 × Riegel speed (exponent 1.07, from your longest ticked race) + 0.0642 × weekly miles ÷ 10. Model 2, two races: the exponent between your two longest ticked races, k, gives the marathon exponent 1.4511 − 0.2380 × k − 0.0141 × weekly miles ÷ 10. Their ranges come from the paper's validation runners: 80% within −6.4% to +7.8% (model 1, n = 310) and −6.2% to +7.3% (model 2, n = 156). When mileage is entered, they replace the races' VDOT estimate for the marathon.</p>
<p><b>LT2</b> is treated as a race at that pace. HRV4Training (Marco Altini, <a href="https://marcoaltini.substack.com/p/lactate-threshold-critical-speed">"Lactate threshold / critical speed"</a>) defines threshold, or critical pace, as what you could hold all out for 10–15 km. That's a 12.5 km race, with the range widened to cover 10 to 15 km. Daniels' T pace is about the pace you could race for an hour, so it's a 60-minute race. HRV4Training's zones are percentages of that pace (Z1 slower than 130%, Z2 115–130%, Z3 105–115%, Z4 95–105%, Z5 90–95%, Z6 faster than 90%). Without an LT2 they're based on the predicted 12.5 km pace.</p>
<p><b>LT1</b> is taken as the top of Daniels' Easy range: 70% of VDOT (the Fitness tab's Easy pace) to 74% (<i>Daniels' Running Formula</i>: easy running is 59–74% of VO2max). The VDOT those give is predicted forward like a race. No study has checked LT1 pace against race times, so its range is that 70–74% span combined with the spread of a 10K-based prediction. That's a floor, not a measured range, so LT1 feeds the prediction only when there's no race or LT2; otherwise it's listed for comparison.</p>
<p><b>Pooling</b>: inputs are averaged on log time, each weighted by 1 ÷ its range². The pooled range is the weighted average of the inputs' ranges, not narrower. In the club data, pooling a 5K and a 10K to predict a half cut the typical error only from 5.3% to 5.0%, because most of the spread is the runner's own make-up (endurance vs. speed), which repeats across their races. Training paces use the VDOT of the pooled focus-distance prediction.</p>
<p><b>Altitude</b>: each input is converted to the prediction elevation two published ways before predicting. Daniels: paces slow %%SPM%% s/mi per 1,000 ft above %%FLOOR%% ft. NCAA 2009 altitude tables: % added by venue altitude for the mile, 5K and 10K. The prediction is the average of the methods, and the range covers all of them. LT1 and LT2 are converted as a 10K would be. The reference runners raced in Britain, near sea level.</p>
<p>Altini &amp; Amft's EMBC 2018 paper estimates 10K time from HRV, training volume and intensity distribution by regression, but doesn't publish the coefficients, so it isn't used here.</p>
</section>
<footer>Updated %%BUILT%%.</footer>
</main>
<script>
(function () {
  var D = %%DATA%%, MI = 1609.344, MARA = 42195, Z80 = 2 * 1.2816;  // 10th-90th percentile = ±1.28 SD
  var DISTS = [[MI, "1 mile"], [2 * MI, "2 miles"], [5000, "5K"], [8000, "8K"], [5 * MI, "5 miles"], [10000, "10K"],
               [15000, "15K"], [10 * MI, "10 miles"], [21097.5, "Half marathon"], [MARA, "Marathon"]];
  var GRID = [["1500", 1500], ["mile", MI], ["5K", 5000], ["10K", 10000], ["half", 21097.5], ["mara", MARA]];  // in the reference data
  var BANDS = [["<40", 40], ["40-50", 50], ["50-60", 60], ["60+", Infinity]];  // by VDOT of the input, at sea level
  var $ = function (id) { return document.getElementById(id); };

  // ---------- Daniels VDOT (same as scripts/fitness_page.py) ----------
  function vo2(v) { return -4.60 + 0.182258 * v + 0.000104 * v * v; }  // v in m/min
  function pctMax(t) { return 0.8 + 0.1894393 * Math.exp(-0.012778 * t) + 0.2989558 * Math.exp(-0.1932605 * t); }
  function vdot(m, s) { var t = s / 60; return vo2(m / t) / pctMax(t); }
  function predict(v, m) {
    var lo = 60, hi = 36000;
    for (var i = 0; i < 60; i++) { var mid = (lo + hi) / 2; if (vdot(m, mid) > v) lo = mid; else hi = mid; }
    return (lo + hi) / 2;
  }
  function paceAt(v, f) {  // s/mi at the speed where VO2 = f * VDOT
    var a = 0.000104, b = 0.182258, c = -4.60 - f * v;
    return MI / ((-b + Math.sqrt(b * b - 4 * a * c)) / (2 * a)) * 60;
  }

  // ---------- altitude (same as scripts/config.py convert_time): one time per method ----------
  function interp(x, pts) {
    if (x <= pts[0][0]) return pts[0][1];
    for (var i = 1; i < pts.length; i++)
      if (x <= pts[i][0]) return pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * (x - pts[i - 1][0]) / (pts[i][0] - pts[i - 1][0]);
    return pts[pts.length - 1][1];
  }
  function ncaaPct(alt, m) {
    return interp(alt, D.ncaa.map(function (r) {
      return [r[0], interp(Math.log(m), r[1].map(function (p) { return [Math.log(p[0]), p[1]]; }))];
    }));
  }
  function spm(alt, k) { return k * Math.max(0, alt - D.floor) / 1000; }
  function convert(secs, m, from, to) {
    var out = D.spm.map(function (k) { return secs + (spm(to, k) - spm(from, k)) * m / MI; });
    out.push(secs / (1 + ncaaPct(from, m) / 100) * (1 + ncaaPct(to, m) / 100));
    return out;
  }
  var NM = D.spm.length + 1;

  // ---------- reference-data misses: [p10, p50, p90] % slower than VDOT, from distance a to b ----------
  // (same as scripts/race_errors.py lookup)
  function cell(bd, i, j) { var k = "|" + GRID[i][0] + "|" + GRID[j][0]; return D.err[bd + k] || D.err["all" + k]; }
  function bracket(m) {  // index and weight on log distance, clamped to the grid
    var L = function (i) { return Math.log(GRID[i][1]); };
    var x = Math.log(Math.min(Math.max(m, GRID[0][1]), GRID[GRID.length - 1][1]));
    for (var i = 0; i < GRID.length - 2 && x > L(i + 1); i++);
    return [i, (x - L(i)) / (L(i + 1) - L(i))];
  }
  function miss(a, b, v) {  // v: sea-level VDOT of the input, for its ability band
    var bd = BANDS.filter(function (x) { return v < x[1]; })[0][0];
    var A = bracket(a), B = bracket(b), out = [0, 0, 0];
    [[0, 0], [0, 1], [1, 0], [1, 1]].forEach(function (c) {
      var w = (c[0] ? A[1] : 1 - A[1]) * (c[1] ? B[1] : 1 - B[1]), q = cell(bd, A[0] + c[0], B[0] + c[1]);
      for (var k = 0; k < 3; k++) out[k] += w * q[k];
    });
    return out;
  }
  function spread(q) { return [Math.log(1 + q[0] / 100) - Math.log(1 + q[1] / 100), Math.log(1 + q[2] / 100) - Math.log(1 + q[1] / 100)]; }

  // ---------- formatting ----------
  function hms(s) {
    s = Math.round(s);
    var h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  }
  function parseClock(s) {
    var p = String(s).trim().split(":");
    if (p.length > 3 || p.some(function (x) { return !/^\\d+(\\.\\d+)?$/.test(x); })) return null;
    return p.reduce(function (t, x) { return t * 60 + parseFloat(x); }, 0) || null;
  }
  function num(el) { var v = parseFloat(el.value.replace(/,/g, "")); return isFinite(v) ? v : null; }
  function ft(x) { return Math.round(x).toLocaleString() + " ft"; }
  function dname(m) {
    var hit = DISTS.filter(function (d) { return Math.abs(d[0] / m - 1) < 0.01; })[0];
    return hit ? hit[1] : (m / MI).toFixed(2) + " mi";
  }
  function mean(a) { return a.reduce(function (s, x) { return s + x; }, 0) / a.length; }

  // ---------- race rows ----------
  var distOpts = DISTS.map(function (d) { return '<option value="' + d[0] + '">' + d[1] + "</option>"; }).join("") +
                 '<option value="c">Other (mi)</option>';
  function addRace(r) {
    var row = document.createElement("div");
    row.className = "race";
    row.innerHTML = '<input type="checkbox" class="use" aria-label="Use this race"><input type="date" class="date" aria-label="Date">' +
      '<span class="dist"><select class="d" aria-label="Distance">' + distOpts + '</select><input class="c" inputmode="decimal" aria-label="Miles" hidden></span>' +
      '<input class="t" placeholder="mm:ss" inputmode="numeric" aria-label="Time"><input class="e" inputmode="numeric" aria-label="Elevation (ft)">' +
      '<button class="x" aria-label="Remove">×</button>';
    var q = function (c) { return row.querySelector("." + c); };
    q("use").checked = r ? r.use : true;
    q("date").value = r ? r.date : "";
    var m = r ? r.m : 5000, hit = DISTS.filter(function (d) { return Math.abs(d[0] / m - 1) < 0.01; })[0];
    q("d").value = hit ? hit[0] : "c";  // a preset within 1% (GPS runs a little long or short), else Other
    q("c").value = hit ? "" : (m / MI).toFixed(2);
    q("c").hidden = !!hit;
    q("t").value = r ? hms(r.secs) : "";
    q("e").value = r ? r.elev : D.home;
    q("d").addEventListener("change", function () { q("c").hidden = this.value !== "c"; });
    q("x").addEventListener("click", function () { row.remove(); compute(); });
    row.classList.toggle("off", !q("use").checked);
    q("use").addEventListener("change", function () { row.classList.toggle("off", !this.checked); });
    $("races").appendChild(row);
    return row;
  }
  D.races.forEach(addRace);
  if (!D.races.length) addRace(null).querySelector(".t").value = "";
  $("add").addEventListener("click", function () { addRace(null).querySelector(".t").focus(); });
  $("focus").innerHTML = DISTS.map(function (d) { return '<option value="' + d[0] + '">' + d[1] + "</option>"; }).join("");
  $("focus").value = (DISTS.filter(function (d) { return Math.abs(d[0] / D.focus - 1) < 0.01; })[0] || [5000])[0];
  $("lt1").value = D.lt1 ? hms(D.lt1) : "";
  $("pe").value = D.home;
  $("mi").value = D.mileage === null ? "" : D.mileage;
  $("tg").value = D.target;

  // ---------- inputs -> estimates ----------
  // An estimate predicts distance b at the target elevation: per altitude method, {c, lo, hi} (s) plus the plain VDOT time.
  function readInputs() {
    var err = [], out = { races: [], lt2: null, lt1: null };
    document.querySelectorAll("input").forEach(function (x) { x.classList.remove("bad"); });
    function bad(el, msg) { el.classList.add("bad"); if (err.indexOf(msg) < 0) err.push(msg); }
    document.querySelectorAll(".race").forEach(function (row) {
      var q = function (c) { return row.querySelector("." + c); };
      if (!q("use").checked) return;
      var traw = q("t").value.trim(); if (!traw) return;
      var t = parseClock(traw), m = q("d").value === "c" ? parseFloat(q("c").value) * MI : parseFloat(q("d").value), e = num(q("e"));
      if (!t) return bad(q("t"), "Race times look like 21:30 or 1:42:05.");
      if (!(m > 0)) return bad(q("c"), "Enter the race distance in miles.");
      if (e === null) return bad(q("e"), "Enter each race's elevation.");
      out.races.push({ m: m, t: t, e: e, date: q("date").value });
    });
    var pe = num($("pe")), tg = num($("tg")), mi = num($("mi"));
    ["lt2", "lt1"].forEach(function (id) {
      var raw = $(id).value.trim(); if (!raw) return;
      var p = parseClock(raw);
      if (!p) return bad($(id), "Paces look like 7:05.");
      if (pe === null) return bad($("pe"), "Enter the elevation the paces were run at.");
      out[id] = p;
    });
    if (tg === null) bad($("tg"), "Enter the elevation to predict at.");
    if ($("mi").value.trim() && (mi === null || mi < 0)) bad($("mi"), "Weekly miles should be a number.");
    if (!err.length && !out.races.length && !out.lt2 && !out.lt1) err.push("Enter at least one race or pace.");
    out.pe = pe; out.tg = tg; out.mi = $("mi").value.trim() ? mi : null; out.err = err;
    return out;
  }

  function slVdot(m, t, e) { return vdot(m, mean(convert(t, m, e, 0))); }
  function fromRace(m, t, e, tg, b) {  // per method: race converted to tg, VDOT forward, shifted by the reference misses
    var q = miss(m, b, slVdot(m, t, e)), s = spread(q);
    return convert(t, m, e, tg).map(function (tk) {
      var raw = predict(vdot(m, tk), b), c = raw * (1 + q[1] / 100);
      return { raw: raw, c: c, lo: c * Math.exp(s[0]), hi: c * Math.exp(s[1]) };
    });
  }
  function union(a, b) {  // widen a's range to cover b's, per method
    return a.map(function (x, k) { return { raw: x.raw, c: x.c, lo: Math.min(x.lo, b[k].lo), hi: Math.max(x.hi, b[k].hi) }; });
  }

  function estimates(I, b) {
    var est = [], tg = I.tg;
    var rs = I.races.slice().sort(function (x, y) { return y.m - x.m; });
    var useModels = b > MARA * 0.97 && I.mi !== null && rs.some(function (r) { return r.m < MARA * 0.97; });
    I.races.forEach(function (r) {
      if (useModels && r.m < MARA * 0.97) return;  // the marathon models stand in for these
      est.push({ name: dname(r.m) + (r.date ? " · " + r.date : ""), per: fromRace(r.m, r.t, r.e, tg, b) });
    });
    if (useModels) {
      var short = rs.filter(function (r) { return r.m < MARA * 0.97; });
      var two = short.filter(function (r) { return Math.abs(r.m / short[0].m - 1) > 0.05; })[0];
      var q = two ? D.err["all|model2|mara"] : D.err["all|model1|mara"], s = spread(q);
      est.push({ name: two ? "Marathon model 2: " + dname(two.m) + " + " + dname(short[0].m) + " + " + I.mi + " mi/wk"
                           : "Marathon model 1: " + dname(short[0].m) + " + " + I.mi + " mi/wk",
        per: Array.from({ length: NM }, function (_, k) { return k; }).map(function (k) {
          var L = short[0], lt = convert(L.t, L.m, L.e, tg)[k], T;
          if (two) {
            var st = convert(two.t, two.m, two.e, tg)[k];
            var kk = Math.log(lt / st) / Math.log(L.m / two.m);
            T = lt * Math.pow(MARA / L.m, 1.4510756 - 0.23797948 * kk - 0.01410023 * I.mi / 10);
          } else {
            T = MARA / (0.16018617 + 0.83076202 * MARA / (lt * Math.pow(MARA / L.m, 1.07)) + 0.06423826 * I.mi / 10);
          }
          var c = T * (1 + q[1] / 100);
          return { raw: predict(vdot(L.m, lt), MARA), c: c, lo: c * Math.exp(s[0]), hi: c * Math.exp(s[1]) };
        }) });
    }
    if (I.lt2) {
      var p = I.lt2, per;
      if ($("lt2def").value === "hrv") {
        per = union(union(fromRace(12500, p * 12500 / MI, I.pe, tg, b), fromRace(10000, p * 10000 / MI, I.pe, tg, b)),
                    fromRace(15000, p * 15000 / MI, I.pe, tg, b));
      } else {
        per = fromRace(3600 / p * MI, 3600, I.pe, tg, b);
      }
      est.push({ name: "LT2 " + hms(p) + "/mi", per: per });
    }
    if (I.lt1) {
      var qb = miss(10000, b, vo2(10000 / (mean(convert(I.lt1 * 10000 / MI, 10000, I.pe, 0)) / 60)) / 0.72), q10 = spread(qb);
      est.push({ name: "LT1 " + hms(I.lt1) + "/mi", fallback: true, per: convert(I.lt1 * 10000 / MI, 10000, I.pe, tg).map(function (t10) {
        var o2 = vo2(10000 / (t10 / 60));
        var tc = predict(o2 / 0.72, b), tf = predict(o2 / 0.70, b), ts = predict(o2 / 0.74, b);  // faster, slower ends
        var c = tc * (1 + qb[1] / 100);
        var lo = Math.log(tf / tc), hi = Math.log(ts / tc);  // LT1 span, combined in quadrature with a 10K's spread
        return { raw: tc, c: c, lo: c * Math.exp(-Math.hypot(lo, q10[0])), hi: c * Math.exp(Math.hypot(hi, q10[1])) };
      }) });
    }
    return est;
  }

  function pool(est) {  // per method: inverse-variance mean on log time; range = weighted mean of the ranges
    var main = est.some(function (e) { return !e.fallback; });  // LT1 counts only when nothing else is entered
    var per = [], wts = est.map(function () { return 0; });
    for (var k = 0; k < NM; k++) {
      var W = 0, mu = 0, dlo = 0, dhi = 0, raw = 0;
      var w = est.map(function (e) {
        var x = e.per[k], sd = Math.max((Math.log(x.hi) - Math.log(x.lo)) / Z80, 0.004);
        return main && e.fallback ? 0 : 1 / (sd * sd);
      });
      est.forEach(function (e, i) {
        var x = e.per[k], lc = Math.log(x.c);
        W += w[i]; mu += w[i] * lc; raw += w[i] * Math.log(x.raw);
        dlo += w[i] * (Math.log(x.lo) - lc); dhi += w[i] * (Math.log(x.hi) - lc);
      });
      mu /= W;
      per.push({ c: Math.exp(mu), lo: Math.exp(mu + dlo / W), hi: Math.exp(mu + dhi / W), raw: Math.exp(raw / W) });
      w.forEach(function (x, i) { wts[i] += x / W / NM; });
    }
    return { c: mean(per.map(function (x) { return x.c; })), raw: mean(per.map(function (x) { return x.raw; })),
             lo: Math.min.apply(null, per.map(function (x) { return x.lo; })),
             hi: Math.max.apply(null, per.map(function (x) { return x.hi; })), w: wts };
  }
  function summarize(per) {  // one estimate across the altitude methods
    return { c: mean(per.map(function (x) { return x.c; })), lo: Math.min.apply(null, per.map(function (x) { return x.lo; })),
             hi: Math.max.apply(null, per.map(function (x) { return x.hi; })) };
  }

  // ---------- render ----------
  function compute() {
    var I = readInputs();
    $("err").hidden = !I.err.length; $("err").textContent = I.err.join(" ");
    if (I.err.length) return;
    var tg = I.tg, F = parseFloat($("focus").value), at = " at " + ft(tg);

    var rows = DISTS.map(function (d) { return { m: d[0], name: d[1], p: pool(estimates(I, d[0])) }; });
    var lo = Math.min.apply(null, rows.map(function (r) { return Math.log(r.p.lo / r.p.c); }));
    var hi = Math.max.apply(null, rows.map(function (r) { return Math.log(r.p.hi / r.p.c); }));
    function bar(p) {
      var x = function (v) { return (100 * (Math.log(v / p.c) - lo) / (hi - lo)).toFixed(1) + "%"; };
      return '<span class="bar"><i style="left:' + x(p.lo) + ";width:calc(" + x(p.hi) + " - " + x(p.lo) + ')"></i><b style="left:' + x(p.c) + '"></b></span>';
    }
    $("eqH").textContent = "Predictions" + at;
    $("eq").innerHTML = "<thead><tr><th></th><th>Prediction</th><th>80% range</th><th></th><th>Pace</th><th>Formula</th></tr></thead><tbody>" +
      rows.map(function (r) {
        var p = r.p;
        return "<tr" + (Math.abs(r.m / F - 1) < 0.01 ? ' class="hl"' : "") + "><td>" + r.name + "</td><td>" + hms(p.c) + "</td><td>" +
          hms(p.lo) + "–" + hms(p.hi) + "</td><td>" + bar(p) + "</td><td>" + hms(p.c / (r.m / MI)) + '/mi</td><td class="dim">' + hms(p.raw) + "</td></tr>";
      }).join("") + "</tbody>";

    var est = estimates(I, F), P = pool(est), fname = dname(F);
    var used = est.filter(function (e, i) { return P.w[i] > 0; });
    $("byH").textContent = "What each input says about the " + fname + at;
    $("by").innerHTML = "<thead><tr><th>Input</th><th>Prediction</th><th>80% range</th><th>Weight</th></tr></thead><tbody>" +
      est.map(function (e, i) {
        var s = summarize(e.per);
        return "<tr><td>" + e.name + "</td><td>" + hms(s.c) + "</td><td>" + hms(s.lo) + "–" + hms(s.hi) + "</td><td>" + (P.w[i] ? Math.round(100 * P.w[i]) + "%" : "comparison only") + "</td></tr>";
      }).join("") + (est.length > 1 ? '<tr class="hl"><td>Pooled</td><td>' + hms(P.c) + "</td><td>" + hms(P.lo) + "–" + hms(P.hi) + "</td><td></td></tr>" : "") + "</tbody>";

    // VDOT of the pooled focus prediction drives the Daniels paces; LT2 (or the pooled 12.5 km) drives the HRV4Training zones
    var V = vdot(F, P.c), Vlo = vdot(F, P.hi), Vhi = vdot(F, P.lo);
    var lt2 = I.lt2 ? mean(convert(I.lt2 * 10000 / MI, 10000, I.pe, tg)) / (10000 / MI)
                    : pool(estimates(I, 12500)).c / (12500 / MI);
    var lt2r = pool(estimates(I, 12500));
    $("tiles").innerHTML = [
      [fname + at, hms(P.c), "80% range " + hms(P.lo) + "–" + hms(P.hi) + " · " + hms(P.c / (F / MI)) + "/mi"],
      ["VDOT" + at, V.toFixed(1), "80% range " + Vlo.toFixed(1) + "–" + Vhi.toFixed(1)],
      ["LT2 pace" + at, hms(lt2) + "/mi", I.lt2 ? "your LT2, converted" : "predicted 12.5 km pace · " + hms(lt2r.lo / (12500 / MI)) + "–" + hms(lt2r.hi / (12500 / MI))],
      ["Inputs used", String(used.length), used.map(function (e) { return e.name.split(" · ")[0]; }).join(", ")]
    ].map(function (t) {
      return '<div class="tile"><div class="tl">' + t[0] + '</div><div class="tv">' + t[1] + '</div><div class="ts">' + t[2] + "</div></div>";
    }).join("");

    $("paceH").textContent = "Training paces" + at + " (per mile)";
    $("danH").textContent = "Daniels, from VDOT " + V.toFixed(1);
    var mara = rows[rows.length - 1].p.c / (MARA / MI);
    $("dan").innerHTML = "<tbody>" + [
      ["Easy", hms(paceAt(V, 0.70)) + "–" + hms(paceAt(V, 0.62))],
      ["Marathon", hms(mara)],
      ["Threshold", hms(paceAt(V, 0.88))],
      ["Interval", hms(paceAt(V, 0.98))]
    ].map(function (r) { return "<tr><td>" + r[0] + "</td><td>" + r[1] + "</td></tr>"; }).join("") + "</tbody>";
    var z = function (f) { return hms(lt2 * f); };
    $("hrvH").textContent = "HRV4Training zones, from LT2 " + hms(lt2) + "/mi";
    $("hrv").innerHTML = "<tbody>" + [
      ["Z1 Active recovery", "slower than " + z(1.30)],
      ["Z2 Endurance", z(1.30) + "–" + z(1.15)],
      ["Z3 Tempo", z(1.15) + "–" + z(1.05)],
      ["Z4 Threshold", z(1.05) + "–" + z(0.95)],
      ["Z5 VO2max", z(0.95) + "–" + z(0.90)],
      ["Z6 Anaerobic", "faster than " + z(0.90)]
    ].map(function (r) { return "<tr><td>" + r[0] + "</td><td>" + r[1] + "</td></tr>"; }).join("") + "</tbody>";
  }

  document.querySelector("main").addEventListener("input", compute);
  document.querySelector("main").addEventListener("change", compute);
  compute();
})();
</script>
</body>
</html>
"""
