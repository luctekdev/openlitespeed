#!/bin/sh
# Troca nginx, Apache e PHP-FPM pelo OLS e repete o que o painel faz com eles.
set -u
IP=$(hostname -i | awk '{print $1}')
status=0

ok() { echo "[ok] $1"; }
fail() { echo "[FALHOU] $1"; status=1; }
expect() {
    label=$1 expected=$2 got=$3
    if [ "$got" = "$expected" ]; then ok "$label: $got"; else fail "$label: '$got', esperado '$expected'"; fi
}
get() { curl -s -H "Host: alpha.test" "http://$IP$1"; }
code() { curl -s -o /dev/null -w '%{http_code}' -H "Host: alpha.test" "http://$IP$1"; }
server() { curl -s -o /dev/null -D - -H "Host: alpha.test" "http://$IP/who.txt" | tr -d '\r' |
           awk -F': ' 'tolower($1) == "server" {print $2}'; }
running() { pgrep -x "$1" >/dev/null && echo sim || echo não; }

status=$(cat /tmp/olsb-status)
systemctl reload nginx && systemctl restart apache2 && sleep 1
expect "depois de reload do nginx e restart do Apache" 200 "$(code /info.php)"
systemctl restart olsbridge && sleep 2
expect "depois de reiniciar o OLS (boot)" 200 "$(code /info.php)"
systemctl stop nginx && sleep 1
expect "stop do nginx não derruba o site" 200 "$(code /who.txt)"
systemctl start nginx

/usr/local/lsws/bin/olsbridge uninstall
sleep 2
expect "desinstalado: servidor em :80" nginx "$(server | cut -d/ -f1)"
expect "PHP de volta ao PHP-FPM" fpm-fcgi "$(get /info.php | cut -d' ' -f1)"
expect "processo do nginx" sim "$(running nginx)"
expect "processo do PHP-FPM" sim "$(running php-fpm8.4)"
exit $status
