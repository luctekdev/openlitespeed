#!/bin/sh
# Converte o exemplo e sobe um OLS real com o resultado.
#   sh htaccess-compat/tools/check_apache2ols.sh
set -eu
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1
image=${OLS_IMAGE:-ols-htaccess:runtime}
name=ols-apache2ols-check

docker rm -f "$name" >/dev/null 2>&1 || true
docker run -d --name "$name" -v "$PWD:/tools:ro" "$image" sh -c '
    set -e
    python3 /tools/apache2ols.py /tools/sample/apache2.conf --out /tmp/out \
        --server-root /tools/sample
    cat /tmp/out/avisos.txt
    cp -r /tmp/out/vhosts/* /usr/local/lsws/conf/vhosts/
    cat > /usr/local/lsws/conf/httpd_config.conf <<EOF
serverName ols
user nobody
group nogroup
autoRestart 0
disableWebAdmin 1
mime conf/mime.properties
errorlog logs/error.log {
    logLevel INFO
}
tuning {
    quicEnable 0
}
accessControl {
    allow ALL
    deny
}
EOF
    cat /tmp/out/httpd_vhosts.conf >> /usr/local/lsws/conf/httpd_config.conf
    chown -R lsadm:nogroup /usr/local/lsws/conf
    mkdir -p /srv/one/public /srv/two/public_html/sub
    echo home-one > /srv/one/public/home.html
    echo gone > /srv/one/public/missing.html
    echo index-two > /srv/two/public_html/index.html
    echo Header set X-Hta one > /srv/one/public/.htaccess
    echo Require all denied > /srv/two/public_html/sub/.htaccess
    chmod -R a+rX /srv
    exec /usr/local/lsws/bin/openlitespeed -n
' >/dev/null

check() {
    label=$1 expected=$2
    shift 2
    got=$(docker exec "$name" curl -s -o /dev/null -w '%{http_code}' "$@")
    if [ "$got" = "$expected" ]; then
        echo "[ok] $label: $got"
    else
        echo "[FALHOU] $label: $got esperado $expected"
        status=1
    fi
}

status=0
tries=0
until docker exec "$name" curl -s -o /dev/null http://127.0.0.1/ 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 30 ] || { docker logs "$name" | tail -n 20; exit 1; }
done

check "domínio principal" 200 -H 'Host: one.test' http://127.0.0.1/
check "alias" 200 -H 'Host: www.one.test' http://127.0.0.1/
check "regra de rewrite da vhost" 301 -H 'Host: one.test' http://127.0.0.1/old
check "segunda vhost" 200 -H 'Host: two.test' http://127.0.0.1/
check ".htaccess aplicado" 403 -H 'Host: two.test' http://127.0.0.1/sub/x
docker exec "$name" curl -s -D - -o /dev/null -H 'Host: one.test' http://127.0.0.1/ | grep -qi '^x-hta: one' \
    && echo "[ok] Header do .htaccess" || { echo "[FALHOU] Header do .htaccess"; status=1; }
docker exec "$name" curl -s -H 'Host: one.test' http://127.0.0.1/nada | grep -q gone \
    && echo "[ok] ErrorDocument da vhost" || { echo "[FALHOU] ErrorDocument da vhost"; status=1; }
echo "--- avisos do conversor"
docker logs "$name" 2>&1 | grep -E 'não convertida' | head -n 5 || true
docker rm -f "$name" >/dev/null
exit $status
