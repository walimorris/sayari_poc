"""
Geocodes the 50 input addresses via OpenStreetMap's Nominatim, for the
risk-colored map in the Streamlit app.

This is a one-time, single-threaded, 50-row batch against the public Nominatim
API, which fits within their documented "smaller one-time bulk task" allowance
(see below). It is NOT called from the deployed Streamlit app, which reads
only the cached results this script produces.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.config import INPUT_DIR, CACHE_DIR

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "sayari-poc-entity-risk-map/1.0 (one-time 50-row research use; contact: opensentop@gmail.com)"
REQUEST_INTERVAL_SEC = 1.1  # stays under the 1 req/s ceiling with margin

GEOCODE_CACHE_PATH = CACHE_DIR / "geocode.json"

# list_1.csv uses ISO 3166-1 alpha-3 codes; Nominatim's `countrycodes` filter
# needs alpha-2, and free-text queries work better with the country name
# spelled out. Covers the 9 countries present in data/input/list_1.csv --
# extend if a different input list is used.
COUNTRY_INFO = {
    "RUS": ("ru", "Russia"), "BLR": ("by", "Belarus"), "CHN": ("cn", "China"),
    "MMR": ("mm", "Myanmar"), "VEN": ("ve", "Venezuela"), "SYR": ("sy", "Syria"),
    "PRK": ("kp", "North Korea"), "IRN": ("ir", "Iran"), "CUB": ("cu", "Cuba"),
}

KNOWN_CITIES = sorted([
    "Naberezhnye Chelny", "Vyatskie Polyany", "Nizhny Tagil", "St. Petersburg",
    "Severodvinsk", "Soligorsk", "Shenzhen", "Hangzhou", "Pyongyang",
    "Damascus", "Caracas", "Havana", "Izhevsk", "Kostroma", "Korolev", "Tehran",
    "Moscow", "Minsk", "Yangon",
], key=len, reverse=True)


def load_cache() -> dict:
    if GEOCODE_CACHE_PATH.exists():
        return json.loads(GEOCODE_CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    GEOCODE_CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _search(query: str, country_code_alpha2: str) -> dict | None:
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": query, "countrycodes": country_code_alpha2, "format": "jsonv2", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return None
    top = results[0]
    return {"lat": float(top["lat"]), "lon": float(top["lon"]), "display_name": top["display_name"]}


def geocode_one(address: str, country: str) -> dict | None:
    alpha2, country_name = COUNTRY_INFO.get(country, ("", ""))

    result = _search(address, alpha2)
    if result:
        return {**result, "precision": "exact"}

    if country_name and country_name.lower() not in address.lower():
        time.sleep(REQUEST_INTERVAL_SEC)
        result = _search(f"{address}, {country_name}", alpha2)
        if result:
            return {**result, "precision": "exact"}

    city = next((c for c in KNOWN_CITIES if c.lower() in address.lower()), None)
    if city and country_name:
        time.sleep(REQUEST_INTERVAL_SEC)
        result = _search(f"{city}, {country_name}", alpha2)
        if result:
            return {**result, "precision": "city"}

    return None


def main():
    with open(INPUT_DIR / "list_1.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    cache = load_cache()
    new_lookups = 0

    for row in rows:
        key = row["address"]
        if key in cache and cache[key] is not None:
            continue  # only retry addresses we haven't already resolved
        print(f"Geocoding: {row['address']} ...")
        try:
            result = geocode_one(row["address"], row["country"])
        except requests.RequestException as e:
            print(f"  FAILED: {e}")
            result = None
        if result is None:
            print("  -> no result at any fallback tier")
        else:
            print(f"  -> {result['precision']} match")
        cache[key] = result
        new_lookups += 1
        save_cache(cache)  # persist incrementally so a partial run isn't wasted
        time.sleep(REQUEST_INTERVAL_SEC)

    exact = sum(1 for v in cache.values() if v and v.get("precision") == "exact")
    city = sum(1 for v in cache.values() if v and v.get("precision") == "city")
    unresolved = sum(1 for v in cache.values() if not v)
    print(f"\n{new_lookups} lookups attempted this run. "
          f"{exact} exact, {city} city-level, {unresolved} unresolved (of {len(cache)} total).")
    if new_lookups == 0:
        print("(All addresses already resolved in cache -- no requests made.)")


if __name__ == "__main__":
    main()
