#!/bin/bash
# Cron-Jobs laufen mit einer eigenen, minimalen Umgebung und erben nicht
# automatisch die Umgebungsvariablen von PID 1 (siehe crontab). Deshalb
# schreiben wir die aktuelle Container-Umgebung (egal ob per docker-compose
# env_file, `docker run -e`, oder z. B. über die Unraid-UI gesetzt) einmalig
# beim Start in /app/.env, damit die crontab-Zeile sie per `. /app/.env`
# sourcen kann.
declare -px > /app/.env

# Sichtbarer Startbeleg im selben Log, das auch die Cron-Läufe schreiben -
# so lässt sich sofort nach dem Start prüfen, ob der Container mit der
# erwarteten Konfiguration hochgefahren ist, statt bis zum nächsten
# 06:00-Uhr-Lauf warten zu müssen.
mkdir -p /data
{
    echo "[entrypoint] Container gestartet: $(date -Iseconds), TZ=${TZ:-nicht gesetzt}, DRY_RUN=${DRY_RUN:-nicht gesetzt}"
    if [ -f /app/config.json ]; then
        anzahl_kinder=$(python3 -c 'import json,sys; print(len(json.load(open("/app/config.json"))))' 2>&1)
        echo "[entrypoint] /app/config.json gefunden, Kinder: $anzahl_kinder"
    else
        echo "[entrypoint] WARNUNG: /app/config.json fehlt - Cron-Lauf wird fehlschlagen"
    fi
} >> /data/order_lunch.log 2>&1

exec "$@"
