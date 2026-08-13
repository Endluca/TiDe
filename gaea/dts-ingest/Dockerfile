# syntax=docker/dockerfile:1.7

# ========== Stage 1: install the DTS-only Python dependency closure ==========
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
COPY backend/requirements-dts-ingest.txt ./requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir --requirement requirements.txt

# ========== Stage 2: lightweight single-process DTS runtime ==========
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
    TIT_PROCESS_PROFILE=dts-ingest \
    TIT_DTS_INGEST_HEARTBEAT=/tmp/tit-dts-ingest-heartbeat \
    TIT_DTS_INGEST_READINESS=/tmp/tit-dts-ingest-readiness

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

WORKDIR /app/operations
COPY --chown=gaea:gaea --from=python-build /opt/venv /opt/venv
COPY --chown=gaea:gaea backend/app ./app
COPY --chown=gaea:gaea backend/scripts/run_dts_ingest.py ./scripts/run_dts_ingest.py

USER gaea

HEALTHCHECK --interval=30s --timeout=20s --start-period=120s --retries=3 \
  CMD ["/opt/venv/bin/python", "/app/operations/scripts/run_dts_ingest.py", "--healthcheck", "--heartbeat-path", "/tmp/tit-dts-ingest-heartbeat", "--readiness-path", "/tmp/tit-dts-ingest-readiness", "--max-heartbeat-age-seconds", "90"]

STOPSIGNAL SIGTERM
CMD ["/opt/venv/bin/python", "/app/operations/scripts/run_dts_ingest.py", "--watch", "--max-messages", "100", "--max-projection-keys", "200", "--idle-timeout-ms", "10000", "--interval-seconds", "3", "--heartbeat-path", "/tmp/tit-dts-ingest-heartbeat", "--readiness-path", "/tmp/tit-dts-ingest-readiness"]
