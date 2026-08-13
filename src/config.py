"""
Central config loader.

Reads credentials from .env locally. In the deployed Streamlit app these are
unset, since the app only reads the cached DuckDB snapshot produced by the
offline acquisition/build scripts under scripts/ -- it never calls external
APIs directly. See README.md for the full data flow.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

SAYARI_CLIENT_ID = os.getenv("SAYARI_CLIENT_ID")
SAYARI_CLIENT_SECRET = os.getenv("SAYARI_CLIENT_SECRET")
SAYARI_BASE_URL = os.getenv("SAYARI_BASE_URL", "https://api.sayari.com")
SAYARI_TOKEN_URL = os.getenv("SAYARI_TOKEN_URL", "https://api.sayari.com/oauth/token")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # optional, only needed for narrative generation step

DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
INPUT_DIR = DATA_DIR / "input"
DB_PATH = DATA_DIR / "sayari.duckdb"

for d in (CACHE_DIR, INPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)
