"""Visualize the knowledge-graph pickle as an interactive HTML page.

Usage::

    python -m defectdojo_crewai.knowledge.kg.visualize
    python -m defectdojo_crewai.knowledge.kg.visualize --kinds cve,cwe,owasp_category
    python -m defectdojo_crewai.knowledge.kg.visualize --graphml
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx

from defectdojo_crewai.knowledge.kg.graph_builder import KG_DIR, get_graph

DEFAULT_HTML = KG_DIR / "graph.html"
DEFAULT_GRAPHML = KG_DIR / "graph_owasp_cwe.graphml"

_KIND_STYLE = {
    "owasp_category": {"color": "#e5484d", "size": 30},
    "cve": {"color": "#f76b15", "size": 18},
    "cwe": {"color": "#3e63dd", "size": 10},
    "finding_template": {"color": "#30a46c", "size": 14},
    None: {"color": "#8b8d98", "size": 8},
}

_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Vulnerability Knowledge Graph</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html, body {{ margin: 0; height: 100%; font-family: sans-serif; }}
  #graph {{ width: 100%; height: 100%; }}
  #legend {{ position: absolute; top: 10px; left: 10px; background: #ffffffdd;
            padding: 8px 12px; border-radius: 6px; font-size: 13px; }}
  #legend span {{ display: inline-block; width: 10px; height: 10px;
                 border-radius: 50%; margin-right: 4px; }}
</style>
</head>
<body>
<div id="legend">
  <div><span style="background:#e5484d"></span>OWASP</div>
  <div><span style="background:#3e63dd"></span>CWE</div>
  <div><span style="background:#f76b15"></span>CVE</div>
  <div><span style="background:#30a46c"></span>Template</div>
  <div><span style="background:#8b8d98"></span>Unresolved</div>
</div>
<div id="graph"></div>
<script>
const nodes = new vis.DataSet({nodes_json});
const edges = new vis.DataSet({edges_json});
const network = new vis.Network(
  document.getElementById("graph"),
  {{ nodes, edges }},
  {{
    physics: {{
      solver: "barnesHut",
      barnesHut: {{ gravitationalConstant: -8000, springLength: 120 }},
      stabilization: {{ iterations: 300 }},
    }},
    edges: {{ arrows: "to", color: {{ opacity: 0.4 }} }},
    interaction: {{ hover: true, tooltipDelay: 100 }},
  }}
);
network.once("stabilizationIterationsDone", () => network.setOptions({{ physics: false }}));
</script>
</body>
</html>
"""


def export_html(graph: nx.DiGraph, out_path: Path) -> None:
    nodes = []
    for node_id, attrs in graph.nodes(data=True):
        style = _KIND_STYLE.get(attrs.get("kind"), _KIND_STYLE[None])
        title = attrs.get("name") or attrs.get("title") or ""
        description = (attrs.get("description") or "")[:300]
        nodes.append(
            {
                "id": node_id,
                "label": node_id.split(":", 1)[-1],
                "title": f"{node_id}\n{title}\n{description}".strip(),
                "color": style["color"],
                "size": style["size"],
            }
        )
    edges = [
        {"from": src, "to": dst, "title": attrs.get("relation", "")}
        for src, dst, attrs in graph.edges(data=True)
    ]
    html = _HTML_TEMPLATE.format(
        nodes_json=json.dumps(nodes, ensure_ascii=False),
        edges_json=json.dumps(edges, ensure_ascii=False),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    print(f"HTML written: {out_path.resolve()}")


def export_graphml(graph: nx.DiGraph, out_path: Path) -> None:
    # write_graphml cannot serialize None attribute values — drop them.
    sanitized = nx.DiGraph()
    for node_id, attrs in graph.nodes(data=True):
        sanitized.add_node(
            node_id,
            **{k: v for k, v in attrs.items() if v is not None},
        )
    for src, dst, attrs in graph.edges(data=True):
        sanitized.add_edge(
            src,
            dst,
            **{k: v for k, v in attrs.items() if v is not None},
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(sanitized, out_path)
    print(f"GraphML written: {out_path.resolve()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_HTML)
    parser.add_argument(
        "--kinds",
        help="Comma-separated node kinds to keep, e.g. cve,cwe,owasp_category",
    )
    parser.add_argument(
        "--graphml",
        nargs="?",
        const=DEFAULT_GRAPHML,
        type=Path,
        help=f"Also export GraphML (default: {DEFAULT_GRAPHML})",
    )
    args = parser.parse_args()

    graph = get_graph()
    if args.kinds:
        kinds = {kind.strip() for kind in args.kinds.split(",")}
        keep = [
            node_id
            for node_id, attrs in graph.nodes(data=True)
            if attrs.get("kind") in kinds
        ]
        graph = graph.subgraph(keep).copy()

    print(f"Graph: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")
    export_html(graph, args.out)
    if args.graphml is not None:
        export_graphml(graph, args.graphml)


if __name__ == "__main__":
    main()
