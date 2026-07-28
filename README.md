# TiDe｜新师成长运营台

面向新外教 30 天试用期的内部 Web App。系统把教师、课程、履约、课堂质量、用户反馈、
产能和成长任务事实，转成可解释的积分、资格、任务和运营行动。

当前状态：**公司测试环境可运行，尚未生产上线。**

## 业务方快速启动

前提：

- Python 3.9 或更高版本；
- Node.js 18 或更高版本；
- 能访问公司测试数据库的网络；
- 单独收到的 `TiDe.env` 和运营登录账号。

```bash
git clone https://github.com/Endluca/TiDe.git
cd TiDe

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
- 可靠性分：完美完课数 `perfect_cnt × 4` + Peak 完课数 `peak_completed_cnt × 2`。
- 课堂质量：当前不设置加分项，维度分为 0，后续规则另行发布。
- 逐课 `is_perfect`：课程状态为 `end`、缺席原因明细为空、迟到为 0、早退为 0；该字段由教师端课程读取视图实时派生，仅作为业务事实展示，不参与当前逐课计分。
- 用户反馈分：好评次数 × 5 + 按学员去重的收藏人数 × 5；15 日复约只保留事实，不计分。
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
TiDe/
├── backend/           # FastAPI、PostgreSQL、Alembic、积分与任务规则
├── frontend/          # React 运营 Web App
├── contracts/         # 教师端共享任务和课程数据契约
├── docs/              # 架构、数据、积分、认证和配置说明
├── project-context/   # 业务方与 AI 的项目背景
└── scripts/           # 一键安装和启动
```

## 数据与系统边界

- PostgreSQL 是运行事实源，Schema 只通过 Alembic 变更。
- 当前交接测试库包含教师宽表和课程基线快照，不是生产日更数据。
- 原始 Excel、学生身份、数据库 dump、环境文件和日志都不进入 Git。
- “任务已创建”不等于“通知已送达”；“测试环境可运行”不等于“生产上线”。
- 当前运营 API 的公开读写路径均直接使用 PostgreSQL 事务/查询，可运行多个 API Worker；
  本地一键启动仍默认单进程，便于开发排查。
- 外部数据日更、教师端生产接入、真实通知回执、监控、备份和回滚仍待完成。

## 生产部署骨架

仓库提供生产镜像和同源反向代理示例；它用于构建可追溯产物，不代表公司生产资源已经开通：

```bash
export TIDE_ENV_FILE=/安全路径/TiDe.production.env

# 发布前单独执行迁移并检查结果。
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
- 同源代理会把 Web App 域名作为 API 的 `Host`，因此 `TIT_ALLOWED_HOSTS`
  必须填写 Web App 域名；同源部署的 `TIT_ALLOWED_ORIGINS` 保持为空；
- 数据库凭据只由部署环境注入，不能复制进镜像；
- HTTPS 应在公司网关/负载均衡终止，网关必须配合 Allowed Host、证书、限流和日志；
- Alembic 迁移是发布前单独动作，不能由每个 API 副本启动时竞争执行。

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
