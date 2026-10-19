# ato_mlh_2026: gluten-free restaurant finder (Raleigh)

Hackathon project (MLH Hacktoberfest Hacks @ All Things Open, 2026-10-19). Projects due 2:00 PM,
presentations 2:30 (demo video required), winners 4:15. Read HANDOFF.md for the full story.

## What it does
Pins allergy-friendly (starting with gluten-free) restaurants near the Raleigh Convention Center
(35.7776, -78.6393) on an OpenStreetMap map, coloured by Wake County health-inspection data.
Filters: gluten-free claim, recent good score, few recent violations, within a 15-minute walk (~1200 m).

## Layout (all in scripts/, Python 3 stdlib + curl only; no pip installs)
- fetch_data.py   download + cache into data/ (OSM via Overpass, Wake County ArcGIS, Leaflet). Incremental.
- annotate.py     find / add / seed / list rows in annotations.csv
- enrich_menu.py  reads restaurant websites -> gf_status. Backends: rules | claude | apfel | openai
- gf_map.py       joins everything, writes map.html (offline once data/ is cached)
- annotations.csv the human + machine gluten-free claims (the heart of the project)
- gf_finder.sh    fetch_data.py then gf_map.py

## Run
    cd scripts
    python3 fetch_data.py --places
    python3 annotate.py seed --limit 60
    python3 enrich_menu.py --backend rules
    python3 enrich_menu.py --backend claude --redo --limit 5   # then --workers 3
    python3 fetch_data.py && python3 gf_map.py
    python3 -m http.server 8000      # open http://localhost:8000/map.html (OSM tiles need a Referer; file:// fails)

## Rules that matter
- Trust order: human-verified > OSM tag > unverified. verified=yes -> solid pin, verified=no -> DASHED pin.
- Machine output (rules/llm) is ALWAYS written verified=no. Never set verified=yes on a machine result;
  only a human who opened evidence_url and read the quote does that.
- A model's answer is kept only if its quote appears word for word on the page; otherwise unknown.
- Never overwrite human rows. enrich_menu --redo only touches rows with empty/machine checked_by and verified != yes.
- annotations.csv: do not clobber on re-extract; HSISIDs may lose leading zeros in spreadsheets (loader tolerates it).
- Pin colours: green score>=90 and 0 violations in 12 months; amber mixed; red <80; gray no data.
- Do not circumvent network blocks; if a site or API refuses, say so. Be polite to restaurant sites
  (identified User-Agent, 1 s pause, max 2 extra same-site pages).
- Overpass is overloaded and flaky: cache and commit data/. Fallback: fetch_data.py --osm-file x.json.
- Claude CLI headless: use `claude -p ... --model haiku --tools ""`; do NOT use --bare (needs ANTHROPIC_API_KEY).
- apfel has a 4096-token context: keep excerpts ~2600 chars.
- Keep it stdlib-only and simple. 2:00 PM deadline beats polish.

## Still to do before 2:00
LICENSE (MIT, 2026, list all team names), commit data/ and annotations.csv, README for judges,
record demo video, submit. Attribution: (c) OpenStreetMap contributors (ODbL); Wake County open data (credit Wake County GIS; license terms vary per dataset, CC BY 4.0 NOT verified).

## Prize notes
Claude/apfel are not open-weight. "Best Open-Source AI" / "Best Use of DigitalOcean" need an open-weight
model (e.g. Ollama via --backend openai with LLM_BASE) or DO inference, plus a public repo with an open-source LICENSE.
