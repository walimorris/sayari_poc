"""
Reads from the pre-built data/sayari.duckdb snapshot and the cached
network/geocode/narrative JSON produced by the scripts/ pipeline (see
README.md).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

from src.db import connect
from src.formatting import humanize_factor_id
from src.narratives import load_narratives
from src.network import load_exported_graph
from src.queries import (
    entity_detail,
    entities_needing_review,
    input_country_breakdown,
    map_data,
    match_quality_breakdown,
    risk_category_breakdown,
    top_risk_factors,
    truncated_traversals,
)

st.set_page_config(page_title="Entity Risk Analytics", layout="wide")

RISK_TIER_COLORS = {
    "critical": [200, 30, 30],
    "high": [230, 140, 40],
    "elevated": [220, 200, 60],
    "none": [120, 120, 120],
}
RISK_TIER_ORDER = ["critical", "high", "elevated", "none"]

EXPLORER_COLUMNS = {
    "row_index": "#",
    "input_name": "Entity",
    "input_country": "Country",
    "strength": "Match confidence",
    "risk_tier": "Risk tier",
    "opensanctions_matched": "On a sanctions list",
    "precision": "Location precision",
}

CENTRALITY_COLUMNS = {
    "label": "Entity",
    "seed_in_degree": "Connections from others on this list",
    "in_degree": "Total incoming links (incl. outside entities)",
    "out_degree": "Total outgoing links (incl. outside entities)",
}


@st.cache_resource
def get_connection():
    return connect(read_only=True)


@st.cache_data
def load_entities(_con):
    return _con.execute("SELECT * FROM entities ORDER BY row_index").df()


@st.cache_data
def load_map_data(_con):
    return map_data(_con)


@st.cache_resource
def load_graph():
    # Reads data/cache/network_export.json -- a small, precomputed distillation
    # of the raw Ownership/Watchlist traversal cache (see
    # scripts/08_export_network_graph.py). The deployed app never touches the
    # raw traversal cache itself, which runs into the hundreds of megabytes.
    return load_exported_graph()


con = get_connection()
entities_df = load_entities(con)
narratives = load_narratives()

st.title("Sanctioned & State-Owned Entity Risk Analytics")
st.caption(
    "50 entities resolved through Sayari's Project Entity API, cross-referenced "
    "against OpenSanctions, geocoded via OpenStreetMap, and linked into a "
    "relationship network from Sayari's Ownership and Watchlist traversals."
)

# st.tabs() has no way to keep its selected tab across a rerun triggered by
# another widget -- it silently resets to the first tab. A
# radio bound to a key doesn't have that problem, since Streamlit persists
# widget state by key across reruns, so it's used here instead despite tabs
# being the more typical choice visually (update if you want).
SECTIONS = ["Overview", "Risk Map", "Entity Explorer", "Relationship Network"]
section = st.radio("Section", SECTIONS, horizontal=True, key="active_section", label_visibility="collapsed")
st.divider()

if section == "Overview":
    match_df = match_quality_breakdown(con)
    tier_counts = load_map_data(con)["risk_tier"].value_counts()
    sanctioned_matched = load_map_data(con)["opensanctions_matched"].sum()

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Entities analyzed", len(entities_df))
    col2.metric("Strong matches", int(match_df.loc[match_df["strength"] == "strong", "entity_count"].sum()))
    col3.metric("Critical risk tier", int(tier_counts.get("critical", 0)))
    col4.metric("OpenSanctions matches", int(sanctioned_matched))
    st.caption(
        "\"Strong matches\" means the search confidently identified the right organization in Sayari's "
        "database. \"Critical risk tier\" counts entities carrying a direct, current sanctions designation. "
        "\"OpenSanctions matches\" is a second, independent check against a separate public sanctions database."
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Match quality")
        st.caption("How confidently each of the 50 input entities was matched to a record in Sayari's database "
                   "-- this is about identification confidence, not risk.")
        st.bar_chart(match_df.set_index("strength")["entity_count"])
        st.subheader("Input country distribution")
        st.bar_chart(input_country_breakdown(con).set_index("country")["entity_count"])
    with right:
        st.subheader("Risk category prevalence")
        st.caption("Broad groupings (e.g. sanctions, state ownership, adverse media) showing how many "
                   "entities carry each type of concern.")
        cat_df = risk_category_breakdown(con)
        st.bar_chart(cat_df.set_index("risk_category_label")["entity_count"])
        st.subheader("Top individual risk factors")
        st.caption("The specific, granular flags behind those categories -- for example, a listing on a "
                   "particular country's sanctions list, or government ownership.")
        factors_df = top_risk_factors(con, limit=15).copy()
        factors_df["risk_factor_id"] = factors_df["risk_factor_id"].map(humanize_factor_id)
        factors_df = factors_df.rename(columns={"risk_factor_id": "Risk factor", "entity_count": "Entities"})
        st.dataframe(factors_df, hide_index=True, use_container_width=True)

    review_df = entities_needing_review(con)
    truncated_df = truncated_traversals(con)
    if not review_df.empty or not truncated_df.empty:
        st.subheader("Data quality notes")
        if not review_df.empty:
            st.caption(f"{len(review_df)} entities resolved with partial confidence or no match -- meaning "
                       f"the search either wasn't fully sure it found the right organization, or found "
                       f"nothing at all. Worth a human double-check before defining a full risk profile.")
            st.dataframe(review_df, hide_index=True, use_container_width=True)
        if not truncated_df.empty:
            st.caption(f"{len(truncated_df)} entities have a relationship network larger than what's shown here "
                       f"-- the search reached its exploration limit before finishing. Their true web of "
                       f"connections is larger than what's displayed, not smaller.")
            st.dataframe(truncated_df, hide_index=True, use_container_width=True)

elif section == "Risk Map":
    st.subheader("Geocoded entities by risk tier")
    st.caption(
        "Each dot is one entity, colored by risk tier (see legend below). Tier is a rule-based "
        "classification derived from Sayari's risk data -- not an official Sayari score -- explained "
        "in the expander below. Map data (c) OpenStreetMap contributors."
    )
    with st.expander("What does \"risk tier\" and \"location precision\" mean?"):
        st.write(
            "**Risk tier** groups each entity into critical, high, elevated, or none based on what kind "
            "of risk flags Sayari's data shows for it: critical means a direct, current sanctions listing; "
            "high means it's owned or controlled by a sanctioned entity, or is state-owned; elevated means "
            "some other flag (like adverse media) but no sanctions link; none means no flags found. This "
            "classification exists to make the data easier to scan visually -- it is not a score Sayari "
            "itself assigns."
        )
        st.write(
            "**Location precision** varies by entity: some addresses pinned to an exact building, others "
            "only to the city they're in, because the mapping service didn't have a precise record for "
            "that address. Both are shown on the map, but city-level pins are a rough approximation, not "
            "an exact location."
        )

    mdf = load_map_data(con).dropna(subset=["lat", "lon"]).copy()
    mdf["color"] = mdf["risk_tier"].map(RISK_TIER_COLORS)
    mdf["radius"] = mdf["precision"].map({"exact": 15000, "city": 8000}).fillna(8000)

    selected_tiers = st.multiselect("Filter by risk tier", RISK_TIER_ORDER, default=RISK_TIER_ORDER)
    plot_df = mdf[mdf["risk_tier"].isin(selected_tiers)]

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=plot_df,
        get_position=["lon", "lat"],
        get_fill_color="color",
        get_radius="radius",
        pickable=True,
        opacity=0.75,
    )

    fitted_view = pdk.data_utils.compute_view(mdf[["lon", "lat"]].values.tolist())
    view_state = pdk.ViewState(
        latitude=fitted_view.latitude, longitude=fitted_view.longitude, zoom=fitted_view.zoom + 1.3
    )
    st.pydeck_chart(pdk.Deck(
        layers=[layer],
        initial_view_state=view_state,
        map_style=None,
        tooltip={"text": "{input_name}\n{risk_tier} risk ({precision} location)"},
    ))

    st.caption("Zoomed to the densest cluster of entities by default -- pan or scroll out to reach "
               "outlying entities (e.g. those in the Americas) if they're not visible.")
    legend_cols = st.columns(len(RISK_TIER_ORDER))
    for col, tier in zip(legend_cols, RISK_TIER_ORDER):
        r, g, b = RISK_TIER_COLORS[tier]
        col.markdown(
            f'<span style="color: rgb({r},{g},{b})">●</span> {tier}',
            unsafe_allow_html=True,
        )

    map_table = plot_df[["row_index", "input_name", "input_country", "risk_tier", "precision", "opensanctions_matched"]]
    st.dataframe(map_table.rename(columns=EXPLORER_COLUMNS), hide_index=True, use_container_width=True)
    unresolved = len(mdf) < len(load_map_data(con))
    if unresolved:
        st.caption(f"{len(load_map_data(con)) - len(mdf)} entity address(es) could not be geocoded at any "
                   f"precision tier and are omitted from the map.")

elif section == "Entity Explorer":
    st.subheader("Entity explorer")
    st.caption("Browse and filter all 50 entities, then select one below for a full profile.")
    filter_col1, filter_col2 = st.columns(2)
    tier_filter = filter_col1.multiselect(
        "Risk tier", RISK_TIER_ORDER, default=RISK_TIER_ORDER, key="explorer_tier_filter"
    )
    country_filter = filter_col2.multiselect(
        "Input country", sorted(entities_df["input_country"].dropna().unique()), key="explorer_country_filter"
    )

    listing = load_map_data(con)
    listing = listing[listing["risk_tier"].isin(tier_filter)]
    if country_filter:
        listing = listing[listing["input_country"].isin(country_filter)]

    listing_table = listing[["row_index", "input_name", "input_country", "strength", "risk_tier", "opensanctions_matched"]]
    st.dataframe(listing_table.rename(columns=EXPLORER_COLUMNS), hide_index=True, use_container_width=True)

    selected_row = st.selectbox(
        "Select an entity for detail",
        listing["row_index"],
        format_func=lambda i: listing.loc[listing["row_index"] == i, "input_name"].iloc[0],
    )
    if selected_row is not None:
        detail = entity_detail(con, int(selected_row))
        core = detail["core"]

        st.markdown(f"### {core['input_name']}")
        d1, d2, d3 = st.columns(3)
        d1.write(f"**Match confidence:** {core['strength'] or 'unresolved'}")
        d2.write(f"**Entity type:** {core['entity_type'] or 'unknown'}")
        d3.write(f"**Sayari's name for this entity:** {core['label'] or '—'}")
        st.caption("Match confidence reflects how closely this record matches the right organization in "
                   "Sayari's database. Sayari's name may differ slightly from how the entity was listed "
                   "in the source list -- that's expected, and not itself a concern.")

        st.write(f"**Countries this entity is connected to:** {', '.join(detail['countries']) or 'none recorded'}")
        st.caption("Not just where it's headquartered -- also countries tied to it through ownership, "
                   "trade, or other recorded relationships.")

        st.write(f"**Risk factors ({len(detail['risk_factors'])}):**")
        st.write(", ".join(humanize_factor_id(f) for f in detail["risk_factors"]) or "none recorded")
        st.caption("Specific flags Sayari's data attaches to this entity -- e.g. a sanctions listing by a "
                   "particular country, or government ownership.")

        if detail["sanctions"]:
            s = detail["sanctions"]
            if s.get("opensanctions_id"):
                st.success(
                    f"Also found on OpenSanctions, a separate public sanctions database that serves as an "
                    f"independent second check: matched to **{s['matched_name']}** (confidence score "
                    f"{s['match_score']}/100) -- programs: {s['program_ids'] or 'none listed'}"
                )
            else:
                st.info("Not found on OpenSanctions, an independent second sanctions check beyond "
                        "Sayari's own data.")

        narrative = narratives.get(str(selected_row))
        st.write("**Risk narrative:**")
        st.caption("A short, plain-language summary of why this entity is flagged, generated from the "
                   "data above.")
        if narrative:
            st.write(narrative)
            st.warning(
                "AI-generated summary. It's produced by an LLM (Claude) from the structured data on "
                "this page and can be incomplete or miss context -- verify against the risk factors, "
                "countries, and sanctions match above before relying on it for a decision."
            )
            with st.expander("What data was this summary based on?"):
                st.caption(
                    "This summary isn't drawn from outside articles or documents -- there's nothing "
                    "external to cite. It's generated only from the structured fields shown on this "
                    "page, listed again here for a direct side-by-side check:"
                )
                st.write(f"- Risk tier: {listing.loc[listing['row_index'] == selected_row, 'risk_tier'].iloc[0]}")
                st.write(f"- Risk factors: "
                         f"{', '.join(humanize_factor_id(f) for f in detail['risk_factors']) or 'none recorded'}")
                st.write(f"- Countries connected: {', '.join(detail['countries']) or 'none recorded'}")
                if detail["sanctions"] and detail["sanctions"].get("opensanctions_id"):
                    st.write(f"- OpenSanctions match: {detail['sanctions']['matched_name']} "
                             f"(programs: {detail['sanctions']['program_ids'] or 'none listed'})")
                else:
                    st.write("- OpenSanctions match: none")
        else:
            st.caption("Not yet generated for this entity.")

elif section == "Relationship Network":
    st.subheader("Relationship network among the 50 entities")
    st.caption(
        "This shows how the 50 entities on this list connect to each other -- shared ownership, "
        "control, or appearing together on watchlists -- not connections to the thousands of other "
        "organizations surfaced along the way."
    )
    with st.expander("How to read this"):
        st.write(
            "**What a line (edge) means:** a direct link identified between two entities on this "
            "list -- most often one owning or controlling the other, or both showing up together on "
            "the same watchlist search. An arrow points from the entity whose search surfaced the "
            "connection to the entity on the other end; it isn't necessarily the direction of ownership."
        )
        st.write(
            "**What dot size and color mean:** bigger, darker dots have more connections to *other "
            "entities on this specific list*. That makes them structural hubs within this group -- "
            "central points that many of the other entities link back to -- not a measure of how "
            "risky or dangerous that entity is on its own."
        )
        st.write(
            "**Why the ranking below isn't just \"most connections\":** each entity's own search "
            "already pulls in up to about 100 outside connections, so counting every link would just "
            "reward entities with a broader outside network, rather than genuine centrality within this "
            "list. The ranking instead counts how many of the *other 49 entities on this list* point "
            "back to it, which is a more reliable signal of who the real hubs are within this group."
        )

    graph, centrality_df = load_graph()
    top_n = 15
    top_labeled = set(centrality_df.head(top_n)["sayari_entity_id"])
    st.dataframe(
        centrality_df.head(top_n)[["label", "seed_in_degree", "in_degree", "out_degree"]]
        .rename(columns=CENTRALITY_COLUMNS),
        hide_index=True, use_container_width=True,
    )

    # graph already contains only seed entities and seed-to-seed edges --
    # see load_exported_graph().
    isolated_count = sum(1 for n in graph.nodes() if graph.degree(n) == 0)
    show_isolated = st.checkbox(
        f"Also show the {isolated_count} entities with no direct connection to another entity on this list",
        value=False,
    )
    diagram = graph if show_isolated else graph.subgraph([n for n in graph.nodes() if graph.degree(n) > 0])

    if diagram.number_of_edges() == 0:
        st.info("No direct edges between seed entities to display.")
    else:
        import networkx as nx

        # k scales the target spacing between nodes; without it the default
        # (1/sqrt(n)) packs this graph tightly enough that labels overlap.
        pos = nx.spring_layout(diagram, seed=42, k=3 / (diagram.number_of_nodes() ** 0.5), iterations=200)
        edge_x, edge_y = [], []
        for u, v in diagram.edges():
            edge_x += [pos[u][0], pos[v][0], None]
            edge_y += [pos[u][1], pos[v][1], None]
        edge_trace = go.Scatter(x=edge_x, y=edge_y, line=dict(width=1, color="#888"),
                                 hoverinfo="none", mode="lines")

        node_x = [pos[n][0] for n in diagram.nodes()]
        node_y = [pos[n][1] for n in diagram.nodes()]
        node_labels = [diagram.nodes[n].get("label", n) if n in top_labeled else "" for n in diagram.nodes()]
        node_hover = [diagram.nodes[n].get("label", n) for n in diagram.nodes()]
        node_degree = [diagram.in_degree(n) + diagram.out_degree(n) for n in diagram.nodes()]
        node_trace = go.Scatter(
            x=node_x, y=node_y, mode="markers+text", text=node_labels, textposition="top center",
            hovertext=node_hover, hoverinfo="text",
            marker=dict(size=[8 + 3 * d for d in node_degree], color=node_degree, colorscale="Reds", showscale=True),
        )

        fig = go.Figure(data=[edge_trace, node_trace], layout=go.Layout(
            showlegend=False, hovermode="closest", margin=dict(l=0, r=0, t=0, b=0),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        ))
        st.plotly_chart(fig, use_container_width=True)
        if not show_isolated:
            st.caption(f"{isolated_count} entities with no direct connection to another entity on this "
                       f"list are hidden by default -- check the box above to include them.")
