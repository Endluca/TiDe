# TiDe — Gaea application 根构建与轻量 DTS 模块

## 部署模式

这是两种构建形态、三个运行项目的受控 TEST 部署：

- `gaea/gaea.yml` 声明 `application` 与 `dts-ingest` 两个模块；现有 application 项目可暂时
  继续读取根级 `gaea/Dockerfile`，切换多模块后选择 `gaea/application/Dockerfile`；两者内容
  由测试强制保持完全一致；
- 海外和国内两个独立 DTS 项目都选择 `gaea/dts-ingest/Dockerfile`，但仍由 Gaea 分别构建、
  推送和发布；海外项目必须位于新加坡数据中心，国内项目必须位于中国大陆数据中心；
- 三个项目使用不同密钥集合。国内学生 ID 的 HMAC 密钥只允许注入国内项目。
  模块选择不会自动创建 Gaea 项目，也不会自动选择正确数据中心或让海外和国内跨项目复用同一个
  image digest。

application 镜像由 s6-overlay 管理五个业务进程入口：

| 进程 | 监听端口 | 职责 |
|---|---:|---|
| `operations` | `8010` | FastAPI 同源提供运营 React、`/api/*` 与 `/api/health` |
| `teacher-web` | `8080` | Nginx 提供教师 React，并把 `/api/*` 代理到本 Pod 的 NestJS |
| `teacher-api` | `3000` | NestJS 教师端 API；只在 Pod 内访问，不配置 Gaea Ingress |
| `score-settlement` | 无 | 固定任务积分结算候选进程、数据库选主和本 Pod heartbeat |
| `source-wide` | 无 | 字段级源事件消费候选进程、数据库选主和本 Pod heartbeat/readiness |

轻量 DTS 镜像不包含上述五个进程、Node、两个前端、教师 NestJS 或 Nginx，只以非 root
Python PID 1 运行 `run_dts_ingest.py`，负责 DTS Avro 消费、接入状态事务、数据库位点、事务后
ACK 和 23/55 字段宽表投影。脚本自身处理 SIGTERM/SIGINT，并通过 Pod 本地 heartbeat/readiness
执行 Docker HEALTHCHECK。

运营端与教师端仍是两套独立 HTTP 服务，只在 `application` Profile 共享 Pod。运营、教师、SourceWide
数据库角色以及两套
API 路由和认证逻辑不合并。FastAPI、NestJS、Nginx 或积分 Worker 任一非零退出，s6 都会
终止整个容器，让 Kubernetes 重建完整 Pod。

因此 DTS 不能把密码注入 `application` 项目。完整 application 镜像内的多个业务进程仍以
同一 UID `1001` 运行，因此其中一个进程被利用后可能读取同 Pod 的数据库、JWT、OSS 或邮件
凭据；海外和国内 DTS 则通过独立项目、轻量镜像、独立密钥集合和强制数据中心放置与 application
隔离。两个 DTS 项目也不得合并，因为 broker、消费组、账号、密码、位点和数据合规边界不同。

## 多副本执行模型

每个 application Pod 都启动相同的五个业务进程，不再为积分或 SourceWide Worker 新建
Gaea 项目，也不按副本注入不同配置：

- FastAPI、教师 Nginx 和 NestJS 都可以横向承接 HTTP 请求；登录会话、任务、积分和上传元数据
  的事实源在 PostgreSQL，不依赖某一 Pod 内存；
- 每个 `score-settlement` 候选进程使用独立 PostgreSQL 会话竞争同一 session advisory lock。
  同一数据库同时只有持锁者结算，其他 Pod 是 standby；连接断开时锁自动释放，standby 在下一
  轮轮询接管；
- 每个 `source-wide` 候选进程使用另一把 session advisory lock；leader 消费
  `source_wide.changed.v1`，standby 只做数据库接管探测。两个 Worker 的执行权互不混用；
- 教师端全局通知、工单清理等调度器通过 `tide.job_leases` 竞争有期限租约；照片处理使用数据库
  行级认领与处理租约。`BACKGROUND_JOBS_ENABLED=true` 可以在所有副本保持一致；
- RollingUpdate 期间旧、新 Pod 可以短暂并存。数据库选主、租约、行锁、幂等键与唯一约束负责
  保持业务逻辑单活或安全并行，因此不再要求 `Recreate`，也不需要发布前缩容到 0。

这里的“单活”只指某项后台逻辑的当前执行权，不等于 Pod 单副本。积分选主连接必须直连
PostgreSQL 或使用 session pooling；transaction pooling 不能承载 session advisory lock。

## 不可变运行约束

- Gaea 应用建议从 `2` 个副本开始，可以设置为 `2` 或更高，并使用 `RollingUpdate`。自动伸缩
  也必须保留至少 2 个副本，并先按“每 Pod 数据库连接上限 × 最大副本数”核对公共 PG 配额。
- `3000` 已由 application 镜像强制绑定 `127.0.0.1`，不得再配置 Ingress、SLB 或 Service 端口；
  教师 API 只能经 `8080/api/*` 访问。
- Alembic 和教师端 migration 都是发布前独立作业，不能放进 Pod 启动流程。
- application 单 Pod 不代表跨服务共用数据库账号：运营 API、积分和 SourceWide 计算统一使用
  `tit_growth_app`；教师端两个连接池统一使用 `tit_teacher_crud`；迁移和只读契约探针
  统一使用现有管理账号 `tide_sys_admin`。
- Gaea 高级设置必须允许 root PID 1 启动 `/init`；s6 随后把业务进程降权到 UID `1001`。
  如果平台强制 `runAsNonRoot`，该镜像会在启动阶段失败。
- 多副本不得使用各 Pod 独立的本地上传目录。私有文件优先使用 OSS；确需
  `FILE_STORAGE_PROVIDER=LOCAL` 时，所有 Pod 必须把同一块 `ReadWriteMany (RWX)` 共享卷挂到
  `/var/lib/tide`，并设置 `fsGroup=1001` 或预先授予 UID/GID 1001 写权限。RWO 或每 Pod
  独立 PVC 都不满足跨副本读取、删除和重试语义。
- 单 Pod 同时运行 Python API、NestJS、Nginx 与后台进程，TEST 建议从 4 GiB 内存起步，
  再按每个 Pod 的实际 RSS、连接数和延迟收缩；这不是容量验收结果。

副本数、RollingUpdate 参数和 PVC access mode 都是 Gaea/Kubernetes 的运行状态，Dockerfile
不能替平台设置或证明它们。每次发布必须从 Gaea 现场读回，不能只凭本仓库文档判定已生效。

## 多副本存储边界

| 状态 | 多副本要求 | 原因 |
|---|---|---|
| 私有上传 `/var/lib/tide/uploads` | 首选 OSS；LOCAL 只允许所有 Pod 共享同一 RWX 卷 | 上传、下载、工单清理可能落到不同 Pod |
| 视频预热账本 `/var/lib/tide/video-prefetch-runs` | 执行预热脚本时必须使用同一 RWX 卷 | 幂等记录和文件锁必须跨执行节点可见；OSS 对象存储不替代该账本 |
| Worker heartbeat `/tmp/tit-score-worker-heartbeat` | 必须保持 Pod 本地，禁止放入共享卷 | 健康检查要证明本 Pod 的候选进程存活，不能借用 leader 的 heartbeat |
| SourceWide heartbeat/readiness `/tmp/tit-source-worker-*` | 必须保持 Pod 本地，禁止放入共享卷 | 同时证明候选进程存活且能以预期专用账号访问数据库 |
| Nginx 临时目录 `/tmp/tide-nginx` | Pod 本地临时空间 | 不承载业务事实 |

当前五个常驻进程不会自动执行视频预热脚本；预热是受控发布动作。若从一次性 Job 或运维
终端执行，仍必须复用同一个持久化 RWX 状态目录。发布前至少用两个实际 Pod 做交叉验收：
Pod A 上传、Pod B 下载，Pod B 删除、Pod A 读回失败；视频预热的相同幂等键只能创建一次。

## 构建参数

教师前端固定使用同源相对路径 `/api/*`，API Origin 不再是镜像构建事实。只有公共素材地址
仍由 Vite 在构建时写入：

| 参数 | TEST 默认值 | 说明 |
|---|---|---|
| `VITE_PUBLIC_ASSET_BASE_URL` | `https://tide-media.51talkjr.com` | 已发布教师素材的 HTTPS 基址 |

素材默认值固化在 Dockerfile，Gaea 无额外 build args 时可以直接构建。预发布和生产若使用
不同素材 Origin，必须覆盖该参数并重建镜像。教师域名由 DNS/Ingress 和后端运行变量决定；
Nginx 按当前请求 Host 提供页面、把 `/api/*` 代理到本 Pod NestJS，并运行时生成绝对分享
图片地址，因此修改教师域名不需要重建镜像。Docker 构建会额外加载 NestJS 与原生 `sharp`
模块并执行 `nginx -t`，用来
尽早暴露 Alpine ABI 或 Nginx 配置不兼容；仍需 Gaea 的真实冷构建作为最终证据。

## Gaea 端口与域名

在唯一 application 应用（现有 Gaea 项目 `tida-camp`、PRE 应用 `pre-tida-camp`）中配置两个端口：

| 容器端口 | 访问方式 | TEST 域名 | 用途 |
|---:|---|---|---|
| `8010` | Ingress / HTTP(S) | `https://tide-camp-ops.test.51talk.biz` | 运营端页面和 API |
| `8080` | Ingress / HTTP(S) | `https://tide.51talk.com` | 教师端页面和同源 API |

Gaea 当前端口管理支持同一应用配置多个容器端口。不要把两个域名都指向同一个端口：两端
都有 `/api/*`，按端口分流才能避免路径冲突。`EXPOSE` 只描述镜像端口，不会替代平台上的
两条域名配置；发布后必须从两个外部 HTTPS 域名分别做 smoke test。

## 聚合健康检查

镜像的 `HEALTHCHECK` 每 30 秒依次验证：

1. `8010/api/health`：运营 API、运营静态页面启动边界和数据库；
2. `8080/healthz`：教师 Nginx 与静态产物；
3. `8080/health/ready`：经 Nginx 代理访问教师 API，并检查两条数据库读取链；
4. 本 Pod 的 `/tmp/tit-score-worker-heartbeat`：积分候选进程持续刷新；leader 与 standby
   使用相同的进程存活判定；
5. 本 Pod 的 SourceWide heartbeat 与 readiness：进程持续运行，且最近一次数据库身份校验、
   选主或 leader ping 成功；随后只读检查 `source_wide.changed.v1` Outbox，不允许存在
   `DEAD_LETTER`，也不允许已发生过失败（`attempt_count > 0`）且超过 `available_at`
   900 秒仍为 `PENDING`。

未持有积分 advisory lock 或教师后台租约是正常 standby 状态，不得导致本 Pod 不健康。
因此 Pod 显示健康只表示五个进程和对应数据库就绪，不表示该 Pod 当前持有后台执行权，也
不代表教师登录、九项任务、积分回写、外部素材、真实通知或完整业务验收已经完成。

## 运营端运行变量

以下变量由 Gaea 配置或密钥管理注入；密钥不得写进镜像、Git、业务 payload 或日志。

| 变量名 | 必填 | 建议值/默认值 | 说明 |
|---|---|---|---|
| `APP_ENV` | 是 | `production` | 启用运营 API 的生产安全校验 |
| `TIT_MIGRATION_MODE` | 是 | `false` | 常驻进程不得执行 Alembic |
| `DATABASE_URL` | 是 | 无 | `tit_growth_app` 的 PostgreSQL URL；固定 `tide_system_test` PRE 专线使用 `sslmode=disable`，正式使用 `verify-full` |
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

application 镜像还读取 Gaea 注入的 `MEMORY_SIZE`，只用于把 Nginx worker 数渲染到 2–16 的有界
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
| `TIT_SCORE_WORKER_HEARTBEAT` | 否 | `/tmp/tit-score-worker-heartbeat` | 镜像已固化为 Pod 本地路径，Gaea 不要覆盖或挂载到共享卷 |

Worker 与运营 API 共用 `DATABASE_URL` 对应的受限运营运行角色，但不使用迁移角色。
每个 Pod 都运行
`settle_shared_task_scores.py --watch --max-events 25 --interval-seconds 3`，由 PostgreSQL
session advisory lock 选出当前 leader；standby 不执行结算，但继续刷新本 Pod heartbeat。

## SourceWide Worker 进程级变量

该 Worker 与运营 API 共用 `tit_growth_app`，启动时会核对目标库、源表只读和派生表写权限；
身份不符合即非零退出，由 s6 终止 Pod。真实密码只由 Gaea 密钥管理注入。
收到 SIGTERM 后会完成当前事务再退出；若 25 秒内仍未结束，s6 强制终止连接，让 PostgreSQL
回滚未提交事务，避免超过 Kubernetes 常见的 30 秒终止宽限期。

| 变量名 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `TIT_SOURCE_WIDE_ENABLED` | 否 | `true` | 仅首次 DTS 投影排空窗口可设为 `false`；只接受小写 `true`／`false`，非法值失败关闭 |
| `TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED` | 否 | `false` | 只接受小写 `true`／`false`；`false` 继续刷新积分与当前门槛，但禁止出营/金牌资格首次变为已获得 |
| `TIT_SOURCE_WORKER_EXPECTED_DATABASE` | 是 | 无 | 固定目标库名，必须与 URL 一致 |
| `TIT_SOURCE_WORKER_DB_POOL_SIZE` | 否 | `1` | Worker 连接池上限 |
| `TIT_SOURCE_WORKER_DB_MAX_OVERFLOW` | 否 | `0` | Worker 溢出连接 |
| `TIT_SOURCE_WORKER_DB_STATEMENT_TIMEOUT_MS` | 否 | `60000` | Worker SQL 超时 |
| `TIT_SOURCE_WORKER_DB_APPLICATION_NAME` | 否 | `tit-growth-source-worker` | PostgreSQL 连接标识 |
| `TIT_SOURCE_WORKER_MAX_PENDING_AGE_SECONDS` | 否 | `900` | SourceWide 健康检查允许已到执行时间的 `PENDING` 事件继续滞留的最大秒数，必须大于 0 |

SourceWide 直接复用运营 `DATABASE_URL`；它与运营 API 属于同一后端信任边界，不再额外
注入一份数据库密码。`TIT_SOURCE_WIDE_ENABLED=false` 只在 `application` Profile 暂停
SourceWide s6 服务；聚合健康检查仍检查运营 API、教师 API 和积分 Worker，但会有意识地
跳过 SourceWide heartbeat/readiness。DTS 项目不读取该变量，因此接入和宽表投影可继续追平。
该开关不是常态运行模式，也不是业务资格规则；只用于首次投影排空，恢复 `true` 并确认
SourceWide heartbeat/readiness 和 Outbox 排空后，才可进行积分、任务和当前门槛链路验收。
不可逆资格另由 `TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED` 独立失败关闭。当前预发布
必须保持 `false`：积分、任务分和 `graduation_criteria_met / gold_criteria_met` 仍会更新，
但任何尚未获得的出营或金牌资格都不会首次变为 `true`；既有已获得资格不受影响。只有后续
业务终态/双流水位门禁完成并单独验收后，才允许明确改为 `true`。空值、大小写变体、`1`、
`yes` 等均为非法配置，应用 API 与两个 Worker 会失败关闭。
SourceWide 健康探针会使用同一个受限数据库身份直接读取 Outbox：任一
`source_wide.changed.v1` 事件进入 `DEAD_LETTER`，或已经失败过的事件在 `available_at`
到期后继续 `PENDING` 超过上述阈值，整个 Pod 即不健康。首次解暂停时积压但尚未尝试的
`attempt_count=0` 事件允许 Worker 追平；尚未到 `available_at` 的正常退避事件也不计为
超龄。数据库探测失败同样失败关闭；重启不会清除终态事件，必须先检查 `last_error`、修复
源数据或投影问题并按运维流程重新入队，不能用反复重启掩盖毒事件。

## DTS ingest 独立 Profile

国内和海外分别建立一个独立 Gaea 项目，两个项目都选择 `dts-ingest` 构建模块并固定
`TIT_PROCESS_PROFILE=dts-ingest`。海外项目选择新加坡数据中心并声明
`TIT_DTS_EXECUTION_REGION=sg`；国内项目选择中国大陆数据中心并声明
`TIT_DTS_EXECUTION_REGION=cn`。区域声明与 `TIT_DTS_SOURCE_REGION` 不匹配时进程失败关闭，但它
不能替代在 Gaea 现场核对项目数据中心。`tida-camp-dts-dom` 必须在中国大陆集群发布，并在新 Pod
上读回实际数据中心；不能只通过修改环境变量宣称完成国内部署。Gaea 仍会为两个项目分别构建和推送内容相同的轻量镜像；
模块选择本身不提供跨项目 digest 复用。每个项目只消费一条订阅，不能在同一进程混放两套
broker、消费组或 SASL 密码。两个项目都不配置运营、教师、JWT、OSS 或邮件密钥；默认
application 项目也不配置任何 DTS 变量。

当前消费者固定 partition 0，每个 DTS 项目先使用 1 个副本。两个项目可以同时把各自事件写入
同一个 `tide_system_test.public`：接入幂等键包含 `source_region + topic + partition + offset`。
国内消息仍在国内容器内时，代码必须在构造任何海外 PostgreSQL SQL 参数前删除原始学生 ID，
并使用仅注入国内项目且由 Gaea 掩码保存的 `TIT_DTS_DOM_STUDENT_HMAC_PASSWORD` 生成
`dom:v1:<HMAC-SHA256>`；海外项目、海外数据库、日志和错误 payload 都不得持有该密钥或原始国内
学生 ID。稳定 token 用于师生去重、收藏/拉黑归因和课程宽表关联，但仍属于伪名数据，必须继续
限制访问。若合规边界连稳定 token 都不允许跨境，则当前 23/55 投影协议不适用，必须改为国内
状态库完成按教师聚合，只向海外发送不含个体稳定标识的指标结果。
国内密钥首次启动会登记单向 fingerprint，后续不匹配即退出；不得直接修改密钥值“轮换”，否则
同一学生会被拆成多个身份。轮换必须单独评审 token 版本和存量迁移。

首次追平阶段两个项目都必须关闭投影；激活后只允许海外项目启用全局宽表投影，国内项目固定
`TIT_DTS_PROJECTION_ENABLED=false` 并只做 ingest。海外项目必须先通过数据库激活门禁，并持有
全局 PostgreSQL session advisory lock；国内项目误开启投影会在启动时失败关闭。每个 DTS Pod
的数据库池固定为 2 条连接，其中
1 条由投影锁专用连接持续占用，另 1 条供接入事务和投影事务串行复用。PostgreSQL
`application_name` 分别为 `tit-dts-ingest-ovs` 和 `tit-dts-ingest-dom`，便于现场区分连接。

两套非敏感订阅配置如下；对应的生产安全基线文件是
`backend/.env.dts-ingest.ovs.production.example` 和
`backend/.env.dts-ingest.dom.production.example`。当前固定 `tide_system_test` PRE 专线数据库的
临时 `ssl=off` 例外由国内、海外项目复用
`backend/dts-ingest.pre-ssl-off.env.example` 中的两项覆盖，不修改生产基线。专线限制网络路径但
不加密 PostgreSQL 流量；2026-08-13 DMS 现场已确认 `SHOW ssl=off` 且当前会话未使用 TLS，
正式环境仍固定 `verify-full`：

| 变量名 | 海外项目 | 国内项目 |
|---|---|---|
| `TIT_DTS_SOURCE_REGION` | `ovs` | `dom` |
| `TIT_DTS_EXECUTION_REGION` | `sg` | `cn` |
| `TIT_DTS_BROKER_URL` | `100.103.7.163:18003` | `dts-cn-beijing-vpc.aliyuncs.com:18003` |
| `TIT_DTS_TOPIC` | `ap_southeast_1_vpc_pc_gs5986x4885426aej_dba_tide_source_ovs_version2` | `cn_beijing_vpc_pc_2ze5w28lmdr8f626y_dba_tide_source_dom_version2` |
| `TIT_DTS_GROUP_ID` | 海外订阅“数据消费”页生成的消费组 ID（sid） | 国内订阅“数据消费”页生成的消费组 ID（sid） |
| `TIT_DTS_ACCOUNT` | `titconsumeovs` | `titconsumedom` |
| `TIT_DTS_START_AT` | `2026-08-10T14:16:00+08:00` | `2026-08-12T16:30:00+08:00` |
| `TIT_DTS_DOM_STUDENT_HMAC_PASSWORD` | 禁止配置 | CSPRNG 生成的 32-byte 密钥，精确编码为 64 位小写 hex；必须使用该含 `PASSWORD` 的名称触发 Gaea 敏感值掩码 |

每个项目还需要以下共同变量：

| 变量名 | 必填 | TEST 值/约束 | 说明 |
|---|---:|---|---|
| `TIT_PROCESS_PROFILE` | 是 | `dts-ingest` | 只启动 DTS 业务进程 |
| `TIT_DTS_STARTUP_RETRY_SECONDS` | 否 | `15` | 仅 `--watch` 容器启动期使用；以该值起步、2 倍退避并在 60 秒封顶，默认 `15/30/60`，允许范围 `(0,60]` |
| `TIT_DTS_PASSWORD` | 是 | 各自 Gaea 密钥 | 只用于本项目对应订阅的 DTS SASL |
| `TIT_DTS_COHORT_START` | 否 | `2026-08-13` | 北京时间新教师 cohort 起点，按 `dom_teacher.status_on_time` 日期筛选；两项目必须一致 |
| `TIT_DTS_COHORT_END_EXCLUSIVE` | 否 | 空 | 开放式人群；需要封闭批次时才设置不含当天的结束边界 |
| `TIT_DTS_PROJECTION_ENABLED` | 否 | `false` | 国内项目始终为 `false`；双流追平并通过激活门禁后，只允许海外项目改为 `true` |
| `TIT_DTS_PROJECTION_MAX_ATTEMPTS` | 否 | `8` | 同一脏键周期的投影尝试上限，范围 `1–100`；默认约 15 分钟退避窗口后失败关闭 |
| `TIT_DTS_ACTIVATION_AT` | 投影开启时 | 显式带时区时间 | 两条订阅都必须追平到该 source time；两个项目使用同一值 |
| `TIT_DTS_REQUIRED_OVS_TOPIC` | 投影开启时 | 海外 topic | 激活门禁核对海外 partition 0 数据库 checkpoint |
| `TIT_DTS_REQUIRED_DOM_TOPIC` | 投影开启时 | 国内 topic | 激活门禁核对国内 partition 0 数据库 checkpoint |
| `TIT_DTS_INGEST_DB_HOST` | 是 | `tide-system.rwlb.singapore.rds.aliyuncs.com` | 不含端口或 scheme |
| `TIT_DTS_INGEST_DB_PORT` | 否 | `5432` | PostgreSQL 端口 |
| `TIT_DTS_INGEST_DB_SSLMODE` | 否 | `verify-full`；固定 PRE 专线端点可覆盖为 `disable` | 国内、海外 PRE 使用相同数据库传输例外；正式环境固定 `verify-full` |
| `TIT_DTS_ALLOW_INSECURE_DB` | 否 | `false`；固定 PRE 专线端点与 `disable` 同时覆盖为 `true` | 必须和 `disable` 成对配置；没有固定 PRE 目标时禁止非 TLS 连接 |
| `TIT_DTS_INGEST_DB_PASSWORD` | 是 | 各项目 Gaea 密钥 | `tit_dts_ingest_runtime` 的数据库密码，不得复用 DTS 密码 |

`TIT_DTS_GROUP_ID` 必须复制各自 DTS 订阅“数据消费”页的系统生成 ID（sid），不能填写可编辑的
消费组名称。运行时会在任何网络连接前拒绝仓库曾误发的名称占位值；SASL 用户名仍由代码按
`<TIT_DTS_ACCOUNT>-<TIT_DTS_GROUP_ID>` 生成。

数据库名、Schema 和角色在代码中失败关闭为
`tide_system_test / public / tit_dts_ingest_runtime`。SSL 默认 `verify-full`。以下明文例外只适用于
国内、海外 DTS 的固定 PRE 专线目标：只有同时设置
`TIT_DTS_INGEST_DB_SSLMODE=disable` 与 `TIT_DTS_ALLOW_INSECURE_DB=true`，且目标精确等于已批准的
`tide-system.rwlb.singapore.rds.aliyuncs.com:5432 / tide_system_test`，才允许当前 PRE 例外；其他
模式、端点或缺少显式授权都会在连接前拒绝启动。每条新建的 PostgreSQL 物理连接都会通过
`pg_stat_ssl` 核验当前会话的实际 TLS 状态，并同时读取服务端 `current_setting('ssl')`；临时 `disable` 例外还会在每次连接池 checkout 时复核。服务端一旦启用 TLS，遗留的 `disable` 配置会立即失败关闭，长期持有的投影锁会话也会在每批投影前复核。此时必须删除两项 PRE 覆盖并恢复生产基线
`verify-full/false`。国内项目即使使用 PRE 例外，仍必须位于中国大陆、只在国内持有 HMAC 密钥且
固定关闭投影；数据库传输例外不放宽这些边界。该例外不得复制到生产。正式业务库若不再是当前固定 test 目标，还必须同步
修改数据库身份契约、迁移和 ACL 并重新验收，不能只改 SSL 变量。revision
`20260813_60_dom_privacy` 必须在 `20260812_59_simple_acl` 之后由
`tide_sys_admin` 应用；运行账号没有建表权限，接入状态的删除/回退由 Trigger 拒绝。
最终再应用 `20260814_61_teacher_copy`，该迁移只更新经审核的教师文案。

DTS heartbeat/readiness 位于每个项目 Pod 自己的 `/tmp/tit-dts-ingest-*`。进程启动时先删除
上一进程留下的两个文件；目标数据库连接/身份/Schema/ACL、bootstrap DNS 解析、当前 Pod 对解析
结果执行的 5 秒共享连接预算无凭据 TCP 探针，以及 Kafka SASL、topic、partition 0、初始位点的
只读探针全部通过后，才写本次进程的
`readiness=ready`。TCP 探针不收发应用数据；TCP 失败输出 `DTS_BROKER_TCP_*`，TCP 成功会先打印
`DTS_STARTUP_PROBE/broker_tcp status=ok`，之后 Kafka 请求超时输出
`DTS_BROKER_KAFKA_REQUEST_TIMEOUT`。Kafka 探针先输出脱敏的客户端契约摘要（客户端版本/API 自动协商模式、
SASL 协议、partition 和有界超时），再按实际位点路径输出不含连接身份的固定阶段；阶段来自
`consumer_open`、`bootstrap_auth`、`topic_metadata`、`partition_check`、
`advertised_broker_auth`、`group_coordinator`、`coordinator_auth`、`offset_fetch`，并按实际位点
路径继续输出 `offsets_for_times`、`beginning_offsets`、`end_offsets`，均带 `begin/ok/fail`。
`topic_metadata` 是在失败 Pod 同一网络命名空间中执行的、与 `kcat -L -t <topic>` 同类语义的
单 Topic Metadata 请求，随后由 `partition_check` 验证配置 partition 0；实现复用正式 kafka-python 客户端，不创建含密码的 kcat 配置
文件。`consumer_open` 会对 bootstrap 连接真实发送 `ApiVersions` 自动协商客户端兼容协议，再完成
该连接的 SASL；日志中的协商结果只是 kafka-python 选择的兼容版本，不是 DTS Broker 精确版本。
后续 `bootstrap_auth` 复核已认证连接，通常显示连接复用。协议分段适配器只接受锁定的
kafka-python 2.2.20，依赖漂移会在发送 Kafka 凭据或协议请求前失败关闭。
连接阶段输出固定状态路径以及 TCP、协议版本、SASL 的安全布尔证据。完成或失败阶段带
`elapsed_ms`；失败只输出白名单 `error_type`、稳定
`error_code` 和 `retriable`；其中 Kafka 阶段的 `retriable` 先表示 kafka-python 对该错误类别的同请求
重试语义，容器进程还会再经过自己的暂态白名单才决定是否重试。上述启动诊断不输出 endpoint、账号、
消费组、密码、异常正文或堆栈。代码会
隔离 kafka-python 原生日志，只保留这些结构化诊断，因为 SASL 调试报文可能包含认证字节。
同理，不得把 `SASL_PLAINTEXT/PLAIN` 会话的完整 `tcpdump -w` 抓包直接上传或发群：payload
包含可还原的消费用户名与密码。网络侧优先只提供 TCP flags/长度/时序；若经安全负责人批准必须
移交完整 pcap，只能走受控通道，并在抓包后立即轮换对应 DTS 消费密码。
`consumer_open=ok` 表示 bootstrap 的 ApiVersions 响应和 SASL 已成功，但不证明目标 Topic、
advertised leader、协调器或 offset 可用，后续远端阶段仍须逐项通过。
消费者手工绑定 partition 0，不执行 `JoinGroup`；不能用“未加入消费组”替代 SASL、FindCoordinator
或 OffsetFetch 的阶段判断。
Kafka 位点探针按同一 15 秒 deadline 收紧剩余请求超时；这是 kafka-python 阻塞 SASL/DNS 调用
协作遵守的预算，不是可强制终止进程的绝对 wall-clock 上限。关闭连接另有 1 秒上限；
探针不读取消息、不写目标库、不提交 offset。`--watch` 容器遇到明确白名单内的暂态网络、Kafka
连接/超时、数据库连接或激活依赖未就绪时，不再退出制造 CrashLoop，而是在同一 PID 内按
`TIT_DTS_STARTUP_RETRY_SECONDS` 起步、2 倍退避并在 60 秒封顶（默认 `15/30/60`）；每轮都关闭失败连接池并使用全新
数据库连接、Kafka 客户端和投影锁会话重跑完整门禁。重试期间 readiness 与 heartbeat 都不存在，
因此 Pod 必须保持 NotReady/不健康，不能把“进程仍活着”解释成链路可用。SASL/Topic/Group/Cluster
授权、协议不兼容或配置错误，以及数据库身份、Schema/ACL、隐私/HMAC/offset 不变量失败仍立即非零退出；
非 `--watch` 诊断命令也保持单次失败退出。国内进程只有在探针成功后，才会在写 readiness 前幂等
登记一条仅含契约版本与 HMAC key fingerprint 的受限状态行；它不包含密钥或学生标识，fingerprint
不匹配会失败关闭。该整套门禁在每次容器进程启动/重启以及每次暂态重试时执行，不在镜像构建或周期 healthcheck
中重复执行。镜像 HEALTHCHECK 表达 readiness 与消费进展，不是独立 liveness；发布时
必须读回 Gaea 实际 `startupProbe/readinessProbe/livenessProbe`，不得让 liveness 因启动期缺少健康文件
而杀掉仍在安全重试的进程。首轮以及后续消费循环成功完成后才
刷新 heartbeat，其中包含本轮接入和宽表投影计数。两者同时健康只证明服务具备消费条件并持续
运行，不证明至少消费到一条业务消息或字段值已通过对账。国内项目的起始边界已固定为
`2026-08-12T16:30:00+08:00`；任一项目未注入
本项目 `TIT_DTS_PASSWORD` 时失败关闭。`TIT_DTS_START_AT` 是首次回放边界，不是 Pod 启动
时间；海外、国内起点均早于 `2026-08-13` cohort。两条链路先追平到同一激活时刻并完成静态
投诉分类字典装载/引用完整性检查。开启投影时，代码要求两地区指定 topic 的 partition 0
checkpoint 均存在且 `source_timestamp >= TIT_DTS_ACTIVATION_AT`，要求未删除的
`dom_complaint_cate` 字典非空，并拒绝任何未删除投诉引用字典中不存在的 `category_ids`；
三项新变量在投影关闭时均不读取。门禁通过且取得全局投影锁后，国内和海外当前态才通过
同一脏键机制汇合计算。暂态依赖缺失按指数退避；同一脏键达到
`TIT_DTS_PROJECTION_MAX_ATTEMPTS` 后不再写成功 heartbeat，DTS 进程以稳定错误
`DTS_WIDE_PROJECTION_RETRY_EXHAUSTED` 退出。脏键记录保留，重启后仍失败关闭；只有该键收到
新的源事件并重置为新一轮 `PENDING` 后才恢复，不能用 Pod 重启掩盖永久毒键。

## 教师端运行变量

| 变量名 | 必填 | TEST 建议值/默认值 | 说明 |
|---|---|---|---|
| `TIDE_TEACHER_NODE_ENV` | 否 | `test` | 仅办公室 TEST 可设 `test`；未设置时失败关闭地使用 `production` |
| `TIDE_TEACHER_HOST` | 是 | 教师域名（不带 scheme） | 聚合健康检查的 Host，例如 `tide.51talk.com` |
| `TIDE_TRUSTED_PROXY_CIDRS` | 否 | 复用 `TIT_TRUSTED_PROXY_IPS` | 教师入口不同时再覆盖；拒绝全网段和非法值 |
| `TRUST_PROXY_HOPS` | 是 | `1` | 只信任本 Pod 的教师 Nginx 一跳 |
| `CORS_ORIGINS` | 是 | 教师域名 | 教师页面与 API 同源 |
| `DATABASE_REQUIRED` | 是 | `true` | 禁止无数据库假启动 |
| `TIDE_DATABASE_URL` | 是 | 无 | `tit_teacher_crud` 连接；固定 `tide_system_test` PRE 专线使用 `sslmode=disable`，正式必须 `verify-full` |
| `SHIWEN_READ_DATABASE_URL` | 是 | 无 | 教师来源读取连接；与业务连接同用 `tit_teacher_crud`，仍保留独立连接池并使用相同 SSL 模式 |
| `DATABASE_MAX_CONNECTIONS` | 是 | `5` | 每条教师数据库链各自的池上限；两条链合计最多 10 |
| `DATABASE_CONNECTION_TIMEOUT_MS` | 是 | `3000` | 建连超时；需小于聚合探针超时 |
| `DATABASE_STATEMENT_TIMEOUT_MS` | 是 | `10000` | 教师 SQL 超时 |
| `SHIWEN_READ_MODE` | 是 | `DIRECT_TABLES` | 公司测试现有读取模式 |
| `SHIWEN_ALLOW_TIDE_FIXTURE_FALLBACK` | 是 | `false` | 禁止用 fixture 冒充真实数据 |
| `SHIWEN_TEACHER_IDENTITY_VIEW` | 条件必填 | 无 | `VIEWS` 模式时必填 |
| `PUBLIC_APP_URL` | 是 | 教师 HTTPS Origin | 邮件与深链基址 |
| `PUBLIC_API_URL` | 是 | 教师 HTTPS Origin | 文件与 API 公共基址；只填 Origin，不附加 `/api` |
| `KUOZHI_LOGIN_URL` | 是 | `https://edu.51talk.com/login/ticket` | 阔知免登票证入口 |
| `KUOZHI_COURSE_URL` | 是 | `https://edu.51talk.com` | 阔知课程页 Origin |
| `KUOZHI_APP_KEY` / `KUOZHI_SECRET_KEY` | 是 | 密钥管理注入 | 只允许教师后端持有，禁止进入前端和日志 |
| `KUOZHI_DETAIL_URL` | 是 | `http://edu.51talk.me/api/me/TeacherCourseDetail` | 阔知课程进度详情接口 |
| `KUOZHI_DETAIL_HOST_IP` | TEST 必填 | `172.16.0.54` | 仅后端覆盖详情域名解析；需验证 Gaea 网络可达 |
| `DATA_HASH_SECRET` | 是 | 密钥管理注入 | 至少 32 字符 |
| `AUTH_JWT_SECRET` | 是 | 密钥管理注入 | 至少 32 字符 |
| `TEACHER_AUTH_MODE` | 是 | `HYBRID` | 当前保留旧登录；正式切换时改为 `CRM_SSO_ONLY` |
| `CRM_SSO_JWT_SECRET_CURRENT` | 启用 SSO 时必填 | 密钥管理注入 | CRM/TIDE 共享 HS256 密钥，至少 32 字符，不得写入镜像或日志 |
| `CRM_SSO_JWT_SECRET_PREVIOUS` | 否 | 无 | 密钥轮换过渡期使用，完成轮换后清空 |
| `CRM_SSO_ISSUER` / `CRM_SSO_AUDIENCE` | 是 | `crm` / `tide` | 必须与 CRM JWT 一致 |
| `CRM_SSO_MAX_TTL_SECONDS` | 是 | `120` | CRM JWT 最大存活时间 |
| `CRM_SSO_CLOCK_TOLERANCE_SECONDS` | 是 | `30` | 双方时钟偏差容忍秒数 |
| `CRM_SSO_EXCHANGE_TTL_SECONDS` | 是 | `60` | 前端一次性兑换码有效期 |
| `CRM_ENTRY_URL` | SSO-only 必填 | 无 | 教师直接访问 TIDE 时展示的 CRM 返回入口 |
| `FILE_STORAGE_PROVIDER` | 是 | `OSS` | 多副本首选 OSS；LOCAL 仅在所有 Pod 共享同一 RWX 卷时允许 |
| `LOCAL_FILE_STORAGE_DIR` | 否 | `/var/lib/tide/uploads` | LOCAL 模式必须挂载同一 `ReadWriteMany` 共享卷 |
| `OSS_REGION` / `OSS_ENDPOINT` / `OSS_BUCKET` | 条件必填 | 无 | `FILE_STORAGE_PROVIDER=OSS` 时必填 |
| `OSS_ACCESS_KEY_ID` / `OSS_ACCESS_KEY_SECRET` | 条件必填 | 密钥管理注入 | OSS 凭据 |
| `MULTIPART_UPLOAD_MAX_CONCURRENCY` | 是 | `4` | 1–16；提高前先做 3×8 MiB 并发验收 |
| `BACKGROUND_JOBS_ENABLED` | 是 | `true` | 所有副本保持一致；全局调度由 `tide.job_leases` 单活，照片由行级租约认领 |
| `BACKGROUND_JOB_LEASE_MS` | 否 | `180000` | 教师全局后台任务租约；故障接管上限受该值影响 |
| `TASK_CATALOG_PUBLIC_WRITE` | 是 | `false` | 教师端不得改共享任务目录 |
| `VIDEO_PREFETCH_STATE_DIR` | 否 | `/var/lib/tide/video-prefetch-runs` | 执行预热发布脚本时必须指向所有执行节点共用的 RWX 状态目录 |
| `MAIL_DELIVERY_PROVIDER` | 否 | `UNAVAILABLE` | 启用公司邮件时还需 `MAIL_API_URL/MAIL_API_ACCESS_KEY` |
| `MODELARK_ENABLED` | 否 | `false` | 启用 BytePlus ModelArk Responses API；启用时必须注入 `ARK_API_KEY` |
| `MODELARK_BASE_URL` | 否 | `https://ark.ap-southeast.bytepluses.com/api/v3` | 只填写 API Base URL，不追加 `/responses` |
| `MODELARK_MODEL` | 否 | `seed-2-0-lite-260228` | BytePlus ModelArk 模型 ID |
| `ARK_API_KEY` | 条件必填 | 密钥管理注入 | 仅教师后端模型调用使用，不进入镜像或业务表 |

application 镜像会把教师 `BIND_HOST` 固定为 `127.0.0.1`、`PORT` 固定为 `3000`，并把单文件
`FILE_UPLOAD_MAX_BYTES` 固定为 10 MiB，以保持在 Nginx 26 MiB 请求上限内；这些值不要在
Gaea 另行配置。教师 Nginx 只从 `TIDE_TRUSTED_PROXY_CIDRS`（未设时复用
`TIT_TRUSTED_PROXY_IPS`）指定的入口解析 `X-Forwarded-For`。仍需平台确认 Ingress 会覆盖
或追加而不是原样透传客户端伪造头。

按上述建议值，教师两条池最多 10 连接；再加运营 `2 × (5 + 2)`、积分 Worker 的
选主／结分连接 1 条和 SourceWide Worker 1 条，单 Pod 最坏约 26 条连接。`N` 个副本按
`N × 26` 预留并给迁移、
人工诊断留余量；自动伸缩上限必须受公共 PG 连接额度约束，不能只看 Pod 是否运行。

办公室公共 PG 若只能使用非严格 TLS，可以在 TEST 环境使用现有
`COMPANY_TEST_DATABASE_ENABLED=true` 适配层，并注入 `TIDE_ADMIN_DB_HOST/PORT/NAME`、
`TIDE_APP_DB_USER/PASSWORD`、`TIDE_ADMIN_DB_SSLMODE`。该适配会让教师写入与来源读取暂时复用
一个测试账号，只能用于受控 TEST；预发布和生产必须恢复独立 URL 与独立角色。

其余可选邮件、OSS、CDN、通知调度、AI Gateway 与文件限制变量，以
`teacher/backend/.env.example` 为完整字段表；启用某项能力时不得依赖代码默认值猜测密钥。

TEST 只验收当前教师绑定的正式阔知课程；不存在示例账号或课程切换开关。若详情接口返回
空课程对象，必须先补齐阔知侧账号课程数据，再继续进度同步验收。

## 构建与本地验证

必须从仓库根目录构建：

```bash
docker build -f gaea/Dockerfile -t tide-camp:gaea .
docker build -f gaea/dts-ingest/Dockerfile -t tide-camp-dts:gaea .
```

Gaea 中现有 application 项目可以暂时保持根构建方式，也可切换 `multi_module/application`；
海外与国内 DTS 项目必须把构建类型切换为 `multi_module` 并都选择 `dts-ingest`。不要在运行
变量区添加所谓“镜像口味”变量，它不会改变 Docker 构建。根 Dockerfile 暂时保留 application
及 DTS Profile 兼容入口，便于尚未切换模块的既有项目回滚；三个项目均完成模块化构建验收后，
再单独决定是否删除该兼容入口。

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
curl -fsS -H 'Host: tide.51talk.com' \
  http://127.0.0.1:8080/healthz
curl -fsS -H 'Host: tide.51talk.com' \
  http://127.0.0.1:8080/health/ready
docker exec tide-camp-gaea-test \
  curl -fsS http://127.0.0.1:3000/health/ready
docker inspect --format '{{.State.Health.Status}}' tide-camp-gaea-test
docker stop tide-camp-gaea-test
```

本地容器验证会真实启动两个 Worker，只能连接隔离测试库。多副本验收还需同时启动至少两个
容器，分别确认 `score-settlement` 和 `source-wide` 只有一个 leader，standby heartbeat/readiness 仍健康；LOCAL 文件模式
必须让两个容器挂载同一个共享测试目录并完成跨容器上传、下载和删除，不得用两个独立目录
冒充 RWX。

## 发布顺序

1. 按跨 Schema 顺序执行 `public 46 → teacher 0028 → public 50 → teacher 0032 → public 54 → teacher 0037 → public 55 → release public 56 → teacher 0038 → release public 57 → teacher 0040 → teacher 0041 → public 59 → public 60 → public 61`，
   先完成 release 内容链到 public 57 / teacher 0041，再应用 ACL/DTS 分支并合并到 public 59，然后应用 public 60 国内学生隐私边界和 public 61 教师文案迁移；
   已批准公司 TEST 库从 public 50 / teacher 0032 继续时，先在仓库根目录用
   `backend/.venv/bin/python backend/scripts/upgrade_company_test_database.py /Git工作区外/company-test-migration.env`
   只读预检；确认备份和维护窗口后才追加
   `--apply --backup-confirmed --maintenance-window-confirmed`。该脚本不发布 Gaea、不重启服务，也不代替后续 teacher 初始化器；
   链内必须先包含 public `20260811_51_g01_tesol_only` / teacher `0033_g01_tesol_only`
   的 G01 TESOL-only 收窄，再包含 public `20260811_54_g04_remove_device_check` / teacher
   `0037_g04_remove_device_check` 的 G04 两模块收敛，并先执行 public
   `20260811_55_source_wide_v12` 再执行 public `20260811_56_p_fb_negative_copy`；当前 G04 不得恢复设备检测步骤。
   确认 public head 为 `20260814_61_teacher_copy`、teacher 账本 head 为
   `0041_crm_sso_hybrid`（包含前序 `0038_personalized_environment_photo`）；其中
   G01 TESOL-only、G04 两模块、G02 原生政策文档与阅读状态、CRM SSO 都必须完成。
   随后执行只读契约探针，并核对个性化任务零分文案、环境拍照步骤与
   `TEACHING_ENVIRONMENT_V1` 审核档案。
2. 配齐统一应用的运营和教师环境变量，确认密钥不在版本化配置中；两个 Worker 复用运营
   `tit_growth_app` 连接，不再注入独立数据库账号。首次 DTS 投影排空前先显式配置
   `TIT_SOURCE_WIDE_ENABLED=false`，并保持
   `TIT_IRREVERSIBLE_QUALIFICATION_GRANTS_ENABLED=false`。
3. 在 Gaea 将统一应用设置为至少 `2` 个副本并使用 `RollingUpdate`；若启用自动伸缩，设置
   `minReplicas >= 2`，并按最大副本数核对数据库连接预算。同时确认平台允许 root `/init`，
   配置 `8010` 运营域名、`8080` 教师域名，以及 OSS 或同一块 RWX 共享卷。
4. 停止旧 `test-tide-camp-worker`，避免它与新镜像内的 Worker 同时常驻。
5. 向现有 `tida-camp` 项目的 `pre-tida-camp` 应用发布 application 镜像，现场读回 replicas、自动伸缩、RollingUpdate、
   两个端口、两个域名、共享存储权限和每个 Pod 的健康状态；此时应确认积分 Worker 有且仅有
   一个 leader、SourceWide s6 服务因显式门禁保持暂停，聚合健康检查只跳过它的
   heartbeat/readiness，而不是把其他进程故障伪装成健康。
6. 从两个外部 HTTPS 域名先验证运营登录、教师登录、工单往返和跨 Pod 文件读写；
   `TIT_SOURCE_WIDE_ENABLED=false` 期间不得把任务、积分或资格结果记为全流程验收通过。
7. 分别建立海外、国内两个 DTS TEST 项目，构建类型均选择 `multi_module`、构建模块均选择
   `dts-ingest`，设置 `TIT_PROCESS_PROFILE=dts-ingest`、副本数 1。海外项目选择新加坡数据中心并
   配置 `TIT_DTS_EXECUTION_REGION=sg`；国内项目选择中国大陆数据中心并配置
   `TIT_DTS_EXECUTION_REGION=cn`。停止并废弃任何位于新加坡数据中心的国内 DTS Pod。两个项目只
   注入各自 DTS 密码与 `tit_dts_ingest_runtime` 数据库密码；国内项目另行注入专用
   `TIT_DTS_DOM_STUDENT_HMAC_PASSWORD`，海外项目禁止持有该密钥。变量名中的 `PASSWORD` 是
   Gaea 敏感值掩码契约，不能改回旧名称。当前固定 PRE 专线目标可在两个项目
   同时加载 `dts-ingest.pre-ssl-off.env.example` 的 `disable/true` 覆盖；这只是受控非 TLS 例外，
   正式环境仍须 `verify-full`。使用各自固化的区域回放边界和
   相同的 `2026-08-13` 开放式 cohort；国内始终保持
   `TIT_DTS_PROJECTION_ENABLED=false`，海外在首次追平阶段也保持 `false`。先确认两套 readiness 表明 DB 与 Broker 启动探针通过，
   再单独读回 heartbeat、事件账本、数据库 checkpoint 和消费组位点，证明真实消息已经进入正式
   消费事务；不能用 readiness 代替接入证据。完成静态投诉分类字典装载与引用完整性检查后，
   两条流追平同一激活时刻，仅在海外项目配置相同的两个 required topic 和带时区
   `TIT_DTS_ACTIVATION_AT` 后打开投影。确认
   双 checkpoint、投诉字典门禁和全局投影锁均通过，再等待 `PENDING/RETRY/PROCESSING`
   脏键清零且连续两轮稳定，并抽样核对两张宽表。随后把 application 项目的
   `TIT_SOURCE_WIDE_ENABLED` 恢复为 `true` 并完成 RollingUpdate；确认 SourceWide 有且仅有
   一个 leader、其余 standby、heartbeat/readiness 正常且 Outbox 排空后，才执行 G01–G09、
   状态更新、幂等结分、积分/课程读取、当前门槛、乱序、单链路故障重启和双链路并发验收；
   不可逆资格授予门禁仍保持 `false`，不得把当前门槛命中写成资格已授予。该顺序允许在
   cohort 开始后完成首次激活，不需要用“镜像内有效教师为 0”规避中间态。
8. 业务验收完成后再下线旧 Worker 项目；不要用“Pod 运行中”代替端到端验收，也不要把
   “DTS 已消费”写成“两张宽表已闭环”。

application 镜像可通过 `RollingUpdate` 回滚到上一个版本；旧、新版本短暂并存时仍由同一数据库锁
保证积分逻辑单活。旧的独立 `test-tide-camp-worker` 必须保持关闭，不能与统一项目使用不
兼容的旧版结算协议。迁移回滚继续遵循向前修复和一致性备份，不由容器启动脚本执行
destructive down。

一次性发布变量仍保持原边界：运营和教师迁移统一使用 `tide_sys_admin`、
`TIT_MIGRATION_MODE=true`、`TIT_MIGRATION_EXPECTED_DATABASE`；当前固定 `tide_system_test` PRE
专线的迁移与只读契约探针可各自在管理连接串使用 `sslmode=disable`，契约探针同时设置
`TIDE_CONTRACT_PROBE_REQUIRE_SSL=false`；两个入口仍校验固定端点、库和角色，正式环境仍为
`verify-full`。首次运营账号初始化只在一次性
命令中注入 `TIT_BOOTSTRAP_USERNAME` 与 `TIT_BOOTSTRAP_PASSWORD`。迁移账号不得复用任何
运行账号。

## 当前证明边界

镜像构建、静态检查、测试和 Pod 健康都不代表生产可用。外部日更、真实通知回执、生产
账号生命周期、监控、备份、恢复、灰度与业务方验收仍是独立门槛。

## 目录结构

```text
gaea/
├── Dockerfile
├── gaea.yml
├── README.md
├── application/
│   └── Dockerfile
├── dts-ingest/
│   └── Dockerfile
├── bin/
│   ├── healthcheck.sh
│   ├── render-nginx-conf.sh
│   ├── render-real-ip-conf.py
│   └── source-wide-enabled.sh
├── nginx/
│   ├── nginx.conf
│   ├── real-ip.conf
│   └── teacher.conf
└── s6-rc.d/
    ├── operations/
    ├── teacher-api/
    ├── teacher-web/
    ├── score-settlement/
    ├── source-wide/
    ├── dts-ingest/
    └── user/contents.d/
```
