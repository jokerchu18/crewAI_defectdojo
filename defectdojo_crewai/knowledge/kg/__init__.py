"""Vulnerability Knowledge Graph (CWE + CVE + OWASP + Finding Templates).

Provides a networkx-based structured graph that complements the Qdrant
vector store.  Use the graph when you need:

- CVE → CWE → OWASP category traversal
- CWE parent/child hierarchy queries
- KEV (Known Exploited Vulnerability) lookups
- Structured context injection into Router fallback prompts
- On-demand CVE enrichment from scan imports
"""

from defectdojo_crewai.knowledge.kg.graph_builder import get_graph
from defectdojo_crewai.knowledge.kg.injector import inject_kg_context
from defectdojo_crewai.knowledge.kg.enricher import (
    enrich_graph_from_scan,
    enrich_graph_with_cves,
    enrich_graph_with_cves_batch,
)
from defectdojo_crewai.knowledge.kg.query import (
    find_path,
    query_by_cve,
    query_by_cwe,
    search_cwe_by_keyword,
    search_kev_cves,
    subgraph_by_owasp,
)

__all__ = [
    "get_graph",
    "inject_kg_context",
    "enrich_graph_from_scan",
    "enrich_graph_with_cves",
    "enrich_graph_with_cves_batch",
    "find_path",
    "query_by_cve",
    "query_by_cwe",
    "search_cwe_by_keyword",
    "search_kev_cves",
    "subgraph_by_owasp",
]
