FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Moscow

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY schema.sql net.py db.py ms.py ping.py cli.py catalog_pull.py \
     products_push.py intake.py agents_sync.py orders_pull.py account.py \
     billing.py export_xlsx.py shipments_pull.py web.py main.py ./
COPY webui ./webui

CMD ["python", "main.py"]
