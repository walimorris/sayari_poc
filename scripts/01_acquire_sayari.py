"""
Run this locally (NOT in a sandboxed/restricted-network environment) --
it makes real calls against the Sayari API.

Usage:
    python scripts/01_acquire_sayari.py --smoke-test      # 3 entities only, sanity check
    python scripts/01_acquire_sayari.py                   # full list_1 (50 entities)

What it does:
  1. Creates one Sayari Project for this run.
  2. For each row in data/input/list_1.csv, creates a Project Entity
     (name/address/country -> Sayari's best match, with a `strength` field
     telling us how confident that match is -- see
     https://documentation.sayari.com/api/guides/understanding-project-entity).
  3. For each Project Entity's best match (sayari_entity_id), pulls Ownership
     and Watchlist traversal data (relationship network).
  4. Caches every raw response as JSON under data/cache/ -- nothing here talks
     to the deployed Streamlit app; this is purely the acquisition step.

Rate limiting and 429 backoff are handled inside SayariClient / RateLimiter,
matching the documented tiers (200/60s standard, 15/10s advanced):
https://documentation.sayari.com/api/key-concepts/rate-limits
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import INPUT_DIR, CACHE_DIR
from src.sayari_client import SayariClient, SayariAPIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("acquire")

PROJECT_ENTITIES_DIR = CACHE_DIR / "project_entities"
NETWORK_DIR = CACHE_DIR / "network"
PROJECT_META_PATH = CACHE_DIR / "project.json"


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:60]


def load_input_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_or_create_project(client: SayariClient, label: str) -> str:
    if PROJECT_META_PATH.exists():
        meta = json.loads(PROJECT_META_PATH.read_text())
        logger.info("Reusing existing project %s (%s)", meta["id"], meta["label"])
        return meta["id"]
    proj = client.create_project(label=label)
    data = proj["data"]
    PROJECT_META_PATH.write_text(json.dumps(data, indent=2))
    logger.info("Created project %s (%s)", data["id"], data["label"])
    return data["id"]


def best_match_entity_id(project_entity: dict) -> str | None:
    matches = project_entity.get("matches") or []
    if not matches:
        return None
    return matches[0].get("sayari_entity_id")


def acquire_project_entity(client: SayariClient, project_id: str, row: dict, index: int) -> dict:
    slug = slugify(row["name"])
    cache_path = PROJECT_ENTITIES_DIR / f"{index:02d}_{slug}.json"
    if cache_path.exists():
        logger.info("[%02d] %s -- cached, skipping", index, row["name"])
        return json.loads(cache_path.read_text())

    logger.info("[%02d] Resolving %s ...", index, row["name"])
    try:
        resp = client.create_project_entity(
            project_id, name=row["name"], address=row.get("address") or None,
            country=row.get("country") or None,
        )
    except SayariAPIError as e:
        logger.error("[%02d] FAILED to resolve %s: %s", index, row["name"], e)
        resp = {"error": str(e), "input_row": row}

    cache_path.write_text(json.dumps(resp, indent=2, ensure_ascii=False))
    data = resp.get("data", {})
    logger.info("[%02d] %s -> strength=%s matches=%d", index, row["name"],
                data.get("strength"), len(data.get("matches", [])))
    return resp


def acquire_network(client: SayariClient, entity_id: str, name: str, index: int) -> None:
    slug = slugify(name)
    own_path = NETWORK_DIR / f"{index:02d}_{slug}_ownership.json"
    watch_path = NETWORK_DIR / f"{index:02d}_{slug}_watchlist.json"

    if not own_path.exists():
        try:
            own = client.ownership(entity_id, limit=50, max_depth=4)
        except SayariAPIError as e:
            logger.error("[%02d] ownership FAILED for %s: %s", index, name, e)
            own = {"error": str(e)}
        own_path.write_text(json.dumps(own, indent=2, ensure_ascii=False))
        logger.info("[%02d] ownership: explored_count=%s partial=%s", index,
                     own.get("explored_count"), own.get("partial_results"))

    if not watch_path.exists():
        try:
            watch = client.watchlist(entity_id, limit=50, max_depth=4)
        except SayariAPIError as e:
            logger.error("[%02d] watchlist FAILED for %s: %s", index, name, e)
            watch = {"error": str(e)}
        watch_path.write_text(json.dumps(watch, indent=2, ensure_ascii=False))
        logger.info("[%02d] watchlist: explored_count=%s partial=%s", index,
                     watch.get("explored_count"), watch.get("partial_results"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true",
                         help="Only process the first 3 rows, to validate auth + one full round trip.")
    parser.add_argument("--input", default=str(INPUT_DIR / "list_1.csv"))
    parser.add_argument("--skip-network", action="store_true",
                         help="Skip ownership/watchlist pulls (Project Entity resolution only).")
    args = parser.parse_args()

    PROJECT_ENTITIES_DIR.mkdir(parents=True, exist_ok=True)
    NETWORK_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_input_rows(Path(args.input))
    if args.smoke_test:
        rows = rows[:3]
        logger.info("SMOKE TEST MODE: processing %d rows only", len(rows))

    client = SayariClient()
    try:
        client._ensure_token()
        logger.info("Auth OK.")
    except SayariAPIError as e:
        logger.error("Auth FAILED: %s", e)
        sys.exit(1)

    project_id = get_or_create_project(client, label="sayari-poc-list1")

    results = []
    for i, row in enumerate(rows, start=1):
        resp = acquire_project_entity(client, project_id, row, i)
        data = resp.get("data", {})
        results.append({"index": i, "name": row["name"], "strength": data.get("strength"),
                         "project_entity_id": data.get("project_entity_id")})

        if not args.skip_network:
            entity_id = best_match_entity_id(data)
            if entity_id:
                acquire_network(client, entity_id, row["name"], i)
            else:
                logger.warning("[%02d] No match found for %s -- skipping network pull", i, row["name"])

    logger.info("\n--- Summary ---")
    for r in results:
        logger.info("[%02d] %-45s strength=%s", r["index"], r["name"], r["strength"])

    no_match = [r for r in results if not r["strength"] or r["strength"] == "no_match"]
    partial = [r for r in results if r["strength"] == "partial"]
    if no_match:
        logger.warning("%d entities had NO MATCH: %s", len(no_match), [r["name"] for r in no_match])
    if partial:
        logger.warning("%d entities had PARTIAL match (review manually): %s",
                        len(partial), [r["name"] for r in partial])


if __name__ == "__main__":
    main()
