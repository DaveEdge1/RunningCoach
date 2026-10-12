"""Fitness tab (docs/fitness.html): volume, training load, aerobic efficiency, zones, race fitness.

Built from data/runs.csv by scripts/build_site.py.
"""
import csv, html, json, math, re
from datetime import date, timedelta
from pathlib import Path

from analyze import EF_HR_BAND, EF_MAX_GRADE, EF_MIN_SECS
from build_site import BASE_CSS, tabs
from config import (AET, DANIELS_FLOOR_FT, DANIELS_SPM, HOME_ELEV_FT, HOME_NAME, LT, MANUAL_LOAD_PER_MIN, ROOT,
                    ZONE_NAMES as ZONE_COLS, ZONES, convert_range, convert_time, load_races, next_race,
                    zone_range)

CTL_DAYS, ATL_DAYS = 42, 7
TREND_DAYS = 28
EF_ELEV_TOL_FT = 1000  # pace-at-AeT uses only runs within this of home elevation
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
        near_home = not r.get("elev_avg_ft") or abs(int(r["elev_avg_ft"]) - HOME_ELEV_FT) <= EF_ELEV_TOL_FT
        if r["aet_pace_secs"] and r["date"] in by_day and near_home:
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
    """Completed races (races.csv or plan type race) and time trials (plan session named so), with results."""
    listed = {r["date"]: r for r in load_races()}
    out = []
    for r in runs:
        d = date.fromisoformat(r["date"])
        p, rc = plan.get(d), listed.get(d)
        is_race = rc is not None or (p is not None and p["type"] == "race")
        if not (is_race or (p is not None and "time trial" in p["session"].lower())):
            continue
        m, s = r["pace"].split(":")
        secs = (int(m) * 60 + int(s)) * float(r["miles"])
        meters = float(r["miles"]) * M_PER_MI
        elev = int(r["elev_avg_ft"]) if r.get("elev_avg_ft") else (rc or {}).get("elev")
        name = rc["name"] if rc else f"{dist_label(meters)} {'race' if is_race else 'time trial'}"
        out.append({"d": d, "name": name, "meters": meters, "secs": secs, "elev": elev,
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


def hms_range(lo, hi):
    a, b = hms(lo), hms(hi)
    return a if a == b else f"{a}–{b}"


def panel(title, big, rows, note=""):
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    return (f'<div class="panel"><h3>{title}</h3>' + (f'<div class="big">{big}</div>' if big else "")
            + f'<table class="t kv">{body}</table>' + (f'<p class="note">{note}</p>' if note else "") + "</div>")


def next_time_trial(plan, today, before):
    """(date, meters, label) of the next planned session named "time trial" before `before`, if its distance parses."""
    for d in sorted(plan):
        sess = plan[d]["session"]
        if today <= d < before and "time trial" in sess.lower():
            m = re.search(r"(\d+(?:\.\d+)?)\s*(km|k|mi|mile)\b", sess, re.I)
            if m:
                n, unit = float(m.group(1)), m.group(2).lower()
                return d, n * (M_PER_MI if unit.startswith("mi") else 1000), sess
    return None


def mmss_range(lo, hi):
    a, b = mmss(lo), mmss(hi)
    return a if a == b else f"{a}–{b}"


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
        tiles.append(tile(f"Pace at AeT ({AET} bpm)", f"{mmss(t_now)}/mi",
                          f"at {HOME_ELEV_FT:,} ft ({HOME_NAME}) · 28-day trend, flat steady running",
                          (signed((t_now - t_ago) / 1, "{:.0f}", good_up=False) + " s/mi vs. 4 weeks ago") if t_ago else ""))
    best = rc[-1] if rc else None

    def src_elev(r):  # no GPS altitude or races.csv elevation: assume home
        return r["elev"] if r["elev"] is not None else HOME_ELEV_FT

    def vdot_range(r, to_ft):  # result re-scored as if run at to_ft, across the altitude methods
        lo, hi = convert_range(r["secs"], r["meters"], src_elev(r), to_ft)
        return vdot(r["meters"], hi), vdot(r["meters"], lo)  # slower time -> lower VDOT

    def vrange(lo, hi):
        return f"{lo:.1f}" if f"{lo:.1f}" == f"{hi:.1f}" else f"{lo:.1f}–{hi:.1f}"

    if best:
        # sea-level VDOT: Daniels (average of its s/mi range) averaged with NCAA
        sl = {k: vdot(best["meters"], t) for k, t in convert_time(best["secs"], best["meters"], src_elev(best), 0).items()}
        daniels = [v for k, v in sl.items() if k.startswith("Daniels")]
        sl_vdot = (sum(daniels) / len(daniels) + sl["NCAA"]) / 2
        tiles.append(tile("VDOT (sea level)", f"{sl_vdot:.1f}",
                          f"{html.escape(best['name'])}, {best['d']:%b} {best['d'].day}, at {ft(src_elev(best))} · "
                          f"Daniels/NCAA average"))

    spm = "–".join(f"{k:g}" for k in DANIELS_SPM)
    methods_note = (f"Altitude is converted two published ways and shown as a range: Daniels "
                    f"({spm} s/mi per 1,000 ft above {DANIELS_FLOOR_FT:,} ft) "
                    f"and the NCAA altitude tables (% by altitude and distance).")

    # next race
    nr = next_race(today)
    next_html = ""
    if nr:
        days = (nr["date"] - today).days
        when = f"{nr['date']:%a %b} {nr['date'].day}, {nr['date'].year} · " + (f"{days} days" if days else "today")
        mi = nr["meters"] / M_PER_MI
        facts = [("Date", when), ("Location", html.escape(nr["location"]) or "—"),
                 ("Elevation", ft(nr["elev"]) or "—"),
                 ("Distance", f"{nr['meters'] / 1000:g} km ({mi:.2f} mi)"),
                 ("Course", html.escape(nr["notes"]) or "—")]
        panels = [panel("Race", None, facts)]
        cur = None
        if best and nr["elev"] is not None:
            t = predict(best["vdot"], nr["meters"])
            by = convert_time(t, nr["meters"], src_elev(best), nr["elev"])
            cur = (min(by.values()), max(by.values()))
            panels.append(panel("Current fitness", hms_range(*cur),
                                [("Pace", f"{mmss_range(cur[0] / mi, cur[1] / mi)}/mi"),
                                 ("Basis", f"{html.escape(best['name'])}, {best['d']:%b} {best['d'].day}")]))
            age = (today - best["d"]).days
            parts = " · ".join(f"{k}: {hms(v)}" for k, v in by.items())
            basis = (f'<p class="note"><b>Current fitness</b> (no projected training gains): {html.escape(best["name"])} on '
                     f'{best["d"]:%b} {best["d"].day} ({age} days ago), {hms(best["secs"])} at {ft(best["elev"]) or "unknown elevation"}, '
                     f'VDOT {best["vdot"]:.1f}. Derived as: that result → VDOT → equivalent {dist_label(nr["meters"])} at '
                     f'{ft(src_elev(best))} ({hms(t)}) → converted to {ft(nr["elev"])}: {parts}.</p>')
        else:
            reason = "this race has no elevation in races.csv" if best else "no completed race or time trial yet"
            basis = f'<p class="note">No estimate: {reason}.</p>'

        g = nr["goal_secs"]
        if g:
            items = [("Pace", f"{mmss(g / mi)}/mi" + (' <span class="dim">(from goal time)</span>' if nr["goal_from"] == "time" else "")),
                     ("Needs", f"VDOT {vdot(nr['meters'], g):.1f} at {ft(nr['elev']) or 'race elevation'}")]
            if nr["goal_from"] == "pace":
                items.insert(0, ("Time", f'{hms(g)} <span class="dim">(from goal pace)</span>'))
            if cur:
                lo, hi = cur
                items.append(("Vs. now", "within current range" if lo <= g <= hi else
                              f"{lo - g:.0f}–{hi - g:.0f} s faster" if g < lo else f"{g - hi:.0f}–{g - lo:.0f} s slower"))
            if nr["elev"] is not None:
                hlo, hhi = convert_range(g, nr["meters"], nr["elev"], HOME_ELEV_FT)
                items.append((f"At {HOME_NAME}", f"{hms_range(hlo, hhi)} · {mmss_range(hlo / mi, hhi / mi)}/mi"))
            panels.append(panel("Goal", hms(g), items))

            tt = next_time_trial(plan, today, nr["date"])
            if tt and cur and nr["elev"] is not None and best["d"] < tt[0]:
                d_tt, m_tt, label = tt
                frac = (d_tt - best["d"]).days / (nr["date"] - best["d"]).days
                v_goal = vdot(nr["meters"], g)
                on_track, full = [], []
                for k, t_now in by.items():  # per altitude method, used the same way in both directions
                    v_now = vdot(nr["meters"], t_now)
                    t_equiv = predict(v_now + frac * (v_goal - v_now), nr["meters"])  # linear in VDOT
                    back = lambda t: convert_time(t, nr["meters"], nr["elev"], HOME_ELEV_FT)[k]
                    on_track.append(predict(vdot(nr["meters"], back(t_equiv)), m_tt))
                    full.append(predict(vdot(nr["meters"], back(g)), m_tt))
                tdays = (d_tt - today).days
                tt_mi = m_tt / M_PER_MI
                panels.append(panel(
                    f"Checkpoint: {html.escape(label)}",
                    f'{hms_range(min(on_track), max(on_track))} <span class="dim">on track</span>',
                    [("Date", f"{d_tt:%a %b} {d_tt.day} · {tdays} days · at {HOME_NAME}"),
                     ("On-track pace", f"{mmss_range(min(on_track) / tt_mi, max(on_track) / tt_mi)}/mi"),
                     ("Goal-equivalent", f"{hms_range(min(full), max(full))} · "
                                         f"{mmss_range(min(full) / tt_mi, max(full) / tt_mi)}/mi")],
                    f"<b>On track</b>: steady progress from current fitness ({best['d']:%b} {best['d'].day}) to "
                    f"{hms(g)} on race day; {d_tt:%b} {d_tt.day} is {frac:.0%} of the way. "
                    f"<b>Goal-equivalent</b>: the {dist_label(m_tt)} you'd run at {HOME_NAME} if already at goal fitness."))

        next_html = (f'\n<section class="card next"><h2>Next race: {html.escape(nr["name"])}</h2>\n'
                     f'<div class="panels">{"".join(panels)}</div>\n{basis}\n</section>')

    race_html = ""
    if best:
        places = [(f"{HOME_NAME} ({HOME_ELEV_FT:,} ft)", HOME_ELEV_FT), ("Sea level", 0)]
        dists = list(DISTANCES[:2])
        if nr and all(abs(m - nr["meters"]) > 1 for m, _ in dists):
            dists.append((nr["meters"], dist_label(nr["meters"])))

        def at_elev(m, ft_):  # equivalent time range for distance m at elevation ft_
            return convert_range(predict(best["vdot"], m), m, src_elev(best), ft_)

        head = "<tr><th></th>" + "".join(f"<th>{n}</th>" for n, _ in places) + "</tr>"
        time_rows = "".join(f"<tr><td>{n}</td>" + "".join(f"<td>{hms_range(*at_elev(m, e))}</td>" for _, e in places)
                            + "</tr>" for m, n in dists)
        vs = [vdot_range(best, e) for _, e in places]  # (low, high) VDOT per place
        pace_fns = [("Easy", lambda v: f"{mmss(pace_at(v[1], 0.70))}–{mmss(pace_at(v[0], 0.62))}"),
                    ("Threshold", lambda v: mmss_range(pace_at(v[1], 0.88), pace_at(v[0], 0.88))),
                    ("Interval", lambda v: mmss_range(pace_at(v[1], 0.98), pace_at(v[0], 0.98)))]
        pace_rows = "".join(f"<tr><td>{label}</td>" + "".join(f"<td>{fn(v)}</td>" for v in vs) + "</tr>"
                            for label, fn in pace_fns)
        vdot_row = "<tr><td>VDOT</td>" + "".join(f"<td>{vrange(*v)}</td>" for v in vs) + "</tr>"
        rows = "".join(f"<tr><td>{r['d']:%b} {r['d'].day}</td><td>{html.escape(r['name'])}</td>"
                       f"<td>{r['meters'] / 1000:.2f} km</td><td>{hms(r['secs'])}</td>"
                       f"<td>{ft(r['elev'])}</td><td>{r['vdot']:.1f}</td><td>{vrange(*vdot_range(r, 0))}</td></tr>"
                       for r in reversed(rc))
        race_html = (
            '\n<section class="card"><h2>Race fitness</h2>\n'
            f'<p class="note">From your most recent race or time trial ({html.escape(best["name"])}, '
            f'{best["d"]:%b} {best["d"].day}, {ft(best["elev"]) or "elevation unknown"}). {methods_note}</p>\n'
            '<div class="grid2">\n'
            f'<div><h3>Equivalent times</h3><table class="t full"><thead>{head}</thead><tbody>{time_rows}</tbody></table></div>\n'
            f'<div><h3>Training paces (per mile)</h3><table class="t full"><thead>{head}</thead><tbody>{pace_rows}{vdot_row}</tbody></table></div>\n'
            '</div>\n'
            '<details><summary>Races and time trials</summary><div class="scroll"><table class="t full"><thead><tr><th>Date</th>'
            '<th>Event</th><th>Distance</th><th>Time</th><th>Elevation</th><th>VDOT</th><th>VDOT (sea level)</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></details>\n</section>')

    wk_rows = "".join(
        f"<tr><td>{date.fromisoformat(w['w']):%b} {date.fromisoformat(w['w']).day}{' (so far)' if w['partial'] else ''}</td>"
        f"<td>{w['mi']}</td><td>{w['runs']}</td><td>{w['load']}</td><td>{w['ctl'] if w['ctl'] is not None else ''}</td>"
        f"<td>{w['atl'] if w['atl'] is not None else ''}</td>" + "".join(f"<td>{z}%</td>" for z in w["z"]) + "</tr>"
        for w in reversed(weeks))

    data = {"daily": daily, "weeks": weeks, "eff": pts, "trend": trend, "today": today.isoformat(),
            "aet": AET, "zones": [f"{n} {zone_range(lo, hi)}" for n, lo, hi in ZONES],
            "breaks": [1, 3]}  # zone indexes below the LT1 (AeT) and LT2 (LT) boundaries
    first = date.fromisoformat(daily[0]["d"])
    about = (
        f"<p><b>Load</b> is HR-zone-weighted minutes: each minute in Z1 counts 1, Z2 counts 2, and so on up to Z5 = 5. "
        f"Runs without real HR count {MANUAL_LOAD_PER_MIN:g} per minute.</p>\n"
        f"<p><b>Fitness / fatigue</b> are exponentially weighted averages of daily load over {CTL_DAYS} and {ATL_DAYS} days. "
        f"History begins {first:%b} {first.day}, {first.year}, so both start at the average load of the first four weeks.</p>\n"
        "<p><b>Form</b> measures training fatigue only. Sleep, stress, heat, and illness aren't included, "
        "so read single runs with that in mind.</p>\n"
        f"<p><b>Pace at AeT</b> uses only seconds with HR {EF_HR_BAND[0]}–{EF_HR_BAND[1]} bpm, moving faster than walking, "
        f"on grades under {EF_MAX_GRADE:.0%}, scaled to exactly {AET} bpm. A run needs at least {EF_MIN_SECS // 60} minutes "
        f"of those seconds. The trend weights each run by how many seconds qualified over the past {TREND_DAYS} days. "
        f"Only runs within {EF_ELEV_TOL_FT:,} ft of {HOME_NAME} ({HOME_ELEV_FT:,} ft) count, so the number is a "
        f"{HOME_NAME}-altitude pace.</p>\n"
        f"<p><b>Zones</b> come from config.json: LT1 (AeT) {AET} bpm and LT2 (LT) {LT} bpm. "
        "The intensity chart puts wider breaks at those two thresholds.</p>\n"
        "<p><b>VDOT</b> is Jack Daniels' formula applied to your most recent race (races.csv, or a planned race) or time trial "
        "(a planned session named \"time trial\"). The next-race estimate uses that current fitness with no projected gains. "
        "Elevation comes from the run's own file.</p>\n"
        "<p><b>Altitude</b> is converted two published ways and shown as a range. "
        '<a href="https://news.vdoto2.com/2011/03/ask-a-coach-how-do-you-adjust-threshold-pace-at-altitude/">Daniels</a>: '
        f"paces slow {spm} s/mi per 1,000 ft above {DANIELS_FLOOR_FT:,} ft (15–18 s/mi at 7,000 ft). "
        '<a href="https://www.ustfccca.org/assets/ncaa-info/2009-outdoor-alt-adjust.pdf">NCAA 2009 altitude tables</a>: '
        "% added by venue altitude (0–7,703 ft) for the mile, 5K and 10K, interpolated by altitude and distance, and held at "
        "the nearest of those distances outside that span. Neither accounts for acclimatization.</p>")
    subs = {"BASE_CSS": BASE_CSS, "TABS": tabs("fitness.html"), "TILES": "".join(tiles), "RACE": race_html,
            "WEEK_ROWS": wk_rows, "DATA": json.dumps(data, separators=(",", ":")),
            "NEXT": next_html, "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}",
            "HOME_ELEV": f"{HOME_ELEV_FT:,}", "FRESH": str(FRESH), "TIRED": str(TIRED).replace("-", "−"), "AET": str(AET), "ABOUT": about,
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
.next .kv td:first-child { color: var(--muted); padding-right: 18px; } .next .kv td:last-child { text-align: left; }
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px 24px; margin-top: 8px; }
.panel h3 { margin: 0 0 2px; } .panel .big { font-size: 24px; font-weight: 600; margin: 2px 0 4px; } .panel .big .dim { font-size: 13px; font-weight: 400; }
.panel .kv td { padding: 2px 12px 2px 0; } .panel .note { margin: 6px 0 0; }
.est { display: flex; gap: 28px; align-items: flex-start; } .est small { font-size: 13px; font-weight: 400; color: var(--muted); margin-left: 2px; }
.next .note b { color: var(--ink); } .dim { color: var(--muted); font-size: 12px; }
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
%%NEXT%%

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

<section class="card"><h2>Aerobic efficiency: pace at %%AET%% bpm, at %%HOME_ELEV%% ft</h2>
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
