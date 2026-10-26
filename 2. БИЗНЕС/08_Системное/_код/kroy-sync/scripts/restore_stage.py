"""Restore a local dump into a NEW database, keeping existing DBs intact."""
import argparse
import re
import subprocess
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('dump')
p.add_argument('database')
a = p.parse_args()
if not re.fullmatch(r'kroy_restore_[a-z0-9_]+', a.database):
    p.error('Use a distinct kroy_restore_* database name')
base = ['docker', 'exec', '-i', 'kroy-app-stage-db']
subprocess.run(base + ['createdb', '-U', 'kroy', a.database], check=True)
raw = Path(a.dump).read_bytes()
listing = subprocess.run(base + ['pg_restore', '-l'], input=raw, capture_output=True, check=True).stdout.decode()
listing = '\n'.join(line for line in listing.splitlines() if ' SCHEMA - public ' not in line)+'\n'
subprocess.run(base + ['sh', '-c', 'cat > /tmp/kroy-restore-list'], input=listing.encode(), check=True)
subprocess.run(base + ['pg_restore', '-U', 'kroy', '-d', a.database, '-L', '/tmp/kroy-restore-list', '--exit-on-error'], input=raw, check=True)
print('Restored into new database; existing databases untouched')
