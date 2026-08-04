# TiDe — Gaea 单项目部署配置

## 部署模式

这是 Gaea 单模块、单镜像、单 Pod 的受控 TEST 部署。Gaea 直接读取根级
`gaea/Dockerfile`，不再使用 `gaea.yml` 或子模块 Dockerfile。

镜像由 s6-overlay 管理四个常驻进程：

| 进程 | 监听端口 | 职责 |
|---|---:|---|
| `operations` | `8010` | FastAPI 同源提供运营 React、`/api/*` 与 `/api/health` |
| `teacher-web` | `8080` | Nginx 提供教师 React，并把 `/api/*` 代理到本 Pod 的 NestJS |
| `teacher-api` | `3000` | NestJS 教师端 API；只在 Pod 内访问，不配置 Gaea Ingress |
| `score-settlement` | 无 | 固定任务积分结算循环和 heartbeat |

运营端与教师端仍是两套独立 HTTP 服务，只是共享镜像和 Pod。两个数据库运行角色、两套
API 路由和认证逻辑不合并。FastAPI、NestJS、Nginx 或积分 Worker 任一非零退出，s6 都会
终止整个容器，让 Kubernetes 重建完整 Pod。

但单容器不是安全隔离边界：`S6_KEEP_ENV=1` 会让进程继承整套运行变量，多个业务进程又以
同一 UID `1001` 运行，因此其中一个进程被利用后可能读取另一个进程的数据库、JWT、OSS
或邮件凭据。这个结构性取舍只为尽快完成办公室 TEST；需要生产级秘密隔离时必须重新拆分
容器或 Pod，不能把“数据库角色不同”解释成“密钥彼此不可见”。

## 不可变运行约束

- Gaea 应用副本数必须固定为 `1`，关闭自动伸缩，并使用 `Recreate` 更新策略；如果平台
  不能设置 `Recreate`，每次发布先把旧应用缩到 `0`，确认旧 Pod 消失后再启动新版本。
  只设置 replicas=1 仍可能因 rolling update 的 `maxSurge` 短暂出现两个 Worker。
- `3000` 已由统一镜像强制绑定 `127.0.0.1`，不得再配置 Ingress、SLB 或 Service 端口；
  教师 API 只能经 `8080/api/*` 访问。
- Alembic 和教师端 migration 都是发布前独立作业，不能放进 Pod 启动流程。
- 单镜像不代表共用数据库账号：运营使用 `tit_growth_app`，教师端使用
  `tit_teacher_crud` 和单独的只读来源账号；迁移分别使用 `tit_growth_migrator` 与
  `tide_migrator`。
- Gaea 高级设置必须允许 root PID 1 启动 `/init`；s6 随后把业务进程降权到 UID `1001`。
  如果平台强制 `runAsNonRoot`，该镜像会在启动阶段失败。
- `LOCAL_FILE_STORAGE_DIR=/var/lib/tide/uploads` 与
  `VIDEO_PREFETCH_STATE_DIR=/var/lib/tide/video-prefetch-runs` 应共用 `/var/lib/tide` PVC，并
  设置 `fsGroup=1001` 或允许启动脚本修正卷根目录权限。没有 PVC 时 Pod 重建会丢失上传
  附件和预热状态，只能做不含真实上传的短期 TEST。
- 单 Pod 同时运行 Python API、NestJS、Nginx 与后台进程，TEST 建议从 4 GiB 内存起步，
  再按实际 RSS、连接数和延迟收缩；这不是容量验收结果。

## 构建参数

教师前端的 API Origin 和公共素材地址是 Vite 构建时事实：

| 参数 | TEST 默认值 | 说明 |
|---|---|---|
| `VITE_API_BASE_URL` | `https://tide-camp-teacher.test.51talk.biz` | 教师 Web 与 API 的同源 HTTPS Origin |
| `VITE_PUBLIC_ASSET_BASE_URL` | `https://tide-media.51talkjr.com` | 已发布教师素材的 HTTPS 基址 |

TEST 默认值固化在 Dockerfile，Gaea 无额外 build args 时可以直接构建。它不是生产安全
门禁：预发布和生产必须用各自已评审 HTTPS 地址覆盖这两个参数并重建镜像，不能把 TEST
Origin 晋级。Docker 构建会额外加载 NestJS 与原生 `sharp` 模块并执行 `nginx -t`，用来
尽早暴露 Alpine ABI 或 Nginx 配置不兼容；仍需 Gaea 的真实冷构建作为最终证据。

## Gaea 端口与域名

在唯一应用（建议继续使用现有 `tide-camp-api` 项目）中配置两个端口：

| 容器端口 | 访问方式 | TEST 域名 | 用途 |
|---:|---|---|---|
| `8010` | Ingress / HTTP(S) | `https://tide-camp-ops.test.51talk.biz` | 运营端页面和 API |
| `8080` | Ingress / HTTP(S) | `https://tide-camp-teacher.test.51talk.biz` | 教师端页面和同源 API |

Gaea 当前端口管理支持同一应用配置多个容器端口。不要把两个域名都指向同一个端口：两端
都有 `/api/*`，按端口分流才能避免路径冲突。`EXPOSE` 只描述镜像端口，不会替代平台上的
两条域名配置；发布后必须从两个外部 HTTPS 域名分别做 smoke test。

## 聚合健康检查

镜像的 `HEALTHCHECK` 每 30 秒依次验证：

1. `8010/api/health`：运营 API、运营静态页面启动边界和数据库；
2. `8080/healthz`：教师 Nginx 与静态产物；
3. `8080/health/ready`：经 Nginx 代理访问教师 API，并检查两条数据库读取链；
4. `settle_shared_task_scores.py --healthcheck --max-heartbeat-age-seconds 90`：积分 Worker。

因此 Pod 显示健康只表示四个进程和对应数据库就绪，不代表教师登录、九项任务、积分回写、
外部素材、真实通知或完整业务验收已经完成。

## 运营端运行变量

以下变量由 Gaea 配置或密钥管理注入；密钥不得写进镜像、Git、业务 payload 或日志。

| 变量名 | 必填 | 建议值/默认值 | 说明 |
|---|---|---|---|
| `APP_ENV` | 是 | `production` | 启用运营 API 的生产安全校验 |
| `TIT_MIGRATION_MODE` | 是 | `false` | 常驻进程不得执行 Alembic |
| `DATABASE_URL` | 是 | 无 | `tit_growth_app` 的 PostgreSQL URL |
| `TIT_ALLOWED_HOSTS` | 是 | 运营域名 | 多值用英文逗号分隔 |
| `TIT_HEALTHCHECK_HOST` | 是 | 运营域名 | 必须包含在 `TIT_ALLOWED_HOSTS` |
| `TIT_ALLOWED_ORIGINS` | 否 | 空 | 运营前后端同源时保持为空 |
| `TIT_SESSION_TTL_HOURS` | 否 | `8` | 运营登录会话小时数 |
| `TIT_DB_POOL_SIZE` | 是 | `5` | 每个 Uvicorn Worker 的连接池 |
| `TIT_DB_MAX_OVERFLOW` | 是 | `2` | 每个 Uvicorn Worker 的溢出连接 |
| `TIT_DB_POOL_TIMEOUT_SECONDS` | 是 | `5` | 获取连接超时 |
| `TIT_DB_POOL_RECYCLE_SECONDS` | 是 | `1800` | 连接回收秒数 |
| `TIT_DB_CONNECT_TIMEOUT_SECONDS` | 是 | `8` | 建连超时 |
| `TIT_DB_LOCK_TIMEOUT_MS` | 是 | `5000` | 锁等待上限 |
| `TIT_DB_IDLE_TRANSACTION_TIMEOUT_MS` | 是 | `60000` | 空闲事务超时 |
| `TIT_DB_STATEMENT_TIMEOUT_MS` | 是 | `30000` | 运营 SQL 超时 |
| `TIT_DB_APPLICATION_NAME` | 否 | `tit-growth-api` | PostgreSQL 连接标识 |
| `TIT_API_WORKERS` | 是 | `2` | Pod 内 Uvicorn Worker 数 |
| `TIT_API_LIMIT_CONCURRENCY` | 是 | `8` | 每个 Worker 的并发上限 |
| `TIT_API_KEEPALIVE_SECONDS` | 是 | `5` | HTTP keep-alive |
| `TIT_TRUSTED_PROXY_IPS` | 是 | Gaea Ingress 精确地址 | 禁止 `*` 或未经确认的大网段 |
| `TIT_ARGON2_MAX_CONCURRENCY` | 是 | `2` | 密码哈希并发上限 |
| `TIT_LOGIN_RATE_LIMIT_ATTEMPTS` | 是 | `10` | 登录窗口内尝试数 |
| `TIT_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | 是 | `60` | 登录限流窗口 |
| `TIT_LOGIN_RATE_LIMIT_MAX_KEYS` | 是 | `10000` | 登录限流键上限 |
| `TIT_SLOW_REQUEST_MS` | 是 | `1000` | 慢请求日志阈值 |
| `AGENT_PROVIDER` | 否 | `deterministic` | 当前任务规则不需要模型 |
| `OPENAI_API_KEY` | 条件必填 | 无 | 仅启用 OpenAI Provider 时通过密钥管理注入 |

`TIT_FRONTEND_REQUIRED=true` 已固定在镜像中，禁止覆盖为 `false`。

统一镜像还读取 Gaea 注入的 `MEMORY_SIZE`，只用于把 Nginx worker 数渲染到 2–16 的有界
范围；未设置时按 8 个 worker 渲染，未知档位安全回退到 2。

## 积分 Worker 进程级变量

单容器环境变量会被所有进程继承，因此 Worker 使用带前缀变量覆盖自己的连接池设置，避免
把运营 API 的池大小直接复制给后台循环：

| 变量名 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `TIT_SCORE_DB_POOL_SIZE` | 否 | `1` | Worker 独立池大小 |
| `TIT_SCORE_DB_MAX_OVERFLOW` | 否 | `0` | Worker 溢出连接 |
| `TIT_SCORE_DB_STATEMENT_TIMEOUT_MS` | 否 | `60000` | Worker SQL 超时 |
| `TIT_SCORE_DB_APPLICATION_NAME` | 否 | `tit-growth-score-worker` | Worker 连接标识 |
| `TIT_SCORE_WORKER_HEARTBEAT` | 否 | `/tmp/tit-score-worker-heartbeat` | 已固化；覆盖时必须同步健康检查 |

Worker 与运营 API 共用 `DATABASE_URL` 对应的受限运营运行角色，但不使用迁移角色。
固定启动命令为
`settle_shared_task_scores.py --watch --max-events 25 --interval-seconds 3`。

## 教师端运行变量

| 变量名 | 必填 | TEST 建议值/默认值 | 说明 |
|---|---|---|---|
| `TIDE_TEACHER_NODE_ENV` | 否 | `test` | 仅办公室 TEST 可设 `test`；未设置时失败关闭地使用 `production` |
| `TIDE_TEACHER_HOST` | 是 | 教师域名（不带 scheme） | 聚合健康检查的 Host，例如 `tide-camp-teacher.test.51talk.biz` |
| `TIDE_TRUSTED_PROXY_CIDRS` | 否 | 复用 `TIT_TRUSTED_PROXY_IPS` | 教师入口不同时再覆盖；拒绝全网段和非法值 |
| `TRUST_PROXY_HOPS` | 是 | `1` | 只信任本 Pod 的教师 Nginx 一跳 |
| `CORS_ORIGINS` | 是 | 教师域名 | 教师页面与 API 同源 |
| `DATABASE_REQUIRED` | 是 | `true` | 禁止无数据库假启动 |
| `TIDE_DATABASE_URL` | 是 | 无 | `tit_teacher_crud` 连接；生产必须 `sslmode=verify-full` |
| `SHIWEN_READ_DATABASE_URL` | 是 | 无 | 教师来源只读连接；生产不得复用写账号 |
| `DATABASE_MAX_CONNECTIONS` | 是 | `5` | 每条教师数据库链各自的池上限；两条链合计最多 10 |
| `DATABASE_CONNECTION_TIMEOUT_MS` | 是 | `3000` | 建连超时；需小于聚合探针超时 |
| `DATABASE_STATEMENT_TIMEOUT_MS` | 是 | `10000` | 教师 SQL 超时 |
| `SHIWEN_READ_MODE` | 是 | `DIRECT_TABLES` | 公司测试现有读取模式 |
| `SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK` | 是 | `false` | 禁止用 fixture 冒充真实数据 |
| `SHIWEN_TEACHER_IDENTITY_VIEW` | 条件必填 | 无 | `VIEWS` 模式时必填 |
| `PUBLIC_APP_URL` | 是 | 教师 HTTPS Origin | 邮件与深链基址 |
| `PUBLIC_API_URL` | 是 | 教师 HTTPS Origin | 文件与 API 公共基址 |
| `DATA_HASH_SECRET` | 是 | 密钥管理注入 | 至少 32 字符 |
| `AUTH_JWT_SECRET` | 是 | 密钥管理注入 | 至少 32 字符 |
| `FILE_STORAGE_PROVIDER` | 是 | TEST 可 `LOCAL`；生产 `OSS` | 本地存储仅用于隔离测试 |
| `LOCAL_FILE_STORAGE_DIR` | 否 | `/var/lib/tide/uploads` | LOCAL 模式需挂载 `/var/lib/tide` PVC |
| `OSS_REGION` / `OSS_ENDPOINT` / `OSS_BUCKET` | 条件必填 | 无 | `FILE_STORAGE_PROVIDER=OSS` 时必填 |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 条件必填 | 密钥管理注入 | OSS 凭据 |
| `MULTIPART_UPLOAD_MAX_CONCURRENCY` | 是 | `4` | 1–16；提高前先做 3×8 MiB 并发验收 |
| `BACKGROUND_JOBS_ENABLED` | 是 | `true` | 教师后台任务；因此 Pod 只能单副本 |
| `TASK_CATALOG_PUBLIC_WRITE` | 是 | `false` | 教师端不得改共享任务目录 |
| `VIDEO_PREFETCH_STATE_DIR` | 否 | `/var/lib/tide/video-prefetch-runs` | 已固化；生产需 PVC |
| `MAIL_DELIVERY_PROVIDER` | 否 | `UNAVAILABLE` | 启用公司邮件时还需 `MAIL_API_URL/MAIL_API_ACCESS_KEY` |
| `AI_GATEWAY_ENABLED` | 否 | `false` | 启用时必须注入 `AI_GATEWAY_API_KEY` |

统一镜像会把教师 `BIND_HOST` 固定为 `127.0.0.1`、`PORT` 固定为 `3000`，并把单文件
`FILE_UPLOAD_MAX_BYTES` 固定为 10 MiB，以保持在 Nginx 26 MiB 请求上限内；这些值不要在
Gaea 另行配置。教师 Nginx 只从 `TIDE_TRUSTED_PROXY_CIDRS`（未设时复用
`TIT_TRUSTED_PROXY_IPS`）指定的入口解析 `X-Forwarded-For`。仍需平台确认 Ingress 会覆盖
或追加而不是原样透传客户端伪造头。

按上述建议值，教师两条池最多 10 连接；再加运营 `2 × (5 + 2)` 和 Worker 1 条，单 Pod
最坏约 25 条连接。发布前必须按公共 PG 的连接额度核对，不能只看 Pod 是否运行。

办公室公共 PG 若只能使用非严格 TLS，可以在 TEST 环境使用现有
`COMPANY_TEST_DATABASE_ENABLED=true` 适配层，并注入 `TIDE_ADMIN_DB_HOST/PORT/NAME`、
`TIDE_APP_DB_USER/PASSWORD`、`TIDE_ADMIN_DB_SSLMODE`。该适配会让教师写入与来源读取暂时复用
一个测试账号，只能用于受控 TEST；预发布和生产必须恢复独立 URL 与独立角色。

其余可选邮件、OSS、CDN、通知调度、照片 Worker、AI Gateway 与文件限制变量，以
`teacher/backend/.env.example` 为完整字段表；启用某项能力时不得依赖代码默认值猜测密钥。

## 构建与本地验证

必须从仓库根目录构建：

```bash
docker build -f gaea/Dockerfile -t tide-camp:gaea .
```

使用隔离测试环境文件启动并暴露两个页面端口：

```bash
docker run --rm -d \
  --name tide-camp-gaea-test \
  --env-file /安全路径/TiDe.gaea.test.env \
  -p 127.0.0.1:8010:8010 \
  -p 127.0.0.1:8080:8080 \
  tide-camp:gaea
```

```bash
curl -fsS -H 'Host: tide-camp-ops.test.51talk.biz' \
  http://127.0.0.1:8010/api/health
curl -fsS -H 'Host: tide-camp-teacher.test.51talk.biz' \
  http://127.0.0.1:8080/healthz
curl -fsS -H 'Host: tide-camp-teacher.test.51talk.biz' \
  http://127.0.0.1:8080/health/ready
docker exec tide-camp-gaea-test \
  curl -fsS http://127.0.0.1:3000/health/ready
docker inspect --format '{{.State.Health.Status}}' tide-camp-gaea-test
docker stop tide-camp-gaea-test
```

本地容器验证会真实启动积分 Worker，只能连接隔离测试库。

## 发布顺序

1. 分别执行 TiDe Alembic 与教师端 migration；教师端至少到
   `0025_fixed_task_semantic_alignment`，随后执行只读契约探针。
2. 配齐统一应用的运营、教师和 Worker 环境变量，确认密钥不在版本化配置中。
3. 在 Gaea 将统一应用副本固定为 `1`、关闭自动伸缩，设置 `Recreate`；若不支持，先缩容
   到 `0`。同时确认平台允许 root `/init`，并配置 `8010` 运营域名和 `8080` 教师域名。
4. 停止旧 `test-tide-camp-worker`，避免它与新镜像内的 Worker 同时常驻。
5. 向现有 `tide-camp-api` 项目发布统一镜像，现场读回 replicas、自动伸缩、更新策略、两个
   端口、两个域名、PVC 权限和健康状态。
6. 从两个外部 HTTPS 域名验证运营登录、教师登录、G01–G09、状态更新、幂等结分、
   积分/课程读取和工单往返。
7. 业务验收完成后再下线旧 Worker 项目；不要用“Pod 运行中”代替端到端验收。

回滚统一镜像时，应先停止统一 Pod，再恢复旧 API 与旧 Worker；禁止两个结算 Worker 重叠
运行。迁移回滚继续遵循向前修复和一致性备份，不由容器启动脚本执行 destructive down。

一次性发布变量仍保持原边界：运营迁移使用 `tit_growth_migrator`、
`TIT_MIGRATION_MODE=true`、`TIT_MIGRATION_EXPECTED_DATABASE`；首次运营账号初始化只在一次性
命令中注入 `TIT_BOOTSTRAP_USERNAME` 与 `TIT_BOOTSTRAP_PASSWORD`。教师 migration 使用
`tide_migrator`，不得复用任何运行账号。

## 当前证明边界

单镜像构建、静态检查、测试和 Pod 健康都不代表生产可用。外部日更、真实通知回执、生产
账号生命周期、监控、备份、恢复、灰度与业务方验收仍是独立门槛。

## 目录结构

```text
gaea/
├── Dockerfile
├── README.md
├── bin/
│   ├── healthcheck.sh
│   ├── render-nginx-conf.sh
│   └── render-real-ip-conf.py
├── nginx/
│   ├── nginx.conf
│   ├── real-ip.conf
│   └── teacher.conf
└── s6-rc.d/
    ├── operations/
    ├── teacher-api/
    ├── teacher-web/
    ├── score-settlement/
    └── user/contents.d/
```
