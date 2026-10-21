"""How far real runners land from VDOT predictions: writes data/ref/race_errors.csv for the Calculator tab.

Usage: python scripts/race_errors.py   (one-off, a few minutes; downloads ~13 MB of data and doesn't keep it; needs openpyxl)

Table: thepowerof10 (Blythe & Kiraly 2016, PLOS ONE 11:e0157257, figshare 3408202, CC BY 4.0), official results of
165,000 British club runners. For each runner and each ordered pair of distances (1500 m, mile, 5K, 10K, half,
marathon), the two races closest in time, within WINDOW_DAYS; one pair per runner. At the same distance it's two
different races, earlier to later. The miss is the later/other time vs. the VDOT prediction from the first, as %
slower (10th, 50th, 90th percentiles), by ability band (VDOT of the input race) and over all runners ("all", the
fallback for a band with fewer than MIN_N runners).

Rows with from = model1/model2 are Vickers & Vertosick's marathon models (BMC Sports Sci Med Rehabil 2016;8:26,
Additional files 1-2, CC BY 4.0) vs. actual, on their validation runners.

Checks printed (not written): the table built on half the club runners, tested on the other half; and the full
table tested on Vickers' survey runners (self-reported recreational races, an independent population).
"""
import csv, io, math, statistics, urllib.request, zipfile
from collections import defaultdict
from datetime import date

import openpyxl

from config import ROOT
from fitness_page import predict, vdot

PO10_URL = "https://ndownloader.figshare.com/files/5323348"
VICKERS_URL = ("https://media.springernature.com/original/springer-static/esm/art%3A10.1186%2Fs13102-016-0052-y/"
               "MediaObjects/13102_2016_52_MOESM2_ESM.xlsx")
WINDOW_DAYS = 90
MIN_N = 50
BANDS = [("<40", 0, 40), ("40-50", 40, 50), ("50-60", 50, 60), ("60+", 60, 999)]
GRID = [("1500", 1500), ("mile", 1609.344), ("5K", 5000), ("10K", 10000), ("half", 21097.5), ("mara", 42195)]
PO10_TYPE = {"5": "1500", "6": "mile", "7": "5K", "8": "10K", "9": "half", "10": "mara"}
METERS = dict(GRID)


def fetch(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})).read()


def pct(a, p):  # nearest-rank percentile
    a = sorted(a)
    return a[min(len(a) - 1, int(p * len(a)))]


def band(v):
    return next(name for name, lo, hi in BANDS if lo <= v < hi)


def miss_pct(a, ta, b, tb):
    return 100 * (tb / predict(vdot(METERS[a], ta), METERS[b]) - 1)


# ---------- thepowerof10 ----------

def load_po10():
    z = zipfile.ZipFile(io.BytesIO(fetch(PO10_URL)))
    races = defaultdict(lambda: defaultdict(list))  # athlete -> distance -> [(day, secs)]
    with z.open("thepowerof10/events.csv") as f:
        for aid, typ, _, t, d in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
            k = PO10_TYPE.get(typ)
            if not k:
                continue
            y, m, dd = map(int, d.strip("[]").split())
            t = float(t)
            if not 1990 <= y <= 2013 or not 1.5 < METERS[k] / t < 6.7:  # dates the data covers; walking to world-record speed
                continue
            try:
                races[aid][k].append((date(y, m, dd).toordinal(), t))
            except ValueError:
                pass
    return races


def pairs(races, a, b, keep=lambda aid: True):
    """(input time, other time) per runner: the closest a and b races within WINDOW_DAYS."""
    out = []
    for aid, r in races.items():
        if a not in r or b not in r or not keep(aid):
            continue
        best = None
        for da, ta in r[a]:
            for db, tb in r[b]:
                gap = abs(da - db)
                if a == b and db <= da:  # same distance: two different races, earlier to later
                    continue
                if gap <= WINDOW_DAYS and (best is None or gap < best[0]):
                    best = (gap, ta, tb)
        if best:
            out.append(best[1:])
    return out


def table_from(races, keep=lambda aid: True):
    table = {}
    for a, _ in GRID:
        for b, _ in GRID:
            by = defaultdict(list)
            for ta, tb in pairs(races, a, b, keep):
                m = miss_pct(a, ta, b, tb)
                by["all"].append(m)
                by[band(vdot(METERS[a], ta))].append(m)
            for name, ms in by.items():
                if len(ms) >= MIN_N:
                    table[(name, a, b)] = (len(ms), pct(ms, .1), statistics.median(ms), pct(ms, .9))
    return table


# ---------- the page's lookup (same as calculator_page.py) ----------

def bracket(m):
    x = math.log(min(max(m, GRID[0][1]), GRID[-1][1]))
    i = 0
    while i < len(GRID) - 2 and x > math.log(GRID[i + 1][1]):
        i += 1
    return i, (x - math.log(GRID[i][1])) / (math.log(GRID[i + 1][1]) - math.log(GRID[i][1]))


def lookup(table, a_m, b_m, v):
    (ia, wa), (ib, wb) = bracket(a_m), bracket(b_m)
    out = [0.0, 0.0, 0.0]
    for ca in (0, 1):
        for cb in (0, 1):
            w = (wa if ca else 1 - wa) * (wb if cb else 1 - wb)
            key = (GRID[ia + ca][0], GRID[ib + cb][0])
            q = table.get((band(v),) + key) or table[("all",) + key]
            for k in range(3):
                out[k] += w * q[k + 1]
    return out


def coverage(table, cases):
    """cases: (a_m, ta, b_m, tb). Share inside [p10, p90], below, above, and median actual vs. predicted center."""
    inside = below = above = 0
    resid = []
    for a_m, ta, b_m, tb in cases:
        v = vdot(a_m, ta)
        q = lookup(table, a_m, b_m, v)
        raw = predict(v, b_m)
        lo, c, hi = (raw * (1 + x / 100) for x in q)
        inside += lo <= tb <= hi
        below += tb < lo
        above += tb > hi
        resid.append(100 * (tb / c - 1))
    n = len(cases)
    return n, 100 * inside / n, 100 * below / n, 100 * above / n, statistics.median(resid)


# ---------- Vickers & Vertosick ----------

VICKERS_EVENTS = [("k5", 5000), ("k10", 10000), ("m5", 8046.72), ("m10", 16093.44), ("mh", 21097.5), ("mf", 42195)]


def load_vickers():
    rows = openpyxl.load_workbook(io.BytesIO(fetch(VICKERS_URL)), read_only=True)["Sheet1"].iter_rows(values_only=True)
    head = next(rows)
    return [r for r in (dict(zip(head, x)) for x in rows) if r["adjusted"] == 1]


def vickers_races(r):  # difficulty-adjusted times; "very difficult"/"very fast" races dropped, as in the paper
    return {m: float(r[k + "_ti_adj"]) for k, m in VICKERS_EVENTS if r[k + "_ti_adj"] and r[k + "_di"] in (2, 3, 4)}


def main():
    print("Downloading thepowerof10 ...")
    races = load_po10()
    print(f"  {len(races):,} runners with races at 1500 m to marathon")

    # check 1: build on even runner IDs, test on odd
    even = lambda aid: int(aid) % 2 == 0
    half_table = table_from(races, even)
    print("\nHeld-out club runners (table from even IDs, tested on odd): share inside the 80% range, below, above, "
          "median miss vs. center")
    for a, am in GRID:
        line = []
        for b, bm in GRID:
            cases = [(am, ta, bm, tb) for ta, tb in pairs(races, a, b, lambda aid: not even(aid))]
            if len(cases) >= MIN_N:
                n, ins, lo, hi, med = coverage(half_table, cases)
                line.append(f"{b}:{ins:.0f}%")
        print(f"  from {a:<5} " + "  ".join(line))

    table = table_from(races)

    # check 2: survey runners (independent population, self-reported, includes 5 and 10 mile)
    print("\nDownloading Vickers & Vertosick survey ...")
    vk = load_vickers()
    cases = defaultdict(list)
    for r in vk:
        x = vickers_races(r)
        for am, ta in x.items():
            for bm, tb in x.items():
                if am != bm:
                    cases[(am, bm)].append((am, ta, bm, tb))
    print("Survey runners vs. the club-runner table: n, inside, below, above, median miss vs. center")
    for (am, bm), cs in sorted(cases.items()):
        if len(cs) >= 30:
            n, ins, lo, hi, med = coverage(table, cs)
            print(f"  {am:>8g} -> {bm:<8g} n={n:4d}  inside {ins:3.0f}%  below {lo:3.0f}%  above {hi:3.0f}%  median {med:+5.1f}%")
    allc = [c for cs in cases.values() for c in cs]
    n, ins, lo, hi, med = coverage(table, allc)
    print(f"  all pairs n={n}: inside {ins:.0f}%, below {lo:.0f}%, above {hi:.0f}%")

    out = [[bd, a, b, *v] for (bd, a, b), v in sorted(table.items(), key=lambda x: (x[0][0] != "all", x[0]))]
    for model in ("model1", "model2"):
        res = [100 * (float(r["mf_ti_adj"]) / (60 * float(r[model + "_time"])) - 1) for r in vk
               if r["group"] == 3 and r[model + "_time"] and r["mf_ti_adj"] and r["mf_di"] in (2, 3, 4)]
        out.append(["all", model, "mara", len(res), pct(res, .1), statistics.median(res), pct(res, .9)])

    path = ROOT / "data" / "ref" / "race_errors.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["band", "from", "to", "n", "p10_pct", "p50_pct", "p90_pct"])
        w.writerows(r[:4] + [round(x, 2) for x in r[4:]] for r in out)
    print(f"\nWrote {path.relative_to(ROOT)} ({len(out)} rows)")


if __name__ == "__main__":
    main()
