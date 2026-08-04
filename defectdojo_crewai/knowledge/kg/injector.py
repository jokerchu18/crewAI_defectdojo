"""Inject knowledge-graph context into Router / Agent prompts.

When the Router produces a low-confidence plan, this module attempts to
extract CWE / CVE / OWASP references from the user message, queries the
graph, and returns structured context snippets that can be appended to
the agent's task description.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from defectdojo_crewai.knowledge.kg.query import (
    get_graph,
    query_by_cve,
    query_by_cwe,
    search_cwe_by_keyword,
)
from defectdojo_crewai.knowledge.kg.schemas import KGInjection

LOGGER = logging.getLogger(__name__)

# Regexes for extracting references from natural-language user messages.
_CWE_RE = re.compile(r"\bCWE[:\-]?\s*(\d{1,5})\b", re.IGNORECASE)
_CVE_RE = re.compile(r"\bCVE[:\-]?\s*(\d{4}[:\-]?\d{4,})\b", re.IGNORECASE)
_OWASP_RE = re.compile(
    r"\b(OWASP\s*(?:Top\s*)?10?\s*)?"
    r"A0?([1-9]|10)\b",
    re.IGNORECASE,
)
_CWE_KEYWORD_RE = re.compile(
    r"(XSS|CSRF|SQL\s*(?:[Ii]njection|注入)|SSRF|RCE|"
    r"路径遍历|注入|跨站|反序列化|缓冲区溢出|"
    r"权限提升|信息泄露|认证|加密)",
    re.IGNORECASE,
)

_CWE_KEYWORD_TO_ID: dict[str, str] = {
    "xss": "CWE-79",
    "csrf": "CWE-352",
    "sql injection": "CWE-89",
    "sqlinjection": "CWE-89",
    "ssrf": "CWE-918",
    "rce": "CWE-78",
    "路径遍历": "CWE-22",
    "注入": "CWE-74",
    "sql 注入": "CWE-89",
    "跨站": "CWE-79",
    "反序列化": "CWE-502",
    "缓冲区溢出": "CWE-120",
    "权限提升": "CWE-269",
    "信息泄露": "CWE-200",
    "认证": "CWE-287",
    "加密": "CWE-327",
}


def inject_kg_context(user_message: str) -> list[KGInjection]:
    """Analyse *user_message* for CWE / CVE / OWASP references and return
    graph-sourced context blocks for injection into the agent prompt.

    Returns an empty list when nothing relevant is found or the graph is
    not available.
    """
    try:
        g = get_graph()
    except Exception:
        LOGGER.debug("Knowledge graph unavailable — skipping injection.")
        return []

    injections: list[KGInjection] = []

    # ── explicit CWE references ────────────────────────────────────
    for match in _CWE_RE.finditer(user_message):
        cwe_id = f"CWE-{match.group(1)}"
        result = query_by_cwe(cwe_id)
        if result is None:
            continue
        text = _format_cwe_injection(cwe_id, result)
        if text:
            injections.append(
                KGInjection(
                    source="cwe",
                    triggered_by=cwe_id,
                    text=text,
                    nodes=result.nodes[:20],
                )
            )

    # ── explicit CVE references ────────────────────────────────────
    for match in _CVE_RE.finditer(user_message):
        cve_num = match.group(1).replace(":", "-")
        cve_id = f"CVE-{cve_num}"
        result = query_by_cve(cve_id)
        if result is None:
            continue
        text = _format_cve_injection(cve_id, result)
        if text:
            injections.append(
                KGInjection(
                    source="cve",
                    triggered_by=cve_id,
                    text=text,
                    nodes=result.nodes,
                )
            )

    # ── keyword-based CWE lookup (when no explicit CWE in message) ─
    if not any(
        inj.source == "cwe" for inj in injections
    ):
        for match in _CWE_KEYWORD_RE.finditer(user_message):
            keyword = match.group(0).lower()
            cwe_id = _CWE_KEYWORD_TO_ID.get(keyword)
            if cwe_id is None:
                continue
            result = query_by_cwe(cwe_id)
            if result is None:
                continue
            text = _format_cwe_injection(cwe_id, result)
            if text:
                injections.append(
                    KGInjection(
                        source="cwe",
                        triggered_by=keyword,
                        text=text,
                        nodes=result.nodes[:20],
                    )
                )

    return injections


# ── formatting helpers ─────────────────────────────────────────────────


def _format_cwe_injection(cwe_id: str, result: Any) -> str:
    """Build a compact Markdown summary block for a CWE subgraph."""
    lines = [f"### 知识图谱: {cwe_id}"]
    for node in result.nodes:
        kind = node.get("kind")
        if kind == "cwe":
            lines.append(
                f"- **{node.get('cwe_id')}** ({node.get('abstraction', '')}): "
                f"{node.get('name', '')}"
            )
            desc = node.get("description", "")
            if desc:
                lines.append(f"  {desc[:300]}")
        elif kind == "owasp_category":
            lines.append(
                f"- OWASP **{node.get('category_id')}** {node.get('name', '')}"
            )
        elif kind == "cve":
            lines.append(
                f"- {node.get('cve_id')} "
                f"(CVSS {node.get('cvss_v3_severity', '?')} "
                f"{node.get('cvss_v3_score', '?')})"
                f"{' [KEV]' if node.get('kev') else ''}"
            )
    if result.edges:
        edge_lines = []
        for edge in result.edges:
            src_label = _short_label(edge.source)
            tgt_label = _short_label(edge.target)
            edge_lines.append(f"  {src_label} --[{edge.relation}]--> {tgt_label}")
        if edge_lines:
            lines.append("**关系:**")
            lines.extend(edge_lines[:15])
    return "\n".join(lines)


def _format_cve_injection(cve_id: str, result: Any) -> str:
    """Build a compact Markdown summary block for a CVE subgraph."""
    lines = [f"### 知识图谱: {cve_id}"]
    for node in result.nodes:
        kind = node.get("kind")
        if kind == "cve":
            lines.append(
                f"- **{node.get('cve_id')}**: {node.get('description', '')[:300]}"
            )
            lines.append(
                f"  CVSS v3: {node.get('cvss_v3_score', '?')} "
                f"({node.get('cvss_v3_severity', '?')})"
                f"{' — CISA KEV' if node.get('kev') else ''}"
            )
        elif kind == "cwe":
            lines.append(
                f"- 关联弱点: **{node.get('cwe_id')}** — {node.get('name', '')}"
            )
        elif kind == "owasp_category":
            lines.append(
                f"- OWASP **{node.get('category_id')}** {node.get('name', '')}"
            )
    return "\n".join(lines)


def _short_label(node_id: str) -> str:
    """Return a compact label for a node ID."""
    if ":" in node_id:
        return node_id.split(":", 1)[1]
    return node_id
