"""Explicit admin CLI; never run a migration on import or API startup."""
import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path

from .db import Base, User, engine_for, transaction
from .service import PERMISSIONS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'user', 'revoke'])
    parser.add_argument('--id')
    parser.add_argument('--name')
    parser.add_argument('--role', choices=PERMISSIONS, default='viewer')
    parser.add_argument('--token-file')
    args = parser.parse_args()
    engine = engine_for()
    if args.command == 'init':
        Base.metadata.create_all(engine)
        print('Schema initialized; no business data written')
        return
    if not args.id:
        parser.error('--id is required')
    with transaction(engine) as s:
        user = s.get(User, args.id)
        if args.command == 'revoke':
            if not user:
                parser.error('User not found')
            user.active = False
            print('Access revoked')
            return
        if not args.token_file or not args.name:
            parser.error('--token-file and --name required')
        if user:
            parser.error('Existing user: revoke first; create a distinct credential identity')
        token = secrets.token_urlsafe(32)
        target = Path(args.token_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(token)
        s.add(User(id=args.id, name=args.name, role=args.role, token_hash=hashlib.sha256(token.encode()).hexdigest()))
    print('User created; credential saved to protected file, not printed')


if __name__ == '__main__':
    main()
