# Sayari Entity Risk Analytics

Analytics report on a list of 50 sanctioned / state-owned entities (`data/input/list_1.csv`),
built on the Sayari API: country and risk-factor breakdowns, sanctions cross-referencing,
a geocoded risk map, and relationship-network analysis.

## Architecture

Data acquisition and the deployed app are deliberately separate:

1. **Acquisition** (`scripts/`, run locally, requires network access to the Sayari API
   and other external sources): resolves each entity via Sayari's Project Entity API,
   pulls ownership/watchlist relationship data, cross-references sanctions lists, and
   geocodes addresses. Every raw response is cached to `data/cache/` and then loaded
   into a single `data/sayari.duckdb` snapshot.
2. **App** (`app/`, deployed on Streamlit Community Cloud): reads only from
   `data/sayari.duckdb` plus two small precomputed files, `data/cache/narratives.json`
   and `data/cache/network_export.json`. It makes no live calls to Sayari, Anthropic,
   or any other external API, and never touches the raw acquisition cache -- which for
   densely-connected entities runs into the hundreds of megabytes and isn't shipped
   with the repo.

This split means the deployed app doesn't depend on API credentials being valid or
rate limits being available at view time, and gives a reproducible snapshot -- the
report and the live app describe the same data.

Scripts under `scripts/` are numbered in run order.

## Setup

```
pip install -r requirements.txt
cp .env.example .env   # fill in credentials
```

Required in `.env`:

| Variable | Purpose |
|---|---|
| `SAYARI_CLIENT_ID` / `SAYARI_CLIENT_SECRET` | Sayari API OAuth client credentials |
| `ANTHROPIC_API_KEY` | Only needed for the risk-narrative generation step |

## Running the pipeline

Run in order. Steps 1, 4, 5, and 7 make live external calls and need an unrestricted
network connection; steps 2 and 6 are pure local transforms.

```
python scripts/01_acquire_sayari.py --smoke-test        # 3 entities, validates auth end-to-end
python scripts/01_acquire_sayari.py                      # full 50-entity list
python scripts/02_build_duckdb.py                         # build data/sayari.duckdb from the cache

python scripts/04_enrich_sanctions.py --smoke-test         # 5 entities, validates matching end-to-end
python scripts/04_enrich_sanctions.py                       # cross-reference against OpenSanctions
python scripts/05_enrich_geocode.py                          # geocode addresses via Nominatim
python scripts/06_load_enrichment_to_duckdb.py                 # merge both into data/sayari.duckdb

python scripts/07_generate_narratives.py --smoke-test           # 3 entities, validates prompt end-to-end
python scripts/07_generate_narratives.py                          # generate all 50 risk narratives

python scripts/08_export_network_graph.py                           # distill the relationship graph for the app
```

Resumable: entities/addresses already cached under `data/cache/` are skipped on rerun,
so an interrupted run can just be restarted. `03_print_aggregations.py` is a standalone
diagnostic that prints every SQL aggregation view for manual review -- not part of the
build order.

## Project layout

```
src/                          Library code (API client, config, rate limiting)
scripts/                       Offline acquisition/build pipeline (run locally)
app/                           Streamlit application
data/input/                     Source entity list(s)
data/cache/                      Raw cached API responses (gitignored, except the two below)
data/cache/narratives.json        Committed -- LLM narratives, read by the app
data/cache/network_export.json    Committed -- distilled relationship graph, read by the app
data/sayari.duckdb               Built snapshot the app reads from
```
