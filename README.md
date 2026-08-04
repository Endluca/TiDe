# TiDe｜新师训练营（运营端 + 教师端）

面向新外教 30 天试用期的成长系统。仓库同时包含运营端和教师端：系统把教师、课程、
履约、课堂质量、用户反馈、产能和成长任务事实，转成可解释的积分、资格、任务和运营行动。

当前状态：**公司测试环境可运行，尚未生产上线。**

## 业务方本地开发启动

前提：

- Python 3.9 或更高版本；
- Node.js 18 或更高版本；
- 能访问公司测试数据库的网络；
- 单独收到的 `TiDe.env` 和运营登录账号。

```bash
git clone git@flow.51talk.biz:ai-efficiency/Tide_teachers_camp.git
cd Tide_teachers_camp

./scripts/setup.sh /安全路径/TiDe.env
./scripts/start.sh /安全路径/TiDe.env
```

首次启动需要加载测试数据，通常要等待几十秒。看到启动成功提示后打开：

- Web App：`http://127.0.0.1:5174`
- API：`http://127.0.0.1:8010`
- API 文档：`http://127.0.0.1:8010/docs`

按 `Ctrl+C` 会同时停止前端、后端和积分结算进程。日志位于 `.runtime/`，不会进入 Git。

`TiDe.env` 只通过安全渠道单独交付，不得上传、转发到公开群或提交到仓库。默认配置连接
独立的业务交接测试库；页面中的修改会真实写入该测试库，但不会影响生产系统。

## 当前页面

- 经营总览
- 待办处置
- 任务进展
- 触达记录
- 教师档案
- 课程证据
- 任务规则
- 积分与门槛
- 操作审计

完成登录后，系统先展示运营台和各页空状态，不自动读取业务数据。只有用户主动点击当前页
的“更新”按钮，才读取该页最新数据；已访问页面在当前会话中保留，切换页面不会偷偷刷新。

## 当前业务口径

- `task_assignments` 是任务实例和状态的唯一事实表。
- 新教师首次进入时，系统幂等初始化当前 9 项必修成长任务，默认状态为 `ASSIGNED`。
- 当前任务目录只有 9 个固定成长任务和 5 个个性化改善任务；系统不得自由发明任务。
- 教师端只更新已有任务的执行状态，不能写积分、总分或资格。
- `task_assignments.why` 是教师端外显原因，固定和个性化任务均必须使用英文；个性化任务的数据库原值包含 `Evidence:` 最小证据摘要，读取时不再二次拼接；上游原始中文值只保留在证据快照。
- 个性化任务的 `task_assignments.display_title` 是英文数据库事实；运营端和教师端直接读取，不在页面或 API 层转换。
- `notifications.payload.title/body` 是触达展示的英文数据库事实，`body` 内含 `Evidence:` 关键证据摘要；运营端和教师端直接读取，不在展示层翻译或补写。
- 可靠性分：课程明细派生的完美完课数 `perfect_cnt × 4` + Peak 完课数 `peak_completed_cnt × 2`。
- 课堂质量：每节课的未开摄像头、CPU 占用过高、网络延迟过高三个字段都明确为 0 时，硬件质量加 2 分；任一字段为 1 或为空均不加分，空值同时标记为 `SOURCE_MISSING`。
- 逐课 `is_perfect`：课程状态为 `end`、迟到为 0、早退为 0；每节达标课程在可靠性维度加 4 分，并进入 `lesson_total_score`。教师维度 `perfect_cnt` 按去重 `source_appoint_id` 汇总。
- 用户反馈分：好评次数 × 5 + 教师维度 `feedback_favorite_cnt` × 5；课程级收藏按
  `teacher_id + student_id_hash` 去重，仅最早一节有效完课的收藏归因加 5 分，后续
  收藏课仍保留收藏事实但该项加分为 0；15 日复约只保留事实，不计分。
- 供给分：`peak_slot_cnt` 首次达到 40 时加 10 分并锁定。
- 固定成长任务：按当前 9 项必修任务的完成状态与配置分值累加，最高 30 分。
- 出营资格：当前 9 项必修任务全部完成、L0 投诉为 0、raw 总分不低于 100。
- 金牌资格：满足当前出营条件、raw 总分不低于 200、`late_cnt <= 1`、
  `early_cnt = 0`、`absent_cnt = 0`。
- 新积分规则发布时，在同一发布事务内按新规则全量重算当前教师积分；重算失败则发布失败。
- 教师一旦获得出营或金牌资格，后续数据修正或新规则降分都不撤回已获得资格。
- 个性化任务由确定性规则触发；当前规则不依赖 OpenAI Key。

完整口径见 [数据与积分规则](docs/数据与积分规则.md)。

## 给业务方和 AI 的背景资料

先阅读 [project-context/README.md](project-context/README.md)。其中包含：

- 产品目标与页面边界；
- 当前页面与已确认的交互约束；
- 页面、接口和后端规则的代码映射；
- 运行、真实数据和敏感信息边界。

开发代理还应遵守 [AGENTS.md](AGENTS.md)。

## 目录

```text
Tide_teachers_camp/
├── backend/           # 运营端 FastAPI、PostgreSQL、Alembic、积分与任务规则
├── frontend/          # React 运营 Web App
├── teacher/           # 教师端 NestJS API、React Web App 及其文档
├── contracts/         # 两端共享任务和课程数据契约
├── deploy/combined/   # 两端同机、不同域名的联合部署入口
├── gaea/              # 运营端、教师端与数据库选主结算 Worker 的单镜像 Gaea 配置
├── docs/              # 架构、数据、积分、认证和配置说明
├── project-context/   # 业务方与 AI 的项目背景
└── scripts/           # 运营端一键安装和启动
```

教师端的独立开发、测试和构建命令见
[`teacher/README.md`](teacher/README.md)。根目录与 `teacher/` 各自保留技术栈和迁移链，
但通过同一 PostgreSQL 业务事实与共享契约协作。

## 数据与系统边界

- PostgreSQL 是运行事实源，Schema 只通过 Alembic 变更。
- 当前交接测试库包含教师宽表和课程基线快照，不是生产日更数据。
- 原始 Excel、学生身份、数据库 dump、环境文件和日志都不进入 Git。
- “任务已创建”不等于“通知已送达”；“测试环境可运行”不等于“生产上线”。
- 当前运营 API 的公开读写路径均直接使用 PostgreSQL 事务/查询，可运行多个 API Worker；
  本地一键启动中的运营 API 默认单 Worker，便于开发排查。
- 外部数据日更、教师端生产接入、真实通知回执、监控、备份和回滚仍待完成。

## Gaea 部署骨架（单项目、单镜像、双域名）

[`gaea/Dockerfile`](gaea/Dockerfile) 同时构建运营 React、教师 React、运营 FastAPI 和
教师 NestJS，并用 s6-overlay 在一个 Pod 中管理运营 API、教师 API、教师 Nginx 与积分
结算 Worker 四个进程。运营域名指向容器 `8010`，教师域名指向 `8080`；教师 NestJS
监听 `3000`，只供同 Pod 的 Nginx 代理：

```bash
docker build -f gaea/Dockerfile -t tide-camp:gaea .
```

同一 Gaea 项目和镜像支持整套 Pod 设置为 `2` 个或更多副本，并使用 `RollingUpdate`：每个
Pod 都启动四个进程，积分结算候选进程通过 PostgreSQL session advisory lock 保持逻辑单活；
未持锁的 standby 仍刷新本 Pod heartbeat 并保持健康。教师全局调度使用
`tide.job_leases`，照片处理按数据库行租约认领，因此无需再拆出新的 Gaea Worker 项目。

多副本的私有文件首选 OSS；`FILE_STORAGE_PROVIDER=LOCAL` 只允许所有 Pod 共享同一块
`ReadWriteMany (RWX)` 卷。视频预热脚本的本地幂等账本若被执行，也必须使用跨执行节点可见
的 RWX 状态目录；Worker heartbeat 必须留在各 Pod 的 `/tmp`，不能共享。完整环境变量、
双域名、健康检查、连接预算和发布验收见 [Gaea 部署说明](gaea/README.md)。逻辑服务与数据库
角色仍然独立；TiDe Alembic 和教师端 migration 仍须作为发布前独立作业执行。该单容器形态
只用于受控 TEST：同一 UID 的进程仍能接触整套容器密钥，不具备生产级秘密隔离。

## 现有分离式生产部署骨架

原有 Compose 继续保留为分离式部署和联合部署参考；它用于构建可追溯产物，不代表公司
生产资源已经开通，也不是 Gaea UI/API 合一镜像的启动方式：

```bash
export TIDE_RUNTIME_ENV_FILE=/安全路径/TiDe.runtime.production.env
export TIDE_MIGRATION_ENV_FILE=/安全路径/TiDe.migration.production.env
export TIDE_MIGRATION_EXPECTED_DATABASE=tit_growth
export TIDE_OPS_HOST=tit-growth.example.com

# 默认只监听本机网关；如公司网关在另一台机器，只填写本机明确的私网 IP。
export TIDE_GATEWAY_BIND_ADDRESS=127.0.0.1
# 必须选择与宿主机及现有 Docker 网络都不重叠的专用网段，并在启动前核对。
export TIDE_RUNTIME_SUBNET=172.31.254.0/24
export TIDE_WEB_PROXY_IP=172.31.254.10

# 先失败关闭校验环境文件、数据库角色、绑定地址和内部网段，再展开 Compose。
python3 scripts/preflight_production.py
docker compose -f docker-compose.production.yml --profile migration config --quiet

# 校验通过后单独执行迁移并检查结果。
docker compose -f docker-compose.production.yml --profile migration run --rm migrate

# 首次部署时单独创建启动运营账号。密码仅注入本次命令，不写入环境文件。
TIT_BOOTSTRAP_USERNAME='<运营账号>' \
TIT_BOOTSTRAP_PASSWORD='<至少 12 位的强密码>' \
docker compose -f docker-compose.production.yml run --rm \
  -e TIT_BOOTSTRAP_USERNAME -e TIT_BOOTSTRAP_PASSWORD \
  api python scripts/bootstrap_operator.py

# 启动两个 API Worker、一个固定任务积分结算进程和静态 Web 服务。
docker compose -f docker-compose.production.yml up -d api score-settlement web
```

- `backend/Dockerfile` 使用非 root 用户运行 FastAPI，默认 2 个 Worker；
- `frontend/Dockerfile` 产出静态资源，Nginx 同源代理 `/api`；
- `TIDE_RUNTIME_ENV_FILE` 只使用受限运行角色 `tit_growth_app`，
  `TIDE_MIGRATION_ENV_FILE` 只使用迁移角色 `tit_growth_migrator`；两个文件不得复用，
  数据库凭据只由部署环境注入，不能复制进镜像；
- 同源代理会把 Web App 域名作为 API 的 `Host`。`TIDE_OPS_HOST` 必须同时出现在运行
  环境文件的 `TIT_ALLOWED_HOSTS` 中，Compose 会用它覆盖 `TIT_HEALTHCHECK_HOST`；
  同源部署的 `TIT_ALLOWED_ORIGINS` 保持为空；
- Web Nginx 是 API 唯一可信 HTTP 代理，`TIDE_WEB_PROXY_IP` 与
  `TIDE_RUNTIME_SUBNET` 都是生产必填项。所选网段必须先通过
  `docker network ls` / `docker network inspect` 和宿主路由核对不重叠，API 只信任
  这个精确容器 IP，不能改回整个 Docker 网段；
- HTTPS 在公司网关/负载均衡终止。原点 Nginx 会丢弃所有入站 `X-Real-IP`、
  `X-Forwarded-For` 与 `X-Forwarded-Proto`，只把直接连接的网关地址传给 API，并将
  协议固定为生产 HTTPS；真实客户端 IP 的限流和审计由公司网关负责。宿主发布端口
  默认只绑定 `127.0.0.1`，跨机接入时仅绑定明确私网 IP 并用防火墙只允许公司网关，
  禁止 `0.0.0.0`；
- Alembic 迁移是发布前单独动作，Compose 会显式注入迁移模式和预期数据库名，不能
  由 API 副本启动时执行。

仓库内 `teacher/` 教师端与根目录运营端同机部署时，使用
[联合部署说明](deploy/combined/README.md) 和
[联合 Compose](deploy/combined/docker-compose.yml)。两端使用不同域名、独立容器与
独立受限数据库角色，只共享同一个逻辑 PostgreSQL 数据库；宿主机只暴露统一 Edge。
联合部署门禁要求教师端生产迁移完整到
`0025_fixed_task_semantic_alignment`，并同时通过固定提交源码中的精确
`G01–G09` 标题/分值预检和目标数据库契约探针。教师端只到 0024、目录缺项或语义错误
都会失败关闭；即使门禁通过，也不能把“已有 Compose”解释为已完成生产切流。

## 开发验证

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/alembic check

cd ../frontend
npm test -- --run
npm run lint
npm run build
```

## 本地空库模式

本地空库只用于开发，不包含当前真实测试数据：

```bash
docker compose up -d postgres

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

export DATABASE_URL='postgresql+psycopg://tit_growth_app@127.0.0.1:5432/tit_growth'
export APP_ENV=local
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_database.py
.venv/bin/python scripts/seed_config_center.py
```

空库 Seed 只创建当前任务模板和默认配置，不创建教师、课程或业务输出。

## 权威文档

- [当前架构](docs/architecture.md)
- [数据与积分规则](docs/数据与积分规则.md)
- [数据库表结构](docs/数据库表结构.md)
- [教师端共享任务表契约](contracts/教师端共享任务表契约.md)
- [教师端积分与课程读取对照表](contracts/教师端积分与课程读取对照表.md)
- [课程级数据契约](contracts/TIT课程级数据与Mock字段契约.md)
- [配置中心运行契约](docs/config-center-contract.md)
- [认证与 RBAC](backend/README_AUTH.md)
- [数据库运行说明](backend/README_DATABASE.md)
