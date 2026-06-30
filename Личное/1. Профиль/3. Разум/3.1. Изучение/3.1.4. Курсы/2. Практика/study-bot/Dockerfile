FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY . /app

# Локальный `config.py` не попадает в контекст (.dockerignore) — для контейнера берём env-based шаблон.
RUN cp /app/config_example.py /app/config.py

RUN mkdir -p /app/Распределение

CMD ["python", "bot.py"]
