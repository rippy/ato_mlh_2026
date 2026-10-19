#!/usr/bin/env python3
"""Download and cache every dataset the gluten-free finder needs into ./data/.

    python3 fetch_data.py                       fetch what is missing, reuse the rest
    python3 fetch_data.py --places              also fetch OSM restaurants that list a website
                                                (used by `annotate.py seed` to pre-fill menu URLs)
    python3 fetch_data.py --refresh             re-download everything
    python3 fetch_data.py --refresh osm,wake    re-download only some parts
                                                (osm, places, wake, inspections, violations, vendor)
    python3 fetch_data.py --osm-file x.json     use an Overpass JSON export you saved by hand
                                                (e.g. from overpass-turbo.eu) instead of downloading
    python3 fetch_data.py --all-facilities      fetch inspections/violations for EVERY county
                                                restaurant in the radius (bigger download)
    python3 fetch_data.py --status              show what is cached; download nothing

Environment overrides:
    LAT, LON          centre point (default: Raleigh Convention Center, approximate)
    CACHE_RADIUS_M    how far out to download (default 3000). Use 8000 to cover most of Raleigh.
                      The map's radius (RADIUS_M) can be anything up to this without re-downloading.

What gets cached in data/:
    osm.json          OpenStreetMap places with a gluten-free tag (Overpass API)
    places.json       OpenStreetMap restaurants/cafes with a website tag (only with --places)
    wake.json         Wake County permitted food establishments (ArcGIS REST)
    inspections.json  Wake County inspection scores, for every place with a gluten-free claim
    violations.json   Wake County violations, for the same places
    vendor/           Leaflet JS/CSS so the map page does not need a CDN
    manifest.json     what was fetched, when, for which location

Inspection history is fetched for every place that has any claim: an OSM tag OR a row in
annotations.csv. After adding annotations, rerun this to pull their health data (only the new
places are downloaded).

Standard library only. Network calls go through curl. If a download is interrupted, rerun:
finished parts are not repeated.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import gf_map

LAT, LON = gf_map.LAT, gf_map.LON
DATA = gf_map.DATA
CACHE_RADIUS_M = int(os.environ.get("CACHE_RADIUS_M", 3000))
UA = "ato_mlh_2026-hackathon/0.1 (https://github.com/rippy/ato_mlh_2026)"
WAKE = os.environ.get(
    "WAKE_BASE",
    "https://maps.wake.gov/arcgis/rest/services/Inspections/RestaurantInspectionsOpenData/MapServer")
# overpass.kumi.systems was renamed overpass.private.coffee (OSM wiki, Overpass_API#Public_Overpass_API_instances)
OVERPASS = os.environ.get(
    "OVERPASS_ENDPOINTS",
    "https://overpass-api.de/api/interpreter https://overpass.private.coffee/api/interpreter "
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter").split()
OVERPASS_TIMEOUT = int(os.environ.get("OVERPASS_TIMEOUT", 50))   # seconds per attempt
LEAFLET = os.environ.get("LEAFLET_BASE", "https://unpkg.com/leaflet@1.9.4/dist")
PAGE = int(os.environ.get("ARCGIS_PAGE", 1000))     # rows requested per ArcGIS page
BATCH = 150                                          # facilities per inspections/violations query
PARTS = {"osm", "places", "wake", "inspections", "violations", "vendor"}
LAYERS = {   # name -> (ArcGIS layer id, fields to request)
    "inspections": (1, "*"),
    "violations": (2, "OBJECTID,HSISID,INSPECTDATE,SEVERITY,POINTVALUE,SHORTDESC,CATEGORY,VIOLATIONTYPE"),
}


class ArcGISError(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- plumbing
def curl(url, params=None, *, get=False, timeout=90):
    """Run curl; return (http_status, body_bytes). params are URL-encoded (query with get=True, else POST body)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "body"
        cmd = ["curl", "-sS", "--compressed", "-A", UA, "--max-time", str(timeout),
               "-o", str(out), "-w", "%{http_code}"]
        if get and params:
            cmd.append("-G")
        for k, v in (params or {}).items():
            cmd += ["--data-urlencode", f"{k}={v}"]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"curl exit {r.returncode}: {r.stderr.strip()}")
        return int(r.stdout.strip() or 0), (out.read_bytes() if out.exists() else b"")


def get_json(url, params, tries=3):
    """POST an ArcGIS query, retrying transient failures. ArcGIS error payloads raise ArcGISError (no retry)."""
    last = "unknown error"
    for attempt in range(1, tries + 1):
        try:
            code, body = curl(url, params)
            if code == 200:
                data = json.loads(body)
                if isinstance(data, dict) and "error" in data:
                    raise ArcGISError(data["error"])
                return data
            last = f"HTTP {code}: {body[:160]!r}"
        except (RuntimeError, ValueError) as e:
            last = str(e)
        if attempt < tries:
            log(f"  retry {attempt}/{tries - 1} after error: {last}")
            time.sleep(2 ** attempt)
    raise SystemExit(f"Giving up on {url}: {last}")


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)          # atomic: an interrupted run never leaves a half-written cache file


def load_cached(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- sources
def overpass(query):
    """Run an Overpass query, trying every public server with growing pauses between rounds."""
    wait = 5
    for rnd in range(1, 5):
        for ep in OVERPASS:
            try:
                code, body = curl(ep, {"data": query}, timeout=OVERPASS_TIMEOUT)
            except RuntimeError as e:
                log(f"  round {rnd}: {ep}: {e}")
                continue
            if code == 200:
                try:
                    data = json.loads(body)
                except ValueError:
                    log(f"  round {rnd}: {ep} returned non-JSON: {body[:160]!r}")
                    continue
                if data.get("remark") and not data.get("elements"):
                    log(f"  round {rnd}: {ep} remark: {data['remark']}")
                    continue
                log(f"  ok: {ep}")
                return data
            log(f"  round {rnd}: HTTP {code} from {ep}: {body[:160].decode('utf-8', 'replace')!r}")
        if rnd < 4:
            log(f"  all endpoints failed; waiting {wait}s")
            time.sleep(wait)
            wait *= 2
    raise SystemExit(
        "Overpass failed on every server. Public Overpass is often overloaded and conference wifi shares one IP.\n"
        "Try: wait a few minutes and rerun (finished parts are cached), or use a phone hotspot, or run the\n"
        "query at overpass-turbo.eu, export raw JSON, and load it with:  python3 fetch_data.py --osm-file x.json")


def fetch_osm():
    r = CACHE_RADIUS_M
    return overpass(f"[out:json][timeout:25];\n(\n"
                    f"  nwr[\"diet:gluten_free\"](around:{r},{LAT},{LON});\n"
                    f"  nwr[\"gluten_free\"](around:{r},{LAT},{LON});\n"
                    f");\nout center tags;")


def fetch_places():
    """OSM food places that list a website (bounding-box query: cheaper for Overpass than `around`)."""
    dlat = CACHE_RADIUS_M / 111320.0
    dlon = CACHE_RADIUS_M / (111320.0 * math.cos(math.radians(LAT)))
    box = f"{LAT - dlat:.5f},{LON - dlon:.5f},{LAT + dlat:.5f},{LON + dlon:.5f}"
    kinds = "^(restaurant|cafe|fast_food|pub|bar|food_court)$"
    return overpass(f"[out:json][timeout:60];\n(\n"
                    f"  nwr[\"amenity\"~\"{kinds}\"][\"website\"]({box});\n"
                    f"  nwr[\"amenity\"~\"{kinds}\"][\"contact:website\"]({box});\n"
                    f");\nout center tags;")


def arcgis_query(layer, params, out_fields="*"):
    """Run a query on a Wake County layer, following pages until the server says it is done."""
    url = f"{WAKE}/{layer}/query"
    base = {"f": "json", "outFields": out_fields, "returnGeometry": "false", **params}
    features, fields, offset, paging = [], None, 0, True
    while True:
        p = dict(base)
        if paging:
            p.update(resultOffset=offset, resultRecordCount=PAGE, orderByFields="OBJECTID")
        try:
            data = get_json(url, p)
        except ArcGISError as e:
            if paging and offset == 0:
                log(f"  paging not accepted ({e}); retrying without it")
                paging = False
                continue
            raise
        fields = fields or data.get("fields")
        got = data.get("features", [])
        features += got
        if not data.get("exceededTransferLimit"):
            break
        if not paging:
            log("  WARNING: server row limit hit and paging unavailable; results are truncated")
            break
        if not got:
            break
        offset += len(got)
    return {"fields": fields, "features": features}


def fetch_wake_restaurants():
    return arcgis_query(0, {
        "geometry": f"{LON},{LAT}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "distance": CACHE_RADIUS_M,
        "units": "esriSRUnit_Meter", "outSR": 4326, "returnGeometry": "true"})


def fetch_layer(layer, ids, out_fields):
    feats, fields = [], None
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        where = "HSISID IN (" + ",".join(gf_map.sql_literal(v) for v in chunk) + ")"
        try:
            part = arcgis_query(layer, {"where": where}, out_fields)
        except ArcGISError as e:
            if out_fields == "*":
                raise SystemExit(f"Wake County layer {layer} rejected the query: {e}")
            log(f"  requested fields rejected ({e}); retrying with all fields")
            out_fields = "*"
            part = arcgis_query(layer, {"where": where}, out_fields)
        feats += part["features"]
        fields = fields or part["fields"]
        log(f"  layer {layer}: {min(i + BATCH, len(ids))}/{len(ids)} facilities, {len(feats)} rows")
    return {"fields": fields, "features": feats}


def fetch_vendor(force):
    vend = DATA / "vendor"
    vend.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in ("leaflet.js", "leaflet.css"):
        dest = vend / name
        if dest.exists() and dest.stat().st_size > 0 and not force:
            continue
        try:
            code, body = curl(f"{LEAFLET}/{name}")
        except RuntimeError as e:
            code, body = 0, b""
            log(f"  could not download {name}: {e}")
        if code == 200 and body:
            dest.write_bytes(body)
        else:
            ok = False
            log(f"  WARNING: {name} not cached (HTTP {code}); the map will load it from a CDN instead")
    return ok


# --------------------------------------------------------------------------- main
def show_status():
    manifest = load_cached(DATA / "manifest.json")
    if not manifest:
        log("Nothing cached yet. Run: python3 fetch_data.py")
        return
    log(f"Cache for centre {manifest.get('center')}, radius {manifest.get('radius_m')} m")
    for name, when in manifest.get("fetched_at", {}).items():
        p = DATA / (f"{name}.json" if name != "vendor" else "vendor/leaflet.js")
        size = f"{p.stat().st_size / 1024:.0f} KB" if p.exists() else "MISSING"
        log(f"  {name:<12} {size:>9}  fetched {when}  {manifest.get('counts', {}).get(name, '')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", nargs="?", const="all", metavar="PARTS",
                    help="re-download 'all' or a comma list of: " + ", ".join(sorted(PARTS)))
    ap.add_argument("--places", action="store_true", help="also fetch OSM restaurants with websites")
    ap.add_argument("--osm-file", metavar="FILE", help="use a saved Overpass JSON export as osm.json")
    ap.add_argument("--all-facilities", action="store_true",
                    help="fetch inspections/violations for every county restaurant in the radius")
    ap.add_argument("--status", action="store_true", help="show what is cached and exit")
    args = ap.parse_args()

    if args.status:
        return show_status()

    refresh = set()
    if args.refresh:
        refresh = set(PARTS) if args.refresh == "all" else {p.strip() for p in args.refresh.split(",")}
        if refresh - PARTS:
            sys.exit(f"Unknown part(s): {', '.join(sorted(refresh - PARTS))}. Choose from: {', '.join(sorted(PARTS))}")

    manifest = load_cached(DATA / "manifest.json") or {}
    if manifest and (manifest.get("center") != [LAT, LON] or manifest.get("radius_m") != CACHE_RADIUS_M):
        log(f"Cache was built for {manifest.get('center')} / {manifest.get('radius_m')} m; "
            f"refreshing everything for {[LAT, LON]} / {CACHE_RADIUS_M} m")
        refresh, manifest = set(PARTS), {}
    manifest.setdefault("fetched_at", {})
    manifest.setdefault("counts", {})
    manifest.setdefault("queried", {})
    manifest.update(center=[LAT, LON], radius_m=CACHE_RADIUS_M)

    def stamp(name, count):
        manifest["fetched_at"][name] = now_iso()
        manifest["counts"][name] = count
        save(DATA / "manifest.json", manifest)

    # 1) OpenStreetMap gluten-free tags
    if args.osm_file:
        osm = load_cached(args.osm_file)
        if not isinstance(osm, dict) or not isinstance(osm.get("elements"), list):
            sys.exit("--osm-file must be an Overpass JSON export (a JSON object with an 'elements' list)")
        save(DATA / "osm.json", osm)
        stamp("osm", f"{len(osm['elements'])} elements (imported from {Path(args.osm_file).name})")
        log(f"OpenStreetMap: imported {len(osm['elements'])} elements from {args.osm_file}")
    else:
        osm = None if "osm" in refresh else load_cached(DATA / "osm.json")
        if osm is None:
            log("OpenStreetMap gluten-free tags (Overpass) ...")
            osm = fetch_osm()
            save(DATA / "osm.json", osm)
            stamp("osm", f"{len(osm.get('elements', []))} elements")
        else:
            log("OpenStreetMap gluten-free tags: cached")

    # 2) Optional: OSM restaurants that list a website (menu URL candidates)
    if args.places or "places" in refresh:
        places = None if "places" in refresh else load_cached(DATA / "places.json")
        if places is None:
            log("OpenStreetMap restaurants with websites (Overpass) ...")
            places = fetch_places()
            save(DATA / "places.json", places)
            stamp("places", f"{len(places.get('elements', []))} elements")
        else:
            log("OpenStreetMap restaurants with websites: cached")

    # 3) Wake County restaurants
    wake = None if "wake" in refresh else load_cached(DATA / "wake.json")
    if wake is None:
        log("Wake County restaurants ...")
        wake = fetch_wake_restaurants()
        save(DATA / "wake.json", wake)
        stamp("wake", f"{len(wake['features'])} facilities")
    else:
        log("Wake County restaurants: cached")

    # 4) Which facilities need inspection history?
    if args.all_facilities:
        ids = [f["attributes"].get("HSISID") for f in wake["features"]]
        needed = {str(i): i for i in ids if i not in (None, "")}
    else:
        needed = gf_map.needed_hsisids(osm, wake, gf_map.load_annotations())
    log(f"{len(needed)} facilities need inspection history")

    # 5) Inspections + violations, fetched only for facilities not already cached
    for name, (layer, fields) in LAYERS.items():
        path = DATA / f"{name}.json"
        cached = None if name in refresh else load_cached(path)
        done = set(manifest["queried"].get(name, [])) if cached else set()
        todo = [needed[k] for k in needed if k not in done]
        if cached and not todo:
            log(f"{name}: cached ({len(cached['features'])} rows for {len(done)} facilities)")
            continue
        log(f"{name}: fetching {len(todo)} facilities ...")
        new = fetch_layer(layer, todo, fields)
        merged = {"fields": (cached or new)["fields"] or new["fields"],
                  "features": (cached["features"] if cached else []) + new["features"]}
        save(path, merged)
        manifest["queried"][name] = sorted(done | set(needed))
        stamp(name, f"{len(merged['features'])} rows / {len(manifest['queried'][name])} facilities")

    # 6) Leaflet, so the map page needs no CDN
    if fetch_vendor("vendor" in refresh):
        stamp("vendor", "leaflet 1.9.4")

    log("\nDone. Cached in " + str(DATA))
    show_status()
    log("\nNext: python3 gf_map.py")


if __name__ == "__main__":
    main()
