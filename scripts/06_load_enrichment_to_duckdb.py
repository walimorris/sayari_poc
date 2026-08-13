"""
Loads the OpenSanctions cross-reference and geocoding results (produced by
04_enrich_sanctions.py and 05_enrich_geocode.py) into data/sayari.duckdb.
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

SANCTIONS_MATCHES_PATH = CACHE_DIR / "opensanctions" / "matches.json"
GEOCODE_CACHE_PATH = CACHE_DIR / "geocode.json"


def build_sanctions_table() -> pd.DataFrame:
    if not SANCTIONS_MATCHES_PATH.exists():
        print(f"No OpenSanctions results at {SANCTIONS_MATCHES_PATH} -- "
              f"run scripts/04_enrich_sanctions.py first. Skipping this table.")
        return pd.DataFrame()

    records = json.loads(SANCTIONS_MATCHES_PATH.read_text())
    rows = []
    for r in records:
        m = r.get("match")
        rows.append({
            "row_index": r["row_index"],
            "opensanctions_id": m["opensanctions_id"] if m else None,
            "matched_name": m["matched_name"] if m else None,
            "match_score": m["match_score"] if m else None,
            "program_ids": "; ".join(m["program_ids"]) if m else None,
            "datasets": "; ".join(m["datasets"]) if m else None,
            "first_seen": m["first_seen"] if m else None,
            "last_seen": m["last_seen"] if m else None,
            "sanctions_detail": m["sanctions_detail"] if m else None,
        })
    return pd.DataFrame(rows)


def build_geocode_table() -> pd.DataFrame:
    if not GEOCODE_CACHE_PATH.exists():
        print(f"No geocode results at {GEOCODE_CACHE_PATH} -- "
              f"run scripts/05_enrich_geocode.py first. Skipping this table.")
        return pd.DataFrame()

    cache = json.loads(GEOCODE_CACHE_PATH.read_text())
    with open(INPUT_DIR / "list_1.csv", newline="", encoding="utf-8") as f:
        input_rows = list(csv.DictReader(f))

    rows = []
    for i, row in enumerate(input_rows, start=1):
        result = cache.get(row["address"])
        rows.append({
            "row_index": i,
            "address": row["address"],
            "lat": result["lat"] if result else None,
            "lon": result["lon"] if result else None,
            "display_name": result["display_name"] if result else None,
            # older cache entries predate the precision field and were all
            # tier-1 exact-address matches, so that's a safe default here.
            "precision": result.get("precision", "exact") if result else None,
        })
    return pd.DataFrame(rows)


def main():
    sanctions_df = build_sanctions_table()
    geocode_df = build_geocode_table()

    con = connect()
    if not sanctions_df.empty:
        con.register("sanctions_df", sanctions_df)
        con.execute("CREATE OR REPLACE TABLE entity_sanctions_crossref AS SELECT * FROM sanctions_df")
        print(f"entity_sanctions_crossref: {len(sanctions_df)} rows "
              f"({sanctions_df['opensanctions_id'].notna().sum()} matched)")
    if not geocode_df.empty:
        con.register("geocode_df", geocode_df)
        con.execute("CREATE OR REPLACE TABLE entity_geocode AS SELECT * FROM geocode_df")
        precision_counts = geocode_df["precision"].value_counts().to_dict()
        print(f"entity_geocode: {len(geocode_df)} rows, {precision_counts}")
    con.close()
    print(f"Updated {DB_PATH}")


if __name__ == "__main__":
    main()
