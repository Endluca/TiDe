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
  CMD python -c "import os, urllib.request; r=urllib.request.Request('http://127.0.0.1:8010/api/health', headers={'Host': os.environ.get('TIT_HEALTHCHECK_HOST', '127.0.0.1')}); urllib.request.urlopen(r, timeout=3).read()" || exit 1

CMD ["python", "scripts/run_api.py"]
