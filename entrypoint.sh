#!/bin/bash
# Cron-Jobs laufen mit einer eigenen, minimalen Umgebung und erben nicht
# automatisch die Umgebungsvariablen von PID 1 (siehe crontab). Deshalb
# schreiben wir die aktuelle Container-Umgebung (egal ob per docker-compose
# env_file, `docker run -e`, oder z. B. über die Unraid-UI gesetzt) einmalig
# beim Start in /app/.env, damit die crontab-Zeile sie per `. /app/.env`
# sourcen kann.
declare -px > /app/.env

exec "$@"
