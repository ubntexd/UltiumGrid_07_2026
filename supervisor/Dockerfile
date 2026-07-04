FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bot /app/bot
COPY supervisor /app/supervisor
ENV PYTHONPATH=/app/bot:/app/supervisor
ENV PYTHONUNBUFFERED=1

WORKDIR /app/supervisor
CMD ["python", "-m", "ultium_supervisor.watchdog"]
