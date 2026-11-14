#!/usr/bin/env python3
# AI-assisted: written with the help of an AI coding assistant (GitHub Copilot); reviewed by the AutoNav-0 author.
"""Fetch a small, fully-cited offline data pack for autonav0d.

Everything downloaded here is public NASA / USGS / PDS data (see CITATIONS.md).
Only small artifacts are stored (a few MB total by default):

  data/catalog/layers.json        Trek layer catalog (WMTS products) + format/max zoom
  data/catalog/nomenclature.json  IAU/USGS feature names (via Trek index)
  data/catalog/bookmarks.json     Trek bookmarks + AutoNav landing sites
  data/dem/*.img + *.json         MOLA MEGDR elevation (global 4 + 16 ppd, 128 ppd site patches)
  data/ti/*.img + *.json         MGS TES thermal inertia (8 ppd, numeric)
  data/catalog/historic_routes.geojson  rover traverses split into plannable legs
  data/vector/*.geojson           MSL / Mars 2020 traverses + waypoints (NASA/JPL MMGIS)
  data/tiles/<layer>/z/row/col    Pre-cached low-zoom WMTS tiles from NASA Trek

Usage: python3 scripts/fetch_data.py [--all|--catalog|--dem|--ti|--vectors|--historic|--tiles] [--no-dem16]
"""
import argparse
import concurrent.futures as cf
import http.client
import json
import os
import re
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA = os.environ.get("AUTONAV_DATA", os.path.join(ROOT, "data"))
UA = "autonav0-fetch/0.1 (NASA Space Apps; offline cache; +https://trek.nasa.gov)"

TREK_INDEX = ("https://trek.nasa.gov/mars/TrekServices/ws/index/eq/searchItems"
              "?proj=urn:ogc:def:crs:EPSG::104905&start=0&rows=5000"
              "&bbox=-180,-90,180,90&intersects=false")
TREK_TILES = "https://trek.nasa.gov/tiles/Mars/EQ"
PDS_MEGDR = "https://pds-geosciences.wustl.edu/mgs/mgs-m-mola-5-megdr-l3-v1/mgsl_300x"
MMGIS = "https://mars.nasa.gov/mmgis-maps"

# Landing / exploration sites (lat N, lon E in -180..180). Coordinates from Agenda.md / NASA.
SITES = [
    {"id": "jezero", "name": "Jezero Crater (Perseverance)", "lat": 18.44, "lon": 77.45},
    {"id": "gale", "name": "Gale Crater (Curiosity)", "lat": -4.59, "lon": 137.44},
    {"id": "gusev", "name": "Gusev Crater (Spirit)", "lat": -14.57, "lon": 175.47},
    {"id": "meridiani", "name": "Meridiani Planum (Opportunity)", "lat": -1.95, "lon": -5.53},
]
PATCH_HALF_DEG = 1.5  # 3x3 deg patch at 128 ppd = 384x384 int16 = 288 KB

# Global layers prefetched for offline use (NASA/USGS/ASU public products).
PREFETCH_GLOBAL = [
    "Mars_MGS_MOLA_ClrShade_merge_global_463m",
    "Mars_Viking_MDIM21_ClrMosaic_global_232m",
    "THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018",
    "TES_Thermal_Inertia",
    "mola_roughness",
]
PREFETCH_GLOBAL_MAXZ = 3
PREFETCH_SITE_LAYERS = ["Mars_Viking_MDIM21_ClrMosaic_global_232m",
                        "THEMIS_DayIR_ControlledMosaics_100m_v2_oct2018"]
PREFETCH_SITE_Z = range(4, 8)


def log(*a):
    print("[fetch]", *a, flush=True)


def get(url, rng=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if rng:
        req.add_header("Range", "bytes=%d-%d" % rng)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def save(path, blob):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)


def save_json(path, obj):
    save(path, json.dumps(obj, indent=1).encode())


# ----------------------------------------------------------------------------- catalog
def wmts_info(label):
    try:
        xml = get(f"{TREK_TILES}/{label}/1.0.0/WMTSCapabilities.xml", timeout=30).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, str(e)
    fmt = re.search(r"<Format>([^<]+)</Format>", xml)
    # Count levels of the first TileMatrixSet only (Trek lists a second set afterwards,
    # restarting at identifier "0").
    ids = re.findall(r"<TileMatrix>\s*<ows:Identifier>(\d+)</ows:Identifier>", xml)
    levels = 0
    for i, v in enumerate(ids):
        if i > 0 and v == "0":
            break
        levels += 1
    if not fmt or levels == 0:
        return None, "no WMTS"
    ext = "png" if "png" in fmt.group(1) else "jpg"
    return {"ext": ext, "maxZoom": levels - 1}, None


def fetch_catalog():
    log("Trek index ...")
    docs = json.loads(get(TREK_INDEX))["response"]["docs"]
    products = [d for d in docs if d.get("itemType") == "product"]
    log(f"{len(docs)} items, {len(products)} products; probing WMTS capabilities")

    layers = []
    with cf.ThreadPoolExecutor(6) as ex:
        futs = {ex.submit(wmts_info, p["productLabel"]): p for p in products}
        for f in cf.as_completed(futs):
            p = futs[f]
            info, err = f.result()
            if not info:
                continue
            bbox = [float(v) for v in p.get("bbox", "-180,-90,180,90").split(",")]
            layers.append({
                "id": p["productLabel"],
                "title": p.get("title", p["productLabel"]),
                "category": p.get("productCat1", "Other"),
                "subcategory": p.get("productCat2", ""),
                "mission": p.get("mission", ""),
                "instrument": p.get("instrument", ""),
                "bbox": bbox,
                "global": bbox[2] - bbox[0] > 300 and p.get("instrument", "") not in ("CTX", "HiRISE", "HRSC"),
                "ext": info["ext"],
                "maxZoom": info["maxZoom"],
                "source": f"{TREK_TILES}/{p['productLabel']}",
                "credit": "NASA Solar System Treks (JPL/Caltech); "
                          + " ".join(x for x in [p.get("mission", ""), p.get("instrument", "")] if x),
            })
    # Viking MDIM 2.1 is used by Trek bookmarks but is not listed as a searchable product.
    if not any(l["id"] == "Mars_Viking_MDIM21_ClrMosaic_global_232m" for l in layers):
        info, _ = wmts_info("Mars_Viking_MDIM21_ClrMosaic_global_232m")
        if info:
            layers.append({"id": "Mars_Viking_MDIM21_ClrMosaic_global_232m",
                           "title": "Viking MDIM 2.1, Global Color Mosaic", "category": "Imagery",
                           "subcategory": "Mosaic", "mission": "Viking", "instrument": "VIS",
                           "bbox": [-180, -90, 180, 90], "global": True, **info,
                           "source": f"{TREK_TILES}/Mars_Viking_MDIM21_ClrMosaic_global_232m",
                           "credit": "NASA Solar System Treks (JPL/Caltech); USGS Astrogeology MDIM 2.1"})
    layers.sort(key=lambda l: (not l["global"], l["category"], l["title"]))
    save_json(os.path.join(DATA, "catalog", "layers.json"), layers)
    log(f"layers.json: {len(layers)} tiled layers")

    nomen = []
    for d in docs:
        if d.get("itemType") != "nomenclature":
            continue
        m = re.match(r"POINT \(([-\d.eE]+) ([-\d.eE]+)\)", d.get("shape", ""))
        if m:
            nomen.append({"name": d["title"], "type": d.get("productCat2", ""),
                          "lon": float(m.group(1)), "lat": float(m.group(2))})
    save_json(os.path.join(DATA, "catalog", "nomenclature.json"), nomen)
    log(f"nomenclature.json: {len(nomen)} features")

    bms = [{"id": s["id"], "name": s["name"], "lat": s["lat"], "lon": s["lon"], "zoom": 8,
            "source": "AutoNav-0 Agenda"} for s in SITES]
    for d in docs:
        if d.get("itemType") != "bookmark":
            continue
        b = [float(v) for v in d["bbox"].split(",")]
        layers_ref = re.findall(r'\\?"([A-Za-z0-9_.\-]+)\\?",\s*1', " ".join(d.get("layers", [])))
        bms.append({"id": re.sub(r"\W+", "_", d["title"]).lower(), "name": d["title"],
                    "bbox": b, "lat": (b[1] + b[3]) / 2, "lon": (b[0] + b[2]) / 2,
                    "layers": layers_ref, "source": "NASA Mars Trek bookmark"})
    save_json(os.path.join(DATA, "catalog", "bookmarks.json"), bms)
    log(f"bookmarks.json: {len(bms)} bookmarks")


# ----------------------------------------------------------------------------- DEM
def fetch_dem_global(ppd=4):
    code = {4: "c", 16: "e"}[ppd]
    url = f"{PDS_MEGDR}/meg{ppd:03d}/megt90n000{code}b.img"
    out = os.path.join(DATA, "dem", f"mola_global_{ppd}ppd")
    if os.path.exists(out + ".img"):
        log(f"global {ppd} ppd DEM present")
        return
    log(f"downloading {url}")
    save(out + ".img", get(url, timeout=600))
    save_json(out + ".json", {
        "name": f"MOLA MEGDR global {ppd} ppd", "file": os.path.basename(out) + ".img",
        "ppd": ppd, "lat_max": 90.0, "lon_min": 0.0, "width": 360 * ppd, "height": 180 * ppd,
        "type": "int16be", "scale": 1.0, "offset": 0.0, "global": True,
        "units": "m above MOLA areoid", "source": url,
        "citation": "Smith et al. (2003) MGS MOLA MEGDR, MGS-M-MOLA-5-MEGDR-L3-V1.0, NASA PDS"})


class RangeReader:
    """Keep-alive HTTP range reader (one connection per thread)."""

    def __init__(self, url):
        u = urllib.parse.urlsplit(url)
        self.host, self.path = u.netloc, u.path
        self.conn = None

    def read(self, start, length):
        for attempt in range(4):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(self.host, timeout=60)
                self.conn.request("GET", self.path, headers={
                    "User-Agent": UA, "Range": f"bytes={start}-{start + length - 1}"})
                r = self.conn.getresponse()
                body = r.read()
                if r.status != 206 or len(body) != length:
                    raise IOError(f"HTTP {r.status} len {len(body)}")
                return body
            except Exception:  # noqa: BLE001
                self.conn = None
                time.sleep(1 + attempt)
        raise IOError(f"range read failed {self.path} @{start}")


def megdr128_tile(lat, lon360):
    """Return (url, lat_top, lon_west) of the 128 ppd MEGDR tile containing a point."""
    tops = [88, 44, 0, -44]
    top = next((t for t in tops if t - 44 <= lat < t or (t == 88 and lat >= 44)), None)
    if top is None:
        raise ValueError("128 ppd MEGDR covers +-88 deg only")
    west = int(lon360 // 90) * 90
    tag = f"{abs(top):02d}{'n' if top >= 0 else 's'}{west:03d}"
    return f"{PDS_MEGDR}/meg128/megt{tag}hb.img", top, west


def fetch_dem_patch(site, ppd=128, half=PATCH_HALF_DEG):
    out = os.path.join(DATA, "dem", f"mola128_{site['id']}")
    if os.path.exists(out + ".img"):
        log(f"patch {site['id']} present")
        return
    lat_max = round(site["lat"] + half, 6)
    lon_min = (site["lon"] % 360.0) - half
    n = int(round(2 * half * ppd))
    # Snap to the 128 ppd pixel grid.
    lat_max = round(lat_max * ppd) / ppd
    lon_min = round(lon_min * ppd) / ppd
    tile_w = 90 * ppd
    readers = {}

    def row(i):
        lat = lat_max - (i + 0.5) / ppd
        buf = bytearray()
        j = 0
        while j < n:
            lon = (lon_min + (j + 0.5) / ppd) % 360.0
            url, top, west = megdr128_tile(lat, lon)
            line = int((top - lat) * ppd)
            samp = int((lon - west) * ppd)
            cnt = min(n - j, tile_w - samp)
            key = (url, threading.get_ident())
            rd = readers.setdefault(key, RangeReader(url))
            buf += rd.read((line * tile_w + samp) * 2, cnt * 2)
            j += cnt
        return i, bytes(buf)

    log(f"patch {site['id']}: {n}x{n} @ {ppd} ppd via HTTP range reads")
    rows = [None] * n
    with cf.ThreadPoolExecutor(6) as ex:
        for i, b in ex.map(row, range(n)):
            rows[i] = b
    save(out + ".img", b"".join(rows))
    save_json(out + ".json", {
        "name": f"MOLA MEGDR 128 ppd - {site['name']}", "file": os.path.basename(out) + ".img",
        "ppd": ppd, "lat_max": lat_max, "lon_min": lon_min % 360.0, "width": n, "height": n,
        "type": "int16be", "scale": 1.0, "offset": 0.0, "global": False,
        "units": "m above MOLA areoid", "source": f"{PDS_MEGDR}/meg128/",
        "citation": "Smith et al. (2003) MGS MOLA MEGDR, MGS-M-MOLA-5-MEGDR-L3-V1.0, NASA PDS"})
    # Sanity check
    vals = struct.unpack(">%dh" % (n * n), b"".join(rows))
    log(f"  elevation range {min(vals)} .. {max(vals)} m")


TI_URL = ("http://ms-mars.mars.asu.edu/ti16?SERVICE=WMS&REQUEST=GetMap&FORMAT=image/vicar"
          "&WIDTH=2880&HEIGHT=1440&SRS=JMARS:4&BBOX=0,-90,360,90&STYLES=&VERSION=1.1.1"
          "&LAYERS=TES_Thermal_Inertia_numeric")


def fetch_ti():
    """MGS TES thermal inertia (Christensen et al. 2001), 8 ppd, via ASU Mars Global Data Sets."""
    out = os.path.join(DATA, "ti", "tes_ti_8ppd")
    if os.path.exists(out + ".img"):
        log("thermal inertia present")
        return
    log("downloading TES thermal inertia (VICAR, ~16 MB) ...")
    for attempt in range(3):
        raw = get(TI_URL, timeout=600)
        hdr = raw[:200].decode("ascii", "replace")
        lbl = int(re.search(r"LBLSIZE=(\d+)", hdr).group(1))
        label = raw[:lbl].decode("ascii", "replace")
        ns, nl = int(re.search(r"\bNS=(\d+)", label).group(1)), int(re.search(r"\bNL=(\d+)", label).group(1))
        fmt = re.search(r"FORMAT='(\w+)'", label).group(1)
        code = {"FULL": "i", "HALF": "h", "BYTE": "B"}[fmt]
        size = struct.calcsize(code)
        if len(raw) >= lbl + ns * nl * size:
            break
        log(f"  truncated download ({len(raw)} bytes), retrying")
    else:
        raise IOError("thermal inertia download incomplete")
    little = "INTFMT='LOW'" in label
    vals = struct.unpack(("<" if little else ">") + f"{ns * nl}{code}", raw[lbl:lbl + ns * nl * size])
    # Values <= 25 are gaps/fill in this product; store 0 = no data.
    blob = struct.pack(f">{ns * nl}h", *[(v if v > 25 else 0) for v in vals])
    save(out + ".img", blob)
    save_json(out + ".json", {
        "name": "MGS TES thermal inertia (Christensen et al. 2001)", "file": "tes_ti_8ppd.img",
        "ppd": ns / 360.0, "lat_max": 90.0, "lon_min": 0.0, "width": ns, "height": nl,
        "type": "int16be", "global": True, "nodata": 0, "units": "J m^-2 K^-1 s^-1/2",
        "source": "https://www.mars.asu.edu/data/tes_ti/",
        "citation": "Christensen, P.R. et al. (2001) JGR 106(E10), 23823-23871; ASU Mars Global Data Sets"})
    log(f"thermal inertia: {ns}x{nl} saved ({len(blob) / 1e6:.1f} MB)")


# ----------------------------------------------------------------------------- vectors
VECTORS = {
    "msl_traverse": f"{MMGIS}/MSL/Layers/json/MSL_traverse.json",
    "msl_waypoints": f"{MMGIS}/MSL/Layers/json/MSL_waypoints.json",
    "m20_traverse": f"{MMGIS}/M20/Layers/json/M20_traverse.json",
    "m20_waypoints": f"{MMGIS}/M20/Layers/json/M20_waypoints.json",
}
VECTOR_KEEP = ["sol", "site", "drive", "dist_total_m", "dist_km", "elev_geoid", "lat", "lon", "Note"]


def fetch_vectors():
    for name, url in VECTORS.items():
        out = os.path.join(DATA, "vector", name + ".geojson")
        try:
            gj = json.loads(get(url, timeout=120))
        except Exception as e:  # noqa: BLE001
            log(f"vector {name} failed: {e}")
            continue
        if "waypoints" in name:  # strip bulky image lists to stay small
            for f in gj.get("features", []):
                f["properties"] = {k: f["properties"].get(k) for k in VECTOR_KEEP
                                   if k in f["properties"]}
        gj["autonav_source"] = url
        gj["autonav_credit"] = "NASA/JPL-Caltech MMGIS (mars.nasa.gov)"
        save(out, json.dumps(gj, separators=(",", ":")).encode())
        log(f"vector {name}: {len(gj.get('features', []))} features")


# ----------------------------------------------------------------------------- historic routes
LEG_TARGET_M = 3000.0   # split long traverses into ~3 km plannable legs
MARS_R_M = 3396190.0


def _hav(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[1], a[0], b[1], b[0]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * MARS_R_M * math.asin(min(1.0, math.sqrt(h)))


def _drives(name):
    """Ordered list of ((sol_start, sol_end), [[lon,lat],...]) drives from a cached MMGIS traverse."""
    gj = json.load(open(os.path.join(DATA, "vector", name + "_traverse.geojson")))
    wps = json.load(open(os.path.join(DATA, "vector", name + "_waypoints.geojson")))["features"]
    wp = [(w["properties"].get("lon"), w["properties"].get("lat"), w["properties"].get("sol")) for w in wps
          if w["properties"].get("lat") is not None]
    out, ptr = [], 0
    for f in gj["features"]:
        g = f["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        pts = [[round(c[0], 7), round(c[1], 7)] for line in lines for c in line]
        if len(pts) < 2:
            continue
        sol = f["properties"].get("sol")
        if sol is not None:
            sols = (int(sol), int(sol))
        else:
            # MSL traverse has no sol (and early drives are merged into one line): match the drive's
            # start and end to waypoints, searching only forward in time (the rover revisits places).
            def match(p, lo):
                win = range(lo, min(len(wp), lo + 80))
                return min(win, key=lambda k: (wp[k][0] - p[0]) ** 2 + (wp[k][1] - p[1]) ** 2) if win else len(wp) - 1
            j0 = match(pts[0], ptr)
            ptr = match(pts[-1], j0)
            sols = (int(wp[j0][2]), int(wp[ptr][2]))
        out.append((sols, pts))
    return out


def _thin(pts, min_m=5.0):
    out = [pts[0]]
    for p in pts[1:-1]:
        if _hav(out[-1], p) >= min_m:
            out.append(p)
    out.append(pts[-1])
    return out


def _leg_feature(fid, name, rover, mission, pts, sols, kind, source):
    pts = _thin(pts)
    dist = sum(_hav(a, b) for a, b in zip(pts, pts[1:]))
    return {"type": "Feature", "id": fid,
            "geometry": {"type": "LineString", "coordinates": pts},
            "properties": {"id": fid, "name": name, "rover": rover, "mission": mission, "kind": kind,
                           "sols": sols, "distance_m": round(dist),
                           "start": {"lat": pts[0][1], "lon": pts[0][0], "label": f"sol {sols[0]}"},
                           "end": {"lat": pts[-1][1], "lon": pts[-1][0], "label": f"sol {sols[1]}"},
                           "source": source, "credit": "NASA/JPL-Caltech"}}


def build_historic():
    feats = []
    for key, rover, mission in (("msl", "Curiosity", "Mars Science Laboratory"),
                                ("m20", "Perseverance", "Mars 2020")):
        try:
            drives = _drives(key)
        except (OSError, KeyError, ValueError) as e:
            log(f"historic {key}: traverse not cached ({e}); run --vectors first")
            continue
        src = VECTORS[key + "_traverse"]
        allpts = [p for _, pts in drives for p in pts]
        feats.append(_leg_feature(f"{key}-full", f"{rover} — full traverse", rover, mission, allpts,
                                  [drives[0][0][0], drives[-1][0][1]], "full-traverse", src))
        leg, acc, n = [], 0.0, 1
        for sol, pts in drives:
            leg.append((sol, pts))
            acc += sum(_hav(a, b) for a, b in zip(pts, pts[1:]))
            if acc >= LEG_TARGET_M or (sol, pts) is drives[-1]:
                lp = [p for _, ps in leg for p in ps]
                sols = [leg[0][0][0], leg[-1][0][1]]
                feats.append(_leg_feature(f"{key}-leg{n:02d}", f"{rover} leg {n} · sols {sols[0]}–{sols[1]}",
                                          rover, mission, lp, sols, "traverse-leg", src))
                leg, acc, n = [], 0.0, n + 1
    # Opportunity: no public traverse file; historic waypoints from the landing site and IAU crater names.
    nomen = {f["name"]: f for f in json.load(open(os.path.join(DATA, "catalog", "nomenclature.json")))}
    opp = [("Landing site (Eagle crater)", -1.9462, -5.5272)]
    for nm in ("Victoria", "Endeavour"):
        if nm in nomen:
            opp.append((f"{nm} crater", nomen[nm]["lat"], nomen[nm]["lon"]))
    sols = {"Landing site (Eagle crater)": 1, "Victoria crater": 951, "Endeavour crater": 2681}
    for (a, la1, lo1), (b, la2, lo2) in zip(opp, opp[1:]):
        f = _leg_feature(f"mer-b-{a.split()[0].lower()}-{b.split()[0].lower()}", f"Opportunity · {a} → {b}",
                         "Opportunity", "Mars Exploration Rover B", [[lo1, la1], [lo2, la2]],
                         [sols[a], sols[b]], "endpoints",
                         "IAU/USGS Gazetteer of Planetary Nomenclature (crater centres); landing site per NASA/JPL")
        f["properties"]["start"]["label"], f["properties"]["end"]["label"] = a, b
        f["properties"]["note"] = ("Endpoints only (no public traverse file): straight line is NOT the driven path; "
                                   "Endeavour point is the 22 km crater's centre, the rover reached its west rim.")
        feats.append(f)
    save(os.path.join(DATA, "catalog", "historic_routes.geojson"),
         json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")).encode())
    log(f"historic_routes.geojson: {len(feats)} routes")


# ----------------------------------------------------------------------------- tiles
def tile_job(layer, ext, z, row, col):
    out = os.path.join(DATA, "tiles", layer, str(z), str(row), f"{col}.{ext}")
    if os.path.exists(out):
        return 0
    url = f"{TREK_TILES}/{layer}/1.0.0/default/default028mm/{z}/{row}/{col}.{ext}"
    try:
        blob = get(url, timeout=30)
    except Exception:  # noqa: BLE001
        return 0
    save(out, blob)
    return len(blob)


def fetch_tiles():
    layers = {l["id"]: l for l in json.load(open(os.path.join(DATA, "catalog", "layers.json")))}
    jobs = []
    for lid in PREFETCH_GLOBAL:
        l = layers.get(lid)
        if not l:
            continue
        for z in range(0, min(PREFETCH_GLOBAL_MAXZ, l["maxZoom"]) + 1):
            for r in range(2 ** z):
                for c in range(2 ** (z + 1)):
                    jobs.append((lid, l["ext"], z, r, c))
    for lid in PREFETCH_SITE_LAYERS:
        l = layers.get(lid)
        if not l:
            continue
        for s in SITES:
            for z in PREFETCH_SITE_Z:
                if z > l["maxZoom"]:
                    continue
                deg = 180.0 / 2 ** z  # tile span in degrees
                r0, r1 = int((90 - s["lat"] - PATCH_HALF_DEG) // deg), int((90 - s["lat"] + PATCH_HALF_DEG) // deg)
                c0, c1 = int((s["lon"] + 180 - PATCH_HALF_DEG) // deg), int((s["lon"] + 180 + PATCH_HALF_DEG) // deg)
                for r in range(max(r0, 0), min(r1, 2 ** z - 1) + 1):
                    for c in range(max(c0, 0), min(c1, 2 ** (z + 1) - 1) + 1):
                        jobs.append((lid, l["ext"], z, r, c))
    jobs = sorted(set(jobs))
    log(f"prefetching {len(jobs)} tiles (skipping cached)")
    total = 0
    with cf.ThreadPoolExecutor(4) as ex:  # polite concurrency
        for n in ex.map(lambda j: tile_job(*j), jobs):
            total += n
    log(f"tiles: {total / 1e6:.1f} MB downloaded")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ["all", "catalog", "dem", "ti", "vectors", "historic", "tiles"]:
        ap.add_argument("--" + k, action="store_true")
    ap.add_argument("--no-dem16", action="store_true", help="skip the global 16 ppd MOLA DEM (~33 MB)")
    a = ap.parse_args()
    if not any([a.catalog, a.dem, a.ti, a.vectors, a.historic, a.tiles]):
        a.all = True
    if a.all or a.catalog:
        fetch_catalog()
    if a.all or a.dem:
        fetch_dem_global(4)
        if not a.no_dem16:
            fetch_dem_global(16)
        for s in SITES:
            fetch_dem_patch(s)
    if a.all or a.ti:
        fetch_ti()
    if a.all or a.vectors:
        fetch_vectors()
    if a.all or a.vectors or a.historic:
        build_historic()
    if a.all or a.tiles:
        fetch_tiles()
    log("done")


if __name__ == "__main__":
    sys.exit(main())
