"""Idempotent download / bootstrap of KG source datasets.

- MITRE CWE catalog (XML zip)
- NVD CVE API 2.0 (incremental JSON)
- OWASP Top-10 2021 YAML (built-in default if missing)
- Finding-template YAML (empty stub if missing)

Usage::

    python -m defectdojo_crewai.knowledge.kg.download
"""

from __future__ import annotations

import json
import logging
import os
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import yaml

from defectdojo_crewai.config.settings import BASE_DIR, settings

LOGGER = logging.getLogger(__name__)

KG_DIR = BASE_DIR / "data" / "kg"
CWE_DIR = KG_DIR / "cwe"
NVD_DIR = KG_DIR / "nvd"
OWASP_DIR = KG_DIR / "owasp"
TEMPLATE_DIR = KG_DIR / "defectdojo"

CWE_URL = "https://cwe.mitre.org/data/xml/cwec_latest.xml.zip"
NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_PAGE_SIZE = 2000
NVD_RATE_LIMIT_SECONDS = 6.0  # NVD recommends 6 s without API key, ~0.6 s with


def ensure_dirs() -> None:
    for path in (CWE_DIR, NVD_DIR, OWASP_DIR, TEMPLATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def download_cwe() -> Path:
    """Download and unzip the MITRE CWE catalog. Idempotent."""
    xml_path = CWE_DIR / "cwec_latest.xml"
    if xml_path.exists():
        LOGGER.info("CWE XML already present: %s", xml_path)
        return xml_path

    ensure_dirs()
    LOGGER.info("Downloading CWE catalog from %s …", CWE_URL)
    resp = httpx.get(CWE_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()

    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        names = zf.namelist()
        xml_name = next((n for n in names if n.endswith(".xml")), names[0])
        zf.extract(xml_name, str(CWE_DIR))
        extracted = CWE_DIR / xml_name
        if extracted != xml_path:
            extracted.rename(xml_path)

    LOGGER.info("CWE catalog saved to %s (%.1f MB)", xml_path, xml_path.stat().st_size / 1e6)
    return xml_path


def download_nvd(api_key: str | None = None) -> Path:
    """Incrementally fetch NVD CVE 2.0 API data into a single merged JSON file.

    Uses *lastModStartDate* / *lastModEndDate* rolling windows to walk
    through the entire NVD corpus.  Already-downloaded records are
    skipped so subsequent runs are fast.
    """
    json_path = NVD_DIR / "nvdcve-2.0.json"
    ensure_dirs()

    api_key = api_key or os.getenv("NVD_API_KEY")
    headers: dict[str, str] = {}
    if api_key:
        headers["apiKey"] = api_key

    # Determine the date range we have already covered.
    existing_ids: set[str] = set()
    if json_path.exists():
        LOGGER.info("Scanning existing NVD JSON for already-covered CVE IDs …")
        existing_ids = _existing_cve_ids(json_path)

    start_date = datetime(2002, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)
    window_days = 120

    total_new = 0
    current_start = start_date
    while current_start < end_date:
        current_end = min(
            current_start + __import__("datetime").timedelta(days=window_days),
            end_date,
        )
        params: dict[str, Any] = {
            "lastModStartDate": _fmt_iso(current_start),
            "lastModEndDate": _fmt_iso(current_end),
            "resultsPerPage": NVD_PAGE_SIZE,
        }

        page_new = _fetch_nvd_window(
            json_path, params, headers, existing_ids
        )
        total_new += page_new
        current_start = current_end

    LOGGER.info(
        "NVD sync complete: %d new CVEs added to %s (total unique: %d)",
        total_new,
        json_path,
        len(existing_ids) + total_new,
    )
    return json_path


def _fetch_nvd_window(
    json_path: Path,
    params: dict[str, Any],
    headers: dict[str, str],
    existing_ids: set[str],
) -> int:
    """Fetch one date window with pagination. Append new records to *json_path*."""
    start_index = 0
    total_new = 0
    while True:
        params["startIndex"] = start_index
        LOGGER.info(
            "NVD API: %s → %s (startIndex=%d)",
            params["lastModStartDate"],
            params["lastModEndDate"],
            start_index,
        )
        try:
            resp = httpx.get(NVD_API, params=params, headers=headers, timeout=90)
        except httpx.TimeoutException:
            LOGGER.warning("NVD API timeout; retrying after %ds …", NVD_RATE_LIMIT_SECONDS)
            time.sleep(NVD_RATE_LIMIT_SECONDS)
            continue

        if resp.status_code == 403:
            LOGGER.error("NVD API 403 — rate limited. Waiting 30 s …")
            time.sleep(30)
            continue
        if resp.status_code != 200:
            LOGGER.error("NVD API returned %d: %s", resp.status_code, resp.text[:500])
            break

        data = resp.json()
        vulns = data.get("vulnerabilities") or []
        if not vulns:
            break

        new_records = 0
        with json_path.open("a", encoding="utf-8") as fh:
            for vuln in vulns:
                cve = vuln.get("cve") or {}
                cve_id = cve.get("id")
                if not isinstance(cve_id, str) or cve_id in existing_ids:
                    continue
                fh.write(json.dumps({"cve": cve}, ensure_ascii=False) + "\n")
                existing_ids.add(cve_id)
                new_records += 1

        total_new += new_records
        total_results = data.get("totalResults", 0)
        start_index += len(vulns)
        if start_index >= total_results:
            break
        time.sleep(NVD_RATE_LIMIT_SECONDS)

    return total_new


def _existing_cve_ids(json_path: Path) -> set[str]:
    """Scan the existing JSON-lines file for CVE IDs."""
    ids: set[str] = set()
    with json_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cve_id = (obj.get("cve") or {}).get("id")
            if isinstance(cve_id, str):
                ids.add(cve_id)
    return ids


def _fmt_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")


# ── OWASP Top-10 2021 built-in default ─────────────────────────────────

_OWASP_DEFAULT = """\
# OWASP Top 10 2021 — Category → CWE mapping
# Edit this file to add or correct mappings.
year: 2021
categories:
  - id: A01
    name: Broken Access Control
    description: |
      Access control enforces policy such that users cannot act outside of
      their intended permissions.  Failures typically lead to unauthorised
      information disclosure, modification, or destruction of all data.
    cwe_ids:
      - CWE-22
      - CWE-23
      - CWE-35
      - CWE-59
      - CWE-200
      - CWE-201
      - CWE-219
      - CWE-264
      - CWE-275
      - CWE-276
      - CWE-284
      - CWE-285
      - CWE-352
      - CWE-359
      - CWE-425
      - CWE-430
      - CWE-441
      - CWE-497
      - CWE-538
      - CWE-540
      - CWE-548
      - CWE-552
      - CWE-566
      - CWE-601
      - CWE-639
      - CWE-647
      - CWE-668
      - CWE-706
      - CWE-862
      - CWE-863
      - CWE-913
      - CWE-922
      - CWE-1275
      - CWE-1321
  - id: A02
    name: Cryptographic Failures
    description: |
      Failures related to cryptography (or lack thereof) which often lead
      to exposure of sensitive data.
    cwe_ids:
      - CWE-261
      - CWE-296
      - CWE-310
      - CWE-319
      - CWE-321
      - CWE-322
      - CWE-323
      - CWE-324
      - CWE-325
      - CWE-326
      - CWE-327
      - CWE-328
      - CWE-329
      - CWE-330
      - CWE-331
      - CWE-335
      - CWE-336
      - CWE-337
      - CWE-338
      - CWE-340
      - CWE-347
      - CWE-523
      - CWE-720
      - CWE-757
      - CWE-759
      - CWE-760
      - CWE-780
      - CWE-818
      - CWE-916
  - id: A03
    name: Injection
    description: |
      User-supplied data is not validated, filtered, or sanitised by the
      application.  Some of the most common injections are SQL, NoSQL, OS
      command, LDAP, and Expression Language (EL) / OGNL injection.
    cwe_ids:
      - CWE-74
      - CWE-75
      - CWE-77
      - CWE-78
      - CWE-79
      - CWE-88
      - CWE-89
      - CWE-90
      - CWE-91
      - CWE-93
      - CWE-94
      - CWE-95
      - CWE-98
      - CWE-99
      - CWE-116
      - CWE-138
      - CWE-184
      - CWE-470
      - CWE-471
      - CWE-564
      - CWE-610
      - CWE-643
      - CWE-644
      - CWE-652
      - CWE-706
      - CWE-917
      - CWE-943
      - CWE-1236
  - id: A04
    name: Insecure Design
    description: |
      Risks related to design and architectural flaws, with a call for
      more use of threat modelling, secure design patterns, and reference
      architectures.
    cwe_ids:
      - CWE-73
      - CWE-183
      - CWE-209
      - CWE-213
      - CWE-235
      - CWE-256
      - CWE-257
      - CWE-266
      - CWE-269
      - CWE-272
      - CWE-280
      - CWE-306
      - CWE-307
      - CWE-311
      - CWE-312
      - CWE-328
      - CWE-340
      - CWE-345
      - CWE-346
      - CWE-367
      - CWE-384
      - CWE-419
      - CWE-430
      - CWE-434
      - CWE-444
      - CWE-451
      - CWE-472
      - CWE-501
      - CWE-522
      - CWE-525
      - CWE-539
      - CWE-579
      - CWE-598
      - CWE-602
      - CWE-642
      - CWE-646
      - CWE-650
      - CWE-653
      - CWE-656
      - CWE-657
      - CWE-799
      - CWE-807
      - CWE-840
      - CWE-841
      - CWE-918
      - CWE-1004
      - CWE-1021
      - CWE-1173
      - CWE-1283
      - CWE-1320
  - id: A05
    name: Security Misconfiguration
    description: |
      The application / infrastructure might be vulnerable due to a
      misconfiguration: missing appropriate security hardening, insecure
      default config, unnecessary features enabled, etc.
    cwe_ids:
      - CWE-2
      - CWE-11
      - CWE-13
      - CWE-15
      - CWE-16
      - CWE-260
      - CWE-315
      - CWE-520
      - CWE-526
      - CWE-537
      - CWE-538
      - CWE-541
      - CWE-547
      - CWE-611
      - CWE-776
      - CWE-942
      - CWE-1004
      - CWE-1035
      - CWE-1174
  - id: A06
    name: Vulnerable and Outdated Components
    description: |
      Using components (libraries, frameworks, modules) that are
      unpatched, unsupported, or out of date.
    cwe_ids:
      - CWE-937
      - CWE-1035
      - CWE-1104
      - CWE-1329
  - id: A07
    name: Identification and Authentication Failures
    description: |
      Confirmation of the user's identity, authentication, and session
      management is critical to protect against authentication-related
      attacks.
    cwe_ids:
      - CWE-255
      - CWE-259
      - CWE-287
      - CWE-288
      - CWE-290
      - CWE-294
      - CWE-295
      - CWE-297
      - CWE-300
      - CWE-302
      - CWE-304
      - CWE-306
      - CWE-307
      - CWE-346
      - CWE-384
      - CWE-521
      - CWE-613
      - CWE-620
      - CWE-640
      - CWE-798
      - CWE-804
      - CWE-840
      - CWE-841
      - CWE-1390
      - CWE-1391
  - id: A08
    name: Software and Data Integrity Failures
    description: |
      Code and infrastructure that does not protect against integrity
      violations.  Example: applications relying upon plugins, libraries,
      or modules from untrusted sources.
    cwe_ids:
      - CWE-345
      - CWE-353
      - CWE-426
      - CWE-494
      - CWE-502
      - CWE-565
      - CWE-784
      - CWE-829
      - CWE-830
      - CWE-915
      - CWE-1275
      - CWE-1321
      - CWE-1426
  - id: A09
    name: Security Logging and Monitoring Failures
    description: |
      Insufficient logging, detection, monitoring, and active response.
    cwe_ids:
      - CWE-117
      - CWE-223
      - CWE-532
      - CWE-778
  - id: A10
    name: Server-Side Request Forgery (SSRF)
    description: |
      SSRF flaws occur whenever a web application is fetching a remote
      resource without validating the user-supplied URL.
    cwe_ids:
      - CWE-918
"""

_FINDING_TEMPLATE_DEFAULT = """\
# Internal DefectDojo finding templates.
# Map your organisation's common finding types to standard CWE IDs.
# Add entries as needed — the graph will link them to CWE nodes.
templates: []
"""


def _write_if_missing(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        LOGGER.info("Already present: %s", path)
        return
    path.write_text(content, encoding="utf-8")
    LOGGER.info("Created default: %s", path)


def bootstrap_owasp() -> Path:
    yaml_path = OWASP_DIR / "top10.yaml"
    _write_if_missing(yaml_path, _OWASP_DEFAULT)
    return yaml_path


def bootstrap_finding_template() -> Path:
    yaml_path = TEMPLATE_DIR / "finding_template.yaml"
    _write_if_missing(yaml_path, _FINDING_TEMPLATE_DEFAULT)
    return yaml_path


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    ensure_dirs()
    bootstrap_owasp()
    bootstrap_finding_template()
    download_cwe()
    download_nvd()
    LOGGER.info("All KG data sources are ready.")


if __name__ == "__main__":
    main()
