# Gluten-free finder (scripts/)

Finds gluten-free restaurants near the Raleigh Convention Center and joins them with Wake County
health inspections on an OpenStreetMap map. Python 3 standard library + curl only.

## Run order
    python3 fetch_data.py --places        # download + cache into data/ (reruns only fetch what is missing)
    python3 annotate.py seed --limit 60   # blank annotation rows for nearby restaurants (menu URLs from OSM)
    export MODEL_ACCESS_KEY=...           # DigitalOcean: Serverless inference > Model Access Keys
    python3 enrich_menu.py --list-models  # confirm the key and exact model IDs (set DO_MODEL if needed)
    python3 enrich_menu.py --dry-run      # then --limit 5, then the full run
    python3 fetch_data.py                 # pull health data for newly annotated places
    python3 gf_map.py                     # build map.html (works offline)
    python3 -m http.server 8000           # open http://localhost:8000/map.html (OSM tiles need a Referer)

`./gf_finder.sh` runs fetch_data.py then gf_map.py.
`CACHE_RADIUS_M=8000` (and `RADIUS_M=...` for the map) covers more of Raleigh. LAT/LON move the centre.

## Annotating
Edit annotations.csv in a spreadsheet, or: `python3 annotate.py find <name>` / `add "<name>" --gf yes --url ... --note ... --by you`.
gf_status: only | yes | limited | no | unknown.  verified=yes -> solid pin; verified=no -> dashed pin.
AI results are always written verified=no: open evidence_url, check the quote, then set verified=yes.

## Notes
- Do not re-extract this zip over a working copy without backing up annotations.csv (it would be overwritten). data/ is not in the zip.
- Overpass is often overloaded: cache and commit data/. Fallback: --osm-file x.json (export from overpass-turbo.eu).
- Attribution: (c) OpenStreetMap contributors (ODbL); Wake County open data (credit Wake County GIS; see top-level README).
