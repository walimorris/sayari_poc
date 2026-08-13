"""Prints every aggregation view for manual review."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import connect
from src import queries

VIEWS = [
    ("Match quality", queries.match_quality_breakdown),
    ("Input country breakdown", queries.input_country_breakdown),
    ("Network country breakdown (top 25)", queries.network_country_breakdown),
    ("Risk category breakdown", queries.risk_category_breakdown),
    ("Top 20 risk factors", queries.top_risk_factors),
    ("Entity type breakdown", queries.entity_type_breakdown),
    ("Most connected entities (top 10)", queries.most_connected_entities),
    ("Truncated traversals (partial_results=true)", queries.truncated_traversals),
    ("Entities needing manual review", queries.entities_needing_review),
]


def main():
    con = connect(read_only=True)
    pd_options = ("display.max_rows", 30, "display.width", 140)
    import pandas as pd
    pd.set_option(*pd_options[:2])
    pd.set_option(*pd_options[2:])
    for title, fn in VIEWS:
        print(f"\n=== {title} ===")
        print(fn(con).to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
