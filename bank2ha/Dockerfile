# Built locally by the HA Supervisor. A plain Python image is used (init: false),
# so no s6/bashio is needed – app/addon.py reads options and the MQTT service itself.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
COPY rules.example.yaml .

CMD ["python", "-m", "app.addon"]
