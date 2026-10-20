"""Build the GitHub Pages calendar (docs/index.html) from plan.csv, data/runs.csv and data/laps.csv.

Usage: python scripts/build_site.py   (run scripts/analyze.py first)
"""
import calendar, csv, html, itertools
from collections import defaultdict
from datetime import date, datetime, timedelta

from config import AET, CROSS_NAMES, LT, ROOT, TZ, ZONE_NAMES as ZONE_COLS, ZONES, next_race, zone_range
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
                                "zones": [0.0] * len(ZONE_COLS), "runs": [], "manual": False, "hist": {}})
    traces = {}
    if (ROOT / "data" / "trace.csv").exists():
        with open(ROOT / "data" / "trace.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                col = lambda k: [None if x == "-" else int(x) for x in r[k].split()]
                traces[(r["date"], r["start"])] = {"bin": int(r["bin_secs"]), "hr": col("hr"),
                                                   "gap": col("gap_secs"), "pace": col("pace_secs")}
    hist = {}
    if (ROOT / "data" / "hr_hist.csv").exists():
        with open(ROOT / "data" / "hr_hist.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                hist[(r["date"], r["start"])] = {int(b): int(c) for b, c in
                                                 (p.split(":") for p in r["bpm_secs"].split())}
    with open(ROOT / "data" / "runs.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = days[date.fromisoformat(r["date"])]
            mi = float(r["miles"])
            d["miles"] += mi
            d["secs"] += secs(r["pace"]) * mi
            d["elev"] += int(r["elev_ft"] or 0)
            d["runs"].append({**r, "laps": laps.get((r["date"], r["start"]), []),
                              "trace": traces.get((r["date"], r["start"]))})
            d["load"] = d.get("load", 0) + int(r["load"] or 0)
            if r["fit"].endswith("(manual)"):
                d["manual"] = True
            elif r["hr_secs"]:
                n = int(r["hr_secs"])
                d["hr_secs"] += n
                d["hr_sum"] += int(r["avg_hr"]) * n
                d["zones"] = [z + float(r[c]) * n / 100 for z, c in zip(d["zones"], ZONE_COLS)]
                for b, c in hist.get((r["date"], r["start"]), {}).items():
                    d["hist"][b] = d["hist"].get(b, 0) + c
    return days


_cross = None


def cross_on(d):
    """HTML lines for the hikes and XC skis recorded on day d (data/cross.csv; load counts, miles don't)."""
    global _cross
    if _cross is None:
        _cross = defaultdict(list)
        path = ROOT / "data" / "cross.csv"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                for c in csv.DictReader(f):
                    _cross[date.fromisoformat(c["date"])].append(c)
    out = ""
    for c in _cross.get(d, []):
        s = int(c["secs"])
        bits = [f"<b>{CROSS_NAMES[c['kind']]}</b>", f"{fmt_mi(float(c['miles']))} mi" if float(c["miles"]) else "",
                f"{s // 3600}:{s % 3600 // 60:02d}", f"{c['avg_hr']} bpm" if c["avg_hr"] else "no HR", f"load {c['load']}"]
        out += f'<div class="act">✓ {" · ".join(b for b in bits if b)}</div>'
    return out


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


HIST_TAIL_SECS = 60            # trim each tail of the curve by up to this much time (dropouts, spikes)
HIST_KERNEL = [1, 2, 3, 2, 1]  # triangular smoothing over 5 bpm


def zone_anchors():
    """bpm where each zone's color is pure: interior zones at their midpoint, the open-ended
    first/last zones half a neighbor's width past the boundary. Colors blend in between."""
    mids = [(lo + hi) / 2 for _, lo, hi in ZONES[1:-1]]
    first = ZONES[1][1] - (ZONES[1][2] - ZONES[1][1]) / 2
    last = ZONES[-1][1] + (ZONES[-2][2] - ZONES[-2][1]) / 2
    return [first] + mids + [last]


_grad_ids = itertools.count(1)
M_PER_MI_ = 1609.344
GAP_FLOOR = 12 * 60  # sec/mi at the bottom of every week-view GAP panel; slower points are drawn on it
GAP_SMOOTH_SECS = 15  # +/- seconds of averaging for running GAP points
SPEED_SIMILAR = 0.2   # speed averages only with neighbors within 20% of it
WALK_PACE = 805      # sec/mi (~13:25); slower points are treated as walking


def hr_color(bpm):
    """Continuous zone color for a heart rate: CSS color-mix between the two nearest zone anchors."""
    anc = zone_anchors()
    if bpm <= anc[0]:
        return "var(--z1)"
    for i in range(1, len(anc)):
        if bpm <= anc[i]:
            t = (bpm - anc[i - 1]) / (anc[i] - anc[i - 1])
            return f"color-mix(in srgb, var(--z{i + 1}) {100 * t:.0f}%, var(--z{i}))"
    return f"var(--z{len(anc)})"


def hr_bar(counts, zones, total, steps=20):
    """Month view bar: the run's heart rates sorted low to high, left to right, in continuous color."""
    if not counts:
        return zone_bar(zones, total)
    n, acc, stops, k = sum(counts.values()), 0, [], 0
    for bpm in sorted(counts):  # color at the middle of each 1/steps slice of time
        acc += counts[bpm]
        while k < steps and acc >= (k + .5) / steps * n:
            stops.append(f"{hr_color(bpm)} {100 * (k + .5) / steps:.1f}%")
            k += 1
    title = " · ".join(f"Z{i + 1} {100 * z / total:.0f}%" for i, z in enumerate(zones)) if total else ""
    return f'<span class="zbar" title="{title}" style="background:linear-gradient(90deg, {", ".join(stops)})"></span>'


def hr_curve(counts, W=420, H=64, compact=False):
    """Smoothed HR distribution as inline SVG: filled by zone, dashed AeT/LT lines, own-peak scaling,
    x-axis trimmed to the run's own HR span."""
    if not counts:
        return ""
    def edge(bpms):  # first bpm, walking in from one end, where the time beyond it reaches the tail limit
        acc = 0
        for b in bpms:
            acc += counts[b]
            if acc >= HIST_TAIL_SECS:
                return b
        return bpms[-1]
    lo, hi = edge(sorted(counts)), edge(sorted(counts, reverse=True))
    lo, hi = min(lo, hi), max(lo, hi)
    if hi - lo < 10:  # keep very narrow runs readable
        mid = (lo + hi) // 2
        lo, hi = mid - 5, mid + 5
    r = len(HIST_KERNEL) // 2
    sm = {b: sum(k * counts.get(b + j - r, 0) for j, k in enumerate(HIST_KERNEL)) / sum(HIST_KERNEL)
          for b in range(lo, hi + 1)}
    top, ph = max(sm.values()) or 1, H - (0 if compact else 14)
    gid = f"hrg{next(_grad_ids)}"
    X = lambda b: (b - lo) / (hi - lo) * W
    Y = lambda v: ph - v / top * (ph - 2)
    parts = []
    for zi, (name, zlo, zhi) in enumerate(ZONES, 1):
        a, b = max(zlo, lo), min(zhi, hi + 1)  # zone spans [a, b) in bpm
        if a >= b:
            continue
        mins = sum(n for bpm, n in counts.items() if zlo <= bpm < zhi) / 60
        xs = [max(lo, a - .5)] + list(range(a, b)) + [min(hi, b - .5)]
        val = lambda x: sm[min(hi, max(lo, round(x)))]
        d = f"M{X(xs[0]):.1f},{ph} " + " ".join(f"L{X(x):.1f},{Y(val(x)):.1f}" for x in xs) + f" L{X(xs[-1]):.1f},{ph}Z"
        parts.append(f'<path class="hit" d="{d}"><title>{html.escape(name)} {html.escape(zone_range(zlo, zhi))}: '
                     f'{mins:.0f} min</title></path>')
    # one continuous shape for the fill (the per-zone shapes above are invisible hover targets)
    parts.insert(0, f'<path fill="url(#{gid})" d="M0,{ph} ' + " ".join(f"L{X(x):.1f},{Y(sm[x]):.1f}" for x in range(lo, hi + 1))
                 + f' L{W},{ph}Z"/>')
    labels = [(0, str(lo), "start"), (W, str(hi), "end")]  # min/max always; threshold labels yield to them
    for v, lab in ((AET, f"AeT {AET}"), (LT, f"LT {LT}")):
        if lo < v < hi:
            parts.append(f'<line class="thr" x1="{X(v):.1f}" x2="{X(v):.1f}" y1="0" y2="{ph}"/>')
            if all(abs(X(v) - lx) > 45 for lx, _, _ in labels):
                labels.append((X(v), lab, "middle"))
    if not compact:
        parts += [f'<text x="{x:.1f}" y="{H - 2}" text-anchor="{a}">{t}</text>' for x, t, a in labels]
    total = sum(counts.values()) / 60
    anchors = zone_anchors()  # continuous color: gradient in bpm, positioned in this run's x scale
    stops = "".join(f'<stop offset="{(b - anchors[0]) / (anchors[-1] - anchors[0]):.3f}" style="stop-color:var(--z{i})"/>'
                    for i, b in enumerate(anchors, 1))
    grad = (f'<defs><linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{X(anchors[0]):.1f}" y1="0" '
            f'x2="{X(anchors[-1]):.1f}" y2="0">{stops}</linearGradient></defs>')
    return (f'<svg class="hrc" viewBox="0 0 {W} {H}" role="img" aria-label="Heart rate distribution, '
            f'{lo}–{hi} bpm, {total:.0f} min">{grad}<line class="base" x1="0" x2="{W}" y1="{ph}" y2="{ph}"/>'
            f'{"".join(parts)}</svg>')


def _mmss(sec):
    return f"{int(sec) // 60}:{int(sec) % 60:02d}"


def run_trace(run, W=640):
    """HR and grade-adjusted pace vs time as two stacked panels sharing the time axis, with lap boundaries.
    Hover data rides on data- attributes for the page script."""
    t = run.get("trace")
    if not t or not any(t["hr"]):
        return ""
    n, b = len(t["hr"]), t["bin"]
    # GAP = speed x grade factor, each smoothed over +/- GAP_SMOOTH_SECS among running neighbors.
    # Speed only averages with neighbors within SPEED_SIMILAR of it (edge-preserving), so GPS jitter on
    # steady running is calmed while a sprint's plateau stays separate from its ramp-up and the walks.
    # Walking points stay raw.
    running = [bool(pc) and pc < WALK_PACE for pc in t["pace"]]
    fac = [pc / g if pc and g else None for pc, g in zip(t["pace"], t["gap"])]
    spd = [M_PER_MI_ / pc if pc else None for pc in t["pace"]]
    r = GAP_SMOOTH_SECS // b
    gs = []
    for i in range(n):
        if not fac[i] or not running[i]:
            gs.append(t["gap"][i])
            continue
        near = [k for k in range(max(0, i - r), min(n, i + r + 1)) if fac[k] and running[k]]
        f = sum(fac[k] for k in near) / len(near)
        v = [spd[k] for k in near if abs(spd[k] - spd[i]) <= SPEED_SIMILAR * spd[i]]
        gs.append(M_PER_MI_ / (sum(v) / len(v) * f))
    hv = [h for h in t["hr"] if h]
    gv = sorted(g for g in gs if g)
    hlo, hhi = min(hv) - 3, max(hv) + 3
    ghi = GAP_FLOOR  # slowest shown; anything slower runs off the bottom
    glo = min(min(gv), ghi - 120) // 30 * 30 if gv else ghi - 240  # fastest point sets the top
    ml, mr = 40, 8
    H1, H2, gapy, axh = 78, 56, 10, 16
    top2 = H1 + gapy
    H = top2 + H2 + axh
    pw = W - ml - mr
    X = lambda i: ml + (i + .5) / n * pw
    Yh = lambda h: 4 + (hhi - h) / (hhi - hlo) * (H1 - 8)
    Yg = lambda g: top2 + 4 + (g - glo) / (ghi - glo) * (H2 - 8)
    shown = [min(g, ghi) if g else None for g in gs]  # slower than the floor (walks) is drawn on it

    def line(vals, Y):
        out, pen = [], "M"
        for i, v in enumerate(vals):
            if v is None:
                pen = "M"
                continue
            out.append(f"{pen}{X(i):.1f},{Y(v):.1f}")
            pen = "L"
        return " ".join(out)

    gid = f"trg{next(_grad_ids)}"
    anc = zone_anchors()
    stops = "".join(f'<stop offset="{(bpm - anc[0]) / (anc[-1] - anc[0]):.3f}" style="stop-color:var(--z{i})"/>'
                    for i, bpm in enumerate(anc, 1))
    p = [f'<defs><linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="0" y1="{Yh(anc[0]):.1f}" x2="0" '
         f'y2="{Yh(anc[-1]):.1f}">{stops}</linearGradient>'
         f'<clipPath id="{gid}c"><rect x="{ml}" y="{top2}" width="{pw}" height="{H2}"/></clipPath></defs>']
    for y0, h in ((0, H1), (top2, H2)):
        p.append(f'<rect class="pnl" x="{ml}" y="{y0}" width="{pw}" height="{h}"/>')
    p.append(f'<text class="pl" x="{ml + 4}" y="11">HR</text><text class="pl" x="{ml + 4}" y="{top2 + 11}">GAP /mi</text>')
    for v in (round(hlo + 3), round(hhi - 3)):
        p.append(f'<text x="{ml - 5}" y="{Yh(v) + 3:.1f}" text-anchor="end">{v}</text>')
    for v in (glo, ghi):
        p.append(f'<text x="{ml - 5}" y="{Yg(v) + 3:.1f}" text-anchor="end">{_mmss(v)}</text>')
    for v, lab in ((AET, "AeT"), (LT, "LT")):
        if hlo < v < hhi:
            p.append(f'<line class="thr" x1="{ml}" x2="{W - mr}" y1="{Yh(v):.1f}" y2="{Yh(v):.1f}"/>'
                     f'<text x="{W - mr - 3}" y="{Yh(v) - 3:.1f}" text-anchor="end">{lab} {v}</text>')
    cum, lap_marks = 0, []
    for l in run["laps"][:-1]:
        cum += int(l["secs"])
        x = ml + cum / (n * b) * pw
        lap_marks.append(cum)
        p.append(f'<line class="lap" x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{top2 + H2}"/>')
    mins = n * b / 60
    step = 10 if mins <= 90 else 20
    for mm in range(0, int(mins) + 1, step):
        x = ml + mm * 60 / (n * b) * pw
        p.append(f'<text x="{x:.1f}" y="{H - 3}" text-anchor="middle">{mm}{" min" if mm == 0 else ""}</text>')
    p.append(f'<path class="gl" clip-path="url(#{gid}c)" d="{line(shown, Yg)}"/>')
    p.append(f'<path class="hl" stroke="url(#{gid})" d="{line(t["hr"], Yh)}"/>')
    p.append(f'<line class="guide" y1="0" y2="{top2 + H2}" style="display:none"/>')
    enc = lambda vals: " ".join("-" if v is None else str(round(v)) for v in vals)
    data = (f'data-bin="{b}" data-hr="{enc(t["hr"])}" data-gap="{enc(gs)}" data-pace="{enc(t["pace"])}" '
            f'data-laps="{" ".join(map(str, lap_marks))}" data-l="{ml}" data-pw="{pw}"')
    return (f'<svg class="trace" viewBox="0 0 {W} {H}" {data} role="img" '
            f'aria-label="Heart rate and grade-adjusted pace over {mins:.0f} min">{"".join(p)}</svg>')


def zone_text(zones, total):
    return " · ".join(f'<span class="zt"><i class="z{i + 1}"></i>{100 * z / total:.0f}%</span>'
                      for i, z in enumerate(zones)) if total else ""


def plan_html(p):
    mi = float(p["miles"])
    label = TYPES.get(p["type"], p["type"])
    sess = f'<div class="sess">{html.escape(p["session"])}</div>' if p["session"] != label else ""
    return (f'<div class="plan"><span class="tag">{label}</span>'
            f'{f" <b>{fmt_mi(mi)}</b> mi" if mi else ""}{sess}</div>')


def act_summary(a, load=False):
    hr = round(a["hr_sum"] / a["hr_secs"]) if a["hr_secs"] else None
    bits = [f'<b>{fmt_mi(round(a["miles"], 1))}</b> mi', fmt_pace(a["secs"], a["miles"])]
    if hr:
        bits.append(f"{hr} bpm")
    if load and a.get("load"):
        bits.append(f'load {a["load"]}')
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
        parts.append(f'<div class="act">{act_summary(a)}{hr_curve(a["hist"], W=150, H=26, compact=True) or zone_bar(a["zones"], a["hr_secs"])}</div>')
    elif missed(d, plan):
        parts.append('<div class="act missed">not recorded</div>')
    parts.append(cross_on(d))
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
        body += f'<div class="act">{act_summary(a, load=True)} · {a["elev"]} ft'
        charts = "".join(run_trace(r) for r in a["runs"])
        body += charts or zone_bar(a["zones"], a["hr_secs"])
        body += "".join(laps_table(r) for r in a["runs"]) + "</div>"
    elif missed(d, plan):
        body += '<div class="act missed">not recorded</div>'
    body += cross_on(d)
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
    cross_on(wk[0])  # loads _cross
    wload = sum(act[d].get("load", 0) for d in wk if d in act) + sum(int(c["load"]) for d in wk for c in _cross.get(d, []))
    zsum = (f'<div class="zts">load {wload}</div>' if wload else "") + zsum
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
    subs = {"WALK": str(WALK_PACE),
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
main { max-width: 1240px; margin: 0 auto; padding: 0 16px 48px; }
header h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: -0.01em; }
.goal { color: var(--muted); margin: 0 0 16px; }
.goal b { color: var(--race); }
.legend { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; color: var(--muted);
  font-size: 12px; margin-bottom: 8px; }
.legend i, .zt i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px;
  vertical-align: -1px; }
/* site bar: always visible; page toolbars stick just below it */
:root { --topbar: 52px; }
.topbar { position: sticky; top: 0; z-index: 20; height: var(--topbar); background: var(--bg);
  display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
nav.tabs { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; flex: none; }
nav.tabs a { padding: 6px 16px; color: var(--muted); text-decoration: none; background: var(--card); font-size: 13px;
  font-weight: 500; }
nav.tabs a + a { border-left: 1px solid var(--line); }
nav.tabs a[aria-current="page"] { background: var(--ink); color: var(--bg); }
.racebar { margin-left: auto; text-align: right; font-size: 13px; color: var(--muted); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.racebar b { color: var(--ink); font-weight: 600; }
.racebar .cd { color: var(--race); font-weight: 600; }
header { margin-bottom: 14px; }
header h1 { margin: 0; }
header .sub { color: var(--muted); margin: 2px 0 0; font-size: 13px; }
@media (max-width: 520px) { .racebar .rdate { display: none; } }
"""


def tabs(current):
    """The site bar: page tabs plus the next race and a live countdown (filled in the browser, so it stays
    right between site builds)."""
    links = [("index.html", "Calendar"), ("fitness.html", "Fitness")]
    nav = '<nav class="tabs">' + "".join(
        f'<a href="{h}"' + (' aria-current="page"' if h == current else "") + f">{t}</a>"
        for h, t in links) + "</nav>"
    nr = next_race()
    race = ""
    if nr:
        goal = f' · goal {fmt_clock(nr["goal_secs"])}' if nr["goal_secs"] else ""
        race = (f'<div class="racebar"><b>{html.escape(nr["name"])}</b>'
                f'<span class="rdate"> · {nr["date"]:%a %b} {nr["date"].day}{goal}</span> · '
                f'<span class="cd" data-race="{nr["date"].isoformat()}"></span></div>'
                '<script>(function () { var c = document.querySelector(".racebar .cd"), n = new Date(),'
                ' t = new Date(n.getFullYear(), n.getMonth(), n.getDate()), r = new Date(c.dataset.race + "T00:00:00"),'
                ' d = Math.round((r - t) / 864e5);'
                ' c.textContent = d > 1 ? d + " days" : d === 1 ? "tomorrow" : d === 0 ? "race day" : "done"; })();</script>')
    return f'<div class="topbar">{nav}{race}</div>'


def fmt_clock(secs):
    secs = round(secs)
    h, m, s = secs // 3600, secs // 60 % 60, secs % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Training Calendar</title>
<style>
%%BASE_CSS%%.bar { position: sticky; top: var(--topbar); z-index: 5; background: var(--bg); padding: 10px 0;
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
#monthly .day { cursor: pointer; }
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
.hrc { display: block; width: 100%; max-width: 420px; height: auto; margin-top: 6px; overflow: visible; }
.hrc text { font-size: 10px; fill: var(--muted); }
.hrc .thr { stroke: var(--ink); stroke-width: 1; stroke-dasharray: 2 2; opacity: .5; }
.hrc .base { stroke: var(--line); }
.trace { display: block; width: 100%; max-width: 640px; height: auto; margin-top: 8px; touch-action: pan-y; }
.trace text { font-size: 10px; fill: var(--muted); font-variant-numeric: tabular-nums; }
.trace .pl { font-weight: 600; fill: var(--ink); opacity: .7; }
.trace .pnl { fill: none; stroke: var(--line); }
.trace .hl { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.trace .gl { fill: none; stroke: var(--s1); stroke-width: 1.5; stroke-linejoin: round; }
.trace .thr { stroke: var(--ink); stroke-width: 1; stroke-dasharray: 2 2; opacity: .45; }
.trace .lap { stroke: var(--muted); stroke-width: 1; opacity: .35; }
.trace .guide { stroke: var(--ink); stroke-width: 1; opacity: .5; }
.ttip { position: fixed; z-index: 20; pointer-events: none; background: var(--card); color: var(--ink);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; font-size: 12px; line-height: 1.45;
  box-shadow: 0 4px 14px rgba(0,0,0,.18); font-variant-numeric: tabular-nums; display: none; }
.hrc .hit { fill: transparent; } .hrc .hit:hover { fill: var(--ink); fill-opacity: .12; }
.f1 { fill: var(--z1); } .f2 { fill: var(--z2); } .f3 { fill: var(--z3); } .f4 { fill: var(--z4); } .f5 { fill: var(--z5); }
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
%%TABS%%
<header>
  <h1>Training Calendar</h1>
  <p class="sub">Plan vs. completed runs</p>
  <div class="legend" style="margin-top:10px">%%LEGEND%%</div>
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
<footer>Plan vs. completed runs. Click a day or week total to open that week. Updated %%BUILT%%.</footer>
</main>
<script>
(function () {
  // hover readout on run traces (HR / grade-adjusted pace vs time)
  var tip = document.createElement("div"); tip.className = "ttip"; document.body.appendChild(tip);
  function mmss(s) { s = Math.round(s); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); }
  document.querySelectorAll("svg.trace").forEach(function (svg) {
    var arr = function (k) { return svg.dataset[k].split(" ").map(function (v) { return v === "-" ? null : +v; }); };
    var hr = arr("hr"), gap = arr("gap"), pace = arr("pace"), bin = +svg.dataset.bin;
    var laps = svg.dataset.laps ? svg.dataset.laps.split(" ").map(Number) : [];
    var L = +svg.dataset.l, PW = +svg.dataset.pw, guide = svg.querySelector(".guide");
    svg.addEventListener("pointermove", function (ev) {
      var r = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal;
      var x = (ev.clientX - r.left) * vb.width / r.width;
      var i = Math.floor((x - L) / PW * hr.length);
      if (i < 0 || i >= hr.length) { guide.style.display = "none"; tip.style.display = "none"; return; }
      var gx = L + (i + .5) / hr.length * PW, t = i * bin;
      guide.setAttribute("x1", gx); guide.setAttribute("x2", gx); guide.style.display = "";
      var lap = 1; laps.forEach(function (c) { if (t >= c) lap++; });
      tip.innerHTML = "<b>" + mmss(t) + "</b> · lap " + lap + "<br>HR " + (hr[i] || "—") +
        "<br>GAP " + (gap[i] ? mmss(gap[i]) + "/mi" + (pace[i] >= %%WALK%% ? " (walking)" : "") : "— (stopped)") + (pace[i] ? "<br>Pace " + mmss(pace[i]) + "/mi" : "");
      tip.style.display = "block";
      var w = tip.offsetWidth, h = tip.offsetHeight, tx = ev.clientX + 14, ty = ev.clientY + 14;
      if (tx + w > innerWidth - 8) tx = ev.clientX - w - 14;
      if (ty + h > innerHeight - 8) ty = ev.clientY - h - 14;
      tip.style.left = tx + "px"; tip.style.top = ty + "px";
    });
    svg.addEventListener("pointerleave", function () { guide.style.display = "none"; tip.style.display = "none"; });
  });

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
  // a click on any day or week total in the month view opens that week
  document.querySelectorAll("#monthly .week[data-week]").forEach(function (el) {
    el.onclick = function (e) {
      if (e.target.closest("a, summary, details")) return;
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
