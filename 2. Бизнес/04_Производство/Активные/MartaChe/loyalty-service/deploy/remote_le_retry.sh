#!/usr/bin/env bash
# One-shot: acme.sh (ZeroSSL) + at для повторного certbot после снятия лимита LE.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
FQDN="${1:-martachepl.neurosell-tilda-ms-order-hook-5f6f.twc1.net}"
apt-get update -qq
apt-get install -y -qq curl at
systemctl enable --now atd
if ! test -d /root/.acme.sh; then
  curl -s https://get.acme.sh | sh -s email=admin@localhost
fi
export PATH="/root/.acme.sh:$PATH"
set +e
/root/.acme.sh/acme.sh --issue -d "$FQDN" -w /var/www/letsencrypt --server zerossl
ACME_EXIT=$?
set -e
echo "acme.sh ZeroSSL exit: $ACME_EXIT"

echo "План: повтор certbot (Let's Encrypt) через 75 мин, лог: /root/letsencrypt-retry.log"
# shellcheck disable=SC2016
echo 'certbot --nginx -d '"$FQDN"' --register-unsafely-without-email --agree-tos --non-interactive 2>&1 | tee -a /root/letsencrypt-retry.log' | at now + 75 minutes
atq
