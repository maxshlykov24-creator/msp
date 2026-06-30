#!/usr/bin/env python3
"""Deploy dg-questions-bot to VPS.

Auth (priority order):
- DEPLOY_SSH_PASSWORD — root password (never store in repo)
- DEPLOY_SSH_KEY      — path to private key
- otherwise ssh-agent / ~/.ssh/*

Usage:
    DEPLOY_SSH_PASSWORD='yourpass' python scripts/_deploy_to_vps.py
"""

from __future__ import annotations

import os
import pathlib
import sys
import tarfile
import tempfile

import paramiko

HOST = "72.56.123.137"
USER = "root"
REMOTE = "/opt/dg-questions-bot"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _connect(client: paramiko.SSHClient) -> None:
    password = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    key_path_raw = os.environ.get("DEPLOY_SSH_KEY", "").strip()

    if password:
        client.connect(
            HOST, username=USER, password=password,
            timeout=45, allow_agent=False, look_for_keys=False,
        )
        return

    if key_path_raw:
        path = pathlib.Path(key_path_raw).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"missing key file: {path}")
        passphrase = os.environ.get("DEPLOY_SSH_KEY_PASSPHRASE") or None
        key_pass = passphrase.encode("utf-8") if passphrase else None
        pkey = None
        for Loader in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                pkey = Loader.from_private_key_file(str(path), password=key_pass)
                break
            except Exception:
                continue
        if pkey is None:
            raise ValueError("could not load private key")
        client.connect(HOST, username=USER, pkey=pkey, timeout=45)
        return

    client.connect(HOST, username=USER, timeout=45, allow_agent=True, look_for_keys=True)


def _tar_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    p = ti.name
    if "/__pycache__/" in p or p.endswith(".pyc") or ".venv/" in p:
        return None
    if ".DS_Store" in p or ".env" == p.split("/")[-1]:
        return None
    return ti


DEPLOY_ITEMS = [
    "app",
    "deploy",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "questions.json",
    ".env.example",
    ".gitignore",
]


def main() -> int:
    missing = [n for n in DEPLOY_ITEMS if not (ROOT / n).exists()]
    if missing:
        print(f"missing files: {missing}", file=sys.stderr)
        return 1

    env_path = ROOT / ".env"
    if not env_path.exists():
        print("ERROR: .env not found. Copy .env.example → .env and fill BOT_TOKEN.", file=sys.stderr)
        return 1

    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        tgz = pathlib.Path(tmp.name)

    try:
        with tarfile.open(tgz, "w:gz") as tar:
            for name in DEPLOY_ITEMS:
                tar.add(ROOT / name, arcname=name, filter=_tar_filter)
            # also pack .env separately
            tar.add(env_path, arcname=".env")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            _connect(client)
        except paramiko.ssh_exception.AuthenticationException:
            print(
                "SSH auth failed. Set DEPLOY_SSH_PASSWORD or DEPLOY_SSH_KEY.",
                file=sys.stderr,
            )
            return 3
        except Exception as e:
            print(f"SSH connect failed: {e}", file=sys.stderr)
            return 4

        remote_tgz = "/tmp/dg-questions-bot.tgz"
        sftp = client.open_sftp()
        print(f"Uploading archive…")
        sftp.put(str(tgz), remote_tgz)
        sftp.close()

        script = f"""set -e
mkdir -p {REMOTE}/data
rm -rf {REMOTE}/app {REMOTE}/deploy 2>/dev/null || true
tar xzf {remote_tgz} -C {REMOTE}
chmod 600 {REMOTE}/.env 2>/dev/null || true
cd {REMOTE}
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found on server" >&2
  exit 1
fi
docker compose down
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs --tail=40
"""
        _, stdout, stderr = client.exec_command(script, get_pty=True)
        print(stdout.read().decode())
        es = stderr.read().decode().strip()
        if es:
            print(es, file=sys.stderr)
        code = stdout.channel.recv_exit_status()
        client.close()
        return code
    finally:
        tgz.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
