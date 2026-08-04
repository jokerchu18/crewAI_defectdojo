# 设计文档 vs 实际实现 — 差异分析

> **设计文档**: `D:\obsidianfiles\work\crewai-Dojo-design3.md` (v6.18)
> **实际代码**: `d:\github\crewAI_defectdojo\defectdojo_crewai\`
> **分析日期**: 2026-07-30

---

## 一、总览

| 维度 | 设计文档 | 实际实现 | 匹配度 |
|------|---------|---------|:---:|
| Agent 数量 | 1 Router + 7 Core + 2 Service = 10 | 1 Router + 7 Core (jira_sync 未实现) = 8 | 80% |
| 调度层 | 4 独立组件 | 内嵌在 routing_service.py | 30% |
| 知识层 | Qdrant 4 分区 | Qdrant 4 分区 ✅ + **KG (新增)** | 110% |
| HITL | PendingAction Django Model + 6 状态机 | PendingApproval Pydantic + WorkflowRun | 60% |
| 执行模式 | async/await + Celery | 全同步 (除 web.py) | 10% |
| Crew 编排 | 单个 monolithic Crew | 按意图动态创建 Crew | 40% |
| Agent 写权限 | "仅分析建议，无写工具" | 有写工具 + ApprovalGatedTool | 0% |

---

## 二、架构层差异（§3.7）

### 2.1 设计文档：三层分离

```
LLM层 (Router) → Python调度层 (Dispatcher/Policy/Executor/ToolHook)
→ 知识层 (Qdrant) → 业务Agent层 (仅分析建议)
```

设计文档定义了 4 个调度组件：

| 组件 | 设计文档定义 | 实际实现 |
|------|------------|---------|
| **WorkflowDispatcher** | `dispatcher/workflow_dispatcher.py`，解析 IntentResult，选择工作流模板 | **不存在** — 逻辑在 `routing_service.py:_execute_intent()` 中 if/elif 分支 |
| **PolicyEngine** | `policy/policy_engine.py`，评估是否需审批，输出 PolicyDecision | **不存在** — 审批触发由 `risk_acceptance_actions.py` + `tool_policy.py` 的写工具包装实现 |
| **IdempotentExecutor** | `executor/idempotent_executor.py`，通过 idempotency_key 去重，失败重试 | **不存在** — 幂等性依赖 `approval_store.claim_pending_approval()` 原子认领 |
| **ToolHook** | 自动审计所有工具调用，异步索引知识层 | **部分实现** — `events.py` 中的 `enqueue_router_outcome()` / `enqueue_approved_execution()` 用 ThreadPoolExecutor（非 Celery） |

### 2.2 调度逻辑：设计 vs 实现

**设计文档**（§3.7.3.2）:
```python
# 独立的 Dispatcher 模块
context = await WorkflowContext.load_or_create(user_id=current_user.id)
dispatch_plan = WorkflowDispatcher.dispatch(intent_result, context)
policy_decision = PolicyEngine.evaluate(context, intent_result)
```

**实际实现**（`routing_service.py:786-817`）:
```python
def _execute_intent(intent, workflow_id, step_id, agent_context=None):
    if intent.intent == "risk_acceptance":
        return _request_risk_acceptance(...)
    if intent.intent == "deduplication":
        return _run_deduplication(...)
    # ...更多 if/elif 分支
```

> **差异**: 设计文档是策略模式 + 独立模块，实际是一个大 if/elif 分支。可维护性随 Agent 增加而下降。

---

## 三、Router Agent 差异（§3.7.3.1）

| 属性 | 设计文档 | 实际实现 |
|------|---------|---------|
| 工具列表 | `tools=[]` **空列表，无工具** | `tools=[KnowledgeSearchDecisionHistoryTool()]` |
| 输出模型 | `IntentResult` (9 种 intent) | `WorkflowPlan` (多步骤 + depends_on) |
| output_pydantic | Task 级别配置 | 手动 `parse_model_output()` |
| 置信度 | `IntentResult.confidence: float` | `WorkflowPlan.confidence: float` ✅ |
| delegation | `allow_delegation=False` | 未设置 (默认 True) |

**关键差异**: 设计文档明确 Router 不应有工具（`tools=[]`），知识检索由 Python 包装层的 `recognize_intent_with_fallback()` 调用。实际 Router 直接持有 KnowledgeSearchDecisionHistoryTool，增加了每次 LLM 调用的延迟。

**设计文档的 Router 输出**（单意图）:
```python
class IntentResult(BaseModel):
    intent: Literal["import_scan", "deduplicate", "triage",
                    "remediate", "accept_risk", "verify_closure",
                    "sync_jira", "generate_report", "query_status"]
    product_id: int | None
    engagement_id: int | None
    test_id: int | None
    finding_id: int | None
    confidence: float
```

**实际 Router 输出**（多步骤工作流）:
```python
class WorkflowPlan(BaseModel):
    steps: list[WorkflowStep]  # 每步含 step_id, intent, depends_on
    message: str
    confidence: float
    fallback_used: FallbackUsed
    context_injections: list[dict]
```

> **差异**: 设计是单意图输出，实际是**多步骤工作流计划**。实际实现更强大（支持多步骤编排 + 依赖关系），但也更复杂（Router 需要理解工作流编排逻辑）。

---

## 四、知识层差异（§3.8）

### 4.1 Qdrant 分区

| 设计文档 | 实际实现 | 匹配 |
|---------|---------|:---:|
| `source_type="library"` — CWE/CVE/OWASP 标准库 | `SOURCE_LIBRARY = "library"` — Markdown 知识库 | ✅ 概念匹配，数据源不同 |
| `source_type="audit"` — Router 决策历史 | `SOURCE_AUDIT = "audit"` — Workflow outcome | ✅ 完全匹配 |
| `source_type="triage"` — 历史分诊结论 | `SOURCE_TRIAGE = "triage"` — 审批通过的 triage 工具 | ✅ 完全匹配 |
| `source_type="remediation"` — 已成功修复方案 | `SOURCE_REMEDIATION = "remediation"` — mitigated finding | ✅ 完全匹配 |

### 4.2 知识写入机制

| 设计文档 | 实际实现 |
|---------|---------|
| Django `post_save` Signal → Celery 异步任务 | `ThreadPoolExecutor(max_workers=2)` — `events.py` |
| `index_finding()` Celery task，max_retries=3 | `enqueue_approved_execution()` fire-and-forget |

**差异**: 设计文档用 Django Signal + Celery（适合 Django 单体），实际用 ThreadPoolExecutor（适合独立 Python 服务，无 Django/Celery 依赖）。

### 4.3 知识图谱（KG）

| 设计文档 | 实际实现 |
|---------|---------|
| **无知识图谱** — 设计文档未提及 | ✅ **已实现** — `knowledge/kg/` 完整子包 |
| 无 CWE/CVE/OWASP 结构化关系查询 | networkx DiGraph + pickle 缓存 |
| 无 `kg_auto_inject` fallback 路径 | `router_fallback.py` 中的 `_try_kg_injection()` |

> **这是实际实现超过设计文档的部分。** 图谱提供了设计文档中"library 分区"无法实现的结构化查询（CVE→CWE→OWASP 链、父子弱点关系、KEV 已知利用）。

---

## 五、HITL 审批机制差异（§3.9）

### 5.1 审批模型

| 设计文档 | 实际实现 |
|---------|---------|
| **PendingAction** Django Model | **PendingApproval** Pydantic Model |
| 6 状态: PENDING → APPROVED/REJECTED/EXPIRED → EXECUTING → EXECUTED/FAILED | 4 状态: pending → completed/failed/rejected |
| `idempotency_key` UNIQUE 防重 | `approval_store.claim_pending_approval()` 原子操作 |
| 24h TTL 自动过期 | 无自动过期机制 |

### 5.2 审批触发

| 设计文档 (6 类触发) | 实际实现 |
|-------------------|---------|
| T1: 高/严重风险接受 | ✅ `risk_acceptance` 工具通过 ApprovalGatedTool |
| T2: 漏洞 close（4 复合语义） | ❌ 未实现 |
| T3: Jira 推送/同步 | ❌ Jira Agent 未实现 |
| T4: 批量操作（>10000） | ❌ 未实现 |
| T5: Critical 分诊降级 | ❌ 未实现 |
| T6: 跨决策冲突 | ❌ 未实现 |

> **实际只实现了 T1（风险接受审批）**，且是通过 `ApprovalGatedTool` 包装写工具实现的，而非设计文档的 PolicyEngine 触发。

### 5.3 审批流架构

**设计文档**（5 阶段）:
```
Router → Dispatcher → PolicyEngine → PendingAction.save() →
Notification(WebSocket+邮件) → Human → IdempotentExecutor.execute()
```

**实际实现**:
```
Router → routing_service._execute_intent() →
Agent.crew.kickoff() → ApprovalGatedTool._run() →
request_write_tool_approval() → approval_service.request_approval() →
[Human via chat.py approve/reject] → execute_write_tool_calls()
```

> **差异**: 设计是异步推送通知 + WebSocket 实时推送，实际是同步命令行交互（`chat.py` 中 `approve <id>` 命令）。

---

## 六、Agent 实现差异（§4）

### 6.1 Agent 清单

| 设计文档 Agent | 简称 | 实际文件 | 状态 |
|---------------|------|---------|:---:|
| Scan Import Agent | SIA | `agents/scan_import.py` | ✅ 已实现 |
| Deduplication Agent | DA | `agents/deduplication.py` | ✅ 已实现 |
| Triage Agent | TA | `agents/triage.py` | ✅ 已实现 |
| Remediation Agent | RA | `agents/remediation.py` | ✅ 已实现 |
| Verification Agent | VA | `agents/verification.py` | ⚠️ 返回 not_implemented |
| Risk Acceptance Agent | RAA | `agents/risk_acceptance.py` | ✅ 已实现 |
| Jira Sync Agent | JSA | `agents/jira_sync.py` | ❌ 空文件/未实现 |
| Notification Service | NSV | `tools/notification_service.py` | ❌ 空文件 |
| Report Service | RPS | 无对应文件 | ❌ 未实现 |

### 6.2 Agent 工具配置差异

以 Scan Import Agent 为例：

**设计文档**（4 个工具）:
```python
tools=[
    defectdojo_import_scan_tool,
    defectdojo_reimport_scan_tool,      # ← 未实现
    defectdojo_get_scan_types_tool,     # ← 注释掉了
    knowledge_search_cve_description_tool, # ← 未分配给 SIA
]
```

**实际实现**（1 个工具）:
```python
# scan_import.py 不直接配置 tools，由 task + _run_crew 间接调用
# 实际通过 defectdojo_import_scan_tool 执行导入
```

### 6.3 设计文档的核心约束

设计文档 §3.7.3 明确规定：

> **业务智能体层（仅分析建议）**: 工具：只读 + 建议生成（**无写权限**）

**实际实现**: 所有 Agent 都有写工具（通过 CrewAI BaseTool），写操作通过 `ApprovalGatedTool` 包装实现审批控制，而非完全禁止写权限。

---

## 七、CrewAI 编排差异（§7）

### 7.1 项目结构

| 设计文档 | 实际实现 |
|---------|---------|
| `crews/orchestrator.py` — MCA 主控 Crew | 不存在 |
| `crews/vulnerability_lifecycle.py` — 单一大 Crew | `crews/vulnerability_lifecycle.py` ✅ 但未使用 |
| `dispatcher/` — 调度层 | 不存在独立模块 |
| `policy/` — 策略引擎 | 不存在 |
| `executor/` — 幂等执行器 | 不存在 |
| `knowledge/` — 知识层 | ✅ 存在且超出设计 |
| `utils/` — 超时重试熔断 | **新增，设计文档未提及** |

### 7.2 Crew 编排方式

**设计文档**（§7.2）:
```python
# 单一 Sequential Crew，包含所有 Agent
vulnerability_lifecycle_crew = Crew(
    agents=[router_agent, scan_import_agent, deduplication_agent, ...],
    tasks=[analyze_scan_task, analyze_deduplication_task, ...],
    process=Process.sequential,
)

# 外部 Python 调度层
async def execute_vulnerability_lifecycle(user_input: str) -> dict:
    intent_result = router_agent.kickoff(...).pydantic
    context = await WorkflowContext.load_or_create(...)
    dispatch_plan = WorkflowDispatcher.dispatch(intent_result, context)
    ...
```

**实际实现**（`routing_service.py:_run_crew()`）:
```python
# 每个 intent 动态创建独立 Crew
def _run_crew(agent, task, inputs, ...):
    crew = Crew(agents=[agent], tasks=[prepared_task], process=Process.sequential)
    output = crew.kickoff(inputs=inputs)
```

> **差异**: 设计是一大 Crew + Python Dispatcher 调度，实际是每个步骤**按需创建微型 Crew**（1 Agent + 1 Task）。实际方式更灵活但缺少统一的调度层抽象。

---

## 八、设计文档有、实际没有的

| 设计文档特性 | 说明 | 影响 |
|------------|------|------|
| Python WorkflowDispatcher | 独立的调度模块 | 调度逻辑分散在 routing_service.py |
| PolicyEngine | 规则引擎，评估审批需求 | 审批触发逻辑硬编码在工具包装中 |
| IdempotentExecutor | 带 idempotency_key 的幂等执行 | 仅靠 approval claim 原子操作 |
| PendingAction Django Model | 持久化审批状态机 | 用 PendingApproval (Pydantic) + WorkflowRun |
| Celery 异步索引 | Signal → Celery task → Qdrant | 用 ThreadPoolExecutor 替代 |
| Jira Sync Agent (JSA) | 双向 Jira 同步 | 未实现 |
| Notification Service (NSV) | 多通道通知 | 未实现 |
| Report Service (RPS) | HTML/PDF/Excel 报告 | 未实现 |
| `defectdojo_reimport_scan_tool` | 重新导入扫描 | 未实现 |
| `async/await` 执行模式 | 异步工作流执行 | 全部同步 |

## 九、实际有、设计文档没有的

| 实际特性 | 说明 | 价值 |
|---------|------|------|
| **知识图谱 (kg/)** | CWE + CVE + OWASP + FindingTemplate 图 | 设计文档无此概念，结构化查询能力 |
| **多步骤 WorkflowPlan** | Router 输出多步骤计划 + depends_on | 设计是单意图输出 |
| **超时/重试/熔断 (utils/)** | TimeoutConfig + CircuitBreaker + Retry | 设计文档仅在部署章节提及 |
| **Agent agent_timeout** | 每个 Agent 级别的 wall-clock timeout | 设计文档无此概念 |
| **WorkflowRun 持久化** | SQLite 持久化工作流状态，支持断点续跑 | 设计用 PendingAction 不同方案 |
| **进度服务 (progress_service)** | 实时步骤级别的进度推送 | 设计文档未提及 |
| **Mixed Context 合并** | 显式 + 存储 + 会话三级 Context 合并 | 设计用 WorkflowContext 单体 |
| **Conversation Summarizer** | Token 预算驱动的增量摘要 | 设计文档未提及 |
| **Router Fallback 多层降级** | decision_history → KG → human_review | 设计只用 decision_history |
| **CVE 按需加载 (kg/enricher.py)** | 扫描导入后从 findings 提取 CVE → NVD API 单条查询 → 图增量更新 | 设计文档只有全量 NVD 下载，无按需加载 |

---

## 十、总结与建议

### 匹配良好的部分
1. Qdrant 4 分区设计与实现**完全一致**
2. 7 核心 Agent 角色定义与实现**高度匹配**
3. Router Agent 的 confidence + fallback 机制**已实现且增强**
4. 知识层写入（Tool Hook 异步索引）**已实现**

### 主要差距
1. **调度层抽象缺失** — 设计文档的 4 个独立调度组件在实际代码中不存在，建议将 `routing_service.py` 中的 if/elif 分支重构为策略模式
2. **Agent 写权限策略** — 设计文档要求"仅分析，不写"，实际是"工具包装 + 审批"，建议统一
3. **HITL 审批持久化** — 设计文档的 PendingAction Django Model 比实际的 Pydantic + SQLite 更规范
4. **异步执行** — 设计文档的 async/await 模式未实现
5. **Jira Agent / Notification / Report** — 3 个组件未实现

### 实际超出设计的创新点
1. **知识图谱** — 是设计文档完全没有涉及的能力
2. **CVE 按需加载** — 导入扫描时自动从 findings 提取 CVE → NVD API 实时查询 → 图增量更新。设计只有全量 NVD 下载，无按需加载概念
3. **多步骤工作流编排** — Router 输出多步骤计划比设计的单意图更强大
4. **弹力工程 (Resilience)** — 超时/重试/熔断三层防护是运维必需的工程实践

---

## 附录 A：设计文档 Agent output_pydantic 对照

设计文档 §4 为每个 Agent 定义了专用的 `output_pydantic` 模型。**实际代码中这些模型全部不存在**——Agent 输出用 `parse_model_output()` 手动解析而非 CrewAI Task 级 `output_pydantic`。

| Agent | 设计文档 output_pydantic | 实际 |
|-------|------------------------|------|
| Router | `WorkflowDecision` (next_stage, human_intervention) | `WorkflowPlan` (steps, confidence, fallback_used) — 增强版 |
| SIA | `ImportScanSummary` (test_id, engagement_id, total_findings, next_action) | `ImportScanResult` — 简化版 |
| DA | `DedupSummary` (original_count, duplicate_count, clusters) | 无 — 返回原始 dict |
| TA | `TriageSummary` (verified_count, false_positive_count, critical_count) | 无 — 返回原始 dict |
| RA | `RemediationSummary` (sla_breaches, priority_list, escalation_required) | 无 — 返回原始 dict |
| RAA | `RiskAcceptanceSummary` (accepted_count, expiring_soon, rejected_count) | `RiskAcceptanceReviewResult` (candidates 列表) — 有 |
| JSA | `JiraSyncSummary` (issues_created, updated, sync_failures) | Agent 未实现 |
| VA | `VerificationSummary` (closed_count, regression_detected, human_confirm) | 无 — 返回 not_implemented |

## 附录 B：设计文档 vs 实际 工具数量对比

| Agent | 设计文档工具数 | 实际工具数 | 缺口 |
|-------|:-----------:|:--------:|:----:|
| SIA | 4 | 1 | `reimport_scan`, `get_scan_types`, `knowledge_search_cve` 未分配 |
| DA | 5 | 1 (注释掉了) | `get_duplicate_cluster`, `reset_duplicate`, `set_original`, `group_findings` 未实现 |
| TA | 14 | 3 | `close_finding`, `manage_notes`, `upload_file`, `add_burp_evidence` 等 11 个未实现 |
| RA | 9 | 1 | `sla_calculate`, `assign_finding`, `close_engagement`, `reopen_engagement` 等 8 个未实现 |
| RAA | 6 | 2 | `get_risk_acceptance`, `expire_risk_acceptance`, `reactivate`, `notes`, `download_proof` 未实现 |
| JSA | 6 | 0 | Agent 未实现，6 个工具均缺失 |
| VA | 3 | 1 (verify) | `close_finding`, `create_notification` 未实现 |
| **合计** | **48** | **~10** | **~38 个缺失** |

## 附录 C：设计文档 48 个工具的完整清单

从设计文档 §9.1 工具清单提取——标 ✅ 的已实现：

| # | 工具名 | Agent | 实际 |
|---|--------|-------|:---:|
| 1 | `defectdojo_import_scan_tool` | SIA | ✅ |
| 2 | `defectdojo_reimport_scan_tool` | SIA | ❌ |
| 3 | `defectdojo_get_scan_types_tool` | SIA | ⚠️ 注释 |
| 4 | `knowledge_search_cve_description_tool` | SIA | ❌ 未分配 |
| 5 | `defectdojo_deduplicate_tool` | DA | ⚠️ 注释 |
| 6 | `defectdojo_get_duplicate_cluster_tool` | DA | ❌ |
| 7 | `defectdojo_reset_duplicate_tool` | DA | ❌ |
| 8 | `defectdojo_set_original_tool` | DA | ❌ |
| 9 | `defectdojo_group_findings_tool` | DA | ❌ |
| 10 | `defectdojo_verify_finding_tool` | TA | ✅ |
| 11 | `defectdojo_get_finding_tool` | TA | ✅ |
| 12 | `defectdojo_update_finding_tool` | TA | ✅ |
| 13 | `defectdojo_update_finding_severity_tool` | TA | ❌ |
| 14 | `defectdojo_close_finding_tool` | TA | ❌ |
| 15 | `defectdojo_upload_finding_file_tool` | TA | ❌ |
| 16 | `defectdojo_add_finding_tag_tool` | TA | ❌ |
| 17 | `defectdojo_remove_finding_tag_tool` | TA | ❌ |
| 18 | `defectdojo_manage_finding_notes_tool` | TA | ❌ |
| 19 | `defectdojo_add_burp_evidence_tool` | TA | ❌ |
| 20 | `defectdojo_manage_finding_metadata_tool` | TA | ❌ |
| 21 | `defectdojo_get_open_findings_tool` | TA | ❌ |
| 22 | `knowledge_search_similar_finding_tool` | TA | ✅ (定义但可能未使用) |
| 23 | `knowledge_search_cve_description_tool` | TA | ✅ (定义但可能未使用) |
| 24 | `defectdojo_get_open_findings_tool` | RA | ❌ |
| 25 | `defectdojo_sla_calculate_tool` | RA | ❌ |
| 26 | `defectdojo_assign_finding_tool` | RA | ❌ |
| 27 | `defectdojo_close_engagement_tool` | RA | ❌ |
| 28 | `defectdojo_reopen_engagement_tool` | RA | ❌ |
| 29 | `defectdojo_add_finding_tag_tool` | RA | ❌ |
| 30 | `defectdojo_remove_finding_tag_tool` | RA | ❌ |
| 31 | `defectdojo_manage_finding_metadata_tool` | RA | ❌ |
| 32 | `knowledge_search_remediation_pattern_tool` | RA | ✅ (定义但可能未使用) |
| 33 | `defectdojo_create_risk_acceptance_tool` | RAA | ✅ |
| 34 | `defectdojo_get_risk_acceptance_tool` | RAA | ❌ |
| 35 | `defectdojo_expire_risk_acceptance_tool` | RAA | ❌ |
| 36 | `defectdojo_reactivate_risk_acceptance_tool` | RAA | ❌ |
| 37 | `defectdojo_risk_acceptance_notes_tool` | RAA | ❌ |
| 38 | `defectdojo_download_proof_tool` | RAA | ❌ |
| 39 | `defectdojo_close_finding_tool` | VA | ❌ |
| 40 | `defectdojo_get_finding_tool` | VA | ✅ (共用) |
| 41 | `defectdojo_create_notification_tool` | VA | ❌ |
| 42 | `defectdojo_jira_push_tool` | JSA | ❌ |
| 43 | `defectdojo_jira_comment_tool` | JSA | ❌ |
| 44 | `defectdojo_jira_link_tool` | JSA | ❌ |
| 45 | `defectdojo_jira_unlink_tool` | JSA | ❌ |
| 46 | `defectdojo_jira_push_status_tool` | JSA | ❌ |
| 47 | `defectdojo_jira_epic_tool` | JSA | ❌ |
| 48 | (其他) | — | — |

## 附录 D：CVE 按需加载 — 设计文档 vs 实际实现

### 设计文档的方案（§3.8 知识层）

设计文档的知识层只有 4 个 Qdrant 分区和 4 个向量检索 Tool。CVE 数据来源是：

1. **全量下载**（`download_nvd()`）：NVD API 2.0 按日期窗口分批拉取 → `data/kg/nvd/nvdcve-2.0.json`，200K+ CVE
2. **library 分区**：一次性批量导入 CWE/CVE/OWASP 标准描述到 Qdrant

**问题**：全量下载耗时长（首次 30-60 分钟），200K+ CVE 大部分与实际业务无关。

### 实际实现的方案（`knowledge/kg/enricher.py`）

在保留全量下载能力的基础上，新增**按需加载**通道：

```
导入扫描 → 自动提取 CVE → NVD API 单条查询 → 图增量更新
```

### 核心函数

| 函数 | 位置 | 作用 |
|------|------|------|
| `enrich_graph_from_scan()` | `kg/enricher.py` | 入口：从导入结果的 test_id 查 findings → 提取 CVE → 图更新 |
| `enrich_graph_with_cves()` | `kg/enricher.py` | 批量加载 CVE 到 networkx DiGraph（幂等，已存在跳过） |
| `_extract_cves_from_test()` | `kg/enricher.py` | 从 DefectDojo finding 的 4 个字段提取 CVE ID：`vulnerability_ids`、`cve`、`vuln_id_from_tool`、`title/description` |
| `_fetch_cve_from_nvd()` | `kg/enricher.py` | NVD API 2.0 `?cveId=CVE-XXXX-XXXXX` 单条查询，返回 CVSS + CWE |
| `_persist_cve()` | `kg/enricher.py` | 线程安全：加节点 + 连 CWE 边 + pickle 持久化 |
| `_enrich_kg_after_import()` | `routing_service.py` | 挂载点：`_run_import_scan()` 成功后自动触发 |

### 集成点

```python
# routing_service.py
def _run_import_scan(...):
    result = _run_crew(...)  # 原有导入逻辑
    if result.get("status") == "completed":
        _enrich_kg_after_import(result)  # ← 新增：非致命，失败不影响导入
    return result
```

### 与设计文档的关键差异

| 维度 | 设计文档 | 实际实现 |
|------|---------|---------|
| CVE 加载方式 | 仅全量 NVD 下载 | 全量下载 + **按需单条查询** |
| 触发时机 | 手动运行 `download_nvd()` | **导入扫描后自动触发** |
| 数据来源 | NVD 日期窗口批量拉取 | NVD `?cveId=` 单条查询 + DefectDojo finding 字段提取 |
| 存储 | JSON-lines 文件 → 图构建 | **直接增量写入 DiGraph + pickle** |
| 幂等性 | 依赖 `_existing_cve_ids()` 扫描 | `g.has_node()` O(1) 判断 |
| 线程安全 | 无 | `threading.Lock` 保护图写操作 |
| 性能 | 全量构建 ~4 分钟 | 单 CVE 加载 <2s（含 NVD API 延迟） |

### 测试验证

```
Before: 1236 nodes → Enrich CVE-2024-21887 → After: 1237 nodes + 1 edge
Re-add same CVE: 0 added (idempotent)
Pickle reload: CVE node survived restart
CVE-2024-21887: CVSS 9.1 (CRITICAL), CWEs: ['CWE-78', 'CWE-77']
```

## 附录 E：设计文档 WorkflowContext vs 实际实现

设计文档 §3.7.5 定义了 `WorkflowContext` Pydantic 模型，字段包括乐观锁 `version`、当前状态 `current_state: WorkflowState`、状态历史 `state_history: list[StateTransition]`、`urgent_mode`、`compliance_audit`、`deduplication_result`、`triage_suggestions`。

**实际实现**: `memory/models.py` 中的 `WorkflowContext` 只有 `steps: list[WorkflowStepContext]`。没有乐观锁、没有状态机状态、没有审计标记。设计文档的 `WorkflowContext` 是一个**有状态的工作流引擎上下文**，实际实现是**工作流步骤历史日志**。
