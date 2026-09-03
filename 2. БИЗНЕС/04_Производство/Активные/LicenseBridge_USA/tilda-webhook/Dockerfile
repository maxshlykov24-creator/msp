# Зеркало ECR вместо Docker Hub: анонимные пуллы с hub упираются в лимит 429
# при нескольких деплоях подряд (проверено 2026-07-24).
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app /app/app
COPY migrations /app/migrations

ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
