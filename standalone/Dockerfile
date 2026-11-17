FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin bank \
 && mkdir -p /data /config && chown bank:bank /data

COPY app ./app

USER bank
# files created by the app are readable by the owner only
CMD ["sh", "-c", "umask 077 && exec uvicorn app.web:app --host 0.0.0.0 --port 8080 --proxy-headers --no-server-header"]

HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1
