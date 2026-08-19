# syntax=docker/dockerfile:1.7

ARG DTS_OFFICIAL_JAR_SHA256=8a1c484a7c01fc5e684b57eb720a4757f3652027c6d1fea41bc5f69451556ef0

# ========== Stage 1: compile the Java 8 transport against the pinned official DTS bundle ==========
FROM hub.51talk.biz/ci/maven:oraclejdk1.8-maven3 AS java-build
ARG DTS_OFFICIAL_JAR_SHA256

WORKDIR /build
COPY gaea/dts-diagnose/dts_subscribe_sdk_dep_demo-1.0-SNAPSHOT-jar-with-dependencies.jar /build/dts-diagnose.jar
COPY gaea/dts-ingest/java /build/src
RUN echo "${DTS_OFFICIAL_JAR_SHA256}  /build/dts-diagnose.jar" \
    | sha256sum -c - \
    && mkdir -p /build/classes \
    && find /build/src -type f -name '*.java' -print | sort > /build/java-sources.list \
    && test -s /build/java-sources.list \
    && javac -encoding UTF-8 -source 1.8 -target 1.8 \
      -cp /build/dts-diagnose.jar \
      -d /build/classes \
      @/build/java-sources.list \
    && java -cp /build/classes:/build/dts-diagnose.jar \
      com.aliyun.dts.subscribe.clients.TitDtsTransportBridgeAvroSelfTest \
    && jar cf /build/dts-transport.jar -C /build/classes .

# ========== Stage 2: install the DTS-only Python dependency closure ==========
FROM hub.51talk.biz/library/python:3.12-alpine AS python-build

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories \
    && apk add --no-cache \
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

# ========== Stage 3: Java 8 transport + Python 3.12 durable ingest runtime ==========
FROM hub.51talk.biz/library/python:3.12-alpine AS runtime
ARG DTS_OFFICIAL_JAR_SHA256
LABEL maintainer="51talk SRE" \
      org.opencontainers.image.title="TIT DTS official transport ingest" \
      com.51talk.tit.dts-ingest.jar-sha256="${DTS_OFFICIAL_JAR_SHA256}"

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.ustc.edu.cn/g' /etc/apk/repositories \
    && apk add --no-cache \
      ca-certificates \
      gcompat \
      libffi \
      libgcc \
      libpq \
      libstdc++ \
      openjdk8-jre-base \
      tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && addgroup -g 1001 gaea \
    && adduser -u 1001 -G gaea -D gaea \
    && mkdir -p /app/operations /deployments/config /deployments/logs \
    && chown -R gaea:gaea /app /deployments

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    JAVA_TOOL_OPTIONS="-Xms64m -Xmx256m -Xss512k -Dlog4j.configuration=file:/deployments/config/log4j.properties" \
    TIT_PROCESS_PROFILE=dts-ingest \
    TIT_DTS_TRANSPORT=official_java \
    TIT_DTS_STARTUP_RETRY_SECONDS=15 \
    TIT_DTS_INGEST_HEARTBEAT=/tmp/tit-dts-ingest-heartbeat \
    TIT_DTS_INGEST_READINESS=/tmp/tit-dts-ingest-readiness

WORKDIR /app/operations
COPY --chown=gaea:gaea --from=python-build /opt/venv /opt/venv
COPY --chown=gaea:gaea --from=java-build /build/dts-transport.jar /deployments/dts-transport.jar
COPY --chown=gaea:gaea --from=java-build /build/dts-diagnose.jar /deployments/dts-diagnose.jar
COPY --chown=gaea:gaea gaea/dts-ingest/log4j-bridge.properties /deployments/config/log4j.properties
COPY --chown=gaea:gaea backend/app ./app
COPY --chown=gaea:gaea backend/scripts/run_dts_ingest.py ./scripts/run_dts_ingest.py
RUN echo "${DTS_OFFICIAL_JAR_SHA256}  /deployments/dts-diagnose.jar" \
    | sha256sum -c -

USER gaea

HEALTHCHECK --interval=30s --timeout=20s --start-period=360s --retries=3 \
  CMD ["/opt/venv/bin/python", "/app/operations/scripts/run_dts_ingest.py", "--healthcheck", "--heartbeat-path", "/tmp/tit-dts-ingest-heartbeat", "--readiness-path", "/tmp/tit-dts-ingest-readiness", "--max-heartbeat-age-seconds", "90"]

STOPSIGNAL SIGTERM
CMD ["/opt/venv/bin/python", "/app/operations/scripts/run_dts_ingest.py", "--watch", "--max-messages", "500", "--max-projection-keys", "1000", "--projection-time-budget-seconds", "20", "--idle-timeout-ms", "10000", "--interval-seconds", "3", "--heartbeat-path", "/tmp/tit-dts-ingest-heartbeat", "--readiness-path", "/tmp/tit-dts-ingest-readiness"]
