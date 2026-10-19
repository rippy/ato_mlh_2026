#!/usr/bin/env python3
"""Find, add and seed gluten-free annotations (annotations.csv).

The county list (about 430 places within 3 km) is the full set of restaurants. Every gluten-free
claim, whether from OpenStreetMap, a human, or the AI step, is a row in annotations.csv that
points at a county facility by HSISID. Edit the CSV in any spreadsheet app, or use this tool.

    python3 annotate.py find beasley                 look up a restaurant's HSISID
    python3 annotate.py add "Beasley's" --gf yes --source menu \\
        --url https://example.com/menu --note "dedicated GF fryer" --by rippy
    python3 annotate.py seed --limit 60              add blank rows for the nearest restaurants
                                                      (menu URLs pre-filled from OSM when known)
    python3 annotate.py list                         show every annotation row

gf_status is one of: only (whole menu GF), yes (GF menu or clearly marked GF items),
limited (vague accommodation, few items), no, unknown.
Rows marked verified=yes show as solid pins; verified=no (e.g. AI-suggested) show as dashed pins.

Note: spreadsheets strip leading zeros from IDs like 04092017423. That is fine, the loader
matches IDs ignoring leading zeros.
"""
import argparse
import difflib
import os
import sys
from collections import Counter
from datetime import date

import gf_map
from gf_map import ANNOT_COLUMNS, GF_STATUSES


def county_list():
    wake = gf_map.DATA / "wake.json"
    if not wake.exists():
        sys.exit("No cached county data. Run: python3 fetch_data.py")
    idx = gf_map.county_index(gf_map.load(wake))
    for c in idx.values():
        c["dist_m"] = (round(gf_map.dist_m(gf_map.LAT, gf_map.LON, c["lat"], c["lon"]))
                       if c["lat"] is not None else 10 ** 9)
    return idx


def search(idx, text):
    q = gf_map.norm(text)
    hits = []
    for c in idx.values():
        n = gf_map.norm(c["name"])
        if not q or not n:
            continue
        score = 1.0 if q in n else difflib.SequenceMatcher(None, q, n).ratio()
        if score >= 0.55:
            hits.append((score, c))
    hits.sort(key=lambda t: (-t[0], t[1]["dist_m"]))
    return hits


def show_hits(hits, limit=15):
    print(f"  {'HSISID':<13} {'dist':>6}  {'type':<22} name / address")
    for _, c in hits[:limit]:
        print(f"  {str(c['hsisid']):<13} {c['dist_m']:>5}m  {c['ftype'][:22]:<22} {c['name']}  ({c['address']})")


def append_rows(new_rows):
    rows, fields = gf_map.read_annotation_rows()
    for col in ANNOT_COLUMNS:
        if col not in fields:
            fields.append(col)
    gf_map.write_annotation_rows(rows + new_rows, fields)


def cmd_find(args):
    idx = county_list()
    hits = search(idx, " ".join(args.text))
    if not hits:
        sys.exit("No county facility matches that name. Try fewer words, or check the spelling.")
    show_hits(hits)


def cmd_add(args):
    idx = county_list()
    target = " ".join(args.target).strip()
    key = gf_map.resolve_hsisid(target, idx) if target.isdigit() else None
    if key is None:
        hits = search(idx, target)
        exact = [h for h in hits if h[0] == 1.0]
        if len(exact) == 1:
            key = str(exact[0][1]["hsisid"])
        elif not exact and len(hits) >= 1 and hits[0][0] >= 0.8 and (len(hits) == 1 or hits[1][0] < hits[0][0] - 0.1):
            key = str(hits[0][1]["hsisid"])
        else:
            print(f"Could not pick a single match for {target!r}. Candidates (rerun `add` with an HSISID):")
            show_hits(hits or [])
            sys.exit(1)
    c = idx[key]
    row = {
        "hsisid": str(c["hsisid"]), "name": c["name"], "address": c["address"], "dist_m": str(c["dist_m"]),
        "osm": "", "gf_status": args.gf, "verified": "no" if args.unverified else "yes",
        "source": args.source, "evidence_url": args.url or "", "evidence_note": args.note or "",
        "checked_by": args.by or os.environ.get("USER", ""), "checked_on": date.today().isoformat(),
    }
    append_rows([row])
    print(f"Added: {c['name']} ({c['hsisid']}) -> {args.gf}, verified={row['verified']}. "
          f"Rerun `python3 fetch_data.py` (health data for new places) then `python3 gf_map.py`.")


def website_candidates(idx):
    """Map HSISID -> website, by matching OSM restaurants (places.json) to county facilities."""
    path = gf_map.DATA / "places.json"
    if not path.exists():
        return {}
    out = {}
    for el in gf_map.load(path).get("elements", []):
        tags = el.get("tags", {})
        url = (tags.get("website") or tags.get("contact:website") or "").strip()
        name = tags.get("name")
        center = el.get("center", {})
        lat = el.get("lat", center.get("lat"))
        lon = el.get("lon", center.get("lon"))
        if not (url and name) or lat is None or lon is None:
            continue
        if "://" not in url:
            url = "http://" + url
        best, best_ratio = None, 0.0
        for k, c in idx.items():
            if c["lat"] is None or gf_map.dist_m(lat, lon, c["lat"], c["lon"]) > gf_map.MATCH_DIST_M:
                continue
            a, b = gf_map.norm(name), gf_map.norm(c["name"])
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if a and b and (a in b or b in a):
                ratio = max(ratio, 0.9)
            if ratio > best_ratio:
                best, best_ratio = k, ratio
        if best and best_ratio >= gf_map.MATCH_MIN_NAME:
            out.setdefault(best, url)
    return out


def cmd_seed(args):
    idx = county_list()
    max_dist = args.max_dist or gf_map.RADIUS_M
    nearby = [c for c in idx.values() if c["dist_m"] <= max_dist]
    print(f"{len(nearby)} county facilities within {max_dist} m. Facility types: "
          + ", ".join(f"{t or '?'}={n}" for t, n in Counter(c["ftype"] for c in nearby).most_common(8)))
    rows, _ = gf_map.read_annotation_rows()
    existing = {r.get("hsisid", "").strip().lstrip("0") for r in rows}
    cands = [c for c in nearby if args.type.lower() in c["ftype"].lower()
             and str(c["hsisid"]).lstrip("0") not in existing]
    cands.sort(key=lambda c: c["dist_m"])
    cands = cands[:args.limit]
    sites = website_candidates(idx)
    new = []
    for c in cands:
        url = sites.get(str(c["hsisid"]), "")
        new.append({"hsisid": str(c["hsisid"]), "name": c["name"], "address": c["address"],
                    "dist_m": str(c["dist_m"]), "osm": "", "gf_status": "", "verified": "",
                    "source": "website" if url else "", "evidence_url": url, "evidence_note": "",
                    "checked_by": "", "checked_on": ""})
    if not new:
        sys.exit(f"No new facilities of type containing {args.type!r}. Use --type '' to include all types.")
    append_rows(new)
    with_url = sum(1 for r in new if r["evidence_url"])
    print(f"Added {len(new)} blank rows ({with_url} with a website pre-filled) to {gf_map.ANNOT_FILE.name}.")
    if not sites:
        print("Tip: `python3 fetch_data.py --places` downloads OSM websites so rows can be pre-filled.")
    print("Next: fill in gf_status/verified/evidence in a spreadsheet, or run `python3 enrich_menu.py` "
          "to have the AI read the pre-filled menu pages.")


def cmd_list(args):
    rows = gf_map.load_annotations()
    if not rows:
        print("annotations.csv has no usable rows yet.")
        return
    counts = Counter((r["gf_status"], r["verified"]) for r in rows)
    print(f"{len(rows)} rows. By (status, verified): " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for r in rows:
        if args.all or r["gf_status"] != "unknown":
            print(f"  {r['hsisid']:<13} {r['gf_status']:<8} verified={r['verified']:<3} "
                  f"{r['source']:<10} {r['name'][:36]:<36} {r['evidence_url'][:50]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("find", help="look up restaurants by name")
    p.add_argument("text", nargs="+")
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser("add", help="append one annotation row")
    p.add_argument("target", nargs="+", help="HSISID or (part of) the restaurant name")
    p.add_argument("--gf", required=True, choices=GF_STATUSES)
    p.add_argument("--source", default="menu", help="menu, website, phone, visit, findmeglutenfree, ...")
    p.add_argument("--url", help="link to the evidence")
    p.add_argument("--note", help="what you saw, ideally quoting the menu")
    p.add_argument("--by", help="your name (default: $USER)")
    p.add_argument("--unverified", action="store_true", help="mark verified=no (default is verified=yes)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("seed", help="add blank rows for the nearest restaurants")
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--type", default="restaurant", help="substring of the county FACILITYTYPE ('' = all)")
    p.add_argument("--max-dist", type=int, help="metres from the centre (default: RADIUS_M)")
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("list", help="show annotation rows")
    p.add_argument("--all", action="store_true", help="include rows whose status is still unknown")
    p.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
