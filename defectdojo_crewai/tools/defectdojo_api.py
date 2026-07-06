from pathlib import Path
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from defectdojo_crewai.config.settings import settings

class DefectDojoImportScanInput(BaseModel):
    base_url: str = Field(...)
    api_key: str = Field(...)
    scan_type: str = Field(...)
    engagement_id: int = Field(...)
    scan_file_path: str = Field(...)


class DefectDojoImportScanTool(BaseTool):
    name: str = "defectdojo_import_scan_tool"
    description: str = "导入扫描报告到 DefectDojo"
    args_schema: type[BaseModel] = DefectDojoImportScanInput

    def _run(
        self,
        base_url: str,
        api_key: str,
        scan_type: str,
        engagement_id: int,
        scan_file_path: str,
    ):
        return defectdojo_import_scan_tool(
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            scan_type=settings.default_scan_type,
            engagement_id=settings.defectdojo_engagement_id,
            scan_file_path=settings.default_scan_file_path,
        )


# def defectdojo_get_scan_types_tool(
#     base_url: str,
#     api_key: str,
# ) -> dict:
#     """Fetch supported scan types from DefectDojo via /api/v2/import-scan-configurations/."""
#     url = f"{base_url.rstrip('/')}/api/v2/import-scan-configurations/"
#     headers = {
#         "Authorization": f"Token {api_key}",
#     }

#     response = httpx.get(
#         url,
#         headers=headers,
#         timeout=30,
#     )
#     response.raise_for_status()

#     payload = response.json()
#     if isinstance(payload, dict) and "results" in payload:
#         results = payload.get("results") or []
#     elif isinstance(payload, list):
#         results = payload
#     else:
#         results = []

#     scan_types = []
#     for item in results:
#         if not isinstance(item, dict):
#             continue

#         scan_type = item.get("name") or item.get("scan_type")
#         if scan_type:
#             scan_types.append(scan_type)

#     return {
#         "count": len(scan_types),
#         "scan_types": sorted(set(scan_types)),
#         "raw": payload,
#     }


def defectdojo_import_scan_tool(
    base_url: str,
    api_key: str,
    scan_type: str,
    engagement_id: int,
    scan_file_path: str,
    active: bool = True,
    verified: bool = True,
) -> dict:
    """Upload a scan report to DefectDojo via /api/v2/import-scan/."""
    file_path = Path(scan_file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Scan file not found: {file_path}")

    url = f"{base_url.rstrip('/')}/api/v2/import-scan/"
    headers = {
        "Authorization": f"Token {api_key}",
    }
    data = {
        "scan_type": scan_type,
        "engagement": str(engagement_id),
        "active": str(active).lower(),
        "verified": str(verified).lower(),
    }

    with file_path.open("rb") as scan_file:
        files = {
            "file": (file_path.name, scan_file, "application/octet-stream"),
        }
        response = httpx.post(
            url,
            headers=headers,
            data=data,
            files=files,
            timeout=120,
        )

    response.raise_for_status()
    return response.json()
