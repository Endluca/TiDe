FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 tide \
    && useradd --uid 10001 --gid tide --no-create-home --shell /usr/sbin/nologin tide

COPY requirements.txt ./
RUN pip install --requirement requirements.txt

COPY alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

RUN chown -R tide:tide /app
USER tide

EXPOSE 8010

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=3).read()" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8010 --workers ${TIT_API_WORKERS:-2} --proxy-headers --forwarded-allow-ips=${TIT_TRUSTED_PROXY_IPS:-127.0.0.1}"]
