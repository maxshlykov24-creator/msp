#!/usr/bin/env python3
"""Деплой на VPS.

Аутентификация (в порядке приоритета):
- DEPLOY_SSH_PASSWORD — пароль root (не хранить в репозитории);
- DEPLOY_SSH_KEY — путь к приватному ключу (RSA/Ed25519/ECDSA);
- иначе пробуется ssh-agent и ~/.ssh/* (если ключ уже добавлен на сервер).

Опционально: DEPLOY_SSH_KEY_PASSPHRASE для зашифрованного ключа.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tarfile
import tempfile

import paramiko

# Прод с 2026-07-31: VPS DKAcademy (ai-msp), не LicenseBridge-хаб.
HOST = os.environ.get("DEPLOY_SSH_HOST", "194.87.226.234")
USER = "root"
REMOTE = "/opt/emmanuel-report-bot"
DEFAULT_KEY = str(pathlib.Path.home() / ".ssh" / "dkacademy_analytics_deploy")
ROOT = pathlib.Path(__file__).resolve().parents[1]


def _connect(client: paramiko.SSHClient) -> None:
    password = os.environ.get("DEPLOY_SSH_PASSWORD", "").strip()
    key_path_raw = os.environ.get("DEPLOY_SSH_KEY", "").strip()

    if password:
        client.connect(
            HOST,
            username=USER,
            password=password,
            timeout=45,
            allow_agent=False,
            look_for_keys=False,
        )
        return

    if not key_path_raw and pathlib.Path(DEFAULT_KEY).is_file():
        key_path_raw = DEFAULT_KEY

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

    client.connect(
        HOST,
        username=USER,
        timeout=45,
        allow_agent=True,
        look_for_keys=True,
    )


def tar_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
    p = ti.name
    if "/__pycache__/" in p or p.endswith(".pyc") or p.startswith(".venv/"):
        return None
    if ".DS_Store" in p:
        return None
    return ti


def main() -> int:
    items = [
        "app",
        "deploy",
        "Dockerfile",
        "docker-compose.yml",
        "requirements.txt",
        ".gitignore",
        ".env.example",
        ".env",
    ]
    for name in items:
        if not (ROOT / name).exists():
            print(f"missing: {name}", file=sys.stderr)
            return 1

    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        tgz = pathlib.Path(tmp.name)

    try:
        with tarfile.open(tgz, "w:gz") as tar:
            for name in items:
                tar.add(ROOT / name, arcname=name, filter=tar_filter)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            _connect(client)
        except paramiko.ssh_exception.AuthenticationException:
            print(
                "SSH auth failed. Set DEPLOY_SSH_PASSWORD or DEPLOY_SSH_KEY, "
                "or add your pubkey to root@ server's authorized_keys.",
                file=sys.stderr,
            )
            return 3
        except Exception as e:
            print(f"SSH connect failed: {e}", file=sys.stderr)
            return 4

        sftp = client.open_sftp()
        remote_tgz = "/tmp/emmanuel-report-bot.tgz"
        sftp.put(str(tgz), remote_tgz)
        sftp.close()

        script = f"""set -e
mkdir -p {REMOTE}/data
rm -rf {REMOTE}/app {REMOTE}/deploy 2>/dev/null || true
tar xzf {remote_tgz} -C {REMOTE}
chmod 600 {REMOTE}/.env 2>/dev/null || true
cd {REMOTE}
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found" >&2
  exit 1
fi
docker compose down
docker compose build
docker compose up -d
docker compose ps
docker compose logs --tail=35
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
