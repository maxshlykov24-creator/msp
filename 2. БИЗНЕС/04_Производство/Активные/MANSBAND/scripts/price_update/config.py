"""Конфиг скрипта обновления прайса (созвон 09.09). Токен и приватные пути —
те же, что у scripts/supply_pipeline/: _private/ лежит на два уровня выше.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # MANSBAND/
PRIVATE = ROOT / "_private"
MOYSKLAD_ENV = PRIVATE / "moysklad.env"

# Отчёты --scan и логи --apply — отдельно от supply_pipeline, чтобы не путать
# прогоны поставки с прогонами прайса.
STATE_DIR = PRIVATE / "price_update"

MS_API = "https://api.moysklad.ru/api/remap/1.2"

RULES_FILE = Path(__file__).resolve().parent / "prices_2026-09.json"
