"""Read-only CrewAI tool for querying the vulnerability knowledge graph.

Agents extract CVE / CWE / OWASP identifiers from DefectDojo findings
(NOT from the raw user message) and call ``knowledge_graph_lookup`` to
obtain compressed, structured evidence before making triage / risk /
remediation decisions.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from defectdojo_crewai.knowledge.kg.graph_builder import get_graph
from defectdojo_crewai.knowledge.kg.query import (
    query_by_cve,
    query_by_cwe,
    subgraph_by_owasp,
)

LOGGER = logging.getLogger(__name__)

_DESC_LIMIT = 300
_OWASP_MEMBER_CAP = 20
_EVIDENCE_CAP = 50

_OWASP_ID_RE = re.compile(r"A?0?(\d{1,2})", re.IGNORECASE)


class KnowledgeGraphLookupInput(BaseModel):
    cve_ids: list[str] = Field(
        default_factory=list,
        description='finding 中提取的 CVE 标识列表，例如 ["CVE-2024-1234"]',
    )
    cwe_ids: list[str] = Field(
        default_factory=list,
        description='finding 中提取的 CWE 标识列表，例如 ["CWE-79"] 或 ["79"]',
    )
    owasp_ids: list[str] = Field(
        default_factory=list,
        description='OWASP Top 10 分类标识列表，例如 ["A03"]',
    )


def knowledge_graph_lookup(
    cve_ids: list[str] | None = None,
    cwe_ids: list[str] | None = None,
    owasp_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Query the knowledge graph by explicit identifiers and return a
    compressed structured summary (never the raw networkx graph)."""
    cve_ids = cve_ids or []
    cwe_ids = cwe_ids or []
    owasp_ids = owasp_ids or []

    if not (cve_ids or cwe_ids or owasp_ids):
        return {
            "status": "error",
            "message": "必须至少提供 cve_ids、cwe_ids 或 owasp_ids 中的一项。",
        }

    try:
        get_graph()
    except Exception:
        LOGGER.exception("Knowledge graph unavailable")
        return {
            "status": "unavailable",
            "message": "知识图谱当前不可用，请基于 DefectDojo 原始字段继续处理。",
        }

    cves: dict[str, dict[str, Any]] = {}
    cwes: dict[str, dict[str, Any]] = {}
    owasp: dict[str, dict[str, Any]] = {}
    evidence: list[str] = []
    unmatched: list[str] = []

    for raw in cve_ids:
        _lookup_cve(raw, cves, cwes, owasp, evidence, unmatched)
    for raw in cwe_ids:
        _lookup_cwe(raw, cwes, owasp, evidence, unmatched)
    for raw in owasp_ids:
        _lookup_owasp(raw, owasp, evidence, unmatched)

    matched = bool(cves or cwes or owasp)
    return {
        "status": "matched" if matched else "no_match",
        "cves": list(cves.values()),
        "cwes": list(cwes.values()),
        "owasp": list(owasp.values()),
        "evidence": evidence[:_EVIDENCE_CAP],
        "unmatched": unmatched,
    }


class KnowledgeGraphLookupTool(BaseTool):
    name: str = "knowledge_graph_lookup"
    description: str = (
        "根据 DefectDojo finding 中提取的 CVE / CWE / OWASP 标识查询漏洞知识图谱，"
        "返回压缩后的结构化证据：CVE 的 CVSS、EPSS、KEV 状态与关联 CWE；"
        "CWE 的名称、描述与 OWASP 分类；OWASP 分类的成员 CWE。"
        "调用时至少提供一个 ID。标识必须来自 finding 字段"
        "（vulnerability_ids、cwe、title、description 等），"
        "不得根据用户消息猜测或编造。返回结果只能作为证据，"
        "不能覆盖 finding 中的原始字段。"
    )
    args_schema: type[BaseModel] = KnowledgeGraphLookupInput

    def _run(
        self,
        cve_ids: list[str] | None = None,
        cwe_ids: list[str] | None = None,
        owasp_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return knowledge_graph_lookup(
            cve_ids=cve_ids,
            cwe_ids=cwe_ids,
            owasp_ids=owasp_ids,
        )


# ── per-kind lookups ───────────────────────────────────────────────────


def _lookup_cve(
    raw: str,
    cves: dict[str, dict],
    cwes: dict[str, dict],
    owasp: dict[str, dict],
    evidence: list[str],
    unmatched: list[str],
) -> None:
    cve_id = raw.strip().upper().replace(":", "-")
    if cve_id in cves:
        return
    result = query_by_cve(cve_id)
    if result is None:
        unmatched.append(cve_id)
        return

    entry: dict[str, Any] = {}
    for node in result.nodes:
        kind = node.get("kind")
        if kind == "cve":
            entry = {
                "cve_id": node.get("cve_id"),
                "description": _clip(node.get("description")),
                "cvss_v3_score": node.get("cvss_v3_score"),
                "cvss_v3_severity": node.get("cvss_v3_severity"),
                "cvss_v4_score": node.get("cvss_v4_score"),
                "cvss_v4_severity": node.get("cvss_v4_severity"),
                "epss_score": node.get("epss_score"),
                "kev": bool(node.get("kev")),
                "cwe_ids": [],
            }
        elif kind == "cwe":
            _add_cwe_node(node, cwes)
        elif kind == "owasp_category":
            _add_owasp_node(node, owasp)

    for edge in result.edges:
        src, tgt = _short(edge.source), _short(edge.target)
        evidence.append(f"{src} -> {tgt}")
        if edge.relation == "cve_uses_cwe" and entry:
            entry["cwe_ids"].append(tgt)

    if entry:
        cves[cve_id] = entry


def _lookup_cwe(
    raw: str,
    cwes: dict[str, dict],
    owasp: dict[str, dict],
    evidence: list[str],
    unmatched: list[str],
) -> None:
    digits = re.sub(r"(?i)^CWE[:\-]?", "", raw.strip())
    cwe_id = f"CWE-{digits}"
    result = query_by_cwe(cwe_id)
    if result is None:
        unmatched.append(cwe_id)
        return

    for node in result.nodes:
        kind = node.get("kind")
        if kind == "cwe":
            _add_cwe_node(node, cwes)
        elif kind == "owasp_category":
            _add_owasp_node(node, owasp)
        # CVE fan-out from a CWE is intentionally dropped — too large.

    for edge in result.edges:
        if edge.relation == "cve_uses_cwe":
            continue
        evidence.append(f"{_short(edge.source)} -> {_short(edge.target)}")


def _lookup_owasp(
    raw: str,
    owasp: dict[str, dict],
    evidence: list[str],
    unmatched: list[str],
) -> None:
    m = _OWASP_ID_RE.fullmatch(raw.strip().split(":")[0])
    category_id = f"A{int(m.group(1)):02d}" if m else raw.strip().upper()
    result = subgraph_by_owasp(category_id)
    if result is None:
        unmatched.append(category_id)
        return

    members: list[dict[str, Any]] = []
    entry: dict[str, Any] = {}
    for node in result.nodes:
        kind = node.get("kind")
        if kind == "owasp_category":
            entry = _owasp_entry(node)
        elif kind == "cwe":
            members.append(
                {"cwe_id": node.get("cwe_id"), "name": node.get("name")}
            )

    if not entry:
        unmatched.append(category_id)
        return

    entry["member_cwe_count"] = len(members)
    entry["member_cwes"] = members[:_OWASP_MEMBER_CAP]
    owasp.setdefault(entry["category_id"], entry)

    for member in members[:_OWASP_MEMBER_CAP]:
        evidence.append(f"{member['cwe_id']} -> {entry['category_id']}")


# ── helpers ────────────────────────────────────────────────────────────


def _add_cwe_node(node: dict, cwes: dict[str, dict]) -> None:
    cwe_id = node.get("cwe_id")
    if not cwe_id or cwe_id in cwes:
        return
    cwes[cwe_id] = {
        "cwe_id": cwe_id,
        "name": node.get("name"),
        "description": _clip(node.get("description")),
        "abstraction": node.get("abstraction"),
    }


def _add_owasp_node(node: dict, owasp: dict[str, dict]) -> None:
    category_id = node.get("category_id")
    if not category_id or category_id in owasp:
        return
    owasp[category_id] = _owasp_entry(node)


def _owasp_entry(node: dict) -> dict[str, Any]:
    return {
        "category_id": node.get("category_id"),
        "name": node.get("name"),
        "description": _clip(node.get("description"), 200),
        "year": node.get("year"),
    }


def _clip(text: Any, limit: int = _DESC_LIMIT) -> str:
    if not isinstance(text, str):
        return ""
    return text[:limit]


def _short(node_id: str) -> str:
    return node_id.split(":", 1)[1] if ":" in node_id else node_id
