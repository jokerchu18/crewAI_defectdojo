# crewAI_defectdojo
## 项目目录总览
defectdojo_crewai/
├── crews/
│   ├── __init__.py
│   └── vulnerability_lifecycle.py  # 漏洞生命周期 Crew
├── agents/
│   ├── __init__.py
│   ├── base.py                     # 基础 Agent 类
│   ├── scan_import.py              # Scan Import Agent
│   ├── deduplication.py            # Deduplication Agent
│   ├── triage.py                   # Triage Agent
│   ├── remediation.py              # Remediation Agent
│   ├── risk_acceptance.py          # Risk Acceptance Agent
│   ├── jira_sync.py                # Jira Sync Agent
│   └── verification.py             # Verification Agent
├── tasks/
│   ├── __init__.py
│   ├── import_tasks.py             # 导入相关任务
│   ├── dedupe_tasks.py             # 去重相关任务
│   ├── triage_tasks.py             # 分诊相关任务
│   ├── remediation_tasks.py        # 修复相关任务
│   ├── risk_tasks.py               # 风险接受相关任务
│   ├── jira_tasks.py               # Jira 同步相关任务
│   └── verification_tasks.py       # 验证关闭相关任务
├── tools/
│   ├── __init__.py
│   ├── defectdojo_api.py           # DefectDojo REST API 客户端
│   ├── jira_client.py              # Jira API 客户端
│   ├── sla_engine.py               # SLA 计算引擎
│   └── notification_service.py     # 通知服务
├── config/
│   ├── __init__.py
│   ├── settings.py                 # 配置管理
│   └── llm_config.py               # LLM 配置
├── models/
│   ├── __init__.py
│   └── schemas.py                  # Pydantic 数据模型
└── main.py                         # 入口文件
