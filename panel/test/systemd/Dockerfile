# O mesmo ambiente do HestiaCP, com systemd, para testar a troca do serviço do Apache.
#   docker build -f panel/test/systemd/Dockerfile -t olsbridge-systemd .
#   sh panel/test/systemd/run.sh
FROM olsbridge-hestia

RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv \
    && rm -rf /var/lib/apt/lists/* \
    && systemctl mask systemd-logind.service getty.target console-getty.service \
    && systemctl disable apache2 php8.3-fpm 2>/dev/null; true

COPY panel/test/systemd/cycle.sh /test/cycle.sh
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
