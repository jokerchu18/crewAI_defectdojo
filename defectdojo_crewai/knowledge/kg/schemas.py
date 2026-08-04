"""Pydantic models for the vulnerability knowledge graph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ── node types ─────────────────────────────────────────────────────────


AbstractionLevel = Literal["Pillar", "Class", "Base", "Variant", "Compound"]

SeverityLevel = Literal["NONE", "LOW", "MEDIUM", "HIGH", "CRITICAL"]


class CWENode(BaseModel):
    """A single CWE weakness entry."""

    cwe_id: str = Field(..., description='e.g. "CWE-79"')
    name: str
    description: str = ""
    abstraction: AbstractionLevel = "Base"
    parent_cwe_ids: list[str] = Field(default_factory=list)
    child_cwe_ids: list[str] = Field(default_factory=list)


class CVENode(BaseModel):
    """A single CVE vulnerability record (CVSS-filtered subset)."""

    cve_id: str = Field(..., description='e.g. "CVE-2024-1234"')
    description: str = ""
    cvss_v3_score: float | None = None
    cvss_v3_severity: SeverityLevel | None = None
    cvss_v4_score: float | None = None
    cvss_v4_severity: SeverityLevel | None = None
    epss_score: float | None = None
    kev: bool = False
    cwe_ids: list[str] = Field(default_factory=list)


class OWASPCategoryNode(BaseModel):
    """An OWASP Top-10 category."""

    category_id: str = Field(..., description='e.g. "A03"')
    name: str
    description: str = ""
    year: int = 2021
    cwe_ids: list[str] = Field(default_factory=list)


class FindingTemplateNode(BaseModel):
    """An internal DefectDojo-style finding template."""

    template_id: str
    title: str
    severity: SeverityLevel | None = None
    cwe_id: str | None = None
    description: str = ""
    remediation: str = ""
    owasp_category: str | None = None


# ── query / injection models ───────────────────────────────────────────


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str


class GraphQueryResult(BaseModel):
    nodes: list[dict]
    edges: list[GraphEdge] = Field(default_factory=list)
    paths: list[list[str]] | None = None


class KGInjection(BaseModel):
    """Context snippet injected into the Router / Agent prompt."""

    source: Literal["cwe", "cve", "owasp", "finding_template", "multi"]
    triggered_by: str  # e.g. "CWE-79" or keyword
    text: str  # human-readable context block
    nodes: list[dict] = Field(default_factory=list)
