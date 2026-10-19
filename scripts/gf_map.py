#!/usr/bin/env python3
"""Build map.html from the cached data in ./data (run fetch_data.py first).

    python3 gf_map.py

Environment overrides:
    LAT, LON    map centre (default: Raleigh Convention Center, approximate)
    RADIUS_M    walking radius to show, in metres (default 1200, about 15 minutes)

Works offline: it only reads data/*.json.
"""
import difflib
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

LAT = float(os.environ.get("LAT", 35.7776))
LON = float(os.environ.get("LON", -78.6393))
RADIUS_M = int(os.environ.get("RADIUS_M", 1200))

MATCH_DIST_M = 150       # OSM point and county point must be within this distance...
MATCH_MIN_NAME = 0.6     # ...and their names at least this similar (0..1)
GOOD_SCORE = 90          # NC grade A
POOR_SCORE = 80          # below this is grade C or worse
EPOCH_MIN = datetime.min.replace(tzinfo=timezone.utc)

STATUS = {
    "good":   ("#1a9850", "Good: score 90+ and no violations in the last 12 months"),
    "mixed":  ("#f59e0b", "Mixed: recent violations, or score 80-89"),
    "poor":   ("#d73027", "Poor: latest score under 80"),
    "nodata": ("#6b7280", "No county match or no inspection data"),
}


# --------------------------------------------------------------------------- helpers
def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "error" in data:
        sys.exit(f"{path}: server returned an error: {data['error']}")
    if isinstance(data, dict) and data.get("remark"):
        print(f"WARNING {path}: {data['remark']}")
    return data


def dist_m(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    h = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * asin(sqrt(h))


def norm(s):
    s = str(s).lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_date(v):
    """ArcGIS dates arrive as epoch milliseconds or as strings; return aware datetime or None."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit() and len(v.strip()) >= 11):
        return datetime.fromtimestamp(float(v) / 1000, tz=timezone.utc)
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def find_field(names, *patterns):
    for pat in patterns:
        for n in names:
            if re.search(pat, n, re.I):
                return n
    return None


def gf_value(tags):
    """Return 'yes' / 'only' / 'limited' if the place offers gluten-free food, else None."""
    v = tags.get("diet:gluten_free")
    if v in ("yes", "only", "limited"):
        return v
    if tags.get("gluten_free") == "yes":
        return "yes"
    return None


def sql_literal(v):
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# --------------------------------------------------------------------------- matching
def match_places(osm, wake, max_dist_m=None, verbose=True):
    """Join OSM gluten-free places to Wake County facilities (by distance + name similarity).

    max_dist_m limits results to places within that many metres of the centre
    (None = keep everything that was downloaded).
    """
    county = []
    for feat in wake.get("features", []):
        g = feat.get("geometry") or {}
        if "x" in g and "y" in g:
            county.append((g["y"], g["x"], feat["attributes"]))

    elements = osm.get("elements", [])
    said_no = sum(1 for el in elements if el.get("tags", {}).get("diet:gluten_free") == "no")
    gf_elements = [el for el in elements if gf_value(el.get("tags", {}))]
    if verbose:
        print(f"   OSM: {len(elements)} places have a gluten-free tag: "
              f"{len(gf_elements)} offer it, {said_no} say 'no'")
        print(f"   County: {len(county)} permitted food establishments downloaded")

    matches = []
    for el in gf_elements:
        tags = el.get("tags", {})
        center = el.get("center", {})
        lat = el.get("lat", center.get("lat"))
        lon = el.get("lon", center.get("lon"))
        name = tags.get("name")
        if not name or lat is None or lon is None:
            continue
        d = dist_m(LAT, LON, lat, lon)
        if max_dist_m is not None and d > max_dist_m:
            continue
        best, best_ratio = None, 0.0
        for clat, clon, attrs in county:
            if dist_m(lat, lon, clat, clon) > MATCH_DIST_M:
                continue
            a, b = norm(name), norm(attrs.get("NAME") or "")
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if a and b and (a in b or b in a):
                ratio = max(ratio, 0.9)
            if ratio > best_ratio:
                best, best_ratio = attrs, ratio
        m = {
            "name": name, "lat": lat, "lon": lon,
            "gluten_free": gf_value(tags),
            "osm_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "dist_m": round(d),
            "hsisid": None, "county_name": None, "address": None,
        }
        if best is not None and best_ratio >= MATCH_MIN_NAME:
            m["hsisid"] = best.get("HSISID")
            m["county_name"] = best.get("NAME")
            m["address"] = ", ".join(str(best[k]) for k in ("ADDRESS1", "CITY") if best.get(k))
        matches.append(m)

    matches.sort(key=lambda m: m["dist_m"])
    return matches


# --------------------------------------------------------------------------- map
def main():
    names = ("osm", "wake", "inspections", "violations")
    missing = [n for n in names if not (DATA / f"{n}.json").exists()]
    if missing:
        sys.exit(f"Missing cached data: {', '.join(missing)}. Run:  python3 fetch_data.py")
    osm, wake, insp_data, viol_data = (load(DATA / f"{n}.json") for n in names)
    try:
        manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}

    matches = match_places(osm, wake, max_dist_m=RADIUS_M)
    print(f"   {len(matches)} gluten-free places within {RADIUS_M} m")
    queried = set(manifest.get("queried", {}).get("inspections", []))
    stale = [m["name"] for m in matches if m["hsisid"] not in (None, "") and str(m["hsisid"]) not in queried]
    if stale and manifest:
        print(f"   NOTE: no cached inspection data yet for: {', '.join(stale)}. Run python3 fetch_data.py")
    for m in matches:
        tag = f"-> {m['county_name']} ({m['hsisid']})" if m["hsisid"] else "-> NO COUNTY MATCH"
        print(f"   {m['dist_m']:>5} m  {m['name']}  {tag}")

    insp = [f["attributes"] for f in insp_data.get("features", [])]
    viol = [f["attributes"] for f in viol_data.get("features", [])]
    insp_names = list(insp[0]) if insp else [f["name"] for f in insp_data.get("fields") or []]
    viol_names = list(viol[0]) if viol else [f["name"] for f in viol_data.get("fields") or []]
    score_f = find_field(insp_names, r"^score$", r"score")
    idate_f = find_field(insp_names, r"^date_?$", r"insp.*date", r"date")
    vdate_f = find_field(viol_names, r"insp.*date", r"date")
    print(f"   inspections: {len(insp)} rows, score field={score_f}, date field={idate_f}")
    print(f"   violations:  {len(viol)} rows, date field={vdate_f}")
    if not score_f:
        print(f"   NOTE: no score field found in layer 1, so pins use violations only. Fields: {insp_names}")

    insp_by, viol_by = {}, {}
    for a in insp:
        insp_by.setdefault(str(a.get("HSISID")), []).append(
            (parse_date(a.get(idate_f)) if idate_f else None,
             to_float(a.get(score_f)) if score_f else None, a))
    for a in viol:
        viol_by.setdefault(str(a.get("HSISID")), []).append(
            (parse_date(a.get(vdate_f)) if vdate_f else None, a))

    cutoff = datetime.now(timezone.utc) - timedelta(days=365)
    esc = lambda x: html.escape(str(x))
    pins, counts = [], {k: 0 for k in STATUS}

    for m in matches:
        key = str(m["hsisid"]) if m["hsisid"] not in (None, "") else None
        rows = sorted(insp_by.get(key, []), key=lambda r: r[0] or EPOCH_MIN, reverse=True)
        recent = [a for d, a in viol_by.get(key, []) if d is None or d >= cutoff]
        latest_date, latest_score = (rows[0][0], rows[0][1]) if rows else (None, None)
        last3 = [f"{r[1]:g}" for r in rows[:3] if r[1] is not None]

        if key is None or (not rows and key not in viol_by):
            status = "nodata"
        elif latest_score is not None and latest_score < POOR_SCORE:
            status = "poor"
        elif (latest_score is None or latest_score >= GOOD_SCORE) and not recent:
            status = "good"
        else:
            status = "mixed"
        counts[status] += 1

        lines = [f"<b>{esc(m['name'])}</b>"]
        if m["county_name"]:
            lines.append(f"County record: {esc(m['county_name'])}")
        if m["address"]:
            lines.append(esc(m["address"]))
        lines.append(f"Gluten-free: <b>{esc(m['gluten_free'])}</b> (OpenStreetMap tag)")
        lines.append(f"{m['dist_m']} m away, ~{max(1, round(m['dist_m'] / 80))} min walk (straight-line)")
        if status == "nodata":
            lines.append("No county inspection data found")
        else:
            if latest_score is not None:
                when = f" on {latest_date:%b %d, %Y}" if latest_date else ""
                lines.append(f"Latest score: <b>{latest_score:g}</b>{when}")
                if len(last3) > 1:
                    lines.append("Recent scores (newest first): " + ", ".join(last3))
            lines.append(f"Violations in the last 12 months: <b>{len(recent)}</b>")
        lines.append("<i>The tag means at least one gluten-free option, not a celiac-safe kitchen. "
                     "Confirm with the restaurant.</i>")
        lines.append(f"<a href='{esc(m['osm_url'])}' target='_blank' rel='noopener'>View/edit on OpenStreetMap</a>")
        pins.append({"lat": m["lat"], "lon": m["lon"], "color": STATUS[status][0],
                     "popup": "<br>".join(lines)})

    legend = [[STATUS[k][0], f"{STATUS[k][1]} ({counts[k]})"] for k in STATUS]
    vend = DATA / "vendor"
    local = (vend / "leaflet.js").exists() and (vend / "leaflet.css").exists()
    css = "data/vendor/leaflet.css" if local else "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    js = "data/vendor/leaflet.js" if local else "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"

    page = TEMPLATE.replace("__LEAFLET_CSS__", css).replace("__LEAFLET_JS__", js)
    for token, value in (("__CENTER__", [LAT, LON]), ("__RADIUS__", RADIUS_M),
                         ("__PINS__", pins), ("__LEGEND__", legend)):
        page = page.replace(token, json.dumps(value).replace("</", "<\\/"))
    (HERE / "map.html").write_text(page, encoding="utf-8")
    print("   " + ", ".join(f"{k}={v}" for k, v in counts.items()) + "  -> map.html"
          + ("" if local else "  (Leaflet from CDN: run fetch_data.py to cache it)"))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gluten-free near the Raleigh Convention Center</title>
<link rel="stylesheet" href="__LEAFLET_CSS__">
<style>
html,body,#map{height:100%;margin:0}
.legend{background:#fff;color:#111;padding:8px 10px;border-radius:6px;font:13px/1.6 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:-1px}
</style></head><body><div id="map"></div>
<script src="__LEAFLET_JS__"></script>
<script>
const CENTER = __CENTER__, RADIUS = __RADIUS__, PINS = __PINS__, LEGEND = __LEGEND__;
const map = L.map('map').setView(CENTER, 15);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
const ring = L.circle(CENTER, {radius: RADIUS, color: '#2563eb', weight: 1, fillOpacity: 0.04}).addTo(map);
L.circleMarker(CENTER, {radius: 8, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1})
  .addTo(map).bindTooltip('Raleigh Convention Center', {permanent: true, direction: 'top'});
PINS.forEach(p => L.circleMarker([p.lat, p.lon],
  {radius: 10, color: '#fff', weight: 2, fillColor: p.color, fillOpacity: 0.95})
  .addTo(map).bindPopup(p.popup));
map.fitBounds(ring.getBounds());
const legend = L.control({position: 'bottomright'});
legend.onAdd = () => {
  const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = LEGEND.map(l => `<div><i style="background:${l[0]}"></i>${l[1]}</div>`).join('');
  return d;
};
legend.addTo(map);
</script></body></html>
"""


if __name__ == "__main__":
    main()
