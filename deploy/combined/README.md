# 教师端与运营端同机部署

状态：**部署骨架与技术加固已建立，联合门禁已固定到教师端
`0025_fixed_task_semantic_alignment` 和唯一当前 `G01–G09` 目录。只有固定到已评审的
干净教师端提交、真实执行 0025 并通过数据库契约探针后，才可解除任务语义阻断；在此之前
禁止上线。**

## 结论

同一台服务器可以承载两套应用，但不能把它们合成一个进程、一个写账号或两份任务事实。
推荐使用一个公司网关或 Edge Nginx，只开放 80/443，按域名分流：

```text
Internet
  |
  v
company gateway / TLS
  |
  v
edge:8080
  |-- ops.example.com
  |     |-- /api/* -> api:8010
  |     `-- /*      -> web:8080
  `-- teacher.example.com
        |-- /api/* -> teacher-api:3000
        `-- /*      -> teacher-web:8080

api -----------\
score-worker ---+--> one logical PostgreSQL database
teacher-api ---/     public = shared/TiDe facts
                       tide = teacher execution facts
```

`3000`、`8010` 和 `5432` 只在容器网络或公司内网可见。两个 Web 容器都监听
容器内 `8080`，不会形成宿主机端口冲突。Edge 默认只绑定宿主机 `127.0.0.1`；
若公司网关不在同机，只能改为经网络控制验证的私网监听地址，不能使用 `0.0.0.0`。
预检只接受 loopback 或 RFC1918 的规范 IPv4；公网、链路本地、全网卡和 IPv6 地址均
失败关闭。IPv6 要等公司网关、宿主机防火墙、Compose 发布语法和真实 IP 链一起完成专项
验收后再启用，不能只把监听值改成 `::`。
生产 PostgreSQL 建议使用公司内网数据库；即使数据库也在同一主机，仍不得发布 `5432`。

## 教师端 0025 任务语义迁移门

TiDe 的唯一当前目录是连续 `G01–G09`，其中 `G04` 为合并的首课备课与设备网络检测。
教师端旧目录 `G01-G04,G06-G10` 的执行记录不能只改展示文案，否则会把教师执行流程、
完成状态和积分结算路由到不同业务任务。教师端必须通过向前迁移
`0025_fixed_task_semantic_alignment`，按稳定共享模板行调整 execution 的业务编码，
保留 execution ID、步骤/规则 ID 和已有进度。

经两端契约确认的映射是：

```text
旧 G01 -> 当前 G01
旧 G03 -> 当前 G02
旧 G04 -> 当前 G03
旧 G02 -> 当前 G04
旧 G06 -> 当前 G05
旧 G07 -> 当前 G06
旧 G08 -> 当前 G07
旧 G09 -> 当前 G08
旧 G10 -> 当前 G09
旧 G05 -> G00，只保留退役历史
```

该映射必须由 0025 在单一事务和生产迁移账本内应用；不能由部署脚本暗改，不能重建
execution ID，也不能清空教师已有进度。0025 是向前语义迁移，生产回退依赖发布前一致性
备份或向前修复，不恢复旧任务路由。

当前工作树已经落地以下技术门禁：

1. 教师端正式迁移器永久排除 `0017/0018` 对 `public.task_assignments` 的 DDL，并包含
   从 `0001` 到 `0025` 的完整有序生产链、迁移账本、checksum 与 advisory lock。
2. 运营回复工单函数在同一事务设置 `WAITING_TEACHER`、最后回复时间和 48 小时截止时间，
   `tit_growth_app` 只获得查询和函数执行的必要权限。
3. 教师端生产配置对双数据库、严格 SSL、HTTPS 公共地址和 OSS fail-closed；readiness
   同时检查两条数据库连接。
4. 教师端 API 的后台调度器目前嵌在 HTTP 进程。本骨架因此只允许一个
   `teacher-api` 副本；提供独立 Worker 入口和 Worker 心跳后才能拆分、横向扩容。

切流前仍需关闭两项：

1. 在目标库真实执行 0025，并用迁移前后快照证明 execution ID、步骤/规则 ID、教师进度以及
   运营端 `task_templates/task_assignments/score_entries` 均未被重建或越权修改。
2. 教师端主 PRD 仍描述“TIDE 刷新后再修改工单状态”，需要与已落地的原子回复函数同步，
   不能同时保留两套状态时序口径。

`preflight.sh` 会正向核对固定提交中的完整 0025 迁移清单以及精确 G01–G09 标题/分值；
`contract-probe.sql` 会在目标库正向核对完整迁移账本、共享目录、assignment 和 execution。
任一通过都不替代另一个，也不替代备份恢复演练和真实压测。
完整的发布前证据、主键级前后对照和停止条件见
[教师端任务语义迁移与联合部署验收](../../docs/教师端任务语义迁移与联合部署验收-20260730.md)。

## 数据库所有权

| 对象 | 唯一迁移所有者 | 运行写入者 |
|---|---|---|
| `public.task_templates/task_assignments`、积分、通知、审计、Outbox、读取视图 | TiDe Alembic | 各自受限列权限 |
| `tide.*` | 教师端 migrator | `tit_teacher_crud` |
| `public.teacher_support_tickets` 与原子函数 | 教师端 migrator | 教师端写事实；运营端只调用函数 |

运行角色不得共用：

- `tit_growth_migrator`：仅发布时运行 TiDe Alembic；
- `tide_migrator`：仅迁 `tide.*` 与共享工单例外；
- `tide_support_ticket_owner`：非登录、非超级的共享工单函数 owner；
- `tit_growth_app`：运营 API；
- `tit_teacher_crud`：教师 API，只更新 assignment 允许的状态五字段；
- `tit_contract_probe`：只在发布门禁连接的独立只读 LOGIN 角色，不属于任何其他角色，
  无 Schema CREATE、表写入、序列和变更函数权限；
- 教师端世文读取连接应使用只读角色，不复用写账号。

TiDe migration 会在连接后核对 `current_user=tit_growth_migrator`、角色不是 superuser、
当前数据库等于 `TIDE_DATABASE_NAME`，并在连接前要求唯一
`sslmode=verify-full`。教师端 migrator 采用同级别的固定角色、目标库、TLS 与函数 owner
门禁。任何一个条件不符都必须在执行 DDL 前停止。

初始连接上限：

- 运营 API：`2 workers * (5 pool + 2 overflow) = 14`；
- 积分 Worker：`1`；
- 教师 API：两个池各 `5`，共 `10`；
- 运行时理论峰值 `25`，不含迁移、监控、备份和 DBA。

该预算必须低于 PostgreSQL `max_connections` 的 70%–80%，否则先缩池，不能靠提高
数据库连接上限掩盖等待。`tit_contract_probe` 只在发布门禁临时占用一个连接，不进入
常驻运行预算。

## 准备

1. 优先检出整仓 `Tide_teachers_camp`，其中运营端位于仓库根目录、教师端位于
   `teacher/`；也继续支持两个独立仓库。无论采用哪种布局，都必须固定到已评审提交，
   不要从浮动分支直接构建。整仓模式下将 `TIDE_TEACHER_REPO_PATH` 指向绝对路径
   `/部署路径/Tide_teachers_camp/teacher`，`TIDE_TEACHER_EXPECTED_COMMIT` 填整仓提交 SHA；
   预检会检查整个整仓工作树无未提交改动。
2. 复制 `.env.example` 到安全目录外的部署变量文件，只填写非敏感坐标，并显式设置 Edge
   的 loopback 或私网监听地址。`TIDE_EDGE_NETWORK_SUBNET` 必须与主机、公司网络和其他
   Docker 网络不重叠；运营 API 只信任该网络中固定的 `TIDE_EDGE_PROXY_IP`，不能信任整个
   RFC1918 地址段。`TIDE_COMPANY_GATEWAY_CIDR` 只填写公司网关实际源 IP（推荐 `/32`）
   或最小必要的 `/24`–`/32` 私网段。
3. 分别创建运营运行、运营迁移、契约探针、教师运行和教师迁移五个受保护环境文件；权限
   至少为仅部署账号可读。运行、迁移和探针账号不能复用，迁移文件和探针文件不得进入运行
   应用容器。探针文件只包含
   `DATABASE_URL=postgresql://tit_contract_probe:...?...&sslmode=verify-full`。
   可从 `deploy/combined/contract-probe.env.example` 复制字段骨架，不能把填写后的文件留在
   仓库工作树。
4. 配置两个域名和 TLS。`edge` 只接收公司网关转发，宿主机不得把其 HTTP 端口直接
   暴露到公网。
5. 教师端公共素材必须先上传到 HTTPS CDN，并完成浏览器读取验证。
6. 教师端联合部署固定 `SHIWEN_READ_MODE=DIRECT_TABLES`。公司网关必须覆盖而不是追加
   不可信客户端传入的转发头；Edge 的 `real_ip` 只接受
   `TIDE_COMPANY_GATEWAY_CIDR`，随后把 `X-Forwarded-For` 覆盖成单个规范客户端 IP。
   运营 API 只信任固定 Edge IP，教师 API 只信任 Edge 这一跳。直连 Edge 的请求即使自行
   携带 XFF 也只能得到直连来源地址。代理拓扑变化时必须同步修改并重跑真实 IP 限流验收。

教师端工单一次允许三张、每张 8 MiB 图片。Edge 与教师 Web 的总请求上限统一为 26 MiB，
并关闭请求体预缓冲，避免在 Edge tmpfs 复制完整请求；应用层仍逐文件执行 8 MiB 限制。
当前 `teacher-api` 内存预算为 1 GiB，默认最多同时解析 4 个 multipart 请求
（`TIT_TEACHER_MULTIPART_UPLOAD_MAX_CONCURRENCY=4`）。提高该值前必须用三张 8 MiB 的
最坏请求做并发 RSS/OOM 验收，不能只按平均图片大小估算。

加载非敏感变量后执行：

```bash
set -a
. /安全路径/combined.env
set +a

bash deploy/combined/preflight.sh
docker compose -f deploy/combined/docker-compose.yml config --quiet
```

教师端只到 0024、目录缺项、仍含 G10 或任一标题/分值语义错误时，应在预检阶段停止，
这是预期结果。

## 发布顺序

1. 评审教师端 0025、任务编码、读取过滤和执行配置；固定包含完整修复的新提交 SHA。
2. 停止两端写流量、教师后台任务和积分结算 Worker。
3. 创建一致性备份，记录 Alembic head、教师迁移账本和任务目录快照；验证恢复路径。
4. DBA 预建或确认 `pg_trgm`。
5. 运行 TiDe migration：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm migrate
   ```

   revision 38 只会在没有既有固定任务积分事实时自动对齐分值；若迁移拒绝，必须先走
   受治理的积分规则发布与同事务全量重算。

6. 使用教师端独立迁移环境文件运行正式 migrator，按完整有序生产链升级到
   `0025_fixed_task_semantic_alignment`；迁移 `tide.*` 和共享工单例外，不得执行
   `0017/0018` 的共享任务表 DDL。迁移器必须确认严格 SSL、目标库、固定非超级账号和受限
   SECURITY DEFINER owner：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm teacher-migrate
   ```
7. 0025 完成旧执行编码迁移后，以只读共享目录模式同步/核对教师端执行内容，确认 execution
   ID 和已有进度不变；`TASK_CATALOG_PUBLIC_WRITE` 必须为 `false`，不得再以迁移器外脚本
   改写共享任务编码。
8. DBA 在完整教师端迁移链结束后统一应用最小权限；由于 0024 改变了函数 owner，权限脚本
   必须在本次迁移后重跑，即在 DBA 自己的受控数据库会话中执行
   `$TIDE_TEACHER_REPO_PATH/backend/database/scripts/grant-tit-teacher-crud.sql`，否则教师角色
   不会获得工单教师消息函数的执行权。DBA 还需通过密码管理系统预建无高权限、无角色成员
   关系的 `tit_contract_probe LOGIN`，然后在同一受控会话应用精确只读授权：

   ```bash
   psql "$DBA_DATABASE_URL" \
     -v ON_ERROR_STOP=1 \
     -v expected_database="$TIDE_DATABASE_NAME" \
     -f deploy/combined/grant-contract-probe.sql
   ```

   授权脚本先撤销该角色在 `public/tide` 的既有表、序列和变更函数权限，再只授予契约读取
   所需对象。随后使用独立探针环境文件运行数据库契约探针：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     --profile migration run --rm contract-probe
   ```

   探针会核对 `current_user=session_user=tit_contract_probe`、目标库、实际 TLS、只读事务、
   无角色继承和无写权限。用迁移账号、运行账号、`SET ROLE` 或非 TLS 会话执行都会失败，
   因此通过结果才真实覆盖探针自身权限，而不是高权限账号代查。

9. 先启动并观察积分 Worker，再启动运营 API、单副本教师 API 和两个 Web：

   ```bash
   docker compose -f deploy/combined/docker-compose.yml \
     up -d score-settlement api teacher-api web teacher-web
   ```

10. 最后启动 `edge`，由公司网关按域名灰度切流。验证运营登录、教师登录、9 项任务、
    状态更新、幂等结分、积分/课程读取和工单往返全链路后再全量。

## 回滚与观测

应用镜像可以回退到兼容版本；数据库变更使用向前修复，不把 destructive down migration
当生产回滚方案。需要回到旧数据结构时使用发布前一致性备份。

灰度期间至少观察：

- 两端 P50/P95/P99、5xx 与超时；
- PostgreSQL 活跃/空闲/等待连接、锁等待、慢 SQL 和事务时长；
- 积分 Outbox 待处理数、最老事件年龄、Worker heartbeat；
- 教师后台租约、图片任务积压和最近成功时间；
- Nginx 上游失败、请求体大小与静态资源命中率；
- 容器 CPU、RSS、OOM、重启次数和日志写入速率。
