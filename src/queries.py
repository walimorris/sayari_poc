"""
SQL aggregation layer over data/sayari.duckdb.

Each function is one named analytic view, callable independently by the
Streamlit app or ad hoc from a notebook/REPL. Schema built by
scripts/02_build_duckdb.py:

  entities(row_index, input_name, input_address, input_country,
           project_entity_id, sayari_entity_id, label, strength, match_count,
           entity_type, ownership_explored_count, ownership_partial_results,
           watchlist_explored_count, watchlist_partial_results)
  entity_countries(row_index, country)
  entity_risk_factors(row_index, risk_factor_id)
  entity_risk_categories(row_index, risk_category_id, risk_category_label)
"""
import duckdb
import pandas as pd


def match_quality_breakdown(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """How confidently each input row resolved to a Sayari entity."""
    return con.execute("""
        SELECT strength, COUNT(*) AS entity_count
        FROM entities
        GROUP BY strength
        ORDER BY entity_count DESC
    """).df()


def input_country_breakdown(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Country distribution of the 50 input entities themselves (as supplied)."""
    return con.execute("""
        SELECT input_country AS country, COUNT(*) AS entity_count
        FROM entities
        GROUP BY input_country
        ORDER BY entity_count DESC
    """).df()


def network_country_breakdown(con: duckdb.DuckDBPyConnection, limit: int = 25) -> pd.DataFrame:
    """
    Countries appearing in entities' rolled-up Sayari risk/relationship profile
    -- i.e. countries these entities are connected to, not just headquartered in.
    Far larger footprint than input_country_breakdown by design.
    """
    return con.execute("""
        SELECT country, COUNT(DISTINCT row_index) AS entity_count
        FROM entity_countries
        GROUP BY country
        ORDER BY entity_count DESC
        LIMIT ?
    """, [limit]).df()


def risk_category_breakdown(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """How many entities carry at least one risk factor in each category."""
    return con.execute("""
        SELECT risk_category_id, risk_category_label, COUNT(DISTINCT row_index) AS entity_count
        FROM entity_risk_categories
        GROUP BY risk_category_id, risk_category_label
        ORDER BY entity_count DESC
    """).df()


def top_risk_factors(con: duckdb.DuckDBPyConnection, limit: int = 20) -> pd.DataFrame:
    """Most common individual risk factors across all entities."""
    return con.execute("""
        SELECT risk_factor_id, COUNT(DISTINCT row_index) AS entity_count
        FROM entity_risk_factors
        GROUP BY risk_factor_id
        ORDER BY entity_count DESC
        LIMIT ?
    """, [limit]).df()


def entity_type_breakdown(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        SELECT COALESCE(entity_type, 'unresolved') AS entity_type, COUNT(*) AS entity_count
        FROM entities
        GROUP BY entity_type
        ORDER BY entity_count DESC
    """).df()


def most_connected_entities(con: duckdb.DuckDBPyConnection, limit: int = 10) -> pd.DataFrame:
    """
    Ranks entities by how large a graph subset Sayari had to explore to answer
    their Watchlist traversal -- a proxy for how deeply embedded they are in
    sanctioned/PEP-adjacent networks, not just how risky they are directly.
    """
    return con.execute("""
        SELECT input_name, watchlist_explored_count, watchlist_partial_results,
               ownership_explored_count, ownership_partial_results
        FROM entities
        WHERE watchlist_explored_count IS NOT NULL
        ORDER BY watchlist_explored_count DESC
        LIMIT ?
    """, [limit]).df()


def truncated_traversals(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Entities where Sayari's traversal hit its exploration ceiling before
    finishing (partial_results=true). true network extent is larger
    than what's captured and that should be surfaced, not hidden.
    """
    return con.execute("""
        SELECT input_name, watchlist_explored_count, ownership_explored_count
        FROM entities
        WHERE watchlist_partial_results = true OR ownership_partial_results = true
        ORDER BY watchlist_explored_count DESC
    """).df()


def entities_needing_review(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Partial-strength or no-match resolutions that need a manual look."""
    return con.execute("""
        SELECT row_index, input_name, input_address, input_country, strength, match_count
        FROM entities
        WHERE strength IN ('partial', 'no_match')
        ORDER BY row_index
    """).df()


def risk_tier_by_entity(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Our own derived risk tier, not Sayari's official severity level. Sayari
    does classify risk factors as critical/high/elevated/relevant, but that
    field is only present on the full /entity endpoint response, not on the
    Project Entity rollup this project acquires data through. This heuristic
    exists to make the map and UI usable

      critical -- carries a *direct* sanctions designation (risk_factor_id
                  like 'sanctioned', 'sanctioned_usa_ofac_sdn', etc.),
                  excluding 'sanctioned_adjacent' and 'formerly_sanctioned',
                  which mean something weaker than a current direct listing.
      high     -- no direct designation, but carries ownership/control-
                  propagated sanctions risk (owner_of_sanctioned_*,
                  owned_by_sanctioned_*, controlled_by_*sanctioned*) or is
                  flagged state_owned / sanctioned_adjacent.
      elevated -- has some other risk factor (e.g. adverse media, regulatory
                  action) but none of the above.
      none     -- no risk factors at all (expect only unresolved entities here).
    """
    factors = con.execute("SELECT row_index, risk_factor_id FROM entity_risk_factors").df()
    all_entities = con.execute("SELECT row_index FROM entities").df()

    def classify(ids: set[str]) -> str:
        direct = {i for i in ids
                  if (i == "sanctioned" or i.startswith("sanctioned_"))
                  and "adjacent" not in i}
        if direct:
            return "critical"
        propagated = {i for i in ids
                      if i.startswith("owner_of_sanctioned") or i.startswith("owned_by_sanctioned")
                      or ("controlled_by" in i and "sanctioned" in i)
                      or i in ("state_owned", "sanctioned_adjacent")}
        if propagated:
            return "high"
        if ids:
            return "elevated"
        return "none"

    grouped = factors.groupby("row_index")["risk_factor_id"].apply(set)
    tiers = all_entities.copy()
    tiers["risk_tier"] = tiers["row_index"].map(lambda i: classify(grouped.get(i, set())))
    return tiers


def map_data(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """One row per entity with everything the map needs: coordinates,
    precision, risk tier, and sanctions cross-reference status."""
    tiers = risk_tier_by_entity(con)
    con.register("tiers_df", tiers)
    return con.execute("""
        SELECT e.row_index, e.input_name, e.input_address, e.input_country, e.strength,
               g.lat, g.lon, g.precision,
               t.risk_tier,
               (s.opensanctions_id IS NOT NULL) AS opensanctions_matched,
               s.program_ids
        FROM entities e
        LEFT JOIN entity_geocode g ON e.row_index = g.row_index
        LEFT JOIN tiers_df t ON e.row_index = t.row_index
        LEFT JOIN entity_sanctions_crossref s ON e.row_index = s.row_index
        ORDER BY e.row_index
    """).df()


def entity_detail(con: duckdb.DuckDBPyConnection, row_index: int) -> dict:
    """Full drill-down record for one entity: core fields, risk factors,
    countries, and sanctions cross-reference, for the Entity Explorer detail view."""
    core = con.execute("SELECT * FROM entities WHERE row_index = ?", [row_index]).df()
    if core.empty:
        return {}
    risk_factors = con.execute(
        "SELECT risk_factor_id FROM entity_risk_factors WHERE row_index = ? ORDER BY 1", [row_index]
    ).df()["risk_factor_id"].to_list()
    countries = con.execute(
        "SELECT country FROM entity_countries WHERE row_index = ? ORDER BY 1", [row_index]
    ).df()["country"].to_list()
    sanctions = con.execute(
        "SELECT * FROM entity_sanctions_crossref WHERE row_index = ?", [row_index]
    ).df()
    return {
        "core": core.iloc[0].to_dict(),
        "risk_factors": risk_factors,
        "countries": countries,
        "sanctions": sanctions.iloc[0].to_dict() if not sanctions.empty else None,
    }
