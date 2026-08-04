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
  --build-arg VITE_PUBLIC_ASSET_BASE_URL=https://tide-media.example.com \
  -t tide-teacher-web:reviewed .
```

- 教师浏览器固定请求同源相对路径 `/api/v1/...`；Nginx 再把 `/api/` 代理到联合部署网络中的 `teacher-api:3000`。`VITE_API_BASE_URL` 不属于 Gaea/Nginx 构建参数，即使 CI 残留该变量，Nginx 构建也会强制忽略。
- `VITE_PUBLIC_ASSET_BASE_URL` 必须是 HTTPS；构建会确认素材地址已编入 JavaScript，并删除本地 `public/assets` 与 readiness 样例。
- `index.html` 保留 `__SITE_ORIGIN__` 标记，Nginx 按请求的可信协议和 `Host` 运行时生成绝对分享图片地址，因此换教师域名不需要重建前端。
- `.env.local` 已被 Git 忽略，不能作为发布所需配置的唯一来源。
- 其余路径按 SPA 静态资源处理；只有外层网关／边缘 Nginx 对外开放 80/443。
- `/healthz` 只检查静态 Web 容器；后端容器必须单独以 `/health/ready` 作为就绪门槛。
- `dist/assets` 的编译哈希资源使用一年 immutable 缓存，`index.html` 禁止缓存，避免版本发布后 HTML 与 chunk 不一致。

换教师域名时只需同步 DNS/Ingress，以及教师后端运行变量 `TIDE_TEACHER_HOST`、`PUBLIC_APP_URL`、`PUBLIC_API_URL`、`CORS_ORIGINS` 后重启；不再修改前端代码或重建镜像。

本地也可以直接验证普通静态产物：

```bash
VITE_PUBLIC_ASSET_BASE_URL=https://tide-media.example.com \
pnpm run build
```

`pnpm run dev` 与 `pnpm run preview` 都会把同源 `/api` 代理到本机 `127.0.0.1:3000`。
只有前端和 API 跨域的遗留 Codex Sites 发布仍需显式设置 HTTPS
`VITE_API_BASE_URL` 并重新构建；`build:sites` 缺少该值时会失败关闭。干净检出还需要先由
Sites 工具在 `.openai/hosting.json` 注入已固定的项目绑定，该本地目录不会提交到 Git。

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
