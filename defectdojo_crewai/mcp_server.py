#!/usr/bin/env python3
"""MCP server exposing DefectDojo read-only finding queries.

Start with::

    python -m defectdojo_crewai.mcp_server

Or point a MCP client at::

    uv run defectdojo_crewai/mcp_server.py

Environment
-----------
``DEFECTDOJO_BASE_URL``   — DefectDojo host (default ``http://localhost:8080``).
``DEFECTDOJO_API_KEY``    — API token.
``MCP_TRANSPORT``         — ``stdio`` (default) or ``sse``.
``MCP_HOST`` / ``MCP_PORT`` — host/port when using SSE transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# ── configuration ──────────────────────────────────────────────────────

DEFECTDOJO_BASE_URL: str = os.getenv("DEFECTDOJO_BASE_URL", "http://localhost:8080").rstrip("/")
DEFECTDOJO_API_KEY: str = os.getenv("DEFECTDOJO_API_KEY", "")
DEFAULT_LIMIT: int = 20
MAX_LIMIT: int = 100
REQUEST_TIMEOUT: float = float(os.getenv("MCP_REQUEST_TIMEOUT", "30"))

LOGGER = logging.getLogger("defectdojo-mcp")

# ── MCP application ────────────────────────────────────────────────────

mcp = FastMCP(
    name="DefectDojo Findings",
    instructions=(
        "DefectDojo 漏洞查询服务。提供三类查询能力："
        "按 test_id 查、按 product_id 查、全量列表查询（支持分页和 severity 过滤）。"
        "所有操作均为只读，不修改 DefectDojo 数据。"
    ),
)


# ── helpers ────────────────────────────────────────────────────────────


def _auth_headers() -> dict[str, str]:
    if not DEFECTDOJO_API_KEY:
        raise RuntimeError(
            "DEFECTDOJO_API_KEY is not set — MCP server cannot authenticate."
        )
    return {"Authorization": f"Token {DEFECTDOJO_API_KEY}"}


def _api_url(path: str) -> str:
    return f"{DEFECTDOJO_BASE_URL}/api/v2/{path.lstrip('/')}"


async def _get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """GET *url* and return the decoded JSON body.

    Retries once on transient network errors (timeout, connection reset).
    """
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=_auth_headers(), params=params)
                resp.raise_for_status()
                return resp.json()
        except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectError) as exc:
            last_exc = exc
            LOGGER.warning("HTTP GET %s failed (attempt %d/2): %s", url, attempt + 1, exc)
            if attempt == 0:
                await asyncio.sleep(1.0)
    raise last_exc  # type: ignore[misc]


def _fmt_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Return a compact view of one finding suitable for LLM consumption."""
    return {
        "id": finding.get("id"),
        "title": finding.get("title"),
        "severity": finding.get("severity"),
        "cwe": finding.get("cwe"),
        "cvssv3_score": finding.get("cvssv3_score"),
        "epss_score": finding.get("epss_score"),
        "active": finding.get("active"),
        "verified": finding.get("verified"),
        "false_p": finding.get("false_p"),
        "duplicate": finding.get("duplicate"),
        "out_of_scope": finding.get("out_of_scope"),
        "risk_accepted": finding.get("risk_accepted"),
        "is_mitigated": finding.get("is_mitigated"),
        "found_by": finding.get("found_by"),
        "test": finding.get("test"),
        "product": finding.get("product"),
        "file_path": finding.get("file_path"),
        "component_name": finding.get("component_name"),
        "created": finding.get("created"),
        "mitigation": finding.get("mitigation"),
    }


def _fmt_response(
    payload: dict[str, Any],
    *,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Shape the raw DefectDojo response into a consistent MCP tool result."""
    results: list[dict[str, Any]] = payload.get("results") or []
    total = payload.get("count", len(results))
    return {
        "total": total,
        "returned": len(results),
        "limit": limit,
        "offset": offset,
        "findings": [_fmt_finding(f) for f in results],
    }


# ── tools ──────────────────────────────────────────────────────────────


@mcp.tool(name="defectdojo_list_findings")
async def list_findings(
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    severity: str | None = None,
    active: bool | None = None,
    verified: bool | None = None,
    false_p: bool | None = None,
    duplicate: bool | None = None,
) -> dict[str, Any]:
    """列出 DefectDojo 中的所有 findings。

    支持分页和可选过滤条件。适合做全局扫描和统计查询。

    Parameters
    ----------
    limit : int
        每页返回的最大记录数（默认 20，最大 100）。
    offset : int
        分页起始位置（从 0 开始）。
    severity : str | None
        按严重级别过滤，可选值: Critical, High, Medium, Low, Info。
    active : bool | None
        过滤活跃/非活跃状态。
    verified : bool | None
        过滤已验证/未验证。
    false_p : bool | None
        过滤误报。
    duplicate : bool | None
        过滤重复。
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if severity:
        params["severity"] = severity
    if active is not None:
        params["active"] = str(active).lower()
    if verified is not None:
        params["verified"] = str(verified).lower()
    if false_p is not None:
        params["false_p"] = str(false_p).lower()
    if duplicate is not None:
        params["duplicate"] = str(duplicate).lower()

    payload = await _get_json(_api_url("findings/"), params=params)
    return _fmt_response(payload, limit=limit, offset=offset)


@mcp.tool(name="defectdojo_get_findings_by_test")
async def get_findings_by_test(
    test_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    severity: str | None = None,
) -> dict[str, Any]:
    """按 test_id 查询该次扫描导入的全部 findings。

    适用于对某次导入的结果做分诊、去重或修复计划。

    Parameters
    ----------
    test_id : int
        DefectDojo 中的 test ID。
    limit : int
        每页最大条数（默认 20，最大 100）。
    offset : int
        分页偏移量。
    severity : str | None
        按严重级别过滤，可选值: Critical, High, Medium, Low, Info。
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params: dict[str, Any] = {"test": test_id, "limit": limit, "offset": offset}
    if severity:
        params["severity"] = severity

    payload = await _get_json(_api_url("findings/"), params=params)
    return _fmt_response(payload, limit=limit, offset=offset)


@mcp.tool(name="defectdojo_get_findings_by_product")
async def get_findings_by_product(
    product_id: int,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    severity: str | None = None,
) -> dict[str, Any]:
    """按 product_id 查询该产品下的全部 findings。

    适用于评估某个产品的整体安全状态或做风险接受预审。

    Parameters
    ----------
    product_id : int
        DefectDojo 中的 product ID。
    limit : int
        每页最大条数（默认 20，最大 100）。
    offset : int
        分页偏移量。
    severity : str | None
        按严重级别过滤，可选值: Critical, High, Medium, Low, Info。
    """
    limit = max(1, min(limit, MAX_LIMIT))
    params: dict[str, Any] = {"product": product_id, "limit": limit, "offset": offset}
    if severity:
        params["severity"] = severity

    payload = await _get_json(_api_url("findings/"), params=params)
    return _fmt_response(payload, limit=limit, offset=offset)


@mcp.tool(name="defectdojo_get_finding_detail")
async def get_finding_detail(finding_id: int) -> dict[str, Any]:
    """获取单个 finding 的完整详情。

    返回所有字段（包括 description, steps_to_reproduce, endpoint 等）。
    适用于深入分析某个具体漏洞。

    Parameters
    ----------
    finding_id : int
        DefectDojo 中的 finding ID。
    """
    payload = await _get_json(_api_url(f"findings/{finding_id}/"))
    return _fmt_finding(payload) if isinstance(payload, dict) else payload


# ── entrypoint ─────────────────────────────────────────────────────────


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if not DEFECTDOJO_API_KEY:
        LOGGER.warning(
            "DEFECTDOJO_API_KEY is not set — tools will fail at runtime. "
            "Export it before starting: export DEFECTDOJO_API_KEY=<your-token>"
        )

    if transport == "sse":
        host = os.getenv("MCP_HOST", "0.0.0.0")
        port = int(os.getenv("MCP_PORT", "8088"))
        LOGGER.info("Starting DefectDojo MCP server via SSE on %s:%d", host, port)
        mcp.run(transport="sse", host=host, port=port)
    else:
        LOGGER.info("Starting DefectDojo MCP server via stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
