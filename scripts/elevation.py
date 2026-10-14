"""Ground elevation along a run from the USGS 3DEP elevation service (1 m lidar where available).

The watch's barometric altitude moves in ~1 m steps and smooths and delays short hills, which hides
the grade of a hill sprint. Looking up elevation at the GPS track gives the real profile.

Each run is looked up once and cached in data/elev.csv as elevation vs distance (no coordinates),
so the daily GitHub update only queries new runs. If the service fails or a run has no GPS, callers
fall back to the barometer.
"""
import csv, json, urllib.parse, urllib.request

from config import ROOT

DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/getSamples"
STEP_M = 5      # one lookup per this much distance along the track
CHUNK = 400     # points per request
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
            w.writerow([fid, " ".join(f"{d:.1f}:{e:.2f}" for d, e in _cache[fid])])


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


def profile(fid, dist, pos):
    """[(distance_m, elevation_m), ...] along the run, or None. dist and pos are per record."""
    cache = _load()
    if fid in cache:
        return cache[fid]
    pts, last = [], None
    for d, p in zip(dist, pos):
        if d is None or p is None or None in p:
            continue
        if last is None or d - last >= STEP_M:
            pts.append((d, p)); last = d
    if len(pts) < 10:
        return None
    try:
        elev = []
        for i in range(0, len(pts), CHUNK):
            elev += _query([p for _, p in pts[i:i + CHUNK]])
    except Exception as e:  # network, service, or a point outside coverage: use the barometer
        print(f"  elevation lookup failed for {fid}: {e}")
        return None
    if any(e < -500 for e in elev):  # no-data values
        return None
    cache[fid] = [(d, e) for (d, _), e in zip(pts, elev)]
    return cache[fid]
