#!/bin/sh
set -eu

: "${RELAY_PASSWORD:?RELAY_PASSWORD is required}"

mkdir -p /run/sshd /keys

if [ ! -f /keys/ssh_host_rsa_key ]; then
    ssh-keygen -q -t rsa -b 3072 -N "" -f /keys/ssh_host_rsa_key
fi

if ! id relay >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash relay
fi

touch /keys/authorized_keys
chown relay:relay /keys/authorized_keys
chmod 0600 /keys/authorized_keys

printf 'relay:%s\n' "$RELAY_PASSWORD" | chpasswd

exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
