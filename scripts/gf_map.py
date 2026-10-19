#!/usr/bin/env python3
"""Build map.html: gluten-free places joined with Wake County health data.

    python3 gf_map.py

Gluten-free evidence comes from three places, all attached to Wake County facilities (HSISID):
    1. OpenStreetMap diet:gluten_free tags            (data/osm.json)
    2. Team-verified rows in annotations.csv          (verified = yes)
    3. AI-suggested rows in annotations.csv           (verified = no; shown dashed)
Health data comes from the county (data/inspections.json, data/violations.json).

Environment overrides:
    LAT, LON    map centre (default: Raleigh Convention Center, approximate)
    RADIUS_M    radius to show, in metres (default 1200, about a 15 minute walk)

Works offline: it only reads local files.
"""
import csv
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
ANNOT_FILE = HERE / "annotations.csv"
ANNOT_COLUMNS = ["hsisid", "name", "address", "dist_m", "osm", "gf_status", "verified",
                 "source", "evidence_url", "evidence_note", "checked_by", "checked_on"]
GF_STATUSES = ("only", "yes", "limited", "no", "unknown")
POSITIVE = ("only", "yes", "limited")

LAT = float(os.environ.get("LAT", 35.7776))
LON = float(os.environ.get("LON", -78.6393))
RADIUS_M = int(os.environ.get("RADIUS_M", 1200))

MATCH_DIST_M = 150       # OSM point and county point must be within this distance...
MATCH_MIN_NAME = 0.6     # ...and their names at least this similar (0..1)
GOOD_SCORE = 90          # NC grade A
POOR_SCORE = 80          # below this is grade C or worse
EPOCH_MIN = datetime.min.replace(tzinfo=timezone.utc)
TIER_RANK = {"verified": 0, "osm": 1, "unverified": 2}

STATUS = {
    "good":   ("#1a9850", "Good: score 90+ and no violations in the last 12 months"),
    "mixed":  ("#f59e0b", "Mixed: recent violations, or score 80-89"),
    "poor":   ("#d73027", "Poor: latest score under 80"),
    "nodata": ("#6b7280", "No county inspection data"),
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
    """Return 'yes' / 'only' / 'limited' if an OSM place offers gluten-free food, else None."""
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


# --------------------------------------------------------------------------- annotations.csv
def read_annotation_rows(path=ANNOT_FILE):
    """Raw rows (all columns as written) and the file's column order."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return [], list(ANNOT_COLUMNS)
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or ANNOT_COLUMNS)
        rows = [{k: (v or "") for k, v in r.items() if k} for r in reader]
    return rows, fields


def write_annotation_rows(rows, fields, path=ANNOT_FILE):
    path = Path(path)
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)          # atomic: never leaves a half-written file


def load_annotations(path=ANNOT_FILE):
    """Normalised annotation rows. Unknown or blank statuses become 'unknown'."""
    rows, _ = read_annotation_rows(path)
    out = []
    for i, raw in enumerate(rows, start=2):
        row = {k: v.strip() for k, v in raw.items()}
        if row.get("hsisid", "").startswith("#"):
            continue
        if not row.get("hsisid") and not row.get("osm"):
            continue
        status = row.get("gf_status", "").lower()
        if status and status not in GF_STATUSES:
            print(f"   WARNING annotations.csv line {i}: gf_status {status!r} not one of "
                  f"{', '.join(GF_STATUSES)}; treating as unknown")
            status = ""
        row["gf_status"] = status or "unknown"
        row["verified"] = "yes" if row.get("verified", "").lower() in ("yes", "y", "true", "1") else "no"
        for col in ANNOT_COLUMNS:
            row.setdefault(col, "")
        out.append(row)
    return out


# --------------------------------------------------------------------------- county + matching
def county_index(wake):
    """Every Wake County facility we downloaded, keyed by str(HSISID)."""
    county = {}
    for feat in wake.get("features", []):
        a = feat.get("attributes", {})
        g = feat.get("geometry") or {}
        h = a.get("HSISID")
        if h in (None, ""):
            continue
        county[str(h)] = {
            "hsisid": h, "name": a.get("NAME") or "", "ftype": a.get("FACILITYTYPE") or "",
            "address": ", ".join(str(a[k]) for k in ("ADDRESS1", "CITY") if a.get(k)),
            "lat": g.get("y"), "lon": g.get("x"),
        }
    return county


def resolve_hsisid(h, county):
    """Exact match, or a match that ignores leading zeros (spreadsheets strip them)."""
    h = str(h).strip()
    if h in county:
        return h
    stripped = h.lstrip("0")
    if stripped:
        for k in county:
            if k.lstrip("0") == stripped:
                return k
    return None


def match_places(osm, wake, max_dist_m=None, verbose=True):
    """Join OSM gluten-free places to Wake County facilities (by distance + name similarity)."""
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
            "name": name, "lat": lat, "lon": lon, "gluten_free": gf_value(tags),
            "osm_key": f"{el['type']}/{el['id']}",
            "osm_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "dist_m": round(d), "hsisid": None, "county_name": None, "address": None,
        }
        if best is not None and best_ratio >= MATCH_MIN_NAME:
            m["hsisid"] = best.get("HSISID")
            m["county_name"] = best.get("NAME")
            m["address"] = ", ".join(str(best[k]) for k in ("ADDRESS1", "CITY") if best.get(k))
        matches.append(m)

    matches.sort(key=lambda m: m["dist_m"])
    return matches


def decide(claims):
    """Order claims by trust (verified > OSM > unverified, newest first). Return (ordered, primary, conflict)."""
    ordered = sorted(sorted(claims, key=lambda c: c["on"], reverse=True), key=lambda c: TIER_RANK[c["tier"]])
    primary = next((c for c in ordered if c["status"] != "unknown"), None)
    statuses = {c["status"] for c in ordered if c["status"] != "unknown"}
    conflict = "no" in statuses and bool(statuses & set(POSITIVE))
    return ordered, primary, conflict


def collect_places(osm, wake, annotations, verbose=False):
    """One record per place, with every gluten-free claim from every source attached."""
    county = county_index(wake)
    places, osm_to_key, problems = {}, {}, []

    def get(key, **init):
        return places.setdefault(key, {"key": key, "claims": [], **init})

    for m in match_places(osm, wake, max_dist_m=None, verbose=verbose):
        key = str(m["hsisid"]) if m["hsisid"] not in (None, "") else "osm:" + m["osm_key"]
        p = get(key, hsisid=m["hsisid"], name=m["name"], lat=m["lat"], lon=m["lon"],
                county_name=m["county_name"], address=m["address"], osm_url=m["osm_url"])
        p["claims"].append({"source": "osm", "tier": "osm", "status": m["gluten_free"], "url": m["osm_url"],
                            "note": f"OpenStreetMap diet:gluten_free={m['gluten_free']}", "by": "", "on": ""})
        osm_to_key[m["osm_key"]] = key

    for a in annotations:
        key = None
        if a["hsisid"]:
            k = resolve_hsisid(a["hsisid"], county)
            if k:
                c = county[k]
                key = k
                get(key, hsisid=c["hsisid"], name=c["name"], lat=c["lat"], lon=c["lon"],
                    county_name=c["name"], address=c["address"], osm_url=None)
        if key is None and a["osm"] in osm_to_key:
            key = osm_to_key[a["osm"]]
        if key is None:
            problems.append(a)
            continue
        places[key]["claims"].append({
            "source": a["source"] or "annotation",
            "tier": "verified" if a["verified"] == "yes" else "unverified",
            "status": a["gf_status"], "url": a["evidence_url"], "note": a["evidence_note"],
            "by": a["checked_by"], "on": a["checked_on"]})

    for p in places.values():
        p["claims"], p["primary"], p["conflict"] = decide(p["claims"])
    return places, problems


def needed_hsisids(osm, wake, annotations):
    """HSISIDs (str -> original value) of every place that has any gluten-free claim."""
    places, _ = collect_places(osm, wake, annotations)
    return {str(p["hsisid"]): p["hsisid"] for p in places.values() if p.get("hsisid") not in (None, "")}


def build_places(osm, wake, annotations, max_dist_m=None, verbose=False):
    places, problems = collect_places(osm, wake, annotations, verbose=verbose)
    out = []
    for p in places.values():
        if p.get("lat") is None or p.get("lon") is None:
            problems.append({"name": p.get("name"), "reason": "no coordinates"})
            continue
        p["dist_m"] = round(dist_m(LAT, LON, p["lat"], p["lon"]))
        if max_dist_m is None or p["dist_m"] <= max_dist_m:
            out.append(p)
    out.sort(key=lambda p: p["dist_m"])
    return out, problems


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

    annotations = load_annotations()
    places, problems = build_places(osm, wake, annotations, max_dist_m=RADIUS_M, verbose=True)
    for pr in problems:
        label = pr.get("name") or pr.get("hsisid") or pr.get("osm")
        print(f"   WARNING: could not place annotation for {label!r} "
              f"(HSISID not in downloaded county data; try a bigger CACHE_RADIUS_M?)")
    shown = [p for p in places if p["primary"] and p["primary"]["status"] in POSITIVE]
    print(f"   {len(annotations)} annotation rows; {len(shown)} gluten-free places within {RADIUS_M} m "
          f"({len(places) - len(shown)} other annotated places hidden: no / unknown)")

    for p in places:
        if p["conflict"] and p not in shown:
            print(f"   NOTE: hiding {p['name']}: a {p['primary']['tier']} annotation says "
                  f"'{p['primary']['status']}' although another source says it offers gluten-free")

    queried = set(manifest.get("queried", {}).get("inspections", []))
    stale = [p["name"] for p in shown if p.get("hsisid") not in (None, "") and str(p["hsisid"]) not in queried]
    if stale and manifest:
        print(f"   NOTE: no cached inspection data yet for: {', '.join(stale)}. Run python3 fetch_data.py")

    insp = [f["attributes"] for f in insp_data.get("features", [])]
    viol = [f["attributes"] for f in viol_data.get("features", [])]
    insp_names = list(insp[0]) if insp else [f["name"] for f in insp_data.get("fields") or []]
    viol_names = list(viol[0]) if viol else [f["name"] for f in viol_data.get("fields") or []]
    score_f = find_field(insp_names, r"^score$", r"score")
    idate_f = find_field(insp_names, r"^date_?$", r"insp.*date", r"date")
    vdate_f = find_field(viol_names, r"insp.*date", r"date")
    print(f"   inspections: {len(insp)} rows, score field={score_f}, date field={idate_f}")
    print(f"   violations:  {len(viol)} rows, date field={vdate_f}")

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
    TIER_TEXT = {"verified": "verified by team", "osm": "from OpenStreetMap tag"}

    for p in shown:
        prim = p["primary"]
        key = str(p["hsisid"]) if p.get("hsisid") not in (None, "") else None
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

        who = f" ({esc(prim['by'])}, {esc(prim['on'])})" if prim["tier"] == "verified" and prim["by"] else ""
        lines = [f"<b>{esc(p['name'])}</b>"]
        if p.get("county_name") and norm(p["county_name"]) != norm(p["name"]):
            lines.append(f"County record: {esc(p['county_name'])}")
        if p.get("address"):
            lines.append(esc(p["address"]))
        if prim["tier"] == "unverified":
            tier_text = ("AI-suggested, NOT yet verified" if prim["source"] == "llm"
                         else "reported, NOT yet verified")
        else:
            tier_text = TIER_TEXT[prim["tier"]]
        lines.append(f"Gluten-free: <b>{esc(prim['status'])}</b>, {tier_text}{who}")
        if p["conflict"]:
            lines.append("<b>Sources disagree</b> (one says no); check the evidence below")
        lines.append(f"{p['dist_m']} m away, ~{max(1, round(p['dist_m'] / 80))} min walk (straight-line)")
        if status == "nodata":
            lines.append("No county inspection data found")
        else:
            if latest_score is not None:
                when = f" on {latest_date:%b %d, %Y}" if latest_date else ""
                lines.append(f"Latest score: <b>{latest_score:g}</b>{when}")
                if len(last3) > 1:
                    lines.append("Recent scores (newest first): " + ", ".join(last3))
            lines.append(f"Violations in the last 12 months: <b>{len(recent)}</b>")
        ev = []
        for c in p["claims"]:
            if c["status"] == "unknown" and not (c["note"] or c["url"]):
                continue            # a blank seeded row carries no evidence
            note = (c["note"] or "")[:240]
            link = f" <a href='{esc(c['url'])}' target='_blank' rel='noopener'>link</a>" if c["url"] else ""
            ev.append(f"&bull; {esc(c['source'])} [{esc(c['tier'])}] {esc(c['status'])}: {esc(note)}{link}")
        lines.append("Evidence:<br>" + "<br>".join(ev))
        lines.append("<i>Gluten-free here means at least one option, not a celiac-safe kitchen. "
                     "Confirm with the restaurant.</i>")
        pins.append({"lat": p["lat"], "lon": p["lon"], "color": STATUS[status][0],
                     "dashed": prim["tier"] == "unverified", "popup": "<br>".join(lines)})

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
    for p in shown:
        prim = p["primary"]
        print(f"   {p['dist_m']:>5} m  {prim['status']:<8} {prim['tier']:<10} {p['name']}")
    print("   " + ", ".join(f"{k}={v}" for k, v in counts.items()) + "  -> map.html"
          + ("" if local else "  (Leaflet from CDN: run fetch_data.py to cache it)"))


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gluten-free near the Raleigh Convention Center</title>
<link rel="stylesheet" href="__LEAFLET_CSS__">
<style>
html,body,#map{height:100%;margin:0}
.legend{background:#fff;color:#111;padding:8px 10px;border-radius:6px;font:13px/1.6 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3);max-width:290px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:-1px}
.legend .note{margin-top:6px;padding-top:6px;border-top:1px solid #ddd;font-size:12px}
</style></head><body><div id="map"></div>
<script src="__LEAFLET_JS__"></script>
<script>
const CENTER = __CENTER__, RADIUS = __RADIUS__, PINS = __PINS__, LEGEND = __LEGEND__;
const map = L.map('map').setView(CENTER, 15);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);
map.attributionControl.addAttribution(
  'Health inspections: <a href="https://data.wake.gov/">Wake County, NC</a> open data'
  + ' &middot; Gluten-free claims: restaurant websites, checked by <a href="https://github.com/rippy/ato_mlh_2026">our team</a>');
const ring = L.circle(CENTER, {radius: RADIUS, color: '#2563eb', weight: 1, fillOpacity: 0.04}).addTo(map);
L.circleMarker(CENTER, {radius: 8, color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1})
  .addTo(map).bindTooltip('Raleigh Convention Center', {permanent: true, direction: 'top'});
PINS.forEach(p => L.circleMarker([p.lat, p.lon], p.dashed
  ? {radius: 10, color: p.color, weight: 3, dashArray: '3 3', fillColor: p.color, fillOpacity: 0.25}
  : {radius: 10, color: '#fff', weight: 2, fillColor: p.color, fillOpacity: 0.95})
  .addTo(map).bindPopup(p.popup, {maxWidth: 340}));
map.fitBounds(ring.getBounds());
const legend = L.control({position: 'bottomright'});
legend.onAdd = () => {
  const d = L.DomUtil.create('div', 'legend');
  d.innerHTML = LEGEND.map(l => `<div><i style="background:${l[0]}"></i>${l[1]}</div>`).join('')
    + '<div class="note">Solid pin: gluten-free confirmed by the team or tagged in OpenStreetMap.<br>'
    + 'Dashed pin: not yet verified (for example AI-suggested from the restaurant\\'s own website).</div>';
  return d;
};
legend.addTo(map);
</script></body></html>
"""


if __name__ == "__main__":
    main()
