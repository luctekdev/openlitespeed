#!/bin/sh
# Monta o ambiente, compara Apache e OLS e mostra os avisos da ponte.
set -u
sh /test/setup.sh || { tail -n 30 /var/log/apache2/error.log /usr/local/lsws/logs/error.log; exit 1; }
IP=$(hostname -i | awk '{print $1}') python3 /test/compare.py
status=$?
echo "--- avisos da ponte"
cat /usr/local/lsws/conf/bridge/warnings.txt
echo "--- logs: Apache e OLS, mesma requisição"
IP=$(hostname -i | awk '{print $1}')
curl -s -o /dev/null -A teste -e http://ref.test/ -H "Host: beta.test" "http://$IP:8080/who.txt?log=1"
curl -s -o /dev/null -A teste -e http://ref.test/ -H "Host: beta.test" "http://$IP:18080/who.txt?log=1"
sleep 3
# Cada processo do OLS descarrega o seu buffer: a ordem das linhas pode mudar.
echo "beta.test.log"
echo "  apache: $(grep 'log=1' /var/log/apache2/domains/beta.test.log)"
echo "  ols:    $(grep 'log=1' /usr/local/lsws/logs/offset/beta.test.log)"
echo "beta.test.bytes (uma linha por requisição)"
echo "  apache: $(wc -l < /var/log/apache2/domains/beta.test.bytes) linhas"
echo "  ols:    $(wc -l < /usr/local/lsws/logs/offset/beta.test.bytes) linhas"
echo "--- erros do OLS"
grep -E '\[(ERROR|WARN)' /usr/local/lsws/logs/error.log | tail -n 15
exit $status
