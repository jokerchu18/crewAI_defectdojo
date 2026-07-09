from pydantic import BaseModel, Field


class FindingMinimal(BaseModel):
    test_id: int
    title: str
    severity: str
    active: bool
    verified: bool
    duplicate: bool
    duplicate_finding: int | None = None
    hash_code: str | None = None
    unique_id_from_tool: str | None = None
    vuln_id_from_tool: str | None = None
    file_path: str | None = None
    line: int | None = None
    component_name: str | None = None
    component_version: str | None = None
    static_finding: bool = False
    dynamic_finding: bool = False
    service: str | None = None
    endpoints: list[int] = Field(default_factory=list)


class DuplicateCluster(BaseModel):
    original_finding_id: int
    duplicate_ids: list[int] = Field(default_factory=list)
    duplicate_count: int = 0


class ImportScanResult(BaseModel):
    stage: str = "import_scan"
    success: bool = True
    test_id: int
    engagement_id: int
    product_id: int


# class DeduplicationResult(BaseModel):
#     stage: str = "deduplication"
#     success: bool = True
#     test_id: int
#     finding_ids: list[int] = Field(default_factory=list)
#     original_finding_ids: list[int] = Field(default_factory=list)
#     duplicate_finding_ids: list[int] = Field(default_factory=list)
#     duplicate_clusters: list[DuplicateCluster] = Field(default_factory=list)


# class WorkflowContext(BaseModel):
#     workflow_id: str | None = None
#     product_id: int | None = None
#     engagement_id: int | None = None
#     test_id: int | None = None
#     finding_ids: list[int] = Field(default_factory=list)
#     original_finding_ids: list[int] = Field(default_factory=list)
#     duplicate_finding_ids: list[int] = Field(default_factory=list)
#     duplicate_clusters: list[DuplicateCluster] = Field(default_factory=list)
#     findings: list[FindingMinimal] = Field(default_factory=list)
#     import_result: ImportScanResult | None = None
#     deduplication_result: DeduplicationResult | None = None
