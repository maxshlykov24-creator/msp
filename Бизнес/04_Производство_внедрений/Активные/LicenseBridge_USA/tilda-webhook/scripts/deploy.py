#!/usr/bin/env python3
"""Деплой tilda-webhook на VPS 72.56.123.137"""
from __future__ import annotations

import os
import pathlib
import sys
import tarfile
import tempfile

import paramiko

HOST = "72.56.123.137"
USER = "root"
REMOTE = "/opt/licensebridge-tilda-webhook"
ROOT = pathlib.Path(__file__).resolve().parents[1]


def connect(client: paramiko.SSHClient) -> None:
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        raise SystemExit("DEPLOY_SSH_PASSWORD required")
    client.connect(HOST, username=USER, password=pw, timeout=45, allow_agent=False, look_for_keys=False)


def main() -> int:
    token = os.environ.get("KOMMO_TOKEN", "").strip()
    if not token:
        raise SystemExit("KOMMO_TOKEN required")

    items = ["app", "Dockerfile", "docker-compose.yml", "Caddyfile", ".env.example"]
    for name in items:
        if not (ROOT / name).exists() and not (ROOT / name.split("/")[0]).exists():
            print(f"missing: {name}", file=sys.stderr)
            return 1

    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        tgz = pathlib.Path(tmp.name)
    with tarfile.open(tgz, "w:gz") as tar:
        for name in items:
            tar.add(ROOT / name, arcname=name)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        connect(client)
    except Exception as e:
        print(f"SSH failed: {e}", file=sys.stderr)
        return 2

    sftp = client.open_sftp()
    remote_tgz = "/tmp/lb-tilda-webhook.tgz"
    sftp.put(str(tgz), remote_tgz)
    sftp.close()
    tgz.unlink(missing_ok=True)

    env_content = f"""KOMMO_TOKEN={token}
KOMMO_BASE=https://licensebridgeusa.kommo.com/api/v4
KOMMO_PIPELINE_ID=11274779
KOMMO_STATUS_ID=108112044
KOMMO_RESPONSIBLE=13291175
"""
    script = f"""set -e
mkdir -p {REMOTE}
tar xzf {remote_tgz} -C {REMOTE}
cat > {REMOTE}/.env << 'ENVEOF'
{env_content}ENVEOF
chmod 600 {REMOTE}/.env
cd {REMOTE}
docker compose down 2>/dev/null || true
docker compose build --no-cache
docker compose up -d
sleep 3
docker compose ps
docker compose logs --tail=30
curl -sS -o /dev/null -w "local_health=%{{http_code}}\\n" http://127.0.0.1:8080/health || true
"""
    _, stdout, stderr = client.exec_command(script, get_pty=True)
    print(stdout.read().decode())
    err = stderr.read().decode().strip()
    if err:
        print(err, file=sys.stderr)
    code = stdout.channel.recv_exit_status()
    client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
