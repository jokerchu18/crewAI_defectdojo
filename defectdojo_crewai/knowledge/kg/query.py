"""Query API for the vulnerability knowledge graph."""

from __future__ import annotations

from typing import Any

import networkx as nx

from defectdojo_crewai.knowledge.kg.graph_builder import get_graph
from defectdojo_crewai.knowledge.kg.schemas import GraphEdge, GraphQueryResult


# ── node lookup ────────────────────────────────────────────────────────


def _node_data(g: nx.DiGraph, node_id: str) -> dict[str, Any] | None:
    if not g.has_node(node_id):
        return None
    data = dict(g.nodes[node_id])
    data["node_id"] = node_id
    return data


# ── single-node queries ────────────────────────────────────────────────


def query_by_cwe(cwe_id: str) -> GraphQueryResult | None:
    """Return a subgraph centred on a CWE: the CWE node, its parents,
    children, associated CVEs, and OWASP categories."""
    g = get_graph()
    node_id = _normalize_cwe(cwe_id)
    if not g.has_node(node_id):
        return None

    nodes: list[dict] = [_node_data(g, node_id)]
    edges: list[GraphEdge] = []

    # Parents and children (1 hop).
    for pred in g.predecessors(node_id):
        if g.nodes[pred].get("kind") == "cwe":
            nodes.append(_node_data(g, pred))
            edges.append(GraphEdge(source=pred, target=node_id, relation="cwe_parent_of"))
    for succ in g.successors(node_id):
        if g.nodes[succ].get("kind") == "cwe":
            nodes.append(_node_data(g, succ))
            edges.append(GraphEdge(source=node_id, target=succ, relation="cwe_parent_of"))

    # OWASP categories (1 hop out).
    for owasp in g.successors(node_id):
        if g.nodes[owasp].get("kind") == "owasp_category":
            nodes.append(_node_data(g, owasp))
            edges.append(GraphEdge(source=node_id, target=owasp, relation="cwe_in_owasp"))

    # Associated CVEs (2 hops — keep the count reasonable).
    cve_count = 0
    for cve in g.predecessors(node_id):
        if g.nodes[cve].get("kind") != "cve":
            continue
        if cve_count < 50:  # cap for display
            nodes.append(_node_data(g, cve))
            edges.append(GraphEdge(source=cve, target=node_id, relation="cve_uses_cwe"))
        cve_count += 1

    return GraphQueryResult(
        nodes=nodes,
        edges=edges,
        paths=None,
    )


def query_by_cve(cve_id: str) -> GraphQueryResult | None:
    """Return a CVE node with its associated CWE(s) and OWASP categories."""
    g = get_graph()
    node_id = _normalize_cve(cve_id)
    if not g.has_node(node_id):
        return None

    nodes: list[dict] = [_node_data(g, node_id)]
    edges: list[GraphEdge] = []

    for cwe in g.successors(node_id):
        if g.nodes[cwe].get("kind") != "cwe":
            continue
        nodes.append(_node_data(g, cwe))
        edges.append(GraphEdge(source=node_id, target=cwe, relation="cve_uses_cwe"))
        # OWASP from that CWE.
        for owasp in g.successors(cwe):
            if g.nodes[owasp].get("kind") == "owasp_category":
                nodes.append(_node_data(g, owasp))
                edges.append(GraphEdge(source=cwe, target=owasp, relation="cwe_in_owasp"))

    return GraphQueryResult(nodes=nodes, edges=edges)


# ── search queries ─────────────────────────────────────────────────────


def search_cwe_by_keyword(keyword: str, top_k: int = 5) -> list[dict[str, Any]]:
    """Simple name/description keyword search on CWE nodes."""
    g = get_graph()
    kw_lower = keyword.lower()
    matches: list[tuple[float, dict]] = []
    for node_id, data in g.nodes(data=True):
        if data.get("kind") != "cwe":
            continue
        name = (data.get("name") or "").lower()
        desc = (data.get("description") or "").lower()
        score = 0.0
        if kw_lower in name:
            score += 2.0
        elif kw_lower in desc:
            score += 1.0
        if score > 0:
            matches.append((score, {"node_id": node_id, **dict(data)}))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [m[1] for m in matches[:top_k]]


def search_kev_cves(limit: int = 100) -> list[dict[str, Any]]:
    """Return all CVE nodes marked as KEV (Known Exploited Vulnerabilities)."""
    g = get_graph()
    results: list[dict] = []
    for node_id, data in g.nodes(data=True):
        if data.get("kind") == "cve" and data.get("kev") is True:
            results.append({"node_id": node_id, **dict(data)})
            if len(results) >= limit:
                break
    return results


def subgraph_by_owasp(category_id: str) -> GraphQueryResult | None:
    """Return the OWASP category, its CWE members, and associated CVEs."""
    g = get_graph()
    node_id = f"owasp:{category_id}"
    if not g.has_node(node_id):
        return None

    nodes: list[dict] = [_node_data(g, node_id)]
    edges: list[GraphEdge] = []

    for cwe in g.predecessors(node_id):
        if g.nodes[cwe].get("kind") != "cwe":
            continue
        nodes.append(_node_data(g, cwe))
        edges.append(GraphEdge(source=cwe, target=node_id, relation="cwe_in_owasp"))
        # Top CVEs for this CWE (capped per CWE).
        cve_budget = 10
        for cve in g.predecessors(cwe):
            if g.nodes[cve].get("kind") != "cve":
                continue
            if cve_budget <= 0:
                break
            nodes.append(_node_data(g, cve))
            edges.append(GraphEdge(source=cve, target=cwe, relation="cve_uses_cwe"))
            cve_budget -= 1

    return GraphQueryResult(nodes=nodes, edges=edges)


# ── path query ─────────────────────────────────────────────────────────


def find_path(from_id: str, to_id: str) -> GraphQueryResult | None:
    """Find shortest path(s) between two nodes (IDs with prefix)."""
    g = get_graph()
    src = from_id if ":" in from_id else f"cve:{from_id}"
    tgt = to_id if ":" in to_id else f"cwe:{to_id}"
    if not g.has_node(src) or not g.has_node(tgt):
        return None

    try:
        paths = list(nx.all_shortest_paths(g, source=src, target=tgt))
    except nx.NetworkXNoPath:
        return GraphQueryResult(nodes=[], edges=[], paths=[])

    # Collect all unique nodes on the paths.
    seen_nodes: dict[str, dict] = {}
    seen_edges: dict[tuple[str, str], GraphEdge] = {}
    for path in paths:
        for i, node_id in enumerate(path):
            if node_id not in seen_nodes:
                seen_nodes[node_id] = _node_data(g, node_id)
            if i > 0:
                prev = path[i - 1]
                key = (prev, node_id)
                if key not in seen_edges:
                    edge_data = g.get_edge_data(prev, node_id) or {}
                    seen_edges[key] = GraphEdge(
                        source=prev,
                        target=node_id,
                        relation=edge_data.get("relation", "unknown"),
                    )

    return GraphQueryResult(
        nodes=list(seen_nodes.values()),
        edges=list(seen_edges.values()),
        paths=paths,
    )


# ── helpers ────────────────────────────────────────────────────────────


def _normalize_cwe(cwe_id: str) -> str:
    cwe_id = cwe_id.strip().upper()
    if cwe_id.startswith("CWE:"):
        cwe_id = cwe_id[4:]
    elif cwe_id.startswith("CWE-"):
        cwe_id = cwe_id[4:]
    return f"cwe:CWE-{cwe_id}"


def _normalize_cve(cve_id: str) -> str:
    cve_id = cve_id.strip().upper()
    return f"cve:{cve_id}"
