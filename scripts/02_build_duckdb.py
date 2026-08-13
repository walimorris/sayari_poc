"""
Builds data/sayari.duckdb from the raw JSON cached by 01_acquire_sayari.py.

idempotent: tables are replaced wholesale from the current cache each time.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.config import INPUT_DIR, CACHE_DIR, DB_PATH
from src.db import connect

PROJECT_ENTITIES_DIR = CACHE_DIR / "project_entities"
NETWORK_DIR = CACHE_DIR / "network"


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:60]


def load_input_rows(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def best_match(project_entity_data: dict) -> dict:
    matches = project_entity_data.get("matches") or []
    return matches[0] if matches else {}


def build_tables(rows: list[dict]):
    entities, entity_countries, entity_risk_factors, entity_risk_categories = [], [], [], []

    for i, row in enumerate(rows, start=1):
        slug = slugify(row["name"])
        pe = load_json(PROJECT_ENTITIES_DIR / f"{i:02d}_{slug}.json").get("data", {})
        match = best_match(pe)

        ownership = load_json(NETWORK_DIR / f"{i:02d}_{slug}_ownership.json")
        watchlist = load_json(NETWORK_DIR / f"{i:02d}_{slug}_watchlist.json")

        entities.append({
            "row_index": i,
            "input_name": row["name"],
            "input_address": row.get("address"),
            "input_country": row.get("country"),
            "project_entity_id": pe.get("project_entity_id"),
            "sayari_entity_id": match.get("sayari_entity_id"),
            "label": pe.get("label"),
            "strength": pe.get("strength"),
            "match_count": len(pe.get("matches", [])),
            "entity_type": match.get("type"),
            "ownership_explored_count": ownership.get("explored_count"),
            "ownership_partial_results": ownership.get("partial_results"),
            "watchlist_explored_count": watchlist.get("explored_count"),
            "watchlist_partial_results": watchlist.get("partial_results"),
        })

        for country in pe.get("countries", []):
            entity_countries.append({"row_index": i, "country": country})

        for rf in pe.get("risk_factors", []):
            entity_risk_factors.append({"row_index": i, "risk_factor_id": rf["id"]})

        for rc in pe.get("risk_categories", []):
            entity_risk_categories.append({
                "row_index": i, "risk_category_id": rc["id"], "risk_category_label": rc["label"],
            })

    return (
        pd.DataFrame(entities),
        pd.DataFrame(entity_countries),
        pd.DataFrame(entity_risk_factors),
        pd.DataFrame(entity_risk_categories),
    )


def main():
    rows = load_input_rows(INPUT_DIR / "list_1.csv")
    entities_df, countries_df, risk_factors_df, risk_categories_df = build_tables(rows)

    con = connect()
    con.register("entities_df", entities_df)
    con.register("countries_df", countries_df)
    con.register("risk_factors_df", risk_factors_df)
    con.register("risk_categories_df", risk_categories_df)

    con.execute("CREATE OR REPLACE TABLE entities AS SELECT * FROM entities_df")
    con.execute("CREATE OR REPLACE TABLE entity_countries AS SELECT * FROM countries_df")
    con.execute("CREATE OR REPLACE TABLE entity_risk_factors AS SELECT * FROM risk_factors_df")
    con.execute("CREATE OR REPLACE TABLE entity_risk_categories AS SELECT * FROM risk_categories_df")
    con.close()

    print(f"Built {DB_PATH}")
    print(f"  entities:               {len(entities_df)} rows")
    print(f"  entity_countries:       {len(countries_df)} rows")
    print(f"  entity_risk_factors:    {len(risk_factors_df)} rows")
    print(f"  entity_risk_categories: {len(risk_categories_df)} rows")


if __name__ == "__main__":
    main()
