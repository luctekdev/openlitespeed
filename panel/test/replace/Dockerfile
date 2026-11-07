# Ubuntu 26.04 com systemd, nginx na frente do Apache e PHP-FPM do Sury: o mesmo
# arranjo do HestiaCP no itzvpc2. O OLS vem do pacote de panel/package.sh.
#   sh panel/package.sh
#   docker build -f panel/test/replace/Dockerfile -t olsbridge-replace .
#   sh panel/test/replace/run.sh
FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive
# As listas do apt ficam: a ponte instala o phpX.Y-litespeed como faria no servidor.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg systemd systemd-sysv apache2 nginx python3 openssl \
        iproute2 procps libaio1t64 nghttp2-client \
    && curl -fsSL https://packages.sury.org/php/apt.gpg -o /usr/share/keyrings/sury.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/sury.gpg] https://packages.sury.org/php/ resolute main" \
        > /etc/apt/sources.list.d/php.list \
    && apt-get update && apt-get install -y --no-install-recommends php8.4-fpm

COPY panel/dist/ /pkg/
RUN tar xzf /pkg/olsbridge-*.tgz -C /tmp && sh /tmp/olsbridge-*/install.sh
COPY panel/bridge/olsbridge.py /usr/local/lsws/bin/olsbridge
COPY panel/test/replace /test
RUN chmod 755 /usr/local/lsws/bin/olsbridge

CMD ["/sbin/init"]
