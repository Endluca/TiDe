# syntax=docker/dockerfile:1.7

# ========== Stage 1: build the operations frontend ==========
FROM hub.51talk.biz/library/node:22-alpine AS operations-frontend-build

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories

WORKDIR /build/frontend
RUN npm config set registry https://repo.bjtest.51talk.biz/repository/npm/ \
    && npm config set replace-registry-host always
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ========== Stage 2: build the teacher frontend ==========
FROM hub.51talk.biz/library/node:22-alpine AS teacher-frontend-build

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories

ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:$PATH" \
    COREPACK_NPM_REGISTRY=https://repo.bjtest.51talk.biz/repository/npm/

RUN npm config set registry https://repo.bjtest.51talk.biz/repository/npm/ \
    && npm config set replace-registry-host always \
    && corepack enable \
    && corepack prepare pnpm@11.13.1 --activate

WORKDIR /build/teacher-frontend
COPY teacher/frontend/package.json teacher/frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY teacher/frontend/ ./

# The teacher browser always calls the same-origin /api path. Only the public
# media origin remains a reviewed build-time fact.
ARG VITE_PUBLIC_ASSET_BASE_URL=https://tide-media.51talkjr.com
ENV VITE_PUBLIC_ASSET_BASE_URL=${VITE_PUBLIC_ASSET_BASE_URL}
RUN pnpm run build:nginx

# ========== Stage 3: build the teacher NestJS backend ==========
FROM hub.51talk.biz/library/node:22-alpine AS teacher-backend-build

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories

ENV PNPM_HOME=/pnpm \
    PATH="/pnpm:$PATH" \
    COREPACK_NPM_REGISTRY=https://repo.bjtest.51talk.biz/repository/npm/

RUN npm config set registry https://repo.bjtest.51talk.biz/repository/npm/ \
    && npm config set replace-registry-host always \
    && corepack enable \
    && corepack prepare pnpm@11.13.1 --activate

WORKDIR /build/teacher-backend
COPY teacher/backend/package.json teacher/backend/pnpm-lock.yaml teacher/backend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY teacher/backend/nest-cli.json teacher/backend/tsconfig.json teacher/backend/tsconfig.build.json ./
COPY teacher/backend/src ./src
RUN pnpm run build \
    && test -f dist/src/main.js \
    && pnpm prune --prod

# ========== Stage 4: install Python dependencies ==========
FROM hub.51talk.biz/library/python:3.12-alpine AS python-build

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories
RUN apk add --no-cache \
    build-base \
    cargo \
    libffi-dev \
    linux-headers \
    postgresql-dev

ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /build
RUN python -m venv /opt/venv
COPY backend/requirements.txt ./requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --requirement requirements.txt

# ========== Stage 5: one image, four supervised processes ==========
FROM hub.51talk.biz/library/python:3.12-alpine AS runtime
LABEL maintainer="51talk SRE"

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories

# The score heartbeat is deliberately Pod-local: standby workers must prove
# their own process liveness instead of reading the elected leader's file.
ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    NODE_ENV=production \
    PORT=3000 \
    TIT_FRONTEND_REQUIRED=true \
    TIT_SCORE_WORKER_HEARTBEAT=/tmp/tit-score-worker-heartbeat \
    VIDEO_PREFETCH_STATE_DIR=/var/lib/tide/video-prefetch-runs \
    S6_KEEP_ENV=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=60000 \
    S6_CMD_RECEIVE_SIGNALS=1

ARG S6_OVERLAY_VERSION=3.1.6.2
ENV GAEA_BASE_URL=https://flow.51talk.biz/pkg/ci/files/files

RUN apk add --no-cache \
    ca-certificates \
    curl \
    gettext \
    libffi \
    libgcc \
    libpq \
    libstdc++ \
    nginx \
    tzdata \
    xz \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && addgroup -g 1001 gaea \
    && adduser -u 1001 -G gaea -D gaea \
    && curl -fsSL ${GAEA_BASE_URL}/s6-overlay-noarch/${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz \
      | tar -C / -Jxpf - \
    && curl -fsSL ${GAEA_BASE_URL}/s6-overlay-x86_64/${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz \
      | tar -C / -Jxpf -

WORKDIR /app

# Operations FastAPI and React static assets.
COPY --chown=gaea:gaea --from=python-build /opt/venv /opt/venv
COPY --chown=gaea:gaea backend/alembic.ini ./operations/alembic.ini
COPY --chown=gaea:gaea backend/app ./operations/app
COPY --chown=gaea:gaea backend/migrations ./operations/migrations
COPY --chown=gaea:gaea backend/scripts ./operations/scripts
COPY --chown=gaea:gaea --from=operations-frontend-build /build/frontend/dist ./operations/app/static

# Teacher NestJS runtime and reviewed Nginx static build.
COPY --chown=gaea:gaea --from=teacher-backend-build /usr/local/bin/node /usr/local/bin/node
COPY --chown=gaea:gaea --from=teacher-backend-build /build/teacher-backend/package.json ./teacher/package.json
COPY --chown=gaea:gaea --from=teacher-backend-build /build/teacher-backend/node_modules ./teacher/node_modules
COPY --chown=gaea:gaea --from=teacher-backend-build /build/teacher-backend/dist ./teacher/dist
COPY --chown=gaea:gaea teacher/backend/config ./teacher/config
COPY --chown=gaea:gaea --from=teacher-frontend-build /build/teacher-frontend/dist /usr/share/nginx/teacher

# Fail the image build if the copied Node runtime or native sharp module is
# incompatible with the final Python Alpine base.
RUN node --version \
    && node -e "require('/app/teacher/node_modules/@nestjs/core'); require('/app/teacher/node_modules/sharp')"

# Supervisor, aggregate health check, and teacher Web proxy.
COPY --chmod=755 gaea/bin /app/bin
COPY gaea/nginx/nginx.conf /etc/nginx/nginx.conf.template
COPY gaea/nginx/real-ip.conf /etc/nginx/conf.d/00-real-ip.conf
COPY gaea/nginx/teacher.conf /etc/nginx/conf.d/teacher.conf
COPY --chmod=755 gaea/s6-rc.d /etc/s6-overlay/s6-rc.d

RUN rm -f /etc/nginx/http.d/default.conf /etc/nginx/conf.d/default.conf \
    && NGINX_WORKER_PROCESSES=2 envsubst '${NGINX_WORKER_PROCESSES}' \
      < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf \
    && mkdir -p \
      /app/operations/logs \
      /var/lib/tide/uploads \
      /var/lib/tide/video-prefetch-runs \
      /var/lib/nginx \
      /var/log/nginx \
      /run \
      /tmp/tide-nginx/client-body \
      /tmp/tide-nginx/proxy \
      /tmp/tide-nginx/fastcgi \
      /tmp/tide-nginx/uwsgi \
      /tmp/tide-nginx/scgi \
    && ln -sf /dev/stdout /var/log/nginx/access.log \
    && chown -R gaea:gaea \
      /app \
      /usr/share/nginx/teacher \
      /var/lib/tide \
      /var/lib/nginx \
      /var/log/nginx \
      /run \
      /tmp/tide-nginx \
    && nginx -t

EXPOSE 8010 8080

HEALTHCHECK --interval=30s --timeout=20s --start-period=120s --retries=3 \
  CMD ["/app/bin/healthcheck.sh"]

STOPSIGNAL SIGTERM
ENTRYPOINT ["/init"]
CMD ["sleep", "infinity"]
