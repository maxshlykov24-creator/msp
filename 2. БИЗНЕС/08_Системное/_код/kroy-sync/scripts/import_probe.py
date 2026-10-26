"""Persist sanitized capabilities in the evaluation app; never stock movements."""
import hashlib
import json
from pathlib import Path
from sqlalchemy import select
from app.db import Entity, engine_for, transaction
from app.service import save


def import_probe(engine, rows):
    with transaction(engine) as s:
        for row in rows:
            key = 'integration:' + row['check']
            old = s.get(Entity, key)
            if old:
                save(old, row)
            else:
                s.add(Entity(id=key, kind='integration', version=1, data=row))


if __name__ == '__main__':
    import sys
    import_probe(engine_for(), json.loads(Path(sys.argv[1]).read_text()))
