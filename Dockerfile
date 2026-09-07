FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY order_lunch.py .
# schulferien.json enthält nur öffentliche Ferientermine (keine
# Zugangsdaten) und wird daher fest ins Image gebacken. config.json (mit
# den echten Zugangsdaten pro Kind) NICHT hierher kopieren - die kommt zur
# Laufzeit als Volume vom Host, siehe docker-compose.yml.
COPY schulferien.json .

# Screenshots/Logs bei Fehlern landen hier -> als Volume mounten
RUN mkdir -p /data

# Cron im Container: läuft z. B. täglich um 06:00 Uhr
# (Alternative: Cron auf dem Unraid-Host selbst, der `docker run` aufruft –
#  dann kann dieser CMD entfallen und der Container läuft "on demand")
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*
COPY crontab /etc/cron.d/lunch-order-cron
RUN chmod 0644 /etc/cron.d/lunch-order-cron && crontab /etc/cron.d/lunch-order-cron

CMD ["cron", "-f"]
