#!/bin/sh
# Hospedagem compartilhada em produção: links simbólicos entre clientes, logs girados
# pelo logrotate do HestiaCP, ajustes de PHP e cache, e a tela de status do painel.
# Roda depois de cycle.sh, com o OLS no lugar.
set -u
IP=$(hostname -i | awk '{print $1}')
B=/usr/local/lsws/bin/olsbridge
C=/usr/local/lsws/conf/bridge/server.conf
A=/home/alice/web/alpha.test/public_html
E=/home/bob/web/beta.test/public_html
status=0
ok() { echo "[ok] $1"; }
fail() { echo "[FALHOU] $1"; status=1; }
expect() {
    if [ "$3" = "$2" ]; then ok "$1: $3"; else fail "$1: '$3', esperado '$2'"; fi
}
code() { curl -s -o /dev/null -w '%{http_code}' -H "Host: $1" "http://$IP$2"; }
body() { curl -s -H "Host: $1" "http://$IP$2"; }

# 1. Um cliente não lê o arquivo de outro por link simbólico; o próprio link vale.
echo "SENHA_DO_ALICE" > $A/segredo.txt
chown alice:alice $A/segredo.txt
ln -sf $A/segredo.txt $E/roubo.txt && chown -h bob:bob $E/roubo.txt
expect "link de bob para arquivo de alice" no \
    "$(body beta.test /roubo.txt | grep -q SENHA_DO_ALICE && echo yes || echo no)"
ln -sf /etc/passwd $E/passwd.txt && chown -h bob:bob $E/passwd.txt
expect "link de bob para /etc/passwd" no "$(body beta.test /passwd.txt | grep -q root: && echo yes || echo no)"
mkdir -p /home/alice/storage && echo "publico" > /home/alice/storage/arquivo.txt
chown -R alice:alice /home/alice/storage
ln -sfn /home/alice/storage $A/storage && chown -h alice:alice $A/storage
expect "link do próprio dono (storage do Laravel)" publico "$(body alpha.test /storage/arquivo.txt)"

# 2. logrotate do HestiaCP: renomeia, cria um novo e faz reload do apache2.
L=/var/log/apache2/domains/alpha.test.log
code alpha.test /who.txt?antes=1 >/dev/null
sleep 1
expect "log do domínio escrito" yes "$(grep -q 'antes=1' $L && echo yes || echo no)"
mv $L $L.1
mv /var/log/apache2/domains/alpha.test.bytes /var/log/apache2/domains/alpha.test.bytes.1
install -m 640 /dev/null $L
install -m 640 /dev/null /var/log/apache2/domains/alpha.test.bytes
systemctl reload apache2
sleep 3
code alpha.test /who.txt?depois=1 >/dev/null
sleep 2
expect "depois do logrotate: log novo" yes "$(grep -q 'depois=1' $L && echo yes || echo no)"
expect "depois do logrotate: nada no girado" no "$(grep -q 'depois=1' $L.1 && echo yes || echo no)"
expect "depois do logrotate: banda no .bytes novo" yes \
    "$([ -s /var/log/apache2/domains/alpha.test.bytes ] && echo yes || echo no)"

# 3. Ajustes pela opção (o que a página do painel chama).
$B set php_timeout 120 >/dev/null; expect "php_timeout 120" yes \
    "$(grep -q 'initTimeout           120' $C && ! grep -q 'initTimeout           60$' $C && echo yes || echo no)"
$B set mem_cache 64M >/dev/null; expect "mem_cache 64M" yes \
    "$(grep -q 'totalInMemCacheSize   64M' $C && echo yes || echo no)"
$B set mem_cache auto >/dev/null
$B set php_idle 300 >/dev/null; expect "php_idle 300" yes \
    "$(grep -q 'LSAPI_PGRP_MAX_IDLE=300' $C && echo yes || echo no)"
$B set php_idle off >/dev/null; expect "php_idle off" no \
    "$(grep -q 'LSAPI_PGRP_MAX_IDLE' $C && echo yes || echo no)"
$B set php_idle 3 >/dev/null 2>&1; expect "php_idle com valor fora da lista" 2 $?

# 4. O lsphp de um site ocioso sai e volta na próxima visita (10 s aqui, no lugar da opção).
$B set php_idle 300 >/dev/null
sed -i 's/LSAPI_PGRP_MAX_IDLE=300/LSAPI_PGRP_MAX_IDLE=10/' $C
kill -USR1 "$(cat /tmp/lshttpd/lshttpd.pid)"
sleep 3
pkill -x lsphp8.4
sleep 1
code alpha.test /info.php >/dev/null
expect "lsphp de alice em pé depois da visita" yes "$(pgrep -u alice -x lsphp8.4 >/dev/null && echo yes || echo no)"
sleep 25
expect "lsphp de alice saiu depois de 10 s ocioso" no "$(pgrep -u alice -x lsphp8.4 >/dev/null && echo yes || echo no)"
expect "e volta na próxima visita" 200 "$(code alpha.test /info.php)"
$B set php_idle 1800 >/dev/null

# 5. Tempo de resposta do PHP: um script de 40 s passa com 60 s e cai com 30 s.
echo '<?php sleep(40); echo "fim";' > $A/lento.php && chown alice:alice $A/lento.php
$B set php_timeout 300 >/dev/null
expect "script de 40 s com php_timeout 300" fim "$(curl -s -m 70 -H 'Host: alpha.test' "http://$IP/lento.php")"
$B set php_timeout 30 >/dev/null
expect "script de 40 s com php_timeout 30" 503 \
    "$(curl -s -m 70 -o /dev/null -w '%{http_code}' -H 'Host: alpha.test' "http://$IP/lento.php")"
$B set php_timeout 300 >/dev/null

# 6. Tela Server > web do painel.
expect "status para o painel" yes "$($B status-html | grep -q 'OPENLITESPEED STATUS' && echo yes || echo no)"

rm -f $A/segredo.txt $A/lento.php $A/storage $E/roubo.txt $E/passwd.txt
exit $status
