#!/bin/sh
# Recursos por cliente (cgroups v2): o lsphp de cada usuário na fatia dele, com os
# limites da ponte ou do plano do HestiaCP. Um cliente queimando CPU não pode deixar o
# site de outro mais lento que o limite medido abaixo. Roda depois de cycle.sh.
set -u
IP=$(hostname -i | awk '{print $1}')
B=/usr/local/lsws/bin/olsbridge
C=/usr/local/lsws/conf/bridge/server.conf
A=/home/alice/web/alpha.test/public_html
G=/home/carol/web/gamma.test/public_html
CPUS=$(nproc)
HOGS=$((CPUS * 2))
status=0
ok() { echo "[ok] $1"; }
fail() { echo "[FALHOU] $1"; status=1; }
expect() {
    if [ "$3" = "$2" ]; then ok "$1: $3"; else fail "$1: '$3', esperado '$2'"; fi
}
code() { curl -s -m 30 -o /dev/null -w '%{http_code}' -H "Host: $1" "http://$IP$2"; }
slice() { echo "/sys/fs/cgroup/user.slice/user-$(id -u "$1").slice"; }
# Cgroup do lsphp de um usuário (o pai e os filhos estão no mesmo).
cgroup_of() { pid=$(pgrep -u "$1" -x lsphp8.4 | head -1); [ -n "$pid" ] && cut -d: -f3 /proc/$pid/cgroup; }

# Um terceiro cliente, carol, com PHP e o dobro de filhos que núcleos: o que ela queima
# passa dos núcleos do servidor, como um site com vários domínios no HestiaCP.
id carol >/dev/null 2>&1 || useradd -m -s /bin/bash carol
mkdir -p $G /home/carol/tmp
chown -R carol:carol /home/carol
chmod 711 /home/carol
cat > /etc/php/8.4/fpm/pool.d/gamma.test.conf <<EOF
[gamma.test]
listen = /run/php/php8.4-fpm-gamma.test.sock
listen.owner = carol
listen.group = www-data
listen.mode = 0660
user = carol
group = carol
pm = ondemand
pm.max_children = $HOGS
pm.process_idle_timeout = 10s
php_admin_value[memory_limit] = -1
php_admin_value[open_basedir] = $G:/home/carol/tmp:/tmp
EOF
cat > /etc/apache2/sites-enabled/gamma.test.conf <<EOF
<VirtualHost $IP:8080>
    ServerName gamma.test
    DocumentRoot $G
    <Directory $G>
        AllowOverride All
        Require all granted
    </Directory>
    <FilesMatch \.php$>
        SetHandler "proxy:unix:/run/php/php8.4-fpm-gamma.test.sock|fcgi://localhost"
    </FilesMatch>
</VirtualHost>
EOF
sed -e "s/%ip%/$IP/g" -e "s/%proxy_port%/80/g" -e "s/%web_port%/8080/g" \
    -e "s/%domain_idn%/gamma.test/g" -e "s/%alias_idn%//g" -e "s/%domain%/gamma.test/g" \
    -e "s|%home%|/home|g" -e "s/%user%/carol/g" -e "s/%web_system%/apache2/g" \
    -e "s|%docroot%|$G|g" -e "s/%proxy_extensions%/css|js|png|txt/g" \
    /test/fixtures/nginx.tpl > /etc/nginx/conf.d/domains/gamma.test.conf
echo '<?php set_time_limit(0); $t = microtime(true) + 15; $x = 0; while (microtime(true) < $t) { $x++; } echo $x;' > $G/burn.php
echo '<?php $a = str_repeat("x", 400 * 1024 * 1024); echo strlen($a);' > $G/mem.php
echo '<?php echo "ok";' > $G/ok.php
echo '<?php $x = 0; for ($i = 0; $i < 3000000; $i++) { $x += $i % 7; } echo $x;' > $A/cpu.php
chown carol:carol $G/*.php && chown alice:alice $A/cpu.php
systemctl restart php8.4-fpm   # como o painel: a ponte gera de novo e assume o pool
sleep 2

# 1. Ligado por padrão: o lscgid põe cada lsphp na fatia do dono, com os limites.
expect "cgroups no CGIRLimit" yes "$(grep -q '^    cgroups               2$' $C && echo yes || echo no)"
expect "gamma.test pelo lsphp" 200 "$(code gamma.test /ok.php)"
expect "alpha.test pelo lsphp" 200 "$(code alpha.test /cpu.php)"
expect "lsphp de carol na fatia dela" "/user.slice/user-$(id -u carol).slice/litespeed-exec.scope" \
    "$(cgroup_of carol)"
expect "lsphp de alice na fatia dela" "/user.slice/user-$(id -u alice).slice/litespeed-exec.scope" \
    "$(cgroup_of alice)"
expect "fatia de carol ativa (StopWhenUnneeded=no)" active "$(systemctl is-active user-$(id -u carol).slice)"
expect "CPU padrão de carol (200%)" "200000 100000" "$(cat $(slice carol)/cpu.max)"
expect "memória padrão de carol (2G)" 2147483648 "$(cat $(slice carol)/memory.max)"
expect "swap de carol (um quarto)" 536870912 "$(cat $(slice carol)/memory.swap.max)"
expect "tarefas de carol" 400 "$(cat $(slice carol)/pids.max)"
$B set limit_cpu 100% >/dev/null
expect "set limit_cpu 100% vale na hora" "100000 100000" "$(cat $(slice carol)/cpu.max)"
$B set limit_cpu 300% >/dev/null 2>&1
expect "valor fora da lista" 2 "$?"

# 2. CPU: carol queima com o dobro de processos que núcleos; o tempo da página PHP de
# alice (mediana de 15) não pode passar de 1,5 vez o de antes mais 20 ms.
median() {
    for i in $(seq 1 15); do
        curl -s -o /dev/null -w '%{time_total}\n' -H "Host: alpha.test" "http://$IP/cpu.php"
    done | sort -n | awk '{a[NR] = $1} END {print a[int((NR + 1) / 2)]}'
}
burn() {
    for i in $(seq 1 $HOGS); do
        curl -s -m 40 -o /dev/null -H "Host: gamma.test" "http://$IP/burn.php" &
    done
    sleep 3
}
measure() {
    median >/dev/null
    base=$(median)
    burn
    load=$(median)
    wait
    echo "$base $load"
}
$B set limits off >/dev/null
sleep 2
code gamma.test /ok.php >/dev/null
expect "limits off: lsphp de carol fora da fatia" no \
    "$(cgroup_of carol | grep -q '^/user.slice/' && echo yes || echo no)"
expect "limits off: drop-ins apagados" 0 "$(ls /etc/systemd/system/user-*.slice.d/60-olsbridge.conf 2>/dev/null | wc -l)"
set -- $(measure)
echo "  limits off: página de alice ${1}s sozinha, ${2}s com carol queimando $HOGS processos em $CPUS núcleos"
off_base=$1 off_load=$2
$B set limits on >/dev/null
sleep 2
code gamma.test /ok.php >/dev/null
code alpha.test /cpu.php >/dev/null
set -- $(measure)
echo "  limits on (100%): página de alice ${1}s sozinha, ${2}s com carol queimando"
expect "limits on: alice no máximo 1,5x + 20 ms com carol queimando ($1 -> $2 s)" yes \
    "$(awk -v b="$1" -v l="$2" 'BEGIN {print (l <= b * 1.5 + 0.02) ? "yes" : "no"}')"
expect "limits off era mais lento que on ($off_load s contra $2 s)" yes \
    "$(awk -v o="$off_load" -v l="$2" 'BEGIN {print (o > l) ? "yes" : "no"}')"
expect "CPU de carol freada pelo limite" yes \
    "$(awk '/^throttled_usec/ {print ($2 > 0) ? "yes" : "no"}' $(slice carol)/cpu.stat)"

# 3. O plano do HestiaCP vale no lugar do padrão.
mkdir -p /usr/local/hestia/data/users/carol
cat > /usr/local/hestia/data/users/carol/user.conf <<EOF
PACKAGE='mini'
CPU_QUOTA='50%'
CPU_QUOTA_PERIOD='unlimited'
MEMORY_LIMIT='256M'
SWAP_LIMIT='0'
EOF
$B sync >/dev/null
expect "plano: CPU 50%" "50000 100000" "$(cat $(slice carol)/cpu.max)"
expect "plano: memória 256M" 268435456 "$(cat $(slice carol)/memory.max)"
expect "plano: sem swap" 0 "$(cat $(slice carol)/memory.swap.max)"
expect "olsbridge limits marca o plano" yes \
    "$($B limits | grep -q '^carol  *mini  *50%\*  *256M\*' && echo yes || echo no)"
expect "alice segue no padrão" "100000 100000" "$(cat $(slice alice)/cpu.max)"

# 4. Memória: passar do teto falha logo (o OOM mata o processo de carol), sem prender a
# requisição até o php_timeout, e sem tocar em alice.
start=$(date +%s)
got=$(code gamma.test /mem.php)
took=$(($(date +%s) - start))
expect "PHP de 400 MB com teto de 256 MB não responde 200" yes "$([ "$got" != 200 ] && echo yes || echo no)"
expect "e falha em menos de 20 s (${took} s)" yes "$([ "$took" -lt 20 ] && echo yes || echo no)"
expect "OOM contado na fatia de carol" yes \
    "$(awk '/^oom_kill / {print ($2 > 0) ? "yes" : "no"}' $(slice carol)/memory.events)"
expect "alice segue respondendo" 200 "$(code alpha.test /cpu.php)"
expect "carol volta na próxima requisição" 200 "$(code gamma.test /ok.php)"
rm -rf /usr/local/hestia/data/users/carol
$B sync >/dev/null
expect "sem plano: volta ao padrão" "100000 100000" "$(cat $(slice carol)/cpu.max)"

# 5. Restart do OLS e sync não desfazem nada.
systemctl restart olsbridge
sleep 3
expect "depois do restart: gamma.test" 200 "$(code gamma.test /ok.php)"
expect "depois do restart: lsphp de carol na fatia" "/user.slice/user-$(id -u carol).slice/litespeed-exec.scope" \
    "$(cgroup_of carol)"
expect "depois do restart: limite mantido" "100000 100000" "$(cat $(slice carol)/cpu.max)"
expect "opção salva" "limit_cpu=100%" "$(grep '^limit_cpu=' /usr/local/lsws/conf/olsbridge.conf)"
# A fatia parada (como depois de um boot): a próxima requisição ou o sync a sobem.
systemctl stop user-$(id -u carol).slice
$B sync >/dev/null
expect "fatia parada volta no sync" active "$(systemctl is-active user-$(id -u carol).slice)"
expect "e com o limite" "100000 100000" "$(cat $(slice carol)/cpu.max)"
expect "status para o painel" yes "$($B status --json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('yes' if d['options']['limits'] == 'on' and any(r['user'] == 'carol' for r in d['limits']) else 'no')")"

$B set limit_cpu 200% >/dev/null
rm -f /etc/php/8.4/fpm/pool.d/gamma.test.conf /etc/apache2/sites-enabled/gamma.test.conf \
    /etc/nginx/conf.d/domains/gamma.test.conf $A/cpu.php
systemctl restart php8.4-fpm
sleep 2
expect "carol sem pool: drop-in apagado" no \
    "$([ -e /etc/systemd/system/user-$(id -u carol).slice.d/60-olsbridge.conf ] && echo yes || echo no)"
exit $status
