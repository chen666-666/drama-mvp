"""Dedicated synthetic reuse acceptance server, separate from existing UI services."""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = os.environ.get('DRAMA_DATABASE_URL', '')
if not url.startswith('sqlite:///'):
    raise SystemExit('Use a synthetic reuse SQLite database')
database = Path(url.removeprefix('sqlite:///')).resolve()
if database.parent != ROOT/'data' or not database.name.startswith('test-reuse-ui-') or not database.is_file():
    raise SystemExit('Use an existing data/test-reuse-ui-*.db fixture only')
import auth
import uvicorn
os.environ.update(DRAMA_SITE_ORIGIN='http://127.0.0.1:8005', DRAMA_ADMIN_USER='synthetic-admin',
    DRAMA_ADMIN_PASSWORD_HASH=auth.password_hash('synthetic-password'), DRAMA_SESSION_SECRET='synthetic-reuse-ui-'*4)
with tempfile.TemporaryDirectory() as directory:
    auth.PRIVATE = Path(directory)
    uvicorn.run('main:app', host='127.0.0.1', port=8005, log_level='warning')
