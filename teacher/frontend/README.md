# TIDE Teacher Frontend

## Design read

Teacher-facing growth Web App for new teachers. Professional, encouraging and lightly playful, using the official 51Talk color system and Toki assets.

- Design variance: 8
- Motion intensity: 4
- Visual density: 6
- Core responsive check: desktop and 390px mobile
- Data and task outcomes: Live API only

## Run locally

```bash
pnpm install
pnpm run dev
```

## 同机 Nginx 生产发布

本仓库的正式发布目标是与运营端同机部署、按不同域名分流，不绑定 Codex Sites。
`pnpm run build` 与 `pnpm run build:nginx` 使用同一套失败关闭的 Nginx 构建流程；
不要直接执行 `vite build`。

联合部署不运行 Vite 开发服务器。教师端前端使用多阶段 Docker 构建生成普通静态目录，再由非 root Nginx 在容器内监听 `8080`：

```bash
docker build \
  --build-arg VITE_API_BASE_URL=https://teacher.example.com \
  --build-arg VITE_PUBLIC_ASSET_BASE_URL=https://tide-media.example.com \
  -t tide-teacher-web:reviewed .
```

- 两个构建参数都必须是 HTTPS；`VITE_API_BASE_URL` 必须是纯 origin，不能附加 `/api`、查询串或 fragment。前端请求本身已经包含 `/api/v1/...`。
- 构建会确认两个地址已编入 JavaScript、删除本地 `public/assets` 与 readiness 样例，并把 `index.html` 中全部 `__SITE_ORIGIN__` 替换为教师端 origin；存在未替换标记时构建失败。
- `.env.local` 已被 Git 忽略，不能作为发布所需配置的唯一来源。
- Nginx 将 `/api/` 代理到联合部署网络中的 `teacher-api:3000`，其余路径按 SPA 静态资源处理；只有外层网关／边缘 Nginx 对外开放 80/443。
- `/healthz` 只检查静态 Web 容器；后端容器必须单独以 `/health/ready` 作为就绪门槛。
- `dist/assets` 的编译哈希资源使用一年 immutable 缓存，`index.html` 禁止缓存，避免版本发布后 HTML 与 chunk 不一致。

本地也可以直接验证普通静态产物：

```bash
VITE_API_BASE_URL=https://teacher.example.com \
VITE_PUBLIC_ASSET_BASE_URL=https://tide-media.example.com \
pnpm run build
```

## Current scope

- `/`: My TIDE, backed by the teacher profile, score summary and course APIs.
- `/path`: the continuous nine-task G01–G09 growth map. The retired pre-renumbering task is retained as hidden G00 history and is not shown.
- `/messages`: live system and source notifications.
- `/task/:taskId`: shared task detail with Why, How, Result, completion standard, materials and help.

The frontend has one runtime data path. Authentication, task content, status, progress,
scores, courses and messages all come from the backend. Missing sources are shown as
unavailable and are never replaced with local sample results.

The language control translates common interface copy only. Task titles, guidance,
steps, questions and completion standards are displayed in the language returned by
the backend.

Local database seeds and contract fixtures remain available for development and tests,
but the frontend has no Mock presentation mode or login bypass.
