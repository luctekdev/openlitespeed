#!/bin/sh
# Instala a ponte no lugar do Apache e repete o que o HestiaCP faz.
set -u
IP=$(hostname -i | awk '{print $1}')
status=0

check() {
    label=$1 expected=$2 host=$3 path=$4
    got=$(curl -s -o /tmp/body -w '%{http_code}' -H "Host: $host" "http://$IP:8080$path")
    if [ "$got" = "$expected" ]; then
        echo "[ok] $label: $got"
    else
        echo "[FALHOU] $label: $got, esperado $expected"
        status=1
    fi
}

server() {
    curl -s -o /dev/null -D - -H "Host: $1" "http://$IP:8080/who.txt" | tr -d '\r' \
        | awk -F': ' 'tolower($1) == "server" {print $2}'
}

sh /test/setup.sh >/dev/null 2>&1 || { echo "[FALHOU] setup"; exit 1; }
echo "antes: servidor em :8080 = $(server beta.test)"

/usr/local/lsws/bin/olsbridge install --mode replace
echo "depois: servidor em :8080 = $(server beta.test)"
[ "$(systemctl is-active apache2)" = active ] && echo "[ok] apache2.service ativo (é o OLS)" \
    || { echo "[FALHOU] apache2.service inativo"; status=1; }
pgrep -x apache2 >/dev/null && { echo "[FALHOU] processo do Apache rodando"; status=1; } \
    || echo "[ok] nenhum processo do Apache"
check "domínio existente" 200 beta.test /who.txt
check "PHP pelo FPM" 200 beta.test /info.php

# O que o HestiaCP faz ao criar um domínio: escreve a config e recarrega o serviço.
mkdir -p /home/bob/web/delta.test/public_html /home/bob/conf/web/delta.test
echo delta.test > /home/bob/web/delta.test/public_html/who.txt
chmod -R a+rX /home/bob/web/delta.test
sed -e 's/gamma\.test/delta.test/g' /home/bob/conf/web/gamma.test/apache2.conf \
    > /home/bob/conf/web/delta.test/apache2.conf
ln -s /home/bob/conf/web/delta.test/apache2.conf /etc/apache2/conf.d/domains/delta.test.conf
# Antes do reload, o nome cai na vhost padrão do IP, que não tem who.txt.
check "domínio novo antes do reload" 404 delta.test /who.txt
apache2ctl configtest >/dev/null 2>&1 && echo "[ok] apache2ctl configtest" \
    || { echo "[FALHOU] apache2ctl configtest"; status=1; }
systemctl reload-or-restart apache2
sleep 2
check "domínio novo depois do reload" 200 delta.test /who.txt
grep -q delta.test /tmp/body && echo "[ok] conteúdo do domínio novo" \
    || { echo "[FALHOU] conteúdo do domínio novo"; status=1; }

# Mudança sem reload: o timer pega.
rm /etc/apache2/conf.d/domains/delta.test.conf
/usr/local/lsws/bin/olsbridge sync
sleep 2
check "domínio removido, sem reload" 404 delta.test /who.txt
systemctl is-enabled olsbridge-sync.timer >/dev/null && echo "[ok] timer ativo" \
    || { echo "[FALHOU] timer inativo"; status=1; }

systemctl restart apache2 && sleep 1
check "restart pelo nome do Apache" 200 beta.test /who.txt
systemctl stop apache2 && sleep 1
curl -s -o /dev/null "http://$IP:8080/" && { echo "[FALHOU] ainda responde após stop"; status=1; } \
    || echo "[ok] stop para o OLS"
systemctl start apache2 && sleep 1

/usr/local/lsws/bin/olsbridge uninstall
sleep 1
echo "desinstalado: servidor em :8080 = $(server beta.test)"
pgrep -x apache2 >/dev/null && echo "[ok] Apache de volta" || { echo "[FALHOU] Apache não voltou"; status=1; }
check "domínio depois de voltar ao Apache" 200 beta.test /who.txt
exit $status
