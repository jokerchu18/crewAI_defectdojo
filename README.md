
## 人机交互路由

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

## 增加Working Memory，支持一次对话内记忆输入过的id

同一 `session_id` 下，导入 Agent 返回的 `test_id`、`product_id` 和
`engagement_id` 会自动保存，后续消息可以直接使用“刚才的扫描”等表达。

## risk_acceptance agent 拆分为预审与执行agent，待审批finding列表写入数据库

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
