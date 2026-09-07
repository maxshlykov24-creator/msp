"""Синхронизация + переключение Sheet1 одним шагом (после ключей CME)."""
from __future__ import annotations

import logging
import sys

from app.setup_sheet1 import main as setup_main
from app.sync import run_once

log = logging.getLogger("cutover")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    result = run_once()
    print("sync:", result)
    if not result.startswith("ok"):
        print("cutover abort: sync не записал Данные, Sheet1 не трогаем", file=sys.stderr)
        return 1
    sys.argv = ["setup_sheet1", "--apply"]
    return setup_main()


if __name__ == "__main__":
    raise SystemExit(main())
