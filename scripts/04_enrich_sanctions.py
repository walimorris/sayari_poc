"""
Cross-references resolved entities against OpenSanctions' free bulk export,
attaching sanctions program(s), source list(s), and first/last-observed dates
to each entity with a confident name match.

Usage:
    python scripts/04_enrich_sanctions.py --smoke-test   # first 5 entities, sanity check
    python scripts/04_enrich_sanctions.py                # full entity set
    python scripts/04_enrich_sanctions.py --refresh       # force re-download of the OpenSanctions export
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests
from rapidfuzz import fuzz

from src.config import CACHE_DIR
from src.db import connect

OPENSANCTIONS_URL = "https://data.opensanctions.org/datasets/latest/default/targets.simple.csv"
OS_CACHE_DIR = CACHE_DIR / "opensanctions"
OS_CSV_PATH = OS_CACHE_DIR / "targets.simple.csv"
MATCHES_PATH = OS_CACHE_DIR / "matches.json"

# OpenSanctions schema names that correspond to legal entities rather than
# people, vessels, aircraft, etc. Our entity list is entirely organizations,
# so filtering to these before fuzzy matching cuts the candidate pool
# significantly. If this filter unexpectedly empties the pool (e.g. the
# schema values differ from what's assumed here), the script falls back to
# the unfiltered set and prints a warning rather than silently matching
# against nothing.
ORG_SCHEMAS = {"Company", "Organization", "LegalEntity", "PublicBody"}

MATCH_SCORE_THRESHOLD = 88  # rapidfuzz token_sort_ratio, 0-100


def download_export(force: bool) -> Path:
    OS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if OS_CSV_PATH.exists() and not force:
        print(f"Using cached export at {OS_CSV_PATH} (pass --refresh to re-download)")
        return OS_CSV_PATH
    print(f"Downloading {OPENSANCTIONS_URL} ...")
    resp = requests.get(OPENSANCTIONS_URL, timeout=300, stream=True)
    resp.raise_for_status()
    with open(OS_CSV_PATH, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    print(f"Saved to {OS_CSV_PATH} ({OS_CSV_PATH.stat().st_size / 1e6:.1f} MB)")
    return OS_CSV_PATH


def split_multi(value) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    return [v.strip() for v in value.split(";") if v.strip()]


def load_candidates(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    print(f"Loaded {len(df)} total OpenSanctions target rows. Columns: {list(df.columns)}")
    print("Sample schema values:", df["schema"].value_counts().head(10).to_dict())

    org_df = df[df["schema"].isin(ORG_SCHEMAS)]
    if org_df.empty:
        print(f"WARNING: schema filter {ORG_SCHEMAS} matched 0 rows -- "
              f"falling back to the unfiltered export. Check the 'schema' "
              f"values printed above and adjust ORG_SCHEMAS if needed.")
        org_df = df
    else:
        print(f"Filtered to {len(org_df)} organization-type rows.")

    org_df = org_df.copy()
    org_df["_aliases"] = org_df["aliases"].apply(split_multi)
    org_df["_countries"] = org_df["countries"].apply(split_multi)
    return org_df


def best_match(query_name: str, query_countries: set[str], candidates: pd.DataFrame) -> dict | None:
    pool = candidates
    if query_countries:
        country_mask = pool["_countries"].apply(lambda cs: bool(query_countries.intersection(cs)))
        narrowed = pool[country_mask]
        if not narrowed.empty:
            pool = narrowed
        # else: no country overlap found (possibly a code-system mismatch,
        # e.g. alpha-2 vs alpha-3) -- fall back to the full pool rather than
        # silently returning no match.

    best_score, best_row = 0, None
    for _, row in pool.iterrows():
        names_to_check = [row["name"], *row["_aliases"]]
        score = max(fuzz.token_sort_ratio(query_name, n) for n in names_to_check)
        if score > best_score:
            best_score, best_row = score, row

    if best_row is None or best_score < MATCH_SCORE_THRESHOLD:
        return None
    return {
        "opensanctions_id": best_row["id"],
        "matched_name": best_row["name"],
        "match_score": best_score,
        "schema": best_row["schema"],
        "program_ids": split_multi(best_row["program_ids"]),
        "datasets": split_multi(best_row["dataset"]),
        "countries": best_row["_countries"],
        "sanctions_detail": best_row["sanctions"],
        "first_seen": best_row["first_seen"],
        "last_seen": best_row["last_seen"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", help="Only process the first 5 entities.")
    parser.add_argument("--refresh", action="store_true", help="Force re-download of the OpenSanctions export.")
    args = parser.parse_args()

    csv_path = download_export(force=args.refresh)
    candidates = load_candidates(csv_path)

    con = connect(read_only=True)
    entities = con.execute("""
        SELECT row_index, input_name, label, input_country
        FROM entities
        WHERE strength IS NOT NULL AND strength != 'no_match'
        ORDER BY row_index
    """).df()
    country_map = con.execute("SELECT row_index, country FROM entity_countries").df()
    con.close()

    if args.smoke_test:
        entities = entities.head(5)
        print(f"SMOKE TEST MODE: processing {len(entities)} entities only")

    results = []
    for _, row in entities.iterrows():
        query_name = row["label"] or row["input_name"]
        row_countries = set(country_map[country_map["row_index"] == row["row_index"]]["country"])
        if row["input_country"]:
            row_countries.add(row["input_country"])

        match = best_match(query_name, row_countries, candidates)
        results.append({"row_index": int(row["row_index"]), "input_name": row["input_name"],
                         "query_name": query_name, "match": match})
        if match:
            print(f"[{row['row_index']:02d}] {query_name!r} -> {match['matched_name']!r} "
                  f"(score={match['match_score']}, programs={match['program_ids']})")
        else:
            print(f"[{row['row_index']:02d}] {query_name!r} -> no confident OpenSanctions match")

    OS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MATCHES_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    matched = sum(1 for r in results if r["match"])
    print(f"\n{matched}/{len(results)} entities matched to an OpenSanctions record "
          f"(threshold={MATCH_SCORE_THRESHOLD}). Written to {MATCHES_PATH}")


if __name__ == "__main__":
    main()
