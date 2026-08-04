"""Streaming / memory-efficient parsers for CWE XML, NVD JSON, OWASP & template YAML.

Each public function returns a **generator** of lightweight dicts so the graph
builder can consume entries one at a time without materialising the full
dataset in memory.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import xml.etree.ElementTree as ET
import yaml as _yaml

from defectdojo_crewai.knowledge.kg.schemas import (
    CWENode,
    CVENode,
    FindingTemplateNode,
    OWASPCategoryNode,
)

LOGGER = logging.getLogger(__name__)

# ── CWE XML ────────────────────────────────────────────────────────────


def iter_cwe_entries(xml_path: Path) -> Iterator[dict[str, Any]]:
    """Stream-parse the MITRE CWE catalog XML.

    Yields one ``dict`` per ``<Weakness>`` element so callers can construct
    ``CWENode`` instances without loading the full ~150 MB file into memory.
    """
    if not xml_path.is_file():
        raise FileNotFoundError(f"CWE catalog not found: {xml_path}")

    ns = _cwe_namespace(xml_path)
    tag_weakness = f"{{{ns}}}Weakness" if ns else "Weakness"

    for _event, element in ET.iterparse(str(xml_path), events=("end",)):
        if element.tag != tag_weakness:
            continue

        cwe_id = element.attrib.get("ID", "")
        if not cwe_id:
            element.clear()
            continue

        name = element.attrib.get("Name", "")
        abstraction = element.attrib.get("Weakness_Abstraction", "Base")
        description = _first_element_text(element, ns, "Description") or ""

        parent_ids: list[str] = []
        child_ids: list[str] = []
        for rel in element.findall(f"{{{ns}}}Related_Weaknesses/{{{ns}}}Related_Weakness"):
            nature = rel.attrib.get("Nature", "")
            target_id = rel.attrib.get("CWE_ID", "")
            if not target_id:
                continue
            if nature == "ChildOf":
                parent_ids.append(target_id)
            elif nature == "ParentOf":
                child_ids.append(target_id)

        element.clear()
        yield {
            "cwe_id": f"CWE-{cwe_id}",
            "name": name,
            "description": description,
            "abstraction": abstraction,
            "parent_cwe_ids": parent_ids,
            "child_cwe_ids": child_ids,
        }


def _cwe_namespace(xml_path: Path) -> str:
    """Extract the XML namespace from the root element without full parse."""
    with xml_path.open("rb") as fh:
        # Read first 2 KB — enough for the root element with xmlns.
        head = fh.read(2048).decode("utf-8", errors="ignore")
    import re

    match = re.search(r'xmlns=["\']([^"\']+)["\']', head)
    return match.group(1) if match else ""


def _first_element_text(parent: ET.Element, ns: str, tag: str) -> str | None:
    el = parent.find(f"{{{ns}}}{tag}")
    return el.text.strip() if el is not None and el.text else None


# ── NVD CVE JSON ───────────────────────────────────────────────────────


def iter_cve_entries(json_path: Path) -> Iterator[dict[str, Any]]:
    """Stream-read NVD CVE JSON-lines file, yielding only CVEs with CVSS scores.

    The file is expected to contain one ``{"cve": {...}}`` JSON object
    per **line** (JSON-lines format as written by ``download.py``).
    This allows line-by-line incremental reading without materialising
    the entire dataset into memory.
    """
    if not json_path.is_file():
        raise FileNotFoundError(f"NVD CVE file not found: {json_path}")

    with json_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                LOGGER.debug("Skipping malformed JSON line in NVD file")
                continue
            cve_data = _extract_cve_fields(entry)
            if cve_data is not None:
                yield cve_data


def _extract_cve_fields(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Return a CVENode-compatible dict or *None* if the CVE should be skipped."""
    cve = entry.get("cve")
    if not isinstance(cve, dict):
        return None
    cve_id = cve.get("id")
    if not isinstance(cve_id, str):
        return None

    # Description (prefer English).
    description = ""
    for desc in cve.get("descriptions") or []:
        if isinstance(desc, dict) and desc.get("lang") == "en":
            description = desc.get("value", "")
            break

    # CVSS scores — we require at least one of v3.1 or v4.0.
    metrics = cve.get("metrics") or {}
    cvss_v31 = _first_cvss(metrics.get("cvssMetricV31"))
    cvss_v40 = _first_cvss(metrics.get("cvssMetricV40"))

    if cvss_v31 is None and cvss_v40 is None:
        return None  # skip — no CVSS score

    # CWE mapping.
    cwe_ids: list[str] = []
    for weakness in cve.get("weaknesses") or []:
        if not isinstance(weakness, dict):
            continue
        for wdesc in weakness.get("description") or []:
            if isinstance(wdesc, dict):
                val = wdesc.get("value", "")
                if isinstance(val, str) and val.upper().startswith("CWE-"):
                    cwe_ids.append(val.strip())

    return {
        "cve_id": str(cve_id),
        "description": description,
        "cvss_v3_score": cvss_v31.get("score") if cvss_v31 else None,
        "cvss_v3_severity": cvss_v31.get("severity") if cvss_v31 else None,
        "cvss_v4_score": cvss_v40.get("score") if cvss_v40 else None,
        "cvss_v4_severity": cvss_v40.get("severity") if cvss_v40 else None,
        "epss_score": None,  # EPSS is a separate data source
        "kev": False,  # KEV is a separate data source
        "cwe_ids": cwe_ids,
    }


def _first_cvss(metric_list: Any) -> dict[str, Any] | None:
    """Extract primary CVSS data from the first metric in the list."""
    if not isinstance(metric_list, list) or not metric_list:
        return None
    first = metric_list[0]
    if not isinstance(first, dict):
        return None
    data = first.get("cvssData")
    if not isinstance(data, dict):
        return None
    return {
        "score": data.get("baseScore"),
        "severity": data.get("baseSeverity"),
    }


# ── OWASP YAML ─────────────────────────────────────────────────────────


def parse_owasp_yaml(yaml_path: Path) -> list[dict[str, Any]]:
    """Return OWASP category dicts suitable for ``OWASPCategoryNode``."""
    if not yaml_path.is_file():
        raise FileNotFoundError(f"OWASP Top-10 file not found: {yaml_path}")
    with yaml_path.open("r", encoding="utf-8") as fh:
        data = _yaml.safe_load(fh) or {}

    categories: list[dict[str, Any]] = []
    for cat in data.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        categories.append(
            {
                "category_id": str(cat.get("id", "")),
                "name": str(cat.get("name", "")),
                "description": str(cat.get("description", "")),
                "year": int(cat.get("year", 2021)),
                "cwe_ids": [
                    str(c).strip()
                    for c in (cat.get("cwe_ids") or [])
                    if c
                ],
            }
        )
    return categories


# ── Finding Template YAML ───────────────────────────────────────────────


def parse_finding_template_yaml(yaml_path: Path) -> list[dict[str, Any]]:
    """Return finding-template dicts."""
    if not yaml_path.is_file():
        return []
    with yaml_path.open("r", encoding="utf-8") as fh:
        data = _yaml.safe_load(fh) or {}

    templates: list[dict[str, Any]] = []
    for tpl in data.get("templates") or []:
        if not isinstance(tpl, dict):
            continue
        templates.append(
            {
                "template_id": str(tpl.get("template_id", "")),
                "title": str(tpl.get("title", "")),
                "severity": tpl.get("severity"),
                "cwe_id": tpl.get("cwe_id"),
                "description": str(tpl.get("description", "")),
                "remediation": str(tpl.get("remediation", "")),
                "owasp_category": tpl.get("owasp_category"),
            }
        )
    return templates
