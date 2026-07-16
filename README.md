# crewAI_defectdojo
## 项目目录总览
```
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
```

## 人机交互路由

启动自然语言路由与人工审批：

```powershell
python -m defectdojo_crewai.main chat
```

可直接输入：

```text
评估 Product 1 的 Medium 漏洞风险接受
对 Product 1 生成修复计划
分诊 Test 18 下的漏洞
查询 Product 1 的漏洞
```

代码接口同时支持显式传入上下文。显式 ID 的优先级高于 Router 从自然语言中提取的 ID：

```python
from defectdojo_crewai.models.schemas import ChatRequest, ConversationContext
from defectdojo_crewai.services.routing_service import handle_chat_request

response = handle_chat_request(
    ChatRequest(
        session_id="user-session-001",
        message="继续分诊刚才导入的漏洞",
        context=ConversationContext(test_id=18, product_id=1),
    )
)
```

同一 `session_id` 下，导入 Agent 返回的 `test_id`、`product_id` 和
`engagement_id` 会自动保存，后续消息可以直接使用“刚才的扫描”等表达。

风险接受 Agent 只生成预审候选项，不会直接修改 DefectDojo。存在 `Accept`
候选项时，系统会生成持久化待审批记录：

```text
approvals
approve <approval_id>
approve <approval_id> 32,35
reject <approval_id>
```

默认审批数据库为 `data/approvals.db`。如需放到其他位置，可在 `.env` 中配置：
c
```dotenv
APPROVAL_DATABASE_PATH=data/approvals.db
```

新的人工审批类型通过 `register_action("action.type")` 注册执行函数，即可复用同一套
待审批、批准、拒绝、状态记录与失败记录逻辑。
