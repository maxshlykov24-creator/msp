import json
import os
from pathlib import Path
import sys
ROOT = Path('/opt/kroy-app-stage')
sys.path.insert(0, str(ROOT))
for line in (ROOT / '.env.app').read_text().splitlines():
    key, value = line.split('=', 1)
    os.environ[key] = value
from app.db import engine_for
from app.mappings import import_candidates
print(json.dumps(import_candidates(engine_for(), json.loads((ROOT / 'wb-catalog.json').read_text()),
                                   json.loads((ROOT / 'ozon-catalog.json').read_text()))))
