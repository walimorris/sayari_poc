"""
Distills the full relationship graph -- built from the raw per-entity
Ownership/Watchlist traversal cache under data/cache/network/, which runs
into the hundreds of megabytes for densely-connected entities -- down to
just the seed-entity degree stats and seed-to-seed edges the app actually
uses. Writes data/cache/network_export.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import connect
from src.network import GRAPH_EXPORT_PATH, export_graph


def main():
    con = connect(read_only=True)
    entities_df = con.execute("SELECT * FROM entities ORDER BY row_index").df()
    con.close()

    export = export_graph(entities_df)
    GRAPH_EXPORT_PATH.write_text(json.dumps(export, indent=2, ensure_ascii=False), encoding="utf-8")

    size_kb = GRAPH_EXPORT_PATH.stat().st_size / 1024
    print(f"{len(export['nodes'])} seed entities, {len(export['edges'])} seed-to-seed edges "
          f"written to {GRAPH_EXPORT_PATH} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
