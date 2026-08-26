FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY data/schema.sql ./data/schema.sql
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# NFA-09: eigener, nicht-root Nutzer als Grundlage fuer spaeteres
# Docker-Hardening (Sprint 6); Datenverzeichnis bleibt beschreibbar.
RUN useradd -m -u 1000 chargeapp \
    && mkdir -p /srv/data \
    && chown -R chargeapp:chargeapp /srv
USER chargeapp

EXPOSE 8501 9000

ENV CHARGE_DB_PATH=/srv/data/charging.db

ENTRYPOINT ["./entrypoint.sh"]
