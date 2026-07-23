# 运营登录与 RBAC

本实现服务于当前本地运营试跑。它提供真实的密码哈希、服务端会话摘要和接口权限校验，但正式环境的账号开通、停用、密码轮换、权限审批和应急回收流程尚未建立。

## 接入应用

```python
from app.auth import auth_router

app.include_router(auth_router)
```

认证接口：

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

登录成功后，原始会话 token 只进入 `HttpOnly + SameSite=Strict` Cookie。数据库
`operator_sessions.token_hash` 只保存 token 的 SHA-256 摘要。`APP_ENV=production` 时 Cookie
自动启用 `Secure`；本地开发模式不启用，以便使用 HTTP localhost。

## 路由权限

```python
from fastapi import Depends
from app.auth import OperatorRole, require_roles

@app.post(
    "/api/task-templates/{template_id}/versions/{version}/publish",
    dependencies=[Depends(require_roles(OperatorRole.CONFIG_PUBLISHER))],
)
def publish_template(template_id: str, version: int):
    ...
```

`require_roles(A, B)` 表示拥有 A 或 B 任一角色即可。需要同时满足两个角色时，应叠加两个
dependency。建议的业务映射：

| 操作 | 角色 |
|---|---|
| 查看运营台、教师、任务、输出 | `VIEWER` |
| 处理普通运营 Case | `CASE_OPERATOR` |
| 高风险复核 | `SENIOR_REVIEWER` |
| 发布模板或运行配置 | `CONFIG_PUBLISHER` |
| 批准外部动作 | `EXTERNAL_ACTION_APPROVER` |
| 查看审计事件 | `AUDITOR` |

## 首次账号

先执行 Alembic 迁移，再通过进程环境注入一次性启动凭据：

```bash
TIT_BOOTSTRAP_USERNAME='<运营账号>' \
TIT_BOOTSTRAP_PASSWORD='<至少 12 位的强密码>' \
python scripts/bootstrap_operator.py
```

脚本不含默认密码，不接受命令行密码，不打印密码。首次运行创建一个拥有全部运营角色的
启动账号；相同凭据重复运行不会创建重复账号或角色。如果同名账号已经存在但密码不同，
脚本会拒绝修改，密码轮换应走单独的受审计流程。

启动账号和密码只通过当前进程环境传入，不写入仓库、`.env.example`、项目账本或日志。OpenAI 等外部服务密钥使用被 Git 忽略的 `.env.local`；运营密码不要与 Provider 密钥共用同一文件。

## 当前边界

- 有角色不等于有全部权限；高风险课程动作仍需要 `EXTERNAL_ACTION_APPROVER`。
- 配置中心高影响版本要求创建人与发布人不同，单个全角色启动账号不能代替四眼发布流程。
- Cookie 与本地密码登录通过不等于正式账号生命周期和权限审计流程已经验收。
- `APP_ENV=production` 会启用 Secure Cookie，但仅切换该变量不代表系统已满足生产上线条件。
