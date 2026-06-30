#!/usr/bin/env bash
# Первичная подготовка VPS (Ubuntu/Debian) под кассу MANSBAND.
# Запускать на сервере под root: bash setup-vps.sh
set -euo pipefail

echo "== Обновление системы =="
apt-get update && apt-get upgrade -y

echo "== Базовые пакеты =="
apt-get install -y ca-certificates curl gnupg ufw fail2ban nginx

echo "== Docker + compose plugin =="
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg || \
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "== Firewall (ufw) =="
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "== fail2ban =="
systemctl enable --now fail2ban

echo "== certbot (snap) =="
apt-get install -y certbot python3-certbot-nginx || true
mkdir -p /var/www/certbot

echo "== Каталог проекта =="
mkdir -p /opt/mansband-kassa

echo "Готово. Дальше: залить код (deploy.sh), создать .env, поднять docker compose, настроить nginx+certbot."
