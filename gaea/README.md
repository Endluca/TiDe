# TiDe 运营端 — Gaea 部署配置

## 部署模式

这是 Gaea 模式 C（多模块）配置，包含两个边界明确的 Python 运行单元：

| 模块 | 常驻进程 | 部署约束 |
|---|---|---|
| `operations` | FastAPI/Uvicorn | 内部 Node 22 镜像先构建 `frontend/`，再把 `dist` 复制到 `/app/app/static`；最终镜像只运行 Python，由 `StaticFiles` 同源提供页面和 API |
| `score-settlement` | 固定任务积分结算循环 | 独立单副本；使用文件 heartbeat 健康检查，不监听 HTTP 端口 |

`operations` 最终镜像不包含 Node.js、Nginx 或 s6，前后端不分离运行。结算 Worker 的独立是业务一致性边界，不是前后端分离。`teacher/` 是独立的教师端 NestJS + React 系统，不在本 Gaea 应用范围内。

## 必备环境变量

Gaea 应用按生产模式失败关闭。下表中的必填变量必须在平台配置中显式设置；密钥只放 Gaea 的密钥管理，不得写入镜像、Git 或业务 payload。

### `operations` 模块

| 变量名 | 必填 | 建议值/默认值 | 说明 |
|---|---|---|---|
| `APP_ENV` | 是 | `production` | 启用生产安全校验并关闭 API 文档 |
| `TIT_MIGRATION_MODE` | 是 | `false` | API 运行进程不得执行 Alembic |
| `DATABASE_URL` | 是 | 无 | `tit_growth_app` 受限角色的 PostgreSQL URL；必须且只能带一个 `sslmode=verify-ca` 或 `verify-full` |
| `TIT_ALLOWED_HOSTS` | 是 | 无 | Gaea 对外业务域名；多个值用英文逗号分隔 |
| `TIT_HEALTHCHECK_HOST` | 是 | 无 | 健康检查 Host，必须包含在 `TIT_ALLOWED_HOSTS` 中 |
| `TIT_ALLOWED_ORIGINS` | 否 | 空 | 前后端同源时保持为空；只有明确跨域时才填写完整 HTTPS Origin |
| `TIT_SESSION_TTL_HOURS` | 否 | `8` | 运营登录会话有效期 |
| `TIT_DB_POOL_SIZE` | 是 | `5` | 每个 API 进程的数据库连接池大小 |
| `TIT_DB_MAX_OVERFLOW` | 是 | `2` | 每个 API 进程的临时溢出连接上限 |
| `TIT_DB_POOL_TIMEOUT_SECONDS` | 是 | `5` | 获取数据库连接的超时秒数 |
| `TIT_DB_POOL_RECYCLE_SECONDS` | 是 | `1800` | 数据库连接回收秒数 |
| `TIT_DB_CONNECT_TIMEOUT_SECONDS` | 是 | `8` | 新建数据库连接超时秒数 |
| `TIT_DB_LOCK_TIMEOUT_MS` | 是 | `5000` | 数据库锁等待上限 |
| `TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS` | 是 | `60000` | 空闲事务超时 |
| `TIT_DB_STATEMENT_TIMEOUT_MS` | 是 | `30000` | API SQL 执行超时；生产不建议设为 `0` |
| `TIT_DB_APPLICATION_NAME` | 否 | `tit-growth-api` | PostgreSQL 连接标识 |
| `TIT_ARGON2_MAX_CONCURRENCY` | 是 | `2` | 密码哈希并发上限 |
| `TIT_LOGIN_RATE_LIMIT_ATTEMPTS` | 是 | `10` | 登录限流窗口内尝试次数 |
| `TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | 是 | `60` | 登录限流窗口秒数 |
| `TIT_LOGIN_RATE_LIMIT_MAX_KEYS` | 是 | `10000` | 登录限流键数量上限 |
| `TIT_SLOW_REQUEST_MS` | 是 | `1000` | 慢 API 日志阈值 |
| `TIT_API_WORKERS` | 是 | `2` | Uvicorn Worker 数；连接池预算要乘以此值核算 |
| `TIT_API_LIMIT_CONCURRENCY` | 是 | `8` | 每个 Worker 的并发请求上限 |
| `TIT_API_KEEPALIVE_SECONDS` | 是 | `5` | HTTP keep-alive 秒数 |
| `TIT_TRUSTED_PROXY_IPS` | 是 | 无 | Gaea Ingress 的精确代理 IP/CIDR；禁止使用 `*` 或未经确认的整个内网网段 |
| `AGENT_PROVIDER` | 否 | `deterministic` | 当前确定性任务规则不需要模型 |
| `OPENAI_AGENT_MODEL` | 否 | `gpt-5.6-terra` | 仅非确定性 Provider 使用 |
| `AGENT_REASONING_EFFORT` | 否 | `low` | 仅非确定性 Provider 使用 |
| `AGENT_TIMEOUT_SECONDS` | 否 | `20` | 仅非确定性 Provider 使用 |
| `OPENAI_API_KEY` | 否 | 无 | 仅启用 OpenAI Provider 时通过密钥管理注入 |

`TIT_FRONTEND_REQUIRED=true` 已固化在镜像中，确保缺失 `index.html` 或 `assets/` 时应用拒绝启动，不要在平台覆盖为 `false`。

### `score-settlement` 模块

Worker 当前复用生产运行校验，因此仍需提供上表中的 `APP_ENV`、`TIT_MIGRATION_MODE`、`DATABASE_URL`、Host 配置及全部必填整数；其中 HTTP/API 专用值只用于通过同一失败关闭校验，不会启动 HTTP 服务。数据库连接预算必须覆盖为单实例 Worker 的独立值：

| 变量名 | 必填 | 建议值/默认值 | 说明 |
|---|---|---|---|
| `APP_ENV` | 是 | `production` | 使用生产数据边界 |
| `TIT_MIGRATION_MODE` | 是 | `false` | Worker 不执行迁移 |
| `DATABASE_URL` | 是 | 无 | `tit_growth_app` 受限运行角色，与 API 共用逻辑库但不使用迁移角色 |
| `TIT_DB_POOL_SIZE` | 是 | `1` | 单实例 Worker 连接池 |
| `TIT_DB_MAX_OVERFLOW` | 是 | `0` | 不允许额外溢出连接 |
| `TIT_DB_POOL_TIMEOUT_SECONDS` | 是 | `5` | 获取连接超时 |
| `TIT_DB_POOL_RECYCLE_SECONDS` | 是 | `1800` | 连接回收秒数 |
| `TIT_DB_STATEMENT_TIMEOUT_MS` | 是 | `60000` | 结算 SQL 执行超时 |
| `TIT_DB_APPLICATION_NAME` | 否 | `tit-growth-score-worker` | PostgreSQL 连接标识 |
| `TIT_SCORE_WORKER_HEARTBEAT` | 否 | `/tmp/tit-score-worker-heartbeat` | 已固化在 Worker 镜像；仅在同步修改健康检查时覆盖 |

平台副本数必须固定为 `1`。API Worker 数与 Gaea 横向副本数不影响这一要求。

### 发布作业与一次性变量

| 用途 | 变量 | 必填性与生命周期 |
|---|---|---|
| Alembic | `APP_ENV=production` | 迁移作业必填 |
| Alembic | `TIT_MIGRATION_MODE=true` | 迁移作业必填；常驻模块必须为 `false` |
| Alembic | `TIT_MIGRATION_EXPECTED_DATABASE` | 迁移作业必填 |
| Alembic | `DATABASE_URL` | 使用且只能使用非超级用户 `tit_growth_migrator`；不得复用运行角色 |
| 初始化运营账号 | `TIT_BOOTSTRAP_USERNAME` | 只注入一次性初始化命令，完成后移除 |
| 初始化运营账号 | `TIT_BOOTSTRAP_PASSWORD` | 只通过密钥管理注入一次性初始化命令，完成后移除 |

## 端口与健康检查

| 端口 | 用途 |
|---|---|
| `8010` | `operations` 的运营 Web App、`/api/*` 和健康检查 |

- 健康检查：`GET /api/health`
- 间隔：10 秒
- 超时：3 秒
- 启动宽限：20 秒

生产健康检查会访问数据库，因此 `200` 表示 API 进程和数据库都可用，不代表教师端、外部日更、通知投递或生产切流已经完成。

`score-settlement` 不暴露端口。其镜像健康检查执行：

```bash
python scripts/settle_shared_task_scores.py --healthcheck --max-heartbeat-age-seconds 90
```

## 构建与本地镜像验证

必须从仓库根目录构建，因为 Dockerfile 同时读取 `frontend/` 和 `backend/`：

```bash
docker build -f gaea/operations/Dockerfile -t tide-operations:gaea .
docker build -f gaea/score-settlement/Dockerfile -t tide-score-settlement:gaea .
```

使用受保护的运行环境文件启动；示例文件不能包含真实密钥：

```bash
docker run --rm \
  --env-file /安全路径/TiDe.runtime.production.env \
  -p 127.0.0.1:8010:8010 \
  tide-operations:gaea
```

验证同一进程同时提供页面和 API：

```bash
curl -fsS -H 'Host: <TIT_ALLOWED_HOSTS中的域名>' http://127.0.0.1:8010/ >/dev/null
curl -fsS -H 'Host: <TIT_HEALTHCHECK_HOST>' http://127.0.0.1:8010/api/health
```

Worker 会消费并修改真实任务/积分事实，只能对隔离测试库做本地容器验证：

```bash
docker run --rm -d \
  --name tide-score-settlement-smoke \
  --env-file /安全路径/TiDe.runtime.isolated-test.env \
  -e TIT_DB_POOL_SIZE=1 \
  -e TIT_DB_MAX_OVERFLOW=0 \
  -e TIT_DB_STATEMENT_TIMEOUT_MS=60000 \
  tide-score-settlement:gaea
docker inspect --format '{{.State.Health.Status}}' tide-score-settlement-smoke
docker stop tide-score-settlement-smoke
```

## 发布边界

- Alembic 仍是唯一 Schema 变更路径。使用独立的 `tit_growth_migrator` 凭据，在 API 发布前执行迁移；API 启动不得自动迁移。迁移作业需要 `APP_ENV=production`、`TIT_MIGRATION_MODE=true`、`TIT_MIGRATION_EXPECTED_DATABASE` 和迁移专用 `DATABASE_URL`，执行 `alembic upgrade head`。
- 首次部署的运营账号通过一次性命令 `python scripts/bootstrap_operator.py` 创建。`TIT_BOOTSTRAP_USERNAME` 与 `TIT_BOOTSTRAP_PASSWORD` 只注入该次命令，不得长期留在应用环境中。
- `score-settlement` 模块固定运行 `scripts/settle_shared_task_scores.py --watch --max-events 25 --interval-seconds 3`。它不能嵌入多 Worker FastAPI，也不能因前后端合并而省略；平台副本数必须固定为 1，健康检查不能改用 API 的 `/api/health`。
- Gaea Ingress 应丢弃外部传入的伪造 `X-Forwarded-*`，只写入平台确认的代理信息；应用的 `TIT_TRUSTED_PROXY_IPS` 只允许精确可信来源。
- 本配置只证明可构建并运行运营 Web/API 镜像，不代表外部数据日更、教师端生产接入、真实通知、监控、备份或生产切流已完成。

建议发布顺序：迁移作业 → 必要时初始化运营账号 → 启动 UI/API → 启动单实例积分结算 Worker → 分别验证页面、API、Worker 心跳和数据库读写。

## 目录结构

```text
gaea/
├── gaea.yml                    # 声明 operations 与 score-settlement 两个模块
├── operations/
│   └── Dockerfile              # Node 构建 + Python UI/API 运行时
├── score-settlement/
│   └── Dockerfile              # 单实例积分结算与 heartbeat 健康检查
└── README.md                    # 平台变量、验证方式与发布边界
```
