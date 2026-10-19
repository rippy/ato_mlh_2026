# Handoff: how this project got here (use for the demo story)

## Goal
Find nearby allergy-friendly restaurants (gluten-free first) around the Raleigh Convention Center using
open data (OpenStreetMap + Wake County/Raleigh), keeping only places with good, recent health scores,
a clean violation trend, and a <=15-minute walk; show them as pins on an OpenStreetMap map.

## Data sources and what we learned
- OpenStreetMap via Overpass: no account or key needed. Tags `diet:gluten_free` (yes/only/limited/no) and `gluten_free`.
  FINDING: gluten-free tagging is nearly absent. Only 1 tagged place within 8 km of the Convention Center.
  This is the key insight: the map needs an annotation layer on top of open data.
- Wake County ArcGIS REST (no key): layer 0 Restaurants (~430 permitted food places within 3 km; 1275 within 8 km),
  layer 1 Food Inspections (SCORE, DATE_), layer 2 Violations (HSISID, INSPECTDATE, SEVERITY, POINTVALUE...).
  HSISID is the join key (11 digits, leading zero).
- findmeglutenfree.com/us/nc/raleigh: human reference only; we did not scrape it.
- data.raleighnc.gov: browsed; the county service gave us the restaurant list and inspections.

## What worked
- County data join: OSM points matched to county facilities by distance (<=150 m) + name similarity (>=0.6).
- Annotation layer: annotations.csv keyed by HSISID. Anyone can edit in a spreadsheet or via annotate.py.
- Caching in data/ with a manifest; incremental fetches; map builds offline. Essential on slow conference wifi.
- Per-HSISID batched queries with paging for violations and a 12-month date filter.
- Dashed vs solid pins make uncertainty visible: machine/unverified claims are dashed.
- Rules backend (keyword rules, no AI) on 34 menu pages: 2 yes, 8 limited, 24 unknown. Free and instant.
- Claude CLI backend check passes (`claude -p`, Haiku, tools off).

## What did not work (and fixes)
- Overpass public servers: 502/504/500, timeouts on shared conference IP. Fixes: mirrors, retries with backoff,
  shorter timeout, bbox queries, local cache, --osm-file fallback.
- First curl scripts ignored HTTP errors, saving HTML error pages as "JSON" (JSONDecodeError). Fixed with status checks and validation.
- Narrow OSM query returned 0; widened and filtered in Python. Then confirmed real sparsity.
- Violations query `where=1=1` hit the row cap: replaced with per-ID batches.
- OSM tiles blocked from file://: serve over http://localhost.
- DigitalOcean credits unavailable: replaced DO with Claude CLI / apfel / rules / generic OpenAI-compatible backend.
- Re-running `enrich_menu.py --backend claude` after the rules pass said "0 rows": rows were already filled. Use `--redo`.
- DuckDB on public S3 datasets was considered; not needed (county ArcGIS is small and queryable directly).

## Safeguards worth showing judges
Only text near "gluten/celiac/GF" goes to a model; the model must quote the page and the quote is checked
verbatim; PDFs/JS-only pages become unknown, not guesses; all machine output is unverified until a human confirms.

## Current state (12:30)
Scripts run end to end on real data. Rules pass done. Claude pass next (`--redo`). Then health data for newly
claimed places, rebuild map, human-verify the best 5-10 pins for a solid-pin demo.

## Demo script idea (3 min)
1. Problem: allergy-friendly food is hard to find; OSM has 1 gluten-free tag in 8 km.
2. Show open data join + health colours.
3. Show annotation layer + AI-assisted reading of menus with quote evidence and dashed pins.
4. Human verifies one live (open evidence_url, set verified=yes, rebuild, pin turns solid).
5. Next: more allergens (nut, dairy), pushing verified tags back to OSM, volunteer annotation.

## Open questions / next steps for Claude Code
- Compare rules vs Claude vs apfel outputs; list disagreements for human review.
- If downtown has too few good pins, raise RADIUS_M (CACHE_RADIUS_M=8000 already cached) and say so.
- Make the map readable on a projector: legend, popups with score, violations, quote, evidence link.
- Add LICENSE and a judge-facing README. Consider an open-weight local model (Ollama) for prize eligibility.
