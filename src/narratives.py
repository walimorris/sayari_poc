"""
Loads cached LLM-generated risk narratives for the Entity Explorer detail view.

Narratives are generated offline by scripts/07_generate_narratives.py (one
Anthropic API call per entity, cached to disk) so the deployed app makes no
LLM calls at request time. See that script for generation logic and prompt.
"""
import json

from src.config import CACHE_DIR

NARRATIVES_PATH = CACHE_DIR / "narratives.json"


def load_narratives() -> dict:
    """maps row_index (str) -> narrative text. Empty dict if not yet generated."""
    if not NARRATIVES_PATH.exists():
        return {}
    return json.loads(NARRATIVES_PATH.read_text(encoding="utf-8"))
