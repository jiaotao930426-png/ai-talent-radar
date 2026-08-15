FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    TALENT_RADAR_HOST=0.0.0.0 \
    TALENT_RADAR_PORT=8765 \
    TALENT_RADAR_DB=/data/talent_radar.db

WORKDIR /app

COPY requirements.txt ./
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir --requirement requirements.txt \
    && groupadd --gid 10001 radar \
    && useradd --uid 10001 --gid radar --home-dir /home/radar --create-home --shell /usr/sbin/nologin radar \
    && mkdir -p /data \
    && chown radar:radar /data

COPY --chown=radar:radar . .

USER 10001:10001

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port=os.environ.get('TALENT_RADAR_PORT', '8765'); urllib.request.urlopen('http://127.0.0.1:' + port + '/api/health', timeout=3).close()"]

STOPSIGNAL SIGTERM
CMD ["python", "app.py"]
