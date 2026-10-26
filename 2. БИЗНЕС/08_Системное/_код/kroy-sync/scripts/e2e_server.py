"""Local subpath fixture. Use only with a disposable test database."""
import os
from fastapi import FastAPI
from app.api import create_app
assert os.environ.get('KROY_DATABASE_URL', '').startswith('sqlite:///'), 'Only local test SQLite'
app = FastAPI()
app.mount('/yuji', create_app())
