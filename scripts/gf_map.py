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
GF_STATUS_ICON = {
    "only": ("⊙", "Only gluten-free items"),
    "yes": ("✓", "Gluten-free options available"),
    "limited": ("◐", "Limited gluten-free options"),
    "no": ("×", "No gluten-free options"),
    "unknown": ("?", "Gluten-free status unknown"),
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


def score_sparkline(scores):
    """Return a compact SVG trend line for scores listed newest first."""
    if len(scores) < 2:
        return ""
    chronological = list(reversed(scores))
    low, high = min(chronological), max(chronological)
    spread = high - low or 1
    points = []
    for index, score in enumerate(chronological):
        x = 3 + index * 66 / (len(chronological) - 1)
        y = 17 - (score - low) * 14 / spread
        points.append((x, y))
    point_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    markers = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2"/>'
                      for x, y in points)
    label = ", ".join(f"{score:g}" for score in chronological)
    return (f'<svg class="popup-sparkline" viewBox="0 0 72 20" role="img" '
            f'aria-label="Inspection scores, oldest to newest: {label}">'
            f'<polyline points="{point_text}"/>{markers}</svg>')


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
        recent_scores = [r[1] for r in rows[:3] if r[1] is not None]

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
        lines = [f'<div class="popup-title">{esc(p["name"])}</div>']
        if p.get("county_name") and norm(p["county_name"]) != norm(p["name"]):
            lines.append(f'<div class="popup-row"><span class="popup-label">County record</span>'
                         f'<span class="popup-value">{esc(p["county_name"])}</span></div>')
        if p.get("address"):
            lines.append(f'<div class="popup-address">{esc(p["address"])}</div>')
        lines.append(f'<div class="popup-distance">{p["dist_m"]} m away, '
                     f'~{max(1, round(p["dist_m"] / 80))} min walk (straight-line)</div>')
        if prim["tier"] == "unverified":
            tier_text = ("AI-suggested, NOT yet verified" if prim["source"] == "llm"
                         else "reported, NOT yet verified")
        else:
            tier_text = TIER_TEXT[prim["tier"]]
        icon, icon_label = GF_STATUS_ICON.get(prim["status"], GF_STATUS_ICON["unknown"])
        lines.append(f'<div class="popup-row"><span class="popup-label">Gluten-free</span>'
                     f'<span class="gf-status"><span class="gf-icon" role="img" '
                     f'aria-label="{esc(icon_label)}" title="{esc(icon_label)}">{icon}</span> '
                     f'<span class="popup-value">{esc(prim["status"])}</span></span> '
                     f'<span class="popup-detail">{tier_text}{who}</span></div>')
        if p["conflict"]:
            lines.append('<div class="popup-alert"><b>Sources disagree</b> (one says no)</div>')
        lines.append('<div class="popup-section">Inspection</div>')
        latest_value = f"{latest_score:g}" if latest_score is not None else "-"
        when = f" ({latest_date:%b %d, %Y})" if latest_date and latest_score is not None else ""
        if len(recent_scores) > 1:
            recent_value = score_sparkline(recent_scores)
        elif recent_scores:
            recent_value = f'<span class="popup-value">{recent_scores[0]:g}</span>'
        else:
            recent_value = '<span class="popup-value">-</span>'
        violations_value = str(len(recent)) if status != "nodata" else "-"
        lines.append('<div class="popup-inspection-grid">'
                     '<div><span class="popup-label">Latest Score</span>'
                     f'<span class="popup-value">{latest_value}</span>'
                     f'<span class="popup-date">{when}</span></div>'
                     '<div><span class="popup-label">Last 3 Scores</span>'
                     f'{recent_value}</div>'
                     '<div><span class="popup-label">Violations (last 12M)</span>'
                     f'<span class="popup-value">{violations_value}</span></div></div>')
        lines.append('<div class="popup-note">At least one gluten-free option; not necessarily a '
                     'celiac-safe kitchen. Confirm with the restaurant.</div>')
        pins.append({"lat": p["lat"], "lon": p["lon"], "name": p["name"],
                     "latest_score": latest_score, "gf_status": prim["status"],
                     "color": STATUS[status][0], "dashed": prim["tier"] == "unverified",
                     "popup": "".join(lines)})

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
.legend{box-sizing:border-box;width:min(290px,calc(100vw - 32px));max-height:calc(100vh - 32px);display:flex;flex-direction:column;overflow:hidden;background:#fff;color:#111;padding:10px 12px;border-radius:6px;font:13px/1.6 system-ui,sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)}
.legend i{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:6px;vertical-align:-1px}
.restaurants{display:flex;flex:1 1 auto;min-height:0;flex-direction:column;margin-bottom:8px;padding-bottom:8px;border-bottom:1px solid #ddd}
.restaurants h2{margin:0 0 4px;font-size:14px;line-height:1.4}
.restaurant-list-wrap{display:flex;flex:1 1 auto;min-height:0;gap:5px}
.restaurants ul{flex:1 1 auto;min-width:0;min-height:0;overflow-y:auto;overscroll-behavior:contain;margin:0;padding:0;list-style:none;scrollbar-width:none}
.restaurants ul::-webkit-scrollbar{display:none;width:0}
.restaurant-scrollbar{position:relative;flex:0 0 12px;min-height:28px;margin:3px 0;border:1px solid #9ca3af;border-radius:7px;background:#e5e7eb;cursor:pointer;touch-action:none}
.restaurant-scrollbar-thumb{position:absolute;top:0;left:1px;width:8px;min-height:26px;border-radius:5px;background:#555;cursor:grab}
.restaurant-scrollbar-thumb:hover{background:#333}
.restaurant-scrollbar-thumb:active{cursor:grabbing}
.restaurants li{margin:0}
.restaurant-button{display:flex;width:100%;align-items:center;justify-content:space-between;gap:8px;padding:3px 0;border:0;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}
.restaurant-button:hover{text-decoration:underline}
.restaurant-button:focus-visible{outline:2px solid #2563eb;outline-offset:1px}
.restaurants .score{display:inline-block;min-width:2.5em;padding:1px 6px;border-radius:999px;font-weight:700;text-align:center;white-space:nowrap}
.restaurant-badges{display:inline-flex;align-items:center;gap:4px;flex:none}
.restaurant-gf-icon{display:inline-grid;width:18px;height:18px;place-items:center;border:1px solid #777;border-radius:50%;font-size:12px;font-weight:700;line-height:1}
.legend .note{margin-top:6px;padding-top:6px;border-top:1px solid #ddd;font-size:12px}
.pin-example{display:inline-block;width:13px;height:13px;box-sizing:border-box;margin-right:5px;border:1.5px solid #000;border-radius:50%;background:rgba(245,158,11,.95);vertical-align:-2px}
.pin-example.dashed{border-width:2px;border-style:dashed;background:rgba(245,158,11,.25)}
.popup-title{font-size:16px;font-weight:700;line-height:1.3}
.popup-section{margin-top:5px;padding-top:4px;border-top:1px solid #ddd;font-size:12px;font-weight:700}
.popup-inspection-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin-top:4px}
.popup-inspection-grid .popup-label{text-transform:none;font-size:10px;line-height:1.2}
.popup-row{margin-top:3px}
.popup-label{display:block;color:#555;font-size:10px;font-weight:700;text-transform:uppercase}
.popup-value{font-size:13px;font-weight:600}
.popup-date{margin-left:4px;color:#666;font-size:11px;font-weight:400}
.popup-detail,.popup-address{color:#444;font-size:12px}
.popup-address{margin-top:1px}
.popup-distance{color:#555;font-size:11px}
.gf-status{display:inline-flex;align-items:center;gap:3px}
.gf-icon{display:inline-grid;width:15px;height:15px;place-items:center;border:1px solid #777;border-radius:50%;font-size:10px;font-weight:700;line-height:1}
.popup-sparkline{display:block;width:72px;height:20px;margin-top:1px;overflow:visible}
.popup-sparkline polyline{fill:none;stroke:#2563eb;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.popup-sparkline circle{fill:#2563eb;stroke:#fff;stroke-width:.8}
.popup-alert{margin-top:3px;color:#9b1c1c;font-size:12px;font-weight:600}
.popup-note{margin-top:5px;padding-top:4px;border-top:1px solid #ddd;color:#555;font-size:11px;font-style:italic}
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
const markers = PINS.map(p => {
    const marker = L.circleMarker([p.lat, p.lon], p.dashed
            ? {radius: 10, color: '#000', weight: 2, dashArray: '3 3', fillColor: p.color, fillOpacity: 0.25}
            : {radius: 10, color: '#000', weight: 1.5, fillColor: p.color, fillOpacity: 0.95});
    marker.addTo(map).bindPopup(p.popup, {maxWidth: 340});
    return marker;
});
map.fitBounds(ring.getBounds());
const legend = L.control({position: 'bottomright'});
legend.onAdd = () => {
  const d = L.DomUtil.create('div', 'legend');
    L.DomEvent.disableClickPropagation(d);
  d.innerHTML = LEGEND.map(l => `<div><i style="background:${l[0]}"></i>${l[1]}</div>`).join('')
        + '<div class="note"><div><span class="pin-example"></span>Solid pin: gluten-free confirmed by the team or tagged in OpenStreetMap.</div>'
        + '<div><span class="pin-example dashed"></span>Dashed pin: not yet verified (for example AI-suggested from the restaurant\\'s own website).</div></div>';
    const restaurants = document.createElement('section');
    restaurants.className = 'restaurants';
    const title = document.createElement('h2');
    title.textContent = 'Gluten Free Restaurants';
    restaurants.appendChild(title);
    const listWrap = document.createElement('div');
    listWrap.className = 'restaurant-list-wrap';
    const list = document.createElement('ul');
    list.tabIndex = 0;
    list.setAttribute('aria-label', 'Gluten-free restaurants sorted by status and score');
    const statusRank = {yes: 0, limited: 1};
    const rankedPins = PINS.map((pin, index) => ({pin, index})).sort((a, b) => {
        const aStatus = statusRank[a.pin.gf_status] ?? 2;
        const bStatus = statusRank[b.pin.gf_status] ?? 2;
        if (aStatus !== bStatus) return aStatus - bStatus;
        const aScore = a.pin.latest_score;
        const bScore = b.pin.latest_score;
        if (aScore == null && bScore != null) return 1;
        if (aScore != null && bScore == null) return -1;
        return (bScore ?? 0) - (aScore ?? 0) || a.pin.name.localeCompare(b.pin.name);
    });
    rankedPins.forEach(({pin, index}) => {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'restaurant-button';
        button.textContent = pin.name;
        button.addEventListener('click', () => {
            const marker = markers[index];
            map.setView(marker.getLatLng(), Math.max(map.getZoom(), 16));
            marker.openPopup();
        });
        const badges = document.createElement('span');
        badges.className = 'restaurant-badges';
        const gfIcon = document.createElement('span');
        gfIcon.className = 'restaurant-gf-icon';
        gfIcon.textContent = pin.gf_status === 'limited' ? '◐' : '✓';
        gfIcon.title = pin.gf_status === 'limited' ? 'Limited gluten-free options' : 'Gluten-free options available';
        gfIcon.setAttribute('aria-label', gfIcon.title);
        const score = document.createElement('span');
        score.className = 'score';
        score.style.backgroundColor = pin.color;
        score.style.color = pin.color === '#1a9850' || pin.color === '#f59e0b' ? '#111' : '#fff';
        score.textContent = pin.latest_score === null ? '-' : `${pin.latest_score}`;
        badges.appendChild(gfIcon);
        badges.appendChild(score);
        button.appendChild(badges);
        item.appendChild(button);
        list.appendChild(item);
    });
        const scrollbar = document.createElement('div');
        scrollbar.className = 'restaurant-scrollbar';
        scrollbar.setAttribute('aria-hidden', 'true');
        const thumb = document.createElement('div');
        thumb.className = 'restaurant-scrollbar-thumb';
        scrollbar.appendChild(thumb);
        const updateScrollbar = () => {
            const trackHeight = scrollbar.clientHeight;
            const scrollRange = list.scrollHeight - list.clientHeight;
            const thumbHeight = Math.min(trackHeight, Math.max(28, trackHeight * list.clientHeight / list.scrollHeight));
            const thumbRange = trackHeight - thumbHeight;
            thumb.style.height = `${thumbHeight}px`;
            thumb.style.transform = `translateY(${scrollRange > 0 ? thumbRange * list.scrollTop / scrollRange : 0}px)`;
        };
        let dragStartY = 0;
        let dragStartScroll = 0;
        thumb.addEventListener('pointerdown', event => {
            event.preventDefault();
            dragStartY = event.clientY;
            dragStartScroll = list.scrollTop;
            thumb.setPointerCapture(event.pointerId);
        });
        thumb.addEventListener('pointermove', event => {
            if (!thumb.hasPointerCapture(event.pointerId)) return;
            const thumbRange = scrollbar.clientHeight - thumb.offsetHeight;
            const scrollRange = list.scrollHeight - list.clientHeight;
            if (thumbRange > 0) {
                list.scrollTop = dragStartScroll + (event.clientY - dragStartY) * scrollRange / thumbRange;
            }
        });
        scrollbar.addEventListener('pointerdown', event => {
            if (event.target === thumb) return;
            const bounds = scrollbar.getBoundingClientRect();
            list.scrollTop = ((event.clientY - bounds.top) / bounds.height) * list.scrollHeight - list.clientHeight / 2;
        });
        list.addEventListener('scroll', updateScrollbar);
        listWrap.appendChild(list);
        listWrap.appendChild(scrollbar);
        restaurants.appendChild(listWrap);
    d.insertBefore(restaurants, d.firstChild);
        requestAnimationFrame(updateScrollbar);
  return d;
};
legend.addTo(map);
</script></body></html>
"""


if __name__ == "__main__":
    main()
