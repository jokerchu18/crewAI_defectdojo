"""Build (or load from cache) a networkx vulnerability knowledge graph.

Usage::

    from defectdojo_crewai.knowledge.kg.graph_builder import get_graph
    g = get_graph()          # lazy-loads from pickle (or builds on first call)

    python -m defectdojo_crewai.knowledge.kg.graph_builder --build  # force rebuild
"""

from __future__ import annotations

import logging
import pickle
import time
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from defectdojo_crewai.config.settings import BASE_DIR
from defectdojo_crewai.knowledge.kg.parser import (
    iter_cve_entries,
    iter_cwe_entries,
    parse_finding_template_yaml,
    parse_owasp_yaml,
)

if TYPE_CHECKING:
    from networkx import DiGraph

LOGGER = logging.getLogger(__name__)

KG_DIR = BASE_DIR / "data" / "kg"
PICKLE_PATH = KG_DIR / "graph.pickle"
CWE_XML = KG_DIR / "cwe" / "cwec_latest.xml"
NVD_JSON = KG_DIR / "nvd" / "nvdcve-2.0.json"
OWASP_YAML = KG_DIR / "owasp" / "top10.yaml"
TEMPLATE_YAML = KG_DIR / "defectdojo" / "finding_template.yaml"

# ── public API ─────────────────────────────────────────────────────────

_graph: DiGraph | None = None


def get_graph(*, force_rebuild: bool = False) -> DiGraph:
    """Return the singleton knowledge graph (lazy-load from pickle)."""
    global _graph
    if _graph is not None and not force_rebuild:
        return _graph
    _graph = _load_or_build()
    return _graph


def _load_or_build() -> DiGraph:
    """Check pickle freshness; rebuild if stale."""
    if PICKLE_PATH.exists() and not _sources_newer_than_pickle():
        LOGGER.info("Loading knowledge graph from pickle (%s) …", PICKLE_PATH)
        try:
            with PICKLE_PATH.open("rb") as fh:
                g = pickle.load(fh)
            LOGGER.info(
                "Graph loaded: %d nodes, %d edges",
                g.number_of_nodes(),
                g.number_of_edges(),
            )
            return g
        except Exception:
            LOGGER.exception("Failed to load pickle — rebuilding …")
    return _build_graph()


def _sources_newer_than_pickle() -> bool:
    """Return True if any source file is newer than the pickle."""
    pickle_mtime = PICKLE_PATH.stat().st_mtime
    for src in (CWE_XML, NVD_JSON, OWASP_YAML, TEMPLATE_YAML):
        if src.is_file() and src.stat().st_mtime > pickle_mtime:
            return True
    return False


def _build_graph() -> DiGraph:
    """Full graph construction from source data."""
    from defectdojo_crewai.knowledge.kg.download import (
        bootstrap_finding_template,
        bootstrap_owasp,
        download_cwe,
        download_nvd,
    )

    # Ensure sources exist (lazy download).
    bootstrap_owasp()
    bootstrap_finding_template()
    download_cwe()
    # NVD download is skipped if already present — otherwise this takes a while.
    if not NVD_JSON.exists():
        LOGGER.warning(
            "NVD data file not found at %s. "
            "Run `python -m defectdojo_crewai.knowledge.kg.download` first "
            "to fetch CVE data (this is a large download).",
            NVD_JSON,
        )

    g: DiGraph = nx.DiGraph()
    start = time.perf_counter()

    # ── CWE nodes + hierarchy edges ─────────────────────────────────
    cwe_count = 0
    for entry in iter_cwe_entries(CWE_XML):
        node_id = f"cwe:{entry['cwe_id']}"
        g.add_node(
            node_id,
            kind="cwe",
            cwe_id=entry["cwe_id"],
            name=entry["name"],
            description=entry["description"],
            abstraction=entry["abstraction"],
        )
        cwe_count += 1
        # Parent / child edges.
        for parent_id in entry["parent_cwe_ids"]:
            g.add_edge(
                f"cwe:{parent_id}",
                node_id,
                relation="cwe_parent_of",
            )
        for child_id in entry["child_cwe_ids"]:
            g.add_edge(
                node_id,
                f"cwe:{child_id}",
                relation="cwe_parent_of",
            )
    LOGGER.info("CWE  nodes: %d", cwe_count)

    # ── OWASP category nodes + CWE→OWASP edges ──────────────────────
    owasp_count = 0
    for cat in parse_owasp_yaml(OWASP_YAML):
        node_id = f"owasp:{cat['category_id']}"
        g.add_node(
            node_id,
            kind="owasp_category",
            category_id=cat["category_id"],
            name=cat["name"],
            description=cat["description"],
            year=cat["year"],
        )
        owasp_count += 1
        for cwe_id in cat["cwe_ids"]:
            cwe_node = f"cwe:{cwe_id}"
            if g.has_node(cwe_node):
                g.add_edge(cwe_node, node_id, relation="cwe_in_owasp")
    LOGGER.info("OWASP nodes: %d", owasp_count)

    # ── CVE nodes + CVE→CWE edges ──────────────────────────────────
    cve_count = 0
    if NVD_JSON.exists():
        for entry in iter_cve_entries(NVD_JSON):
            node_id = f"cve:{entry['cve_id']}"
            g.add_node(
                node_id,
                kind="cve",
                cve_id=entry["cve_id"],
                description=entry["description"],
                cvss_v3_score=entry["cvss_v3_score"],
                cvss_v3_severity=entry["cvss_v3_severity"],
                cvss_v4_score=entry["cvss_v4_score"],
                cvss_v4_severity=entry["cvss_v4_severity"],
                epss_score=entry["epss_score"],
                kev=entry["kev"],
            )
            cve_count += 1
            for cwe_id in entry["cwe_ids"]:
                cwe_node = f"cwe:{cwe_id}"
                if g.has_node(cwe_node):
                    g.add_edge(node_id, cwe_node, relation="cve_uses_cwe")
        LOGGER.info("CVE  nodes: %d", cve_count)
    else:
        LOGGER.info("CVE  nodes: 0 (NVD file missing — run download first)")

    # ── Finding-template nodes + template→CWE edges ─────────────────
    tmpl_count = 0
    for tpl in parse_finding_template_yaml(TEMPLATE_YAML):
        node_id = f"template:{tpl['template_id']}"
        g.add_node(
            node_id,
            kind="finding_template",
            template_id=tpl["template_id"],
            title=tpl["title"],
            severity=tpl["severity"],
            cwe_id=tpl["cwe_id"],
            description=tpl["description"],
            remediation=tpl["remediation"],
        )
        tmpl_count += 1
        cwe_target = tpl.get("cwe_id")
        if cwe_target:
            cwe_node = f"cwe:{cwe_target}"
            if g.has_node(cwe_node):
                g.add_edge(node_id, cwe_node, relation="template_maps_to_cwe")
    LOGGER.info("Template nodes: %d", tmpl_count)

    # ── persist ─────────────────────────────────────────────────────
    KG_DIR.mkdir(parents=True, exist_ok=True)
    with PICKLE_PATH.open("wb") as fh:
        pickle.dump(g, fh, protocol=5)
    elapsed = time.perf_counter() - start
    LOGGER.info(
        "Graph built in %.1f s: %d nodes, %d edges → %s",
        elapsed,
        g.number_of_nodes(),
        g.number_of_edges(),
        PICKLE_PATH,
    )
    return g


# ── CLI ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    force = "--build" in sys.argv
    g = get_graph(force_rebuild=force)
    print(f"Nodes: {g.number_of_nodes():,}  Edges: {g.number_of_edges():,}")
