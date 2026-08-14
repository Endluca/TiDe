# syntax=docker/dockerfile:1.7

FROM hub.51talk.biz/runtime/oraclejdk:1.8-debian11
LABEL maintainer="51talk SRE" \
      org.opencontainers.image.title="TIT DTS official diagnostic" \
      org.opencontainers.image.source="https://github.com/silly-fofo/dts-subscribe-demo/tree/48596de62f01ea7d4b84082b562c33e9e5350287" \
      com.51talk.tit.dts-diagnose.jar-sha256="8a1c484a7c01fc5e684b57eb720a4757f3652027c6d1fea41bc5f69451556ef0"

ENV TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    JAVA_TOOL_OPTIONS="-Xms128m -Xmx512m -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/deployments/logs" \
    TIT_DTS_DIAG_LOG_LEVEL=TRACE

RUN groupadd --gid 1001 gaea \
    && useradd --uid 1001 --gid 1001 --create-home --shell /bin/sh gaea \
    && mkdir -p /deployments/bin /deployments/config /deployments/logs \
    && chown -R gaea:gaea /deployments

WORKDIR /deployments/logs

COPY --chown=gaea:gaea gaea/dts-diagnose/dts_subscribe_sdk_dep_demo-1.0-SNAPSHOT-jar-with-dependencies.jar /deployments/dts-diagnose.jar
COPY --chown=gaea:gaea gaea/dts-diagnose/log4j-diagnose.properties /deployments/config/log4j.properties
COPY --chown=gaea:gaea --chmod=0755 gaea/dts-diagnose/run.sh /deployments/bin/run.sh
RUN echo "8a1c484a7c01fc5e684b57eb720a4757f3652027c6d1fea41bc5f69451556ef0  /deployments/dts-diagnose.jar" \
    | sha256sum -c -

USER gaea

HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
  CMD ["/bin/sh", "-c", "test -s /tmp/tit-dts-diagnose-java.pid && kill -0 \"$(cat /tmp/tit-dts-diagnose-java.pid)\""]

STOPSIGNAL SIGTERM
CMD ["/deployments/bin/run.sh"]
