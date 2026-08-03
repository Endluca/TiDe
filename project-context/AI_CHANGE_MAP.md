# AI 修改地图

## 先判断改动属于哪一层

| 想改什么 | 主要文件 |
|---|---|
| 导航、页面名称、登录后结构 | `frontend/src/App.tsx` |
| 页面布局和文案 | `frontend/src/pages/*.tsx` |
| 全局视觉 | `frontend/src/styles.css`、`frontend/src/components/Common.tsx` |
| 前端接口与类型 | `frontend/src/api.ts`、`frontend/src/types.ts` |
| 前端静态托管与 Gaea 打包 | `backend/app/frontend_static.py`、`gaea/operations/Dockerfile`、`gaea/README.md` |
| 任务目录 | `backend/app/task_catalog.py`、`backend/app/task_seed.py` |
| 个性化触发 | `backend/app/personalized_rules.py`、`backend/app/lesson_ingestion.py` |
| 积分与资格 | `backend/app/services.py`、`backend/app/config_service.py` |
| 任务实例与统计 | `backend/app/task_service.py`、`backend/app/task_routes.py` |
| 运营待办 | `backend/app/operations_service.py`、`backend/app/operations_routes.py` |
| 数据库字段 | `backend/app/db_models.py`、`backend/migrations/versions/` |

## 修改规则

- 文案、间距、颜色和已有字段的展示方式可以直接在前端修改。
- 指标含义、筛选口径、状态机、积分、资格和触发条件必须先改后端，再同步测试和权威文档。
- 不在前端复制一套业务判断。
- 不恢复旧任务、旧页面或历史规则；当前任务目录只有连续编号的 G01–G09 和 5 类个性化改善任务，G00 只读保留历史。
- `task_assignments.why` 必须在数据库原值中保存完整英文原因和 `Evidence:` 最小证据摘要；教师端、运营端和 API 直接读取，不在展示时补写。中文原始值仍只放在 `evidence_snapshot`。
- 不把 API Key、数据库密码、原始学生信息、真实数据文件或日志写进代码。

## 最小验证

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/alembic check

cd ../frontend
npm test -- --run
npm run lint
npm run build
```

如果修改的是页面，还应启动应用后实际点击对应入口，确认空状态、主动更新、筛选和下钻。
