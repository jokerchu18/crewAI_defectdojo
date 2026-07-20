
## 人机交互路由

<<<<<<< HEAD
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

启动自然语言路由与人工审批：
=======
启动自然语言路由与人工审批，启动方式：
>>>>>>> a6f1c437134f5f3681743105d15435d725577a11

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

同一 `session_id` 下，导入 Agent 返回的 `test_id`、`product_id` 和
`engagement_id` 会自动保存，后续消息可以直接使用“刚才的扫描”等表达。

## Redis Session

会话上下文保存在 Redis 中，不再依赖进程内字典。先安装客户端：

```powershell
python -m pip install "redis>=5,<7"
```

本地可以使用 Docker 启动 Redis：

```powershell
docker run --name defectdojo-redis -p 6379:6379 -d redis:7-alpine
```

`.env` 配置：

```dotenv
REDIS_URL=redis://localhost:6379/0
SESSION_REDIS_PREFIX=defectdojo:session
SESSION_TTL_SECONDS=86400
SESSION_REFRESH_TTL_ON_READ=true
REDIS_SOCKET_TIMEOUT_SECONDS=5
```

默认 Session 有效期为 24 小时。开启 `SESSION_REFRESH_TTL_ON_READ` 后，
每次读取会话都会重新计算有效期。多个应用进程只要连接同一个 Redis，
就可以共享相同的 `session_id` 上下文。

## PostgreSQL 对话记录

对话消息（用户提问与 Agent 最终答复，含各步骤执行结果）持久化在
PostgreSQL 中，刷新页面或重启服务后可恢复历史对话。先安装客户端：

```powershell
python -m pip install "psycopg[binary,pool]"
```

本地使用 Docker 启动 PostgreSQL（端口 5433，避免与 DefectDojo 自带的
postgres 冲突；数据保存在命名卷 `defectdojo-chat-pgdata` 中）：

```powershell
docker run -d --name defectdojo-chat-postgres --restart unless-stopped `
  -e POSTGRES_USER=chat -e POSTGRES_PASSWORD=chatpass -e POSTGRES_DB=chat_history `
  -p 5433:5432 -v defectdojo-chat-pgdata:/var/lib/postgresql `
  postgres:18.4-alpine
```

注意：postgres 18+ 镜像的数据目录挂载点是 `/var/lib/postgresql`（不再是
旧版的 `/var/lib/postgresql/data`），挂错会导致容器反复重启。

`.env` 配置：

```dotenv
CHAT_DATABASE_URL=postgresql://chat:chatpass@localhost:5433/chat_history
CHAT_DATABASE_POOL_SIZE=5
CHAT_DATABASE_TIMEOUT_SECONDS=5
SESSION_HISTORY_MAX_MESSAGES=200
```

表结构（`chat_messages`）在服务启动时自动创建。每个会话最多保留
`SESSION_HISTORY_MAX_MESSAGES` 条消息，且查询时只返回 `SESSION_TTL_SECONDS`
内的消息，与 Redis 会话上下文的过期策略保持一致。
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
