# Данные для шага 14

## Файл от владельца

Скопируйте экспорт МойСклад в репозиторий:

```text
detensor/data/nomenclature_original.csv
```

Исходный файл (у вас на Mac) или вставка из экспорта MS (таб):

`20260519215507_admin@spine-shop_1208229526 - Sheet0.csv`

Сохраните как `data/nomenclature_export_user.tsv` (разделитель — таб).

Колонки экспорта: **Наименование** → длинное имя, **Доп. поле: Артикул** (последняя колонка) → артикул.

```bash
cd /workspace/detensor
python3 scripts/normalize_moysklad_export.py data/nomenclature_export_user.tsv --only-rename-map
# или CSV из Excel:
python3 scripts/normalize_nomenclature_csv.py "путь/к/Sheet0.csv"
```

## Резерв до загрузки CSV

`nomenclature_original_from_snapshot.csv` — 84 артикула из `RENAME_MAP`, длинные имена из `all_products.json`.

Использовать как вход step14:

```bash
cp data/nomenclature_original_from_snapshot.csv data/nomenclature_original.csv
```
