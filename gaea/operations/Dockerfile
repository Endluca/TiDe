# ========== Stage 1: build the operations frontend ==========
FROM hub.51talk.biz/library/node:22-alpine AS frontend-build

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories

WORKDIR /build/frontend
RUN npm config set registry https://repo.bjtest.51talk.biz/repository/npm/ \
    && npm config set replace-registry-host always
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ========== Stage 2: install Python dependencies ==========
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

# ========== Stage 3: one Python process serves API and frontend ==========
FROM hub.51talk.biz/library/python:3.12-alpine AS runtime
LABEL maintainer="51talk SRE"

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TIT_FRONTEND_REQUIRED=true

RUN apk add --no-cache \
    ca-certificates \
    libffi \
    libgcc \
    libpq \
    libstdc++ \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && addgroup -g 1001 gaea \
    && adduser -u 1001 -G gaea -D gaea

WORKDIR /app
COPY --chown=gaea:gaea --from=python-build /opt/venv /opt/venv
COPY --chown=gaea:gaea backend/alembic.ini ./alembic.ini
COPY --chown=gaea:gaea backend/app ./app
COPY --chown=gaea:gaea backend/migrations ./migrations
COPY --chown=gaea:gaea backend/scripts ./scripts
COPY --chown=gaea:gaea --from=frontend-build /build/frontend/dist ./app/static
RUN chown gaea:gaea /app

USER gaea
EXPOSE 8010

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; request = urllib.request.Request('http://127.0.0.1:8010/api/health', headers={'Host': os.environ.get('TIT_HEALTHCHECK_HOST', '127.0.0.1')}); urllib.request.urlopen(request, timeout=2).read()"]

STOPSIGNAL SIGTERM
CMD ["python", "scripts/run_api.py"]
