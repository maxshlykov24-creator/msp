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


KEY = pathlib.Path.home() / ".ssh" / "licensebridge_hub_deploy"


def connect(client: paramiko.SSHClient) -> None:
    """Сначала ключ деплоя, затем пароль (DEPLOY_SSH_PASSWORD)."""
    if KEY.exists():
        try:
            client.connect(HOST, username=USER, key_filename=str(KEY), timeout=45,
                           allow_agent=False, look_for_keys=False)
            return
        except Exception as exc:
            print(f"key auth failed ({exc}), пробуем пароль", file=sys.stderr)
    pw = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    if not pw:
        raise SystemExit("нет ключа ~/.ssh/licensebridge_hub_deploy и DEPLOY_SSH_PASSWORD")
    client.connect(HOST, username=USER, password=pw, timeout=45, allow_agent=False, look_for_keys=False)


def main() -> int:
    # Секреты берём из локального .env (создан рядом с проектом, chmod 600).
    local_env = ROOT / ".env"
    if not local_env.exists() and not os.environ.get("KOMMO_TOKEN", "").strip():
        raise SystemExit("нужен tilda-webhook/.env или KOMMO_TOKEN в окружении")

    items = ["app", "migrations", "requirements.txt", "Dockerfile",
             "docker-compose.yml", "Caddyfile", ".env.example"]
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

    if local_env.exists():
        env_content = local_env.read_text(encoding="utf-8")
    else:
        env_content = "\n".join(
            f"{k}={os.environ.get(k, '')}" for k in (
                "KOMMO_TOKEN", "PLEEP_API_KEY", "KOMMO_WEBHOOK_SECRET", "INTERNAL_API_KEY",
            )
        ) + "\nKOMMO_BASE=https://licensebridgeusa.kommo.com/api/v4\n"
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
docker compose exec -T api python -c "import urllib.request as u; \
print('local_health=' + str(u.urlopen('http://127.0.0.1:8080/health', timeout=10).status))" || true
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
