"""
Generates a short, plain-language risk narrative for each entity, grounded
strictly in the Sayari risk data, derived risk tier, and OpenSanctions
cross-reference already in data/sayari.duckdb.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import ANTHROPIC_API_KEY, CACHE_DIR
from src.db import connect
from src.formatting import humanize_factor_id
from src.queries import entity_detail, risk_tier_by_entity

NARRATIVES_PATH = CACHE_DIR / "narratives.json"
MODEL = "claude-sonnet-5"
MAX_TOKENS = 300

SYSTEM_PROMPT = (
    "You write short risk-profile summaries for a due-diligence analytics dashboard "
    "aimed at non-technical readers. Ground every sentence strictly in the facts you "
    "are given -- do not invent detail, speculate, or draw legal conclusions the facts "
    "don't support. Write 2 to 4 sentences, neutral and factual in tone. If the facts "
    "show no meaningful risk indicators, say so plainly rather than padding the summary. "
    "Do not mention that you are an AI or comment on the summarization process itself -- "
    "write as a direct statement of what the data shows."
)


def _build_prompt(row_index: int, tier: str, detail: dict) -> str:
    core = detail["core"]
    factors = ", ".join(humanize_factor_id(f) for f in detail["risk_factors"]) or "none recorded"
    countries = ", ".join(detail["countries"]) or "none recorded"
    sanctions = detail["sanctions"]
    if sanctions and sanctions.get("opensanctions_id"):
        sanctions_line = (
            f"Matched on OpenSanctions to \"{sanctions['matched_name']}\" "
            f"(programs: {sanctions['program_ids'] or 'none listed'})"
        )
    else:
        sanctions_line = "No OpenSanctions match."

    return (
        f"Entity: {core['input_name']}\n"
        f"Country: {core['input_country']}\n"
        f"Match confidence to Sayari's database: {core['strength'] or 'unresolved'}\n"
        f"Derived risk tier: {tier} "
        f"(critical = direct sanctions designation, high = ownership/control-propagated "
        f"sanctions risk or state ownership, elevated = other risk factor present, "
        f"none = no risk factors recorded)\n"
        f"Risk factors: {factors}\n"
        f"Countries connected: {countries}\n"
        f"{sanctions_line}\n\n"
        f"Write the 2-4 sentence risk-profile summary for this entity."
    )


@retry(
    retry=retry_if_exception_type((anthropic.APIStatusError, anthropic.APIConnectionError)),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
)
def _generate(client: anthropic.Anthropic, prompt: str) -> str:
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def load_cache() -> dict:
    if NARRATIVES_PATH.exists():
        return json.loads(NARRATIVES_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict) -> None:
    NARRATIVES_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true", help="Only process the first 3 entities.")
    parser.add_argument("--refresh", action="store_true", help="Regenerate all narratives, ignoring the cache.")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set in .env -- nothing to do.")
        return

    con = connect(read_only=True)
    tiers = risk_tier_by_entity(con).set_index("row_index")["risk_tier"]
    row_indices = list(tiers.index)
    if args.smoke_test:
        row_indices = row_indices[:3]
        print(f"SMOKE TEST MODE: processing {len(row_indices)} entities only")

    cache = {} if args.refresh else load_cache()
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    generated = 0
    for row_index in row_indices:
        key = str(row_index)
        if key in cache:
            continue

        detail = entity_detail(con, row_index)
        tier = tiers.loc[row_index]
        prompt = _build_prompt(row_index, tier, detail)

        try:
            narrative = _generate(client, prompt)
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            print(f"[{row_index:02d}] FAILED after retries: {e}")
            continue

        cache[key] = narrative
        generated += 1
        print(f"[{row_index:02d}] {detail['core']['input_name']}: {narrative[:100]}...")
        save_cache(cache)  # persist incrementally so a partial run isn't wasted

    con.close()
    print(f"\n{generated} narratives generated this run. {len(cache)} total cached at {NARRATIVES_PATH}")


if __name__ == "__main__":
    main()
