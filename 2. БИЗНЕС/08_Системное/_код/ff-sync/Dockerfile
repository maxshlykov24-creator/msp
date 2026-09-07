FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Moscow

WORKDIR /app

# DejaVu нужен для этикеток: в slim-образе шрифтов нет, а встроенные в reportlab
# Vera и Helvetica кириллицу не умеют — артикул и наименование вышли бы квадратами.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Маской, а не перечислением: при перечислении новый модуль легко забыть,
# и контейнер падает на импорте уже в проде. Так было с registry.py и statuses.py.
COPY schema.sql ./
COPY *.py ./
COPY webui ./webui

CMD ["python", "main.py"]
