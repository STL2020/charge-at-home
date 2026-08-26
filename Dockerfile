FROM python:3.12-slim

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Das Schema gehoert NICHT nach /srv/data — dort haengt der Anwender sein
# Datenverzeichnis ein und ueberdeckt damit alles, was im Abbild liegt.
# Deshalb ein eigener Ort, den kein Volume trifft.
COPY data/schema.sql /srv/schema/schema.sql
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
ENV CHARGE_SCHEMA_PATH=/srv/schema/schema.sql

ENTRYPOINT ["./entrypoint.sh"]
