# Apache e PHP-FPM montados como o HestiaCP monta, com o OLS do fork ao lado.
#   docker build -f panel/test/hestia/Dockerfile -t olsbridge-hestia .
#   docker run --rm olsbridge-hestia
FROM ols-htaccess:runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        apache2 php8.3-fpm openssl curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY panel/bridge/olsbridge.py /usr/local/lsws/bin/olsbridge
COPY panel/test/hestia /test
RUN chmod 755 /usr/local/lsws/bin/olsbridge /test/setup.sh

CMD ["sh", "/test/run.sh"]
