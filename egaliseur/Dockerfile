FROM python:3.12-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY bot /app/bot
COPY egaliseur /app/egaliseur
ENV PYTHONPATH=/app/bot:/app/egaliseur
ENV PYTHONUNBUFFERED=1
ENV EGALISEUR_OPERATION_MODE=test_only
ENV EGALISEUR_RESTRICTED_MODE=true

WORKDIR /app/egaliseur
CMD ["python", "-m", "ultium_egaliseur.runner"]
