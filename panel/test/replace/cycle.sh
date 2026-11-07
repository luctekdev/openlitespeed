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

sh /test/setup.sh || { echo "[FALHOU] setup"; exit 1; }
expect "antes: servidor em :80" nginx "$(server | cut -d/ -f1)"
expect "antes: PHP pelo PHP-FPM" "fpm-fcgi alice 128M 33M basedir" "$(get /info.php | cut -d' ' -f1,3-)"
expect ".env pelo nginx" 404 "$(code /.env)"

/usr/local/lsws/bin/olsbridge install --mode replace || fail "install"
expect "servidor em :80" LiteSpeed "$(server)"
for unit in apache2 nginx php8.4-fpm olsbridge; do
    expect "$unit.service ativo" active "$(systemctl is-active $unit)"
done
expect "processo do nginx" não "$(running nginx)"
expect "processo do Apache" não "$(running apache2)"
expect "processo do PHP-FPM" não "$(running php-fpm8.4)"
expect "porta 8080 livre" "" "$(ss -Hltn "sport = :8080")"
expect "PHP pelo lsphp, com o usuário, o .user.ini e o pool" \
    "litespeed alice 128M 33M basedir" "$(get /info.php | cut -d' ' -f1,3-)"
expect ".env bloqueado como no nginx" 404 "$(code /.env)"
expect "página de erro do domínio" "404 de alpha" "$(get /nao-existe)"

# O painel muda o php.ini e reinicia o PHP-FPM da versão.
sed -i 's/^memory_limit = .*/memory_limit = 200M/' /etc/php/8.4/fpm/php.ini
systemctl restart php8.4-fpm
sleep 2
expect "php.ini depois do restart do php8.4-fpm" 200M "$(get /info.php | cut -d' ' -f4)"
# O painel muda o pool (limites, valores) e reinicia o PHP-FPM.
sed -i 's|^php_admin_value\[upload_tmp_dir\].*|&\nphp_admin_value[max_execution_time] = 99|' \
    /etc/php/8.4/fpm/pool.d/alpha.test.conf
echo '<?php echo ini_get("max_execution_time");' > /home/alice/web/alpha.test/public_html/t.php
chown alice:alice /home/alice/web/alpha.test/public_html/t.php
systemctl restart php8.4-fpm
sleep 2
expect "valor novo do pool" 99 "$(get /t.php)"

# Pool menor que o número de processos do OLS: nenhuma requisição pode ficar esperando
# um filho do lsphp preso à conexão de outro processo.
sed -i 's/^pm.max_children = .*/pm.max_children = 2/' /etc/php/8.4/fpm/pool.d/alpha.test.conf
systemctl restart php8.4-fpm
sleep 2
slow=0
for i in $(seq 1 30); do
    t=$(curl -s -m 10 -o /dev/null -w '%{time_total}' -H "Host: alpha.test" "http://$IP/t.php")
    [ "$(echo "$t > 3" | awk '{print ($1 > $3)}')" = 1 ] && slow=$((slow + 1))
done
for i in $(seq 1 30); do
    curl -s -m 10 -o /dev/null -w '%{time_total}\n' -H "Host: alpha.test" "http://$IP/t.php" &
done > /tmp/times
wait
slow=$((slow + $(awk '$1 > 3' /tmp/times | wc -l)))
expect "requisições lentas com pool de 2 filhos (60)" 0 "$slow"

# Proteções. O cliente aqui é o próprio container: nenhum proxy confiável, então os
# limites valem para ele.
/usr/local/lsws/bin/olsbridge set waf block >/dev/null || fail "set waf block"
sleep 2
expect "WAF: SQL injection bloqueada" 403 "$(code "/who.txt?id=1%27%20OR%201=1--")"
expect "WAF: página normal" 200 "$(code /who.txt?pagina=2)"
expect "WAF: PHP normal" 200 "$(code /t.php)"
/usr/local/lsws/bin/olsbridge set waf off >/dev/null
# O limite por IP é testado de outro container: o OLS libera os IPs locais (run.sh).

echo "$status" > /tmp/olsb-status
