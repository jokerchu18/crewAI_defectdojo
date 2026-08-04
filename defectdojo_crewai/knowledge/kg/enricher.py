"""On-demand CVE enrichment — fetch single CVEs from NVD API and add to graph.

Called automatically after a scan import completes, or manually via::

    from defectdojo_crewai.knowledge.kg.enricher import enrich_graph_with_cves
    enrich_graph_with_cves(["CVE-2024-43047", "CVE-2024-1234"])
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from typing import Any

import httpx

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.knowledge.kg.graph_builder import PICKLE_PATH, get_graph
from defectdojo_crewai.tools.defectdojo_api import (
    defectdojo_get_finding_tool,
)

LOGGER = logging.getLogger(__name__)

NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
_GUARD = threading.Lock()  # protect graph mutations + pickle writes

# Rate limit: 6 req/min without key, ~50 req/min with key.
_DELAY_NO_KEY = 1.2   # seconds between NVD requests (safe for no-API-key)
_DELAY_WITH_KEY = 0.3


# ── public API ─────────────────────────────────────────────────────────


def enrich_graph_from_scan(import_output: dict[str, Any]) -> int:
    """Called after scan import — extracts CVE IDs from findings and adds to graph.

    Returns the number of new CVE nodes added.  Never raises — failures are
    logged and the import result is NOT affected.
    """
    test_id = import_output.get("test_id") if isinstance(import_output, dict) else None
    if not test_id:
        LOGGER.debug("No test_id in import output; skipping KG enrichment.")
        return 0

    try:
        cve_ids = _extract_cves_from_test(test_id)
    except Exception:
        LOGGER.exception("Failed to extract CVEs from test %d", test_id)
        return 0

    if not cve_ids:
        LOGGER.info("No CVE IDs found in test %d findings.", test_id)
        return 0

    LOGGER.info("Found %d unique CVE(s) in test %d: %s", len(cve_ids), test_id, cve_ids)
    return enrich_graph_with_cves(cve_ids)


def enrich_graph_with_cves(
    cve_ids: list[str],
    *,
    api_key: str | None = None,
) -> int:
    """Fetch *cve_ids* from NVD API and add them to the knowledge graph.

    CVE IDs already present in the graph are silently skipped.  The pickle
    cache is updated after each successful batch.

    Returns the number of **new** CVE nodes added.
    """
    api_key = api_key or os.getenv("NVD_API_KEY")
    delay = _DELAY_WITH_KEY if api_key else _DELAY_NO_KEY
    added = 0

    for cve_id in cve_ids:
        cve_id = _normalize(cve_id)
        # Fast-path: check if already present (no lock needed for read).
        g = get_graph()
        if g.has_node(f"cve:{cve_id}"):
            LOGGER.debug("%s already in graph — skipping", cve_id)
            continue

        data = _fetch_cve_from_nvd(cve_id, api_key)
        if data is None:
            continue  # NVD query failed — logged inside _fetch

        _persist_cve(data)
        added += 1
        time.sleep(delay)

    if added:
        LOGGER.info("Added %d new CVE(s) to knowledge graph.", added)
    return added


def enrich_graph_with_cves_batch(
    cve_ids: list[str],
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Bulk variant that returns per-CVE status.  Convenience for callers
    that need fine-grained reporting (e.g. API endpoints)."""
    api_key = api_key or os.getenv("NVD_API_KEY")
    delay = _DELAY_WITH_KEY if api_key else _DELAY_NO_KEY
    results: dict[str, Any] = {"added": 0, "skipped": 0, "failed": 0, "details": []}

    for cve_id in cve_ids:
        cve_id = _normalize(cve_id)
        g = get_graph()
        if g.has_node(f"cve:{cve_id}"):
            results["skipped"] += 1
            results["details"].append({"cve_id": cve_id, "status": "skipped"})
            continue

        data = _fetch_cve_from_nvd(cve_id, api_key)
        if data is None:
            results["failed"] += 1
            results["details"].append({"cve_id": cve_id, "status": "failed"})
            continue

        _persist_cve(data)
        results["added"] += 1
        results["details"].append({"cve_id": cve_id, "status": "added"})
        time.sleep(delay)

    LOGGER.info(
        "Batch enrichment: %d added, %d skipped, %d failed",
        results["added"], results["skipped"], results["failed"],
    )
    return results


# ── internal ───────────────────────────────────────────────────────────


def _normalize(cve_id: str) -> str:
    cve_id = cve_id.strip().upper()
    # Handle CVE-2024-1234 or CVE:2024-1234
    return cve_id.replace(":", "-")


_CVE_FROM_FINDING_RE = __import__("re").compile(
    r"CVE[:\-]\d{4}[:\-]\d{4,}", __import__("re").IGNORECASE
)


def _extract_cves_from_test(test_id: int) -> list[str]:
    """Query DefectDojo findings for *test_id* and extract unique CVE IDs."""
    try:
        findings_resp = defectdojo_get_finding_tool(
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            test_id=test_id,
        )
    except Exception:
        LOGGER.exception("Failed to fetch findings for test %d", test_id)
        return []

    results = findings_resp.get("results") if isinstance(findings_resp, dict) else []
    if not isinstance(results, list):
        return []

    cve_ids: set[str] = set()
    for finding in results:
        if not isinstance(finding, dict):
            continue
        # 1) vulnerability_ids: [{"vulnerability_id": "CVE-..."}, ...]
        for vid in finding.get("vulnerability_ids") or []:
            if isinstance(vid, dict):
                raw = str(vid.get("vulnerability_id", ""))
                for m in _CVE_FROM_FINDING_RE.finditer(raw):
                    cve_ids.add(_normalize(m.group(0)))
        # 2) cve field (some versions)
        cve_field = finding.get("cve")
        if isinstance(cve_field, str) and cve_field.strip():
            for m in _CVE_FROM_FINDING_RE.finditer(cve_field):
                cve_ids.add(_normalize(m.group(0)))
        # 3) vuln_id_from_tool
        tool_id = finding.get("vuln_id_from_tool")
        if isinstance(tool_id, str):
            for m in _CVE_FROM_FINDING_RE.finditer(tool_id):
                cve_ids.add(_normalize(m.group(0)))
        # 4) title / description (some scanners embed CVE in title)
        for field in ("title", "description"):
            val = finding.get(field)
            if isinstance(val, str):
                for m in _CVE_FROM_FINDING_RE.finditer(val):
                    cve_ids.add(_normalize(m.group(0)))

    return sorted(cve_ids)


def _fetch_cve_from_nvd(cve_id: str, api_key: str | None) -> dict[str, Any] | None:
    """Query NVD API 2.0 for a single CVE.  Returns CVENode-compatible dict or None."""
    headers: dict[str, str] = {}
    if api_key:
        headers["apiKey"] = api_key

    params = {"cveId": cve_id}
    try:
        resp = httpx.get(NVD_API, params=params, headers=headers, timeout=45)
    except httpx.TimeoutException:
        LOGGER.warning("NVD API timeout for %s", cve_id)
        return None
    except Exception:
        LOGGER.exception("NVD API request failed for %s", cve_id)
        return None

    if resp.status_code == 404:
        LOGGER.debug("NVD API: %s not found", cve_id)
        return None
    if resp.status_code != 200:
        LOGGER.warning("NVD API returned %d for %s", resp.status_code, cve_id)
        return None

    try:
        data = resp.json()
    except Exception:
        LOGGER.exception("NVD API: invalid JSON for %s", cve_id)
        return None

    vulns = data.get("vulnerabilities") or []
    if not vulns:
        LOGGER.debug("NVD API: no data for %s", cve_id)
        return None

    cve = vulns[0].get("cve") or {}
    return _parse_cve_response(cve_id, cve)


def _parse_cve_response(cve_id: str, cve: dict[str, Any]) -> dict[str, Any] | None:
    """Extract fields from a single NVD CVE object.  Returns None if no CVSS data."""
    # Description (English)
    description = ""
    for desc in cve.get("descriptions") or []:
        if isinstance(desc, dict) and desc.get("lang") == "en":
            description = desc.get("value", "")
            break

    # CVSS scores
    metrics = cve.get("metrics") or {}
    cvss_v31 = _first_cvss(metrics.get("cvssMetricV31"))
    cvss_v40 = _first_cvss(metrics.get("cvssMetricV40"))

    if cvss_v31 is None and cvss_v40 is None:
        LOGGER.debug("%s has no CVSS data — skipping", cve_id)
        return None

    # CWE mapping
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
        "cve_id": cve_id,
        "description": description,
        "cvss_v3_score": cvss_v31.get("score") if cvss_v31 else None,
        "cvss_v3_severity": cvss_v31.get("severity") if cvss_v31 else None,
        "cvss_v4_score": cvss_v40.get("score") if cvss_v40 else None,
        "cvss_v4_severity": cvss_v40.get("severity") if cvss_v40 else None,
        "epss_score": None,
        "kev": False,
        "cwe_ids": cwe_ids,
    }


def _first_cvss(metric_list: Any) -> dict[str, Any] | None:
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


def _persist_cve(data: dict[str, Any]) -> None:
    """Thread-safe: add CVE node + edges to in-memory graph, then update pickle."""
    cve_id = data["cve_id"]
    node_id = f"cve:{cve_id}"

    with _GUARD:
        g = get_graph()
        if g.has_node(node_id):
            return  # another caller already added it

        g.add_node(
            node_id,
            kind="cve",
            cve_id=cve_id,
            description=data["description"],
            cvss_v3_score=data["cvss_v3_score"],
            cvss_v3_severity=data["cvss_v3_severity"],
            cvss_v4_score=data["cvss_v4_score"],
            cvss_v4_severity=data["cvss_v4_severity"],
            epss_score=data["epss_score"],
            kev=data["kev"],
        )

        for cwe_id in data["cwe_ids"]:
            cwe_node = f"cwe:{cwe_id}"
            if g.has_node(cwe_node):
                g.add_edge(node_id, cwe_node, relation="cve_uses_cwe")

        # Persist
        PICKLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with PICKLE_PATH.open("wb") as fh:
            pickle.dump(g, fh, protocol=5)

    LOGGER.info("Added %s to knowledge graph (CVSS %.1f, %d CWE(s)).",
                 cve_id, data["cvss_v3_score"] or 0, len(data["cwe_ids"]))
