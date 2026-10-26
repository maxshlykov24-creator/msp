"""Локальный запуск. --vault-access используется только с разрешения владельца."""
import argparse
import os
from pathlib import Path


def load_vault_access():
    vault = Path(__file__).resolve().parents[4]
    path = vault / "2. БИЗНЕС/08_Системное/ДОСТУПЫ.md"
    for line in path.read_text().splitlines():
        cells = [c.strip().strip('`') for c in line.split('|')[1:-1]]
        if len(cells) >= 4 and cells[0] == "ЛУКДЕКОР" and "katrina10" in cells[2]:
            os.environ["MOYSKLAD_LOGIN"], os.environ["MOYSKLAD_PASSWORD"] = cells[2], cells[3]
            return
    raise SystemExit("Нужная запись доступа не найдена")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-access", action="store_true")
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    if args.vault_access:
        load_vault_access()
    # Хост проверен GET-запросом из заказа katrina10 26.10.2026.
    os.environ.setdefault('TOPSELLER_LABEL_HOSTS', 'print.topseller.ru')
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=args.port)
