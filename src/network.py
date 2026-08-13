"""
Builds a NetworkX graph from the cached Ownership and Watchlist traversal
responses (scripts/01_acquire_sayari.py), for relationship-network analysis:
identifying structural hubs, and answering "how is X connected to Y" queries
that a flat entity table can't.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pandas as pd

from src.config import CACHE_DIR

NETWORK_DIR = CACHE_DIR / "network"
GRAPH_EXPORT_PATH = CACHE_DIR / "network_export.json"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _add_traversal_edges(graph: nx.DiGraph, source_id: str, source_label: str,
                          traversal: dict, seed_labels: dict) -> None:
    for path_entry in traversal.get("data", []):
        target = path_entry.get("target", {})
        target_id = target.get("id")
        if not target_id:
            continue
        graph.add_node(source_id, label=source_label, in_seed_list=True)
        # seed_labels takes priority: a target discovered here may itself be
        # one of our 50 seeds (just not processed yet, or processed earlier
        # and about to be revisited as someone else's target), and without
        # this check whichever traversal file happens to be read last would
        # win and silently overwrite a readable English name with Sayari's
        # native-language one.
        target_label = seed_labels.get(target_id, target.get("label", target_id))
        graph.add_node(target_id, label=target_label,
                        countries=target.get("countries", []),
                        sanctioned=target.get("sanctioned", False),
                        in_seed_list=graph.nodes.get(target_id, {}).get("in_seed_list", False))
        # relationship field on the first path hop, if present
        rel = path_entry.get("path", [{}])[0].get("field") if path_entry.get("path") else None
        graph.add_edge(source_id, target_id, relationship=rel or "related_to")


def build_graph(entities_df) -> nx.DiGraph:
    """
    entities_df: the `entities` table (pandas DataFrame) from data/sayari.duckdb,
    used to map row_index -> sayari_entity_id/label and to mark our 50 seed
    entities distinctly from entities discovered only via traversal.
    """
    graph = nx.DiGraph()

    seed_labels = {
        row["sayari_entity_id"]: row["input_name"] or row["label"]
        for _, row in entities_df.iterrows() if row["sayari_entity_id"]
    }

    for _, row in entities_df.iterrows():
        if not row["sayari_entity_id"]:
            continue
        source_id, source_label = row["sayari_entity_id"], seed_labels[row["sayari_entity_id"]]
        slug = _slug(row["input_name"])
        idx = row["row_index"]

        ownership = _load_json(NETWORK_DIR / f"{idx:02d}_{slug}_ownership.json")
        watchlist = _load_json(NETWORK_DIR / f"{idx:02d}_{slug}_watchlist.json")

        graph.add_node(source_id, label=source_label, in_seed_list=True, row_index=int(idx))
        _add_traversal_edges(graph, source_id, source_label, ownership, seed_labels)
        _add_traversal_edges(graph, source_id, source_label, watchlist, seed_labels)
        graph.nodes[source_id]["in_seed_list"] = True  # re-assert; may have been overwritten as a target

    return graph


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")[:60]


def seed_subgraph(graph: nx.DiGraph) -> nx.DiGraph:
    seed_ids = [n for n, d in graph.nodes(data=True) if d.get("in_seed_list")]
    return graph.subgraph(seed_ids).copy()


def centrality_table(graph: nx.DiGraph, seed_only: bool = True):
    seed_ids = {n for n, d in graph.nodes(data=True) if d.get("in_seed_list")}

    rows = []
    for node_id, data in graph.nodes(data=True):
        if seed_only and node_id not in seed_ids:
            continue
        seed_in_degree = sum(1 for pred in graph.predecessors(node_id) if pred in seed_ids and pred != node_id)
        rows.append({
            "sayari_entity_id": node_id,
            "label": data.get("label", node_id),
            "seed_in_degree": seed_in_degree,
            "in_degree": graph.in_degree(node_id),
            "out_degree": graph.out_degree(node_id),
        })
    df = pd.DataFrame(rows).sort_values(["seed_in_degree", "in_degree"], ascending=False).reset_index(drop=True)
    return df


def export_graph(entities_df) -> dict:
    """
    Distills the full graph -- built from the raw per-entity traversal cache
    under data/cache/network/, which runs into the hundreds of megabytes for
    densely-connected entities -- down to just what the deployed app needs:
    each seed entity's degree stats and the seed-to-seed edges. Written to
    disk by scripts/08_export_network_graph.py; read back by
    load_exported_graph(). The raw traversal cache itself is never shipped
    with the deployed app.
    """
    graph = build_graph(entities_df)
    centrality_df = centrality_table(graph)
    sub = seed_subgraph(graph)

    return {
        "nodes": centrality_df.to_dict(orient="records"),
        "edges": [
            {"source": u, "target": v, "relationship": data.get("relationship", "related_to")}
            for u, v, data in sub.edges(data=True)
        ],
    }


def load_exported_graph() -> tuple[nx.DiGraph, pd.DataFrame]:
    """
    Reconstructs the seed-entity relationship graph and its centrality table
    directly from data/cache/network_export.json, without touching the raw
    per-entity traversal cache. This is what the deployed app calls.
    """
    export = _load_json(GRAPH_EXPORT_PATH)
    if not export:
        empty_columns = ["sayari_entity_id", "label", "seed_in_degree", "in_degree", "out_degree"]
        return nx.DiGraph(), pd.DataFrame(columns=empty_columns)

    centrality_df = pd.DataFrame(export["nodes"])

    graph = nx.DiGraph()
    for _, row in centrality_df.iterrows():
        graph.add_node(row["sayari_entity_id"], label=row["label"], in_seed_list=True)
    for edge in export["edges"]:
        graph.add_edge(edge["source"], edge["target"], relationship=edge["relationship"])

    return graph, centrality_df
