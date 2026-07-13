from ctypes.util import test
from pathlib import Path
import time
from urllib import response
import httpx
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from defectdojo_crewai.config.settings import settings

# 导入智能体业务规范函数

class DefectDojoImportScanInput(BaseModel):
    base_url: str = Field(...)
    api_key: str = Field(...)
    scan_type: str = Field(...)
    engagement_id: int = Field(...)
    scan_file_path: str = Field(...)

class ImportScanResult(BaseModel):
    stage: str = "import_scan"
    success: bool = True
    test_id: int
    engagement_id: int
    product_id: int

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


# 去重智能体业务

# class DefectDojoDeduplicateInput(BaseModel):
#     test_id: int = Field(..., description="需要执行去重的 DefectDojo test_id")
#     base_url: str | None = Field(default=None, description="DefectDojo Base URL，默认读取 settings")
#     api_key: str | None = Field(default=None, description="DefectDojo API Token，默认读取 settings")


# class DefectDojoDeduplicateTool(BaseTool):
#     name: str = "defectdojo_deduplicate_tool"
#     description: str = (
#         "对指定的 DefectDojo finding 执行去重，并返回去重后的完整 finding 信息与结果摘要"
#     )
#     args_schema: type[BaseModel] = DefectDojoDeduplicateInput

#     def _run(
#         self,
#         test_id: int,
#         base_url: str | None = None,
#         api_key: str | None = None,
#     ) -> dict:
#         return defectdojo_deduplicate_tool(
#             test_id=test_id,
#             base_url=base_url or settings.defectdojo_base_url,
#             api_key=api_key or settings.defectdojo_api_key,
#         )


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
) -> ImportScanResult:
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

    payload=response.json()

    result = ImportScanResult(
    test_id=payload["test_id"],
    engagement_id=payload["engagement_id"],
    product_id=payload["product_id"],
    )
    
    return result

# 读取漏洞列表工具

class DefectDojoGetFindingsInput(BaseModel):
    base_url: str = Field(..., description="DefectDojo 服务地址")
    api_key: str = Field(..., description="DefectDojo API Key")


class DefectDojoGetFindingsTool(BaseTool):
    name: str = "defectdojo_get_findings_tool"
    description: str = "从 DefectDojo 获取 findings 列表"
    args_schema: type[BaseModel] = DefectDojoGetFindingsInput

    def _run(
        self,
        base_url: str,
        api_key: str,
    ):
        return defectdojo_get_findings_tool(
            base_url=base_url,
            api_key=api_key,
        )


def defectdojo_get_findings_tool(
    base_url: str,
    api_key: str,
) -> dict:
    """Fetch one finding from DefectDojo via /api/v2/findings/."""
    url = f"{base_url.rstrip('/')}/api/v2/findings/"
    headers = {
        "Authorization": f"Token {api_key}",
    }

    response = httpx.get(
        url,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()

    return response.json()


# 读取findings by test id工具

class DefectDojoGetFindingInput(BaseModel):
    base_url: str = Field(...)
    api_key: str = Field(...)
    test_id: int = Field(..., description="DefectDojo test ID")

class DefectDojoGetFindingTool(BaseTool):
    name: str = "defectdojo_get_finding_tool"
    description: str = "从 DefectDojo 获取 finding "
    args_schema: type[BaseModel] = DefectDojoGetFindingInput

    def _run(
        self,
        base_url: str,
        api_key: str,
        test_id: int,
    ):
        return defectdojo_get_finding_tool(
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            test_id=test_id,
        )

def defectdojo_get_finding_tool(
    base_url: str,
    api_key: str,
    test_id: int,
) -> dict:
    url = f"{base_url.rstrip('/')}/api/v2/findings/?test={test_id}"

    headers = {
        "Authorization": f"Token {api_key}",
    }

    response = httpx.get(
        url,
        headers=headers,
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


# 读取findings by product id工具

class DefectDojoGetFindingByProductIDInput(BaseModel):
    base_url: str = Field(...)
    api_key: str = Field(...)
    product_id: int = Field(..., description="DefectDojo product ID")

class DefectDojoGetFindingByProductIDTool(BaseTool):
    name: str = "defectdojo_get_finding_by_product_tool"
    description: str = "从 DefectDojo 获取 finding "
    args_schema: type[BaseModel] = DefectDojoGetFindingByProductIDInput

    def _run(
        self,
        base_url: str,
        api_key: str,
        product_id: int,
    ):
        return defectdojo_get_finding_by_product_tool(
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            product_id=product_id,
        )

def defectdojo_get_finding_by_product_tool(
    base_url: str,
    api_key: str,
    product_id: int,
) -> dict:
    url = f"{base_url.rstrip('/')}/api/v2/findings/?product={product_id}"

    headers = {
        "Authorization": f"Token {api_key}",
    }

    response = httpx.get(
        url,
        headers=headers,
        timeout=60,
    )

    response.raise_for_status()

    return response.json()


# 更新finding工具

class DefectDojoUpdateFindingInput(BaseModel):
    finding_id: int = Field(..., description="Need to update DefectDojo finding_id")
    base_url: str = Field(..., description="DefectDojo base URL")
    api_key: str = Field(..., description="DefectDojo API key")
    mitigated: str | None = Field(default=None, description="Mitigated timestamp")
    mitigated_by: int | None = Field(default=None, description="Mitigated by user ID")
    tags: list[str] | None = Field(default=None, description="Finding tags")
    push_to_jira: bool | None = Field(default=None, description="Whether to push to Jira")
    found_by: list[int] | None = Field(default=None, description="Found by scanner IDs")
    vulnerability_ids: list[dict] | None = Field(default=None, description="Vulnerability IDs")
    reporter: int | None = Field(default=None, description="Reporter user ID")
    endpoints: list[int] | None = Field(default=None, description="Endpoint IDs")
    title: str | None = Field(default=None, description="Finding title")
    date: str | None = Field(default=None, description="Finding date")
    sla_start_date: str | None = Field(default=None, description="SLA start date")
    sla_expiration_date: str | None = Field(default=None, description="SLA expiration date")
    cwe: int | None = Field(default=None, description="CWE ID")
    epss_percentile: float | None = Field(default=None, description="EPSS percentile")
    kev_date: str | None = Field(default=None, description="Known exploited date")
    cvssv3: str | None = Field(default=None, description="CVSS v3 vector")
    cvssv4: str | None = Field(default=None, description="CVSS v4 vector")
    active: bool | None = Field(default=None, description="Whether the finding remains active")
    verified: bool | None = Field(default=None, description="Whether the finding is verified")
    false_p: bool | None = Field(default=None, description="Whether the finding is a false positive")
    duplicate: bool | None = Field(default=None, description="Whether the finding is duplicate")
    out_of_scope: bool | None = Field(default=None, description="Whether the finding is out of scope")
    risk_accepted: bool | None = Field(default=None, description="Whether the finding risk is accepted")
    under_review: bool | None = Field(default=None, description="Whether the finding is under review")
    under_defect_review: bool | None = Field(default=None, description="Whether the finding is under defect review")
    is_mitigated: bool | None = Field(default=None, description="Whether the finding is mitigated")
    numerical_severity: str | None = Field(default=None, description="Numerical severity")
    line: int | None = Field(default=None, description="Source line")
    file_path: str | None = Field(default=None, description="File path")
    severity: str | None = Field(default=None, description="Finding severity")
    description: str | None = Field(default=None, description="Finding description")
    mitigation: str | None = Field(default=None, description="Mitigation text")
    fix_available: bool | None = Field(default=None, description="Whether fix is available")
    fix_version: str | None = Field(default=None, description="Fix version")
    impact: str | None = Field(default=None, description="Impact description")
    steps_to_reproduce: str | None = Field(default=None, description="Steps to reproduce")
    severity_justification: str | None = Field(default=None, description="Reason for severity adjustment")
    references: str | None = Field(default=None, description="References")
    cvssv3_score: float | None = Field(default=None, description="CVSS v3 score")
    cvssv4_score: float | None = Field(default=None, description="CVSS v4 score")
    epss_score: float | None = Field(default=None, description="EPSS score")
    known_exploited: bool | None = Field(default=None, description="Whether it is known exploited")
    ransomware_used: bool | None = Field(default=None, description="Whether it is used in ransomware")
    component_name: str | None = Field(default=None, description="Component name")
    component_version: str | None = Field(default=None, description="Component version")
    static_finding: bool | None = Field(default=None, description="Whether static finding")
    dynamic_finding: bool | None = Field(default=None, description="Whether dynamic finding")
    unique_id_from_tool: str | None = Field(default=None, description="Unique ID from tool")
    vuln_id_from_tool: str | None = Field(default=None, description="Vulnerability ID from tool")
    sast_source_object: str | None = Field(default=None, description="SAST source object")
    sast_sink_object: str | None = Field(default=None, description="SAST sink object")
    sast_source_line: int | None = Field(default=None, description="SAST source line")
    sast_source_file_path: str | None = Field(default=None, description="SAST source file path")
    nb_occurences: int | None = Field(default=None, description="Number of occurrences")
    publish_date: str | None = Field(default=None, description="Publish date")
    service: str | None = Field(default=None, description="Service name")
    planned_remediation_date: str | None = Field(default=None, description="Planned remediation date")
    planned_remediation_version: str | None = Field(default=None, description="Planned remediation version")
    effort_for_fixing: str | None = Field(default=None, description="Effort for fixing")
    review_requested_by: int | None = Field(default=None, description="Review requested by user ID")
    defect_review_requested_by: int | None = Field(default=None, description="Defect review requested by user ID")
    sonarqube_issue: int | None = Field(default=None, description="SonarQube issue ID")
    reviewers: list[int] | None = Field(default=None, description="Reviewer user IDs")


class DefectDojoUpdateFindingTool(BaseTool):
    name: str = "defectdojo_update_finding_tool"
    description: str = "Update DefectDojo finding triage fields such as verified, false_p, out_of_scope, CVSS and EPSS."
    args_schema: type[BaseModel] = DefectDojoUpdateFindingInput

    def _run(
        self,
        finding_id: int,
        base_url: str,
        api_key: str,
        mitigated: str | None = None,
        mitigated_by: int | None = None,
        tags: list[str] | None = None,
        push_to_jira: bool | None = None,
        found_by: list[int] | None = None,
        vulnerability_ids: list[dict] | None = None,
        reporter: int | None = None,
        endpoints: list[int] | None = None,
        title: str | None = None,
        date: str | None = None,
        sla_start_date: str | None = None,
        sla_expiration_date: str | None = None,
        cwe: int | None = None,
        epss_percentile: float | None = None,
        kev_date: str | None = None,
        cvssv3: str | None = None,
        cvssv4: str | None = None,
        active: bool | None = None,
        verified: bool | None = None,
        false_p: bool | None = None,
        duplicate: bool | None = None,
        out_of_scope: bool | None = None,
        risk_accepted: bool | None = None,
        under_review: bool | None = None,
        under_defect_review: bool | None = None,
        is_mitigated: bool | None = None,
        numerical_severity: str | None = None,
        line: int | None = None,
        file_path: str | None = None,
        severity: str | None = None,
        description: str | None = None,
        mitigation: str | None = None,
        fix_available: bool | None = None,
        fix_version: str | None = None,
        impact: str | None = None,
        steps_to_reproduce: str | None = None,
        severity_justification: str | None = None,
        references: str | None = None,
        cvssv3_score: float | None = None,
        cvssv4_score: float | None = None,
        epss_score: float | None = None,
        known_exploited: bool | None = None,
        ransomware_used: bool | None = None,
        component_name: str | None = None,
        component_version: str | None = None,
        static_finding: bool | None = None,
        dynamic_finding: bool | None = None,
        unique_id_from_tool: str | None = None,
        vuln_id_from_tool: str | None = None,
        sast_source_object: str | None = None,
        sast_sink_object: str | None = None,
        sast_source_line: int | None = None,
        sast_source_file_path: str | None = None,
        nb_occurences: int | None = None,
        publish_date: str | None = None,
        service: str | None = None,
        planned_remediation_date: str | None = None,
        planned_remediation_version: str | None = None,
        effort_for_fixing: str | None = None,
        review_requested_by: int | None = None,
        defect_review_requested_by: int | None = None,
        sonarqube_issue: int | None = None,
        reviewers: list[int] | None = None,
    ):
        return defectdojo_update_finding_tool(
            finding_id=finding_id,
            base_url=settings.defectdojo_base_url,
            api_key=settings.defectdojo_api_key,
            mitigated=mitigated,
            mitigated_by=mitigated_by,
            tags=tags,
            push_to_jira=push_to_jira,
            found_by=found_by,
            vulnerability_ids=vulnerability_ids,
            reporter=reporter,
            endpoints=endpoints,
            title=title,
            date=date,
            sla_start_date=sla_start_date,
            sla_expiration_date=sla_expiration_date,
            cwe=cwe,
            epss_percentile=epss_percentile,
            kev_date=kev_date,
            cvssv3=cvssv3,
            cvssv4=cvssv4,
            active=active,
            verified=verified,
            false_p=false_p,
            duplicate=duplicate,
            out_of_scope=out_of_scope,
            risk_accepted=risk_accepted,
            under_review=under_review,
            under_defect_review=under_defect_review,
            is_mitigated=is_mitigated,
            numerical_severity=numerical_severity,
            line=line,
            file_path=file_path,
            severity=severity,
            description=description,
            mitigation=mitigation,
            fix_available=fix_available,
            fix_version=fix_version,
            impact=impact,
            steps_to_reproduce=steps_to_reproduce,
            severity_justification=severity_justification,
            references=references,
            cvssv3_score=cvssv3_score,
            cvssv4_score=cvssv4_score,
            epss_score=epss_score,
            known_exploited=known_exploited,
            ransomware_used=ransomware_used,
            component_name=component_name,
            component_version=component_version,
            static_finding=static_finding,
            dynamic_finding=dynamic_finding,
            unique_id_from_tool=unique_id_from_tool,
            vuln_id_from_tool=vuln_id_from_tool,
            sast_source_object=sast_source_object,
            sast_sink_object=sast_sink_object,
            sast_source_line=sast_source_line,
            sast_source_file_path=sast_source_file_path,
            nb_occurences=nb_occurences,
            publish_date=publish_date,
            service=service,
            planned_remediation_date=planned_remediation_date,
            planned_remediation_version=planned_remediation_version,
            effort_for_fixing=effort_for_fixing,
            review_requested_by=review_requested_by,
            defect_review_requested_by=defect_review_requested_by,
            sonarqube_issue=sonarqube_issue,
            reviewers=reviewers,
        )


def defectdojo_update_finding_tool(
    finding_id: int,
    base_url: str,
    api_key: str,
    mitigated: str | None = None,
    mitigated_by: int | None = None,
    tags: list[str] | None = None,
    push_to_jira: bool | None = None,
    found_by: list[int] | None = None,
    vulnerability_ids: list[dict] | None = None,
    reporter: int | None = None,
    endpoints: list[int] | None = None,
    title: str | None = None,
    date: str | None = None,
    sla_start_date: str | None = None,
    sla_expiration_date: str | None = None,
    cwe: int | None = None,
    epss_percentile: float | None = None,
    kev_date: str | None = None,
    cvssv3: str | None = None,
    cvssv4: str | None = None,
    active: bool | None = None,
    verified: bool | None = None,
    false_p: bool | None = None,
    duplicate: bool | None = None,
    out_of_scope: bool | None = None,
    risk_accepted: bool | None = None,
    under_review: bool | None = None,
    under_defect_review: bool | None = None,
    is_mitigated: bool | None = None,
    numerical_severity: str | None = None,
    line: int | None = None,
    file_path: str | None = None,
    severity: str | None = None,
    description: str | None = None,
    mitigation: str | None = None,
    fix_available: bool | None = None,
    fix_version: str | None = None,
    impact: str | None = None,
    steps_to_reproduce: str | None = None,
    severity_justification: str | None = None,
    references: str | None = None,
    cvssv3_score: float | None = None,
    cvssv4_score: float | None = None,
    epss_score: float | None = None,
    known_exploited: bool | None = None,
    ransomware_used: bool | None = None,
    component_name: str | None = None,
    component_version: str | None = None,
    static_finding: bool | None = None,
    dynamic_finding: bool | None = None,
    unique_id_from_tool: str | None = None,
    vuln_id_from_tool: str | None = None,
    sast_source_object: str | None = None,
    sast_sink_object: str | None = None,
    sast_source_line: int | None = None,
    sast_source_file_path: str | None = None,
    nb_occurences: int | None = None,
    publish_date: str | None = None,
    service: str | None = None,
    planned_remediation_date: str | None = None,
    planned_remediation_version: str | None = None,
    effort_for_fixing: str | None = None,
    review_requested_by: int | None = None,
    defect_review_requested_by: int | None = None,
    sonarqube_issue: int | None = None,
    reviewers: list[int] | None = None,
) -> dict:
    """Selectively update provided finding fields via PUT /api/v2/findings/{id}/."""
    url = f"{base_url.rstrip('/')}/api/v2/findings/{finding_id}/"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }

    updates = {
        "mitigated": mitigated,
        "mitigated_by": mitigated_by,
        "tags": tags,
        "push_to_jira": push_to_jira,
        "found_by": found_by,
        "vulnerability_ids": vulnerability_ids,
        "reporter": reporter,
        "endpoints": endpoints,
        "title": title,
        "date": date,
        "sla_start_date": sla_start_date,
        "sla_expiration_date": sla_expiration_date,
        "cwe": cwe,
        "epss_percentile": epss_percentile,
        "kev_date": kev_date,
        "cvssv3": cvssv3,
        "cvssv4": cvssv4,
        "active": active,
        "verified": verified,
        "false_p": false_p,
        "duplicate": duplicate,
        "out_of_scope": out_of_scope,
        "risk_accepted": risk_accepted,
        "under_review": under_review,
        "under_defect_review": under_defect_review,
        "is_mitigated": is_mitigated,
        "numerical_severity": numerical_severity,
        "line": line,
        "file_path": file_path,
        "severity": severity,
        "description": description,
        "mitigation": mitigation,
        "fix_available": fix_available,
        "fix_version": fix_version,
        "impact": impact,
        "steps_to_reproduce": steps_to_reproduce,
        "severity_justification": severity_justification,
        "references": references,
        "cvssv3_score": cvssv3_score,
        "cvssv4_score": cvssv4_score,
        "epss_score": epss_score,
        "known_exploited": known_exploited,
        "ransomware_used": ransomware_used,
        "component_name": component_name,
        "component_version": component_version,
        "static_finding": static_finding,
        "dynamic_finding": dynamic_finding,
        "unique_id_from_tool": unique_id_from_tool,
        "vuln_id_from_tool": vuln_id_from_tool,
        "sast_source_object": sast_source_object,
        "sast_sink_object": sast_sink_object,
        "sast_source_line": sast_source_line,
        "sast_source_file_path": sast_source_file_path,
        "nb_occurences": nb_occurences,
        "publish_date": publish_date,
        "service": service,
        "planned_remediation_date": planned_remediation_date,
        "planned_remediation_version": planned_remediation_version,
        "effort_for_fixing": effort_for_fixing,
        "review_requested_by": review_requested_by,
        "defect_review_requested_by": defect_review_requested_by,
        "sonarqube_issue": sonarqube_issue,
        "reviewers": reviewers,
    }
    updates = {key: value for key, value in updates.items() if value is not None}

    if not updates:
        raise ValueError("No finding fields were provided for update.")

    response = httpx.patch(
        url,
        headers=headers,
        json=updates,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()

# 确认漏洞有效工具

class DefectDojoVerifyFindingInput(BaseModel):
    finding_id: int = Field(..., description="Need to verify DefectDojo finding's ID")
    base_url: str = Field(..., description="DefectDojo base URL")
    api_key: str = Field(..., description="DefectDojo API key")
    note: str | None = Field(default=None, description="Optional verification note")
    note_type: int | None = Field(default=None, description="Optional note type ID")

class DefectDojoVerifyFindingTool(BaseTool):
    name: str = "defectdojo_verify_finding_tool"
    description: str = "Call the DefectDojo verify endpoint and mark a finding as verified."
    args_schema: type[BaseModel] = DefectDojoVerifyFindingInput

    def _run(
        self,
        finding_id: int,
        base_url: str,
        api_key: str,
        note: str | None = None,
        note_type: int | None = None,
    ):
        return defectdojo_verify_finding_tool(
            finding_id=finding_id,
            base_url=base_url,
            api_key=api_key,
            note=note,
            note_type=note_type,
        )

def defectdojo_verify_finding_tool(
    finding_id: int,
    base_url: str,
    api_key: str,
    note: str | None = None,
    note_type: int | None = None,
) -> dict:
    """Verify a finding via /api/v2/findings/{id}/verify/."""
    url = f"{base_url.rstrip('/')}/api/v2/findings/{finding_id}/verify/"
    headers = {
        "Authorization": f"Token {api_key}",
    }

    data = {}
    if note is not None:
        data["note"] = note
    if note_type is not None:
        data["note_type"] = note_type

    response = httpx.post(
        url,
        headers=headers,
        data=data,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


# def defectdojo_deduplicate_tool(
#     test_id: int,
#     base_url: str,
#     api_key: str,
# ) -> dict:
#     """Trigger DefectDojo deduplication for one finding, then fetch the updated finding."""
#     deduplicate_url = f"{base_url.rstrip('/')}/api/v2/findings/{test_id}/duplicate/"
#     headers = {
#         "Authorization": f"Token {api_key}",
#     }

#     deduplicate_response = httpx.get(
#         deduplicate_url,
#         headers=headers,
#         timeout=60,
#     )
#     deduplicate_response.raise_for_status()

#     try:
#         deduplicate_raw = deduplicate_response.json()
#     except ValueError:
#         deduplicate_raw = deduplicate_response.text

#     finding = defectdojo_get_finding_tool(
#         test_id=test_id,
#         base_url=base_url,
#         api_key=api_key,
#     )

#     finding_minimal = FindingMinimal(
#         id=finding["id"],
#         test_id=finding["test"],
#         title=finding["title"],
#         severity=finding["severity"],
#         active=finding["active"],
#         verified=finding["verified"],
#         duplicate=finding["duplicate"],
#         duplicate_finding=finding.get("duplicate_finding"),
#         hash_code=finding.get("hash_code"),
#         unique_id_from_tool=finding.get("unique_id_from_tool"),
#         vuln_id_from_tool=finding.get("vuln_id_from_tool"),
#         file_path=finding.get("file_path"),
#         line=finding.get("line"),
#         component_name=finding.get("component_name"),
#         component_version=finding.get("component_version"),
#         static_finding=finding.get("static_finding", False),
#         dynamic_finding=finding.get("dynamic_finding", False),
#         service=finding.get("service"),
#         endpoints=finding.get("endpoints", []),
#     )

#     duplicate_clusters = []
#     original_finding_ids = []
#     duplicate_finding_ids = []

#     if finding.get("duplicate") and finding.get("duplicate_finding") is not None:
#         original_id = finding["duplicate_finding"]
#         duplicate_id = finding["id"]
#         duplicate_finding_ids.append(duplicate_id)
#         original_finding_ids.append(original_id)
#         duplicate_clusters.append(
#             DuplicateCluster(
#                 original_finding_id=original_id,
#                 duplicate_ids=[duplicate_id],
#                 duplicate_count=1,
#             )
#         )
#     else:
#         original_finding_ids.append(finding["id"])

#     result = DeduplicationResult(
#         test_id=finding["test"],
#         finding_ids=[finding["id"]],
#         original_finding_ids=original_finding_ids,
#         duplicate_finding_ids=duplicate_finding_ids,
#         duplicate_clusters=duplicate_clusters,
#     )

#     payload = result.model_dump()
#     payload["deduplicate_response"] = deduplicate_raw
#     payload["findings"] = [finding_minimal.model_dump()]
#     payload["raw_finding"] = finding
#     return payload

# 创建risk acceptance工具

class DefectDojoCreateRiskAcceptanceInput(BaseModel):
    base_url: str = Field(..., description="DefectDojo base URL")
    api_key: str = Field(..., description="DefectDojo API key")
    name: str = Field(..., description="Risk acceptance name")
    recommendation: str = Field(..., description="Recommendation code, such as A")
    recommendation_details: str | None = Field(default=None, description="Recommendation details")
    decision: str = Field(..., description="Decision code, such as A")
    decision_details: str | None = Field(default=None, description="Decision details")
    accepted_by: str | None = Field(default=None, description="Name of the approver")
    expiration_date: str | None = Field(default=None, description="Risk acceptance expiration datetime in ISO 8601 format")
    expiration_date_warned: str | None = Field(default=None, description="Expiration warned datetime in ISO 8601 format")
    expiration_date_handled: str | None = Field(default=None, description="Expiration handled datetime in ISO 8601 format")
    reactivate_expired: bool | None = Field(default=None, description="Whether to reactivate findings when expired")
    restart_sla_expired: bool | None = Field(default=None, description="Whether to restart SLA when expired")
    owner: int | None = Field(default=None, description="Owner user ID")
    accepted_findings: list[int] = Field(..., description="Finding IDs to include in this risk acceptance")


class DefectDojoCreateRiskAcceptanceTool(BaseTool):
    name: str = "defectdojo_create_risk_acceptance_tool"
    description: str = "Create a DefectDojo risk acceptance record for one or more findings."
    args_schema: type[BaseModel] = DefectDojoCreateRiskAcceptanceInput

    def _run(
        self,
        base_url: str,
        api_key: str,
        name: str,
        recommendation: str,
        recommendation_details: str | None = None,
        decision: str = "A",
        decision_details: str | None = None,
        accepted_by: str | None = None,
        expiration_date: str | None = None,
        expiration_date_warned: str | None = None,
        expiration_date_handled: str | None = None,
        reactivate_expired: bool | None = None,
        restart_sla_expired: bool | None = None,
        owner: int | None = None,
        accepted_findings: list[int] | None = None,
    ) -> dict:
        return defectdojo_create_risk_acceptance_tool(
            base_url=base_url,
            api_key=api_key,
            name=name,
            recommendation=recommendation,
            recommendation_details=recommendation_details,
            decision=decision,
            decision_details=decision_details,
            accepted_by=accepted_by,
            expiration_date=expiration_date,
            expiration_date_warned=expiration_date_warned,
            expiration_date_handled=expiration_date_handled,
            reactivate_expired=reactivate_expired,
            restart_sla_expired=restart_sla_expired,
            owner=owner,
            accepted_findings=accepted_findings or [],
        )


def defectdojo_create_risk_acceptance_tool(
    base_url: str,
    api_key: str,
    name: str,
    recommendation: str,
    recommendation_details: str | None = None,
    decision: str = "A",
    decision_details: str | None = None,
    accepted_by: str | None = None,
    expiration_date: str | None = None,
    expiration_date_warned: str | None = None,
    expiration_date_handled: str | None = None,
    reactivate_expired: bool | None = None,
    restart_sla_expired: bool | None = None,
    owner: int | None = None,
    accepted_findings: list[int] | None = None,
) -> dict:
    """Create a risk acceptance via POST /api/v2/risk_acceptances/."""
    if not accepted_findings:
        raise ValueError("accepted_findings must contain at least one finding_id.")

    url = f"{base_url.rstrip('/')}/api/v2/risk_acceptances/"
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "name": name,
        "recommendation": recommendation,
        "recommendation_details": recommendation_details,
        "decision": decision,
        "decision_details": decision_details,
        "accepted_by": accepted_by,
        "expiration_date": expiration_date,
        "expiration_date_warned": expiration_date_warned,
        "expiration_date_handled": expiration_date_handled,
        "reactivate_expired": reactivate_expired,
        "restart_sla_expired": restart_sla_expired,
        "owner": owner,
        "accepted_findings": accepted_findings,
    }

    response = httpx.post(
        url,
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()
