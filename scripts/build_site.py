"""Build the GitHub Pages calendar (docs/index.html) from plan.csv, data/runs.csv and data/laps.csv.

Usage: python scripts/build_site.py   (run scripts/analyze.py first)
"""
import calendar, csv, html, itertools, re
from collections import defaultdict
from datetime import date, datetime, timedelta

from config import AET, CROSS_NAMES, LT, ROOT, TZ, ZONE_NAMES as ZONE_COLS, ZONES, load_races, next_race, trail_laps, zone_range
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
                                "zones": [0.0] * len(ZONE_COLS), "runs": [], "manual": False, "hist": {},
                                "gap_secs": 0, "gap_mi": 0.0, "ghist": {}})
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
            if r["gap_secs"]:  # whole-run grade-adjusted time
                d["gap_secs"] += int(r["gap_secs"])
                d["gap_mi"] += mi
            t = traces.get((r["date"], r["start"]))
            if t:
                for k, c in gap_hist(t).items():
                    d["ghist"][k] = d["ghist"].get(k, 0) + c
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
TT_DIST = re.compile(r"(\d+(?:\.\d+)?)\s*(km|k|mi|mile)\b", re.I)  # distance in a time-trial session
GAP_FLOOR = 12 * 60  # sec/mi at the bottom of every week-view GAP panel; slower points are drawn on it
GAP_SMOOTH_SECS = 15  # +/- seconds of averaging for running GAP points
SPEED_SIMILAR = 0.2   # speed averages only with neighbors within 20% of it
WALK_PACE = 805      # sec/mi (~13:25); slower points are treated as walking
GAP_BUCKET = 5       # sec/mi per bin of the week view GAP curve


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


def smooth_gap(t):
    """GAP = speed x grade factor, each smoothed over +/- GAP_SMOOTH_SECS among running neighbors.
    Speed only averages with neighbors within SPEED_SIMILAR of it (edge-preserving), so GPS jitter on
    steady running is calmed while a sprint's plateau stays separate from its ramp-up and the walks.
    Walking points stay raw."""
    n, b = len(t["pace"]), t["bin"]
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
    return gs


def gap_hist(t):
    """Seconds at each GAP_BUCKET of grade-adjusted pace (running only, like the HR curve's bpm)."""
    out = {}
    for g, pc in zip(smooth_gap(t), t["pace"]):
        if g and pc and pc < WALK_PACE:
            k = round(g / GAP_BUCKET) * GAP_BUCKET
            out[k] = out.get(k, 0) + t["bin"]
    return out


def gap_curve(counts, W=420, H=64, compact=False):
    """Smoothed GAP distribution as inline SVG, the twin of hr_curve: faster to the right (harder, like
    higher HR), x-axis trimmed to the run's own span."""
    if not counts:
        return ""
    def edge(keys):
        acc = 0
        for k in keys:
            acc += counts[k]
            if acc >= HIST_TAIL_SECS:
                return k
        return keys[-1]
    lo, hi = edge(sorted(counts)), edge(sorted(counts, reverse=True))  # lo = fastest, hi = slowest
    lo, hi = min(lo, hi), max(lo, hi)
    if hi - lo < 40:  # keep very even runs readable
        mid = (lo + hi) // 2 // GAP_BUCKET * GAP_BUCKET
        lo, hi = mid - 20, mid + 20
    r = len(HIST_KERNEL) // 2
    ks = range(lo, hi + 1, GAP_BUCKET)
    sm = {k: sum(w * counts.get(k + (j - r) * GAP_BUCKET, 0) for j, w in enumerate(HIST_KERNEL)) / sum(HIST_KERNEL)
          for k in ks}
    top, ph = max(sm.values()) or 1, H - (8 if compact else 14)
    X = lambda k: (hi - k) / (hi - lo) * W
    Y = lambda v: ph - v / top * (ph - 2)
    path = f"M0,{ph} " + " ".join(f"L{X(k):.1f},{Y(sm[k]):.1f}" for k in reversed(ks)) + f" L{W},{ph}Z"
    # x-axis: a tick and label at each whole minute of pace (the middle of the span if none falls inside)
    mins_in = list(range((lo + 59) // 60 * 60, hi + 1, 60)) or [(lo + hi) // 2]
    edge_a = lambda x: "start" if x < 10 else "end" if x > W - 10 else "middle"
    labels = "".join(f'<line class="tick" x1="{X(m):.1f}" x2="{X(m):.1f}" y1="{ph}" y2="{ph + 1.5}"/>'
                     f'<text x="{X(m):.1f}" y="{H - 1}" text-anchor="{edge_a(X(m))}">'
                     f'{m // 60 if m % 60 == 0 and compact else _mmss(m)}</text>'  # compact: whole minutes, "9" = 9:00/mi
                     for m in mins_in)
    mins = sum(counts.values()) / 60
    return (f'<svg class="hrc gpc" viewBox="0 0 {W} {H}" role="img" aria-label="Grade-adjusted pace distribution, '
            f'{_mmss(hi)}–{_mmss(lo)} /mi, {mins:.0f} min"><title>GAP {_mmss(hi)}–{_mmss(lo)} /mi</title>'
            f'<line class="base" x1="0" x2="{W}" y1="{ph}" y2="{ph}"/><path class="gf" d="{path}"/>{labels}</svg>')


def run_trace(run, W=640):
    """HR and grade-adjusted pace vs time as two stacked panels sharing the time axis, with lap boundaries.
    Hover data rides on data- attributes for the page script."""
    t = run.get("trace")
    if not t or not any(t["hr"]):
        return ""
    n, b = len(t["hr"]), t["bin"]
    gs = smooth_gap(t)
    hv = [h for h in t["hr"] if h]
    gv = sorted(g for g in gs if g)
    hlo, hhi = min(hv) - 3, max(hv) + 3
    ghi = GAP_FLOOR  # slowest shown; anything slower runs off the bottom
    glo = min(min(gv), ghi - 120) // 30 * 30 if gv else ghi - 240  # fastest point sets the top
    ml, mr = 40, 8
    H1, H2, gapy, axh = 70, 70, 10, 16  # HR and GAP panels the same size
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
    gap = fmt_pace(a["gap_secs"], a["gap_mi"])  # GAP and HR side by side, equal weight
    bits = [f'<b>{fmt_mi(round(a["miles"], 1))}</b> mi',
            f"<b>{gap}</b> GAP" if gap else fmt_pace(a["secs"], a["miles"])]
    if hr:
        bits.append(f"<b>{hr}</b> bpm")
    if load and gap:
        bits.append(f'pace {fmt_pace(a["secs"], a["miles"])}')
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


DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------- month view: a traditional grid, fitted to the screen ----------

def month_cell(d, plan, act, month):
    p, a = plan.get(d), act.get(d)
    parts = [f'<span class="mn">{d.day}</span>']
    tt = p and "time trial" in p["session"].lower()
    if p and p["type"] != "off":
        mi = float(p["miles"])
        if p["type"] == "race" and d in _race_mi():  # the race distance, not the day's miles with warm-up
            mi = _race_mi()[d]
        elif tt:  # the distance in the session ("5K time trial"), like a race
            m = TT_DIST.search(p["session"])
            mi = round(float(m.group(1)) * (1 if m.group(2).lower().startswith("mi") else 1000 / M_PER_MI_), 1) if m else mi
        parts.append(f'<span class="mp"><span class="ml">{"Time trial" if tt else TYPES.get(p["type"], p["type"])}</span>'
                     f'{f" {fmt_mi(mi)}" if mi else ""}</span>')
    if a:
        parts.append(f'<span class="ma">✓ {fmt_mi(round(a["miles"], 1))}</span>')
    elif missed(d, plan):
        parts.append('<span class="ma miss">missed</span>')
    cross_on(d)  # loads _cross
    for c in _cross.get(d, []):
        parts.append(f'<span class="ma">✓ {CROSS_NAMES[c["kind"]].lower()}</span>')
    title = f' title="{html.escape(p["session"])}"' if p else ""
    cls = "mc" + (" other" if d.month != month else "") + (" t-race" if tt else f" t-{p['type']}" if p else "")
    return (f'<div class="{cls}" data-date="{d}" data-week="{d - timedelta(d.weekday())}"'
            f'{title}>{"".join(parts)}</div>')


def _race_mi(_c={}):
    if not _c:
        _c.update({r["date"]: round(r["meters"] / M_PER_MI_, 1) for r in load_races()})
    return _c


def month_html(year, month, plan, act):
    wks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    cells = []
    for wk in wks:
        cells += [month_cell(d, plan, act, month) for d in wk]
        pm, am = week_totals(wk, plan, act)
        tot = (f"<b>{fmt_mi(pm)}</b><small>plan</small>" if wk[0] > date.today() else
               f"<b>{fmt_mi(round(am, 1))}</b>" + (f"<small>/ {fmt_mi(pm)}</small>" if pm else "")) if pm or am else ""
        cells.append(f'<div class="mwk" data-week="{wk[0]}">{tot}</div>')
    head = "".join(f'<div class="mh">{n}</div>' for n in DAY_NAMES + ["Week"])
    return (f'<section class="view mview" data-view="month" data-key="{year}-{month:02d}" hidden>'
            f'<h2>{calendar.month_name[month]} {year}</h2>'
            f'<div class="mgrid" style="--rows:{len(wks)}">{head}{"".join(cells)}</div></section>')


# ---------- week view: one week, a card per day with its HR curve ----------

def week_cell(d, plan, act):
    p, a = plan.get(d), act.get(d)
    parts = [f'<div class="dnum"><span class="wd">{d:%a}</span> {d:%b} {d.day}</div>']
    if p:
        parts.append(plan_html(p))
    if a:
        parts.append(f'<div class="act">{act_summary(a)}{hr_curve(a["hist"], W=150, H=26, compact=True) or zone_bar(a["zones"], a["hr_secs"])}'
                     f'{gap_curve(a["ghist"], W=150, H=34, compact=True)}</div>')
    elif missed(d, plan):
        parts.append('<div class="act missed">not recorded</div>')
    parts.append(cross_on(d))
    return f'<div class="{day_classes(d, plan)}" data-date="{d}">{"".join(parts)}</div>'


def plan_week_label(wk0, plan, start):
    n = (wk0 - start).days // 7 + 1 if start else 0
    return f"Plan week {n} · " if start and start <= wk0 <= max(plan) else ""


def week_html(wk0, plan, act, start):
    wk = [wk0 + timedelta(i) for i in range(7)]
    pm, am = week_totals(wk, plan, act)
    zones = [sum(act[d]["zones"][i] for d in wk if d in act) for i in range(len(ZONE_COLS))]
    hr_total = sum(act[d]["hr_secs"] for d in wk if d in act)
    end = wk[-1]
    zsum = f'<div class="zts">{zone_text(zones, hr_total)}</div>' if hr_total else ""
    cross_on(wk[0])  # loads _cross
    wload = sum(act[d].get("load", 0) for d in wk if d in act) + sum(int(c["load"]) for d in wk for c in _cross.get(d, []))
    zsum = (f'<div class="zts">load {wload}</div>' if wload else "") + zsum
    head = (f'<div class="whead"><h2>{plan_week_label(wk0, plan, start)}{wk0:%b} {wk0.day} – {end:%b} {end.day}</h2>'
            f'<div class="wsum">{total_html(pm, am, wk0)}{zsum}</div></div>')
    days = "".join(f'<div class="mh">{n}</div>' for n in DAY_NAMES)
    return (f'<section class="view wview" data-view="week" data-key="{wk0}" hidden>{head}'
            f'<div class="head">{days}</div><div class="week" data-week="{wk0}">'
            f'{"".join(week_cell(d, plan, act) for d in wk)}</div></section>')


# ---------- day view: one day in full (traces, laps) ----------

def laps_table(run):
    laps = run["laps"]
    if len(laps) < 2:
        return ""
    trail = trail_laps().get(run["date"], set())
    tag = lambda l: ' <span class="trl">trail</span>' if int(l["lap"]) in trail else ""  # notes.md "trail lap N"
    rows = "".join(f'<tr><td>{l["lap"]}{tag(l)}</td><td>{l["miles"]}</td><td><b>{l["gap"]}</b></td>'
                   f'<td><b>{l["avg_hr"]}</b></td><td>{l["pace"]}</td><td>{l["max_hr"]}</td>'
                   f'<td>+{l["ascent_ft"]}/−{l["descent_ft"]}</td></tr>' for l in laps)
    return (f'<details class="lapd" open><summary>Laps <small>{len(laps)}</small></summary><table class="laps"><thead><tr>'
            f'<th>Lap</th><th>Mi</th><th>GAP</th><th>HR</th><th>Pace</th><th>Max HR</th><th>Ft</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></details>')


def day_html(d, plan, act, start):
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
    wk0 = d - timedelta(d.weekday())
    label = plan_week_label(wk0, plan, start).rstrip(" ·")
    return (f'<section class="view dview" data-view="day" data-key="{d}" hidden>'
            f'<div class="whead"><h2>{d:%A, %B} {d.day}, {d.year}</h2>'
            f'<div class="wsum zts">{label}</div></div>'
            f'<div class="wday {day_classes(d, plan)}" data-date="{d}"><div class="wbody">{body}</div></div></section>')


def main():
    plan, act = load_plan(), load_actuals()
    first, last = min(min(plan), min(act)), max(max(plan), max(act))
    start = plan_start(plan)
    months, y, m = [], first.year, first.month
    while (y, m) <= (last.year, last.month):
        months.append(month_html(y, m, plan, act))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    weeks, wk0 = [], first - timedelta(first.weekday())
    while wk0 <= last:
        weeks.append(week_html(wk0, plan, act, start))
        wk0 += timedelta(7)
    days = [day_html(first + timedelta(i), plan, act, start) for i in range((last - first).days + 1)]

    built = datetime.now(TZ)
    legend = "".join(f'<span class="tag t-{k}">{v}</span>' for k, v in TYPES.items() if k != "off")
    zleg = "".join(f'<span><i class="z{i + 1}"></i>{n} {zone_range(lo, hi)}</span>' for i, (n, lo, hi) in enumerate(ZONES))
    subs = {"WALK": str(WALK_PACE),
            "LEGEND": legend, "ZLEG": zleg, "VIEWS": "".join(months + weeks + days),
            "BUILT": f"{built:%b} {built.day}, {built:%Y %H:%M %Z}",
            "BASE_CSS": BASE_CSS, "TABS": tabs("index.html")}
    page = TEMPLATE
    for k, v in subs.items():
        page = page.replace(f"%%{k}%%", v)
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "index.html").write_text(page, encoding="utf-8")
    (ROOT / "docs" / ".nojekyll").touch()
    print(f"Wrote docs/index.html ({len(months)} months, {len(weeks)} weeks, {len(days)} days)")

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
.nav { display: inline-flex; gap: 4px; }
.nav button { border: 1px solid var(--line); border-radius: 6px; }
#views { margin-top: 14px; }
.view h2, .whead h2 { font-size: 18px; margin: 0 0 8px; }
.mh { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); padding: 0 6px 4px; }
.t-easy { --tc: var(--easy); } .t-long { --tc: var(--long); } .t-workout { --tc: var(--workout); }
.t-race { --tc: var(--race); } .t-cross { --tc: var(--cross); }
/* month view: fitted to the window (height set in the script) */
.mgrid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)) 64px;
  grid-template-rows: auto repeat(var(--rows), minmax(52px, 1fr)); gap: 3px; height: 600px; }
.mc, .mwk { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px;
  overflow: hidden; min-width: 0; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.mc { border-top: 3px solid var(--tc, var(--line)); font-size: 12px; font-variant-numeric: tabular-nums; }
.mc.other { opacity: .4; }
.mc.today { outline: 2px solid var(--today); outline-offset: 1px; }
.mc.current, .mwk.current { background: color-mix(in srgb, var(--today) 9%, var(--card)); }
.mn { font-size: 12px; color: var(--muted); }
.mc.today .mn { color: var(--ink); font-weight: 700; }
.mp { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mp::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 2px; margin-right: 4px;
  background: var(--tc, var(--muted)); vertical-align: 0; }
.ma { color: var(--ok); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ma.miss { color: var(--miss); font-weight: 400; font-style: italic; }
.mwk { align-items: flex-end; justify-content: center; text-align: right; font-variant-numeric: tabular-nums; }
.mwk b { font-size: 15px; } .mwk small { color: var(--muted); font-size: 11px; }
/* week view */
.head, .week { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 4px; }
.week .day { cursor: pointer; }
.day { background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: 6px; min-height: 150px;
  border-top: 3px solid var(--line); }
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
.wsum small { color: var(--muted); font-size: 11px; margin-left: 4px; }
.whead { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 8px;
  margin-bottom: 10px; }
.wsum { text-align: right; font-variant-numeric: tabular-nums; }
.wsum b { font-size: 20px; }
.zts { color: var(--muted); font-size: 12px; margin-top: 4px; }
.zt { white-space: nowrap; }
/* day view */
.dview .trace { max-width: 980px; }
.wday { background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--tc, var(--line));
  border-radius: 6px; padding: 12px 14px; }
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
.trace .gl { fill: none; stroke: var(--s1); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.gpc .gf { fill: var(--s1); fill-opacity: .75; }
.gpc .tick { stroke: var(--muted); stroke-width: .5; }
.week .gpc text { font-size: 6.5px; }
/* day view laps: open, full size, ruled rows */
.lapd { margin-top: 14px; }
.lapd summary { font-size: 15px; font-weight: 600; color: var(--ink); margin-bottom: 4px; }
.lapd summary small { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 4px; }
.lapd table.laps { font-size: 14px; width: 100%; max-width: 640px; }
.lapd .laps th, .lapd .laps td { padding: 5px 12px 5px 0; border-bottom: 1px solid var(--line); }
.lapd .laps th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
.trl { font-size: 11px; color: var(--muted); border: 1px solid var(--line); border-radius: 3px; padding: 0 4px; margin-left: 4px; }
.lapd .laps tbody tr:hover { background: color-mix(in srgb, var(--ink) 6%, transparent); }
.act b { color: var(--ink); }
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
  .head { display: none; }
  .week { grid-template-columns: 1fr; gap: 3px; }
  .day { min-height: 0; border-top-width: 1px; border-left: 4px solid var(--tc, var(--line)); }
  .wd { display: inline; }
  /* month: a colored bar for the planned type and the miles, no words */
  .mgrid { grid-template-columns: repeat(7, minmax(0, 1fr)) 40px; gap: 2px; }
  .mc, .mwk { padding: 3px; border-radius: 4px; }
  .mc { font-size: 11px; }
  .mh { padding: 0 2px 3px; font-size: 10px; letter-spacing: 0; }
  .ml { display: none; }
  .mp::before { width: 100%; height: 3px; display: block; margin: 0 0 2px; }
  .mwk b { font-size: 12px; } .mwk small { display: none; }
  .week .hrc { max-width: 260px; }
}
</style>
</head>
<body>
<main>
%%TABS%%
<header>
  <h1>Training Calendar</h1>
  <p class="sub">Plan vs. completed runs</p>
</header>
<div class="bar">
  <div class="seg" role="group" aria-label="View">
    <button id="vMonth" aria-pressed="true">Month</button><button id="vWeek" aria-pressed="false">Week</button><button id="vDay" aria-pressed="false">Day</button>
  </div>
  <div class="nav"><button id="prev" aria-label="Earlier">‹</button><button id="today">Today</button><button id="next" aria-label="Later">›</button></div>
</div>
<div id="views">%%VIEWS%%</div>
<footer>
  <div class="legend">%%LEGEND%%</div>
  <div class="legend">HR zones: %%ZLEG%%</div>
  Click a day to open it, or a week total to open that week. ← and → step through the current view. Updated %%BUILT%%.
</footer>
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
  function parse(s) { var p = s.split("-"); return new Date(+p[0], +p[1] - 1, +(p[2] || 1)); }
  function monday(s) { var d = parse(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); }
  var today = iso(new Date()), curWeek = monday(today);

  document.querySelectorAll('[data-date="' + today + '"]').forEach(function (el) {
    if (!el.classList.contains("other")) el.classList.add("today");
  });
  document.querySelectorAll('.mc[data-week="' + curWeek + '"], .mwk[data-week="' + curWeek + '"]').forEach(function (el) {
    el.classList.add("current");
  });

  // three views, one section shown at a time; `sel` is the selected date, shared by all three
  var VIEWS = ["month", "week", "day"], secs = { month: [], week: [], day: [] };
  document.querySelectorAll("section.view").forEach(function (s) { secs[s.dataset.view].push(s); });
  var view = "month", sel = today;
  function key(v, d) { return v === "month" ? d.slice(0, 7) : v === "week" ? monday(d) : d; }
  function index(v, d) {  // the section holding d, clamped to the range
    var list = secs[v], k = key(v, d);
    if (k <= list[0].dataset.key) return 0;
    if (k >= list[list.length - 1].dataset.key) return list.length - 1;
    return list.findIndex(function (s) { return s.dataset.key === k; });
  }
  function fit() {  // the month grid fills the window below its top
    var g = document.querySelector(".mview:not([hidden]) .mgrid");
    if (g) g.style.height = Math.max(340, innerHeight - (g.getBoundingClientRect().top + scrollY) - 12) + "px";
  }
  function render() {
    var list = secs[view], i = index(view, sel);
    VIEWS.forEach(function (v) { secs[v].forEach(function (s, j) { s.hidden = !(v === view && j === i); }); });
    VIEWS.forEach(function (v) {
      document.getElementById("v" + v[0].toUpperCase() + v.slice(1)).setAttribute("aria-pressed", String(v === view));
    });
    document.getElementById("prev").disabled = i === 0;
    document.getElementById("next").disabled = i === list.length - 1;
    try { localStorage.setItem("view", view); } catch (e) {}
    if (view === "month") fit();
  }
  function shift(n) {
    var k = secs[view][index(view, sel)].dataset.key, d = parse(k);  // step from what's shown
    if (view === "month") d = new Date(d.getFullYear(), d.getMonth() + n, 1);
    else d.setDate(d.getDate() + n * (view === "week" ? 7 : 1));
    sel = iso(d);
    render();
  }
  function go(v, d) { view = v; if (d) sel = d; render(); window.scrollTo(0, 0); }
  VIEWS.forEach(function (v) {
    document.getElementById("v" + v[0].toUpperCase() + v.slice(1)).onclick = function () { go(v); };
  });
  document.getElementById("prev").onclick = function () { shift(-1); };
  document.getElementById("next").onclick = function () { shift(1); };
  document.getElementById("today").onclick = function () { go(view, today); };
  document.addEventListener("keydown", function (e) {
    if (e.target.closest("input, textarea")) return;
    if (e.key === "ArrowLeft") shift(-1);
    if (e.key === "ArrowRight") shift(1);
  });
  // month: a day opens the day view, a week total the week view; week: a day opens the day view
  document.getElementById("views").addEventListener("click", function (e) {
    if (e.target.closest("a, summary, details, button")) return;
    var wk = e.target.closest(".mview .mwk[data-week]"), day = e.target.closest(".mview .mc, .wview .day");
    if (wk) go("week", wk.dataset.week);
    else if (day) go("day", day.dataset.date);
  });
  window.addEventListener("resize", function () { if (view === "month") fit(); });

  var saved = null;
  try { saved = localStorage.getItem("view"); } catch (e) {}
  view = VIEWS.indexOf(saved) >= 0 ? saved : "month";
  render();
})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
