"""Ground elevation along a run from the USGS 3DEP elevation service (1 m lidar where available).

The watch's barometric altitude moves in ~1 m steps and smooths and delays short hills, which hides
the grade of a hill sprint. Looking up elevation at the GPS track gives the real profile.

Sampling is adaptive: points every COARSE_M along the track, then each stretch is bisected while its
midpoint sits more than TOL_M off the straight line between its ends, down to MIN_STEP_M. Flat or
steady grades get few points; short steep hills get dense ones.

Each run is looked up once and cached in data/elev.csv as elevation vs distance (no coordinates),
so the scheduled GitHub update only queries new runs. If the service fails or a run has no GPS, callers
fall back to the barometer.
"""
import bisect, csv, json, urllib.parse, urllib.request

from config import ROOT

DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
COARSE_M = 40     # first pass spacing along the track
MIN_STEP_M = 2.5  # finest spacing (lidar is 1 m; GPS position error is a few m)
TOL_M = 0.15      # refine a stretch while its midpoint is this far off a straight line
CHUNK = 400       # points per request
CACHE = ROOT / "data" / "elev.csv"

_cache = None


def _load():
    global _cache
    if _cache is None:
        _cache = {}
        if CACHE.exists():
            with open(CACHE, newline="") as f:
                for r in csv.DictReader(f):
                    _cache[r["fit"]] = [tuple(map(float, p.split(":"))) for p in r["dist_elev_m"].split()]
    return _cache


def save():
    if _cache is None:
        return
    with open(CACHE, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fit", "dist_elev_m"])  # "distance:elevation" pairs in meters, along the run
        for fid in sorted(_cache):
            w.writerow([fid, " ".join(f"{d:.2f}:{e:.2f}" for d, e in _cache[fid])])


def _query(points):
    body = urllib.parse.urlencode({
        "geometry": json.dumps({"points": [[lon, lat] for lat, lon in points], "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryMultipoint", "returnFirstValueOnly": "true",
        "interpolation": "RSP_BilinearInterpolation", "f": "json"}).encode()
    req = urllib.request.Request(DEM_URL, data=body, headers={"User-Agent": "RunningCoach/1.0"})
    res = json.loads(urllib.request.urlopen(req, timeout=90).read())
    samples = sorted(res["samples"], key=lambda s: s["locationId"])
    if len(samples) != len(points):
        raise ValueError("incomplete response")
    return [float(s["value"]) for s in samples]


def _lookup(points):
    out = []
    for i in range(0, len(points), CHUNK):
        out += _query(points[i:i + CHUNK])
    if any(e < -500 for e in out):  # no-data values
        raise ValueError("no elevation data at some points")
    return out


def profile(fid, dist, pos):
    """[(distance_m, elevation_m), ...] along the run, or None. dist and pos are per record."""
    cache = _load()
    if fid in cache:
        return cache[fid]
    track = []  # (distance, lat, lon), distance strictly increasing
    for d, p in zip(dist, pos):
        if d is not None and p is not None and None not in p and (not track or d > track[-1][0]):
            track.append((d, *p))
    if len(track) < 10 or track[-1][0] - track[0][0] < 2 * COARSE_M:
        return None
    ds = [t[0] for t in track]

    def at(x):  # GPS position at a distance along the track (linear between records)
        i = min(len(ds) - 1, max(1, bisect.bisect_left(ds, x)))
        (d0, a0, o0), (d1, a1, o1) = track[i - 1], track[i]
        f = 0 if d1 == d0 else (x - d0) / (d1 - d0)
        return a0 + f * (a1 - a0), o0 + f * (o1 - o0)

    try:
        xs = [ds[0] + k * COARSE_M for k in range(int((ds[-1] - ds[0]) // COARSE_M) + 1)]
        if ds[-1] - xs[-1] > 1:
            xs.append(ds[-1])
        elev = dict(zip(xs, _lookup([at(x) for x in xs])))
        todo = list(zip(xs, xs[1:]))
        while todo:
            mids = [(a + b) / 2 for a, b in todo]
            elev.update(zip(mids, _lookup([at(m) for m in mids])))
            nxt = []
            for (a, b), m in zip(todo, mids):
                if abs(elev[m] - (elev[a] + elev[b]) / 2) > TOL_M and (b - a) / 2 > MIN_STEP_M:
                    nxt += [(a, m), (m, b)]
            todo = nxt
    except Exception as e:  # network, service, or a point outside coverage: use the barometer
        print(f"  elevation lookup failed for {fid}: {e}")
        return None
    cache[fid] = sorted(elev.items())
    return cache[fid]
