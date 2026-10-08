from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = 'anthropic/claude-haiku-5.5'

def config():
    # This module never loads the production .env or imports bot.config.
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1); values[k.strip()] = v.strip()
    values.update({k:v for k,v in os.environ.items() if k.startswith('LAB_')})
    if values.get('LAB_MODEL', MODEL) != MODEL:
        raise RuntimeError('Lab model must be Haiku 5.5; fallback disabled')
    return values
