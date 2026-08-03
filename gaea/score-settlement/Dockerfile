# ========== Stage 1: install Python dependencies ==========
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

# ========== Stage 2: singleton score-settlement runtime ==========
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
    TIT_SCORE_WORKER_HEARTBEAT=/tmp/tit-score-worker-heartbeat

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
COPY --chown=gaea:gaea backend/app ./app
COPY --chown=gaea:gaea backend/scripts ./scripts
RUN chown gaea:gaea /app

USER gaea

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "scripts/settle_shared_task_scores.py", "--healthcheck", "--max-heartbeat-age-seconds", "90"]

STOPSIGNAL SIGTERM
CMD ["python", "scripts/settle_shared_task_scores.py", "--watch", "--max-events", "25", "--interval-seconds", "3"]
