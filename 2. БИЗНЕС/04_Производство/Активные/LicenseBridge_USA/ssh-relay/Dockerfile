FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/*

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh

RUN chmod 0755 /entrypoint.sh

EXPOSE 2222

ENTRYPOINT ["/entrypoint.sh"]
