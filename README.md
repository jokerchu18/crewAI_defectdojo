
## 人机交互路由

### Web 工作台

启动浏览器界面、文件暂存、对话工作流和人工审批面板：

```powershell
python -m defectdojo_crewai.main web
```

然后访问 `http://127.0.0.1:8000`。上传报告只会保存到 `data/uploads/`，
不会自动导入 DefectDojo；需要在对话框中明确发出导入指令。

可在 `.env` 配置：

```dotenv
WEB_UPLOAD_DIR=data/uploads
WEB_UPLOAD_MAX_BYTES=20971520
```

启动自然语言路由与人工审批，启动方式：

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

默认审批数据库为 `data/workflow.db`。如需放到其他位置，可在 `.env` 中配置：

```dotenv
APPROVAL_DATABASE_PATH=data/approvals.db
```
## 注册函数解耦人工审批逻辑

新的人工审批类型通过 `register_action("action.type")` 注册执行函数，即可复用同一套
待审批、批准、拒绝、状态记录与失败记录逻辑。
