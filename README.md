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

页面进入时不会自动刷新。只有用户主动点击更新按钮，才读取最新数据。

## 当前业务口径

- `task_assignments` 是任务实例和状态的唯一事实表。
- 新教师首次进入时，系统幂等初始化 G01–G10，默认状态为 `ASSIGNED`。
- 当前任务目录只有 10 个固定成长任务和 5 个个性化改善任务；系统不得自由发明任务。
- 教师端只更新已有任务的执行状态，不能写积分、总分或资格。
- 课堂质量分：`perfect_cnt × 1.6`。
- 供给分：`peak_slot_cnt` 首次达到 40 时加 10 分并锁定。
- 固定成长任务：按 G01–G10 已完成任务的配置分值累加，最高 30 分。
- 出营资格：G01–G10 全部完成、L0 投诉为 0、raw 总分不低于 100。
- 金牌资格：已满足出营资格且 raw 总分不低于 200。
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
- 当前后端只允许一个 API 进程，不使用多 Worker。
- 外部数据日更、教师端生产接入、真实通知回执、监控、备份和回滚仍待完成。

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
- [课程级数据契约](contracts/TIT课程级数据与Mock字段契约.md)
- [配置中心运行契约](docs/config-center-contract.md)
- [认证与 RBAC](backend/README_AUTH.md)
- [数据库运行说明](backend/README_DATABASE.md)
