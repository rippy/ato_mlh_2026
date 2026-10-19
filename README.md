# Gluten-free restaurant finder (Raleigh)

Built at MLH Hacktoberfest Hacks @ All Things Open, 2026-10-19.

Finds allergy-friendly restaurants (starting with gluten-free) within a 15-minute walk of the
Raleigh Convention Center and pins them on an OpenStreetMap map, coloured by Wake County
health-inspection results. Every gluten-free claim shows its evidence and whether a human has checked it.

<https://rippy.github.io/ato_mlh_2026/scripts/map.html>

## The problem

Allergy-friendly food is hard to find, and the open data doesn't help:

- **OpenStreetMap has almost no gluten-free data.** Only 1 place within 8 km of the Convention Center has a
  `diet:gluten_free` tag.
- **Wake County publishes good health data**: ~430 permitted food places within 3 km, plus inspection
  scores and violations, joined by `HSISID`.

So the map needs an annotation layer on top of the open data. That layer is `scripts/annotations.csv`.

## How it works

### 1. Where the data comes from (`fetch_data.py`)

Everything is downloaded once, cached in `scripts/data/`, and committed, so the map builds offline.

```mermaid
flowchart TD
    subgraph SRC["Public sources (no keys, no accounts)"]
        OVP["OpenStreetMap<br/>Overpass API"]
        ARC["Wake County, NC<br/>ArcGIS REST service<br/>maps.wake.gov"]
        UNP["Leaflet 1.9.4<br/>(map library)"]
    end

    OVP -->|"places with a diet:gluten_free tag<br/>(only 1 within 8 km)"| OSMJ["data/osm.json"]
    OVP -->|"restaurants + cafes with a website tag<br/>(fetch_data.py --places)"| PLJ["data/places.json"]
    ARC -->|"layer 0: permitted food places<br/>~430 within 3 km<br/>(HSISID = join key for all 3 layers)"| WKJ["data/wake.json"]
    ARC -->|"layer 1: inspection scores + dates<br/>only for places with a claim"| INJ["data/inspections.json"]
    ARC -->|"layer 2: violations, last 12 months<br/>only for places with a claim"| VIJ["data/violations.json"]
    UNP --> VEN["data/vendor/<br/>leaflet.js, leaflet.css"]

    PLJ -->|"website URLs"| SEED["annotate.py seed<br/>pre-fills evidence_url"]
    SEED --> ANN[("annotations.csv")]
    ANN -->|"which places need health data"| INJ
```

OSM points are matched to county facilities by distance (<= 150 m) and name similarity (>= 0.6).

### 2. How a gluten-free claim gets made and checked

```mermaid
flowchart TD
    ANN[("annotations.csv<br/>one row per restaurant,<br/>keyed by HSISID")]
    SITE["Restaurant's own website<br/>(identified User-Agent, 1 s pause,<br/>max 2 extra pages)"]

    ANN -->|"evidence_url"| FETCH["enrich_menu.py<br/>download page, keep only text<br/>near gluten / celiac / GF"]
    SITE --> FETCH

    FETCH --> RULES["Backend: rules<br/>keywords, no AI"]
    FETCH --> LLM["Backend: claude / apfel / openai<br/>model must quote the page"]

    LLM --> CHK{"Quote appears word<br/>for word on the page?"}
    CHK -->|no| UNK["unknown, verified = no<br/>answer thrown away"]
    CHK -->|yes| MACH
    RULES --> MACH["Machine claim: yes / limited<br/>always written verified = no"]

    MACH --> UI["verify_ui.py (human)<br/>quote highlighted on the live page<br/>+ link to open it yourself"]
    UNK -->|"JS-only, blocked, or bad quote"| UI2["verify_ui.py ?mode=failed<br/>human reads the site,<br/>pastes the exact quote"]

    UI -->|"Confirm"| HYES["verified = yes<br/>checked_by = human:name"]
    UI -->|"Reject"| HNO["gf_status = no<br/>verified = yes<br/>original quote kept in note"]
    UI2 --> HYES
    UI2 -->|"nothing found"| STAY["stays unknown<br/>dashed / not shown"]

    HYES --> ANN
    HNO --> ANN
```

Re-running `enrich_menu.py --redo` only touches machine rows; human rows are never overwritten.

### 3. How the map is built (`gf_map.py`)

```mermaid
flowchart LR
    ANN[("annotations.csv")] --> J
    OSMJ["osm.json<br/>OSM tags"] --> J
    WKJ["wake.json"] --> J
    INJ["inspections.json"] --> J
    VIJ["violations.json"] --> J
    J["Join on HSISID<br/>+ 15-minute walk filter (~1200 m)"] --> P["Pin per place"]

    P --> COL{"Colour from health data"}
    COL -->|"score >= 90 and<br/>0 violations in 12 months"| G["green"]
    COL -->|"mixed"| A["amber"]
    COL -->|"score < 80"| R["red"]
    COL -->|"no data"| GR["gray"]

    P --> TRUST{"Trust tier"}
    TRUST -->|"verified = yes"| SOLID["solid pin"]
    TRUST -->|"OSM tag"| SOLID
    TRUST -->|"verified = no"| DASH["dashed pin"]

    G --> OUT["map.html<br/>OpenStreetMap tiles + popups<br/>(score, violations, quote, evidence link)"]
    A --> OUT
    R --> OUT
    GR --> OUT
    SOLID --> OUT
    DASH --> OUT
    OUT --> PAGES["GitHub Pages"]
```

1. **Open data.** Restaurants from OpenStreetMap (Overpass) are matched to Wake County facilities by
   distance (<= 150 m) and name similarity. Inspection scores and the last 12 months of violations are
   pulled from the county ArcGIS service. Everything is cached in `scripts/data/`, so the map builds offline.
2. **Machine pass.** Keyword rules and an LLM (Claude Haiku via the `claude` CLI) read each restaurant's own
   website. Only text near "gluten / celiac / GF" is sent to a model. The model must quote the page, and the
   quote is checked word for word; if it isn't on the page the answer is thrown away.
   Machine output is always written `verified=no`.
3. **Human pass.** A small local web UI walks you through each machine claim: the quote, the surrounding page
   text with the quote highlighted, and a link to the live page. Only a human click sets `verified=yes`.
4. **Map.** Solid pins are human-verified, dashed pins are unverified. Pin colour comes from the health data.

![The human verification UI](docs/img/verify-ui.png)

### Current results

80 restaurants annotated near the Convention Center: **18 human-verified gluten-free places** (10 yes, 8 limited,
with cross-contact notes), 16 checked with nothing found, the rest not yet reviewed.

### Pin colours

| Colour | Meaning |
|---|---|
| Green | Score >= 90 and 0 violations in the last 12 months |
| Amber | Mixed |
| Red | Score < 80 |
| Gray | No inspection data |

Solid outline = human-verified. Dashed outline = unverified.

### Trust rules

- human-verified > OSM tag > unverified
- Machine output is never marked verified.
- Human rows are never overwritten by a re-run (`enrich_menu.py --redo` only touches machine rows).
- PDF or JavaScript-only menus become "unknown", not guesses.
- We are polite to restaurant sites: identified User-Agent, 1 second pause, at most 2 extra pages per site.

## Run it

Python 3 standard library plus `curl`. No installs. Data is already cached in the repo.

```
cd scripts
python3 gf_map.py                 # builds map.html from cached data
python3 -m http.server 8000       # open http://localhost:8000/map.html
```

OpenStreetMap tiles need a Referer header, so serve the map over http; `file://` won't show tiles.

Human verification UI:

```
cd scripts
python3 verify_ui.py              # open http://localhost:8001
                                  # http://localhost:8001/?mode=failed  = second pass for sites the AI couldn't read
```

Rebuild from scratch:

```
cd scripts
python3 fetch_data.py --places
python3 annotate.py seed --limit 60
python3 enrich_menu.py --backend rules
python3 enrich_menu.py --backend claude --redo --workers 3
python3 fetch_data.py && python3 gf_map.py
```

`enrich_menu.py` backends: `rules` (no AI), `claude`, `apfel` (on-device), `openai` (any OpenAI-compatible
endpoint, e.g. a local Ollama model via `LLM_BASE`).

## Repository layout

| Path | What it does |
|---|---|
| `scripts/fetch_data.py` | Downloads and caches OSM, Wake County and Leaflet data |
| `scripts/annotate.py` | Find, add, seed and list rows in `annotations.csv` |
| `scripts/enrich_menu.py` | Reads restaurant websites and proposes `gf_status` with a quote |
| `scripts/verify_ui.py` | Local web UI for human verification |
| `scripts/gf_map.py` | Joins everything and writes `map.html` |
| `scripts/annotations.csv` | The gluten-free claims: status, evidence URL, quote, who checked, when |
| `docs/EVENT_NOTES.md` | Raw notes from the day |
| `HANDOFF.md` | What worked, what didn't, and the demo story |

## What's next

- More allergens (nut, dairy, shellfish) using the same evidence-and-verification model.
- Pushing human-verified tags back to OpenStreetMap so everyone benefits.
- Volunteer annotation: the verification UI could run as a hosted page.
- An open-weight model (Ollama) for the machine pass.

## Team

Jonathan Rippy and Rebekah Callari-Kaczmarczyk.

## Licence and attribution

Code: MIT, see [LICENSE](LICENSE).

Data and libraries we use, and who owns them:

- **Map data and tiles:** (c) [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, available under the
  [Open Database Licence (ODbL)](https://opendatacommons.org/licenses/odbl/). Our cached `scripts/data/osm.json` is
  derived from OSM and stays under ODbL. Tiles are loaded from `tile.openstreetmap.org` under the
  [OSM tile usage policy](https://operations.osmfoundation.org/policies/tiles/).
- **Restaurants, health inspections and violations:** [Wake County, NC](https://data.wake.gov/) (Wake County GIS /
  Environmental Services), via the public ArcGIS REST service
  `maps.wake.gov/arcgis/rest/services/Inspections/RestaurantInspectionsOpenData`. Wake County publishes these under
  open-data terms that can differ per dataset, so check the dataset pages on data.wake.gov before reusing the data.
  We credit Wake County on the map and here. We have not changed the scores or violations.
- **Gluten-free claims:** taken from each restaurant's own public website (the evidence URL and quote are stored with
  every claim) and checked by a human on our team. Restaurant menus change, so confirm with the restaurant,
  especially if you have coeliac disease. This is not medical advice.
- **Map library:** [Leaflet](https://leafletjs.com/) 1.9.4, BSD 2-Clause, vendored in `scripts/data/vendor/`.
- **AI assistance:** Claude (Anthropic) read menu text, and its answers were kept only when the quote matched the page.
