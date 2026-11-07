#!/bin/sh
# Comparação justa entre servidores web no mesmo servidor, com h2load em HTTPS/HTTP2:
#   - estático: número fixo de requisições, no máximo 900 por conexão (o nginx do
#     HestiaCP fecha a conexão a cada 1000, keepalive_requests);
#   - PHP: home e post do WordPress, com 20 conexões e com 1 (latência de um usuário).
#   sh bench2.sh DOMÍNIO IP [rótulo]
# Saída: uma linha por cenário, com requisições por segundo, latência média e falhas.
# Para medir o PHP sem cache de página, desative o plugin de cache do site e limpe o
# cache do servidor antes (no OLS: /usr/local/lsws/cachedata/DOMÍNIO).
set -u
host=$1 ip=$2 label=${3:-}
static=${STATIC:-/wp-includes/js/jquery/jquery.min.js}
post=${POST:-/post-de-teste-7/}

run() {
    name=$1 path=$2 conns=$3 total=$4
    t=2; [ "$conns" -lt 2 ] && t=1
    h2load -t "$t" -c "$conns" -n $((conns * 20)) "https://$ip$path" -H "Host: $host" \
        -H "accept-encoding: gzip, br" >/dev/null 2>&1
    out=$(h2load -t "$t" -c "$conns" -n "$total" "https://$ip$path" -H "Host: $host" \
        -H "accept-encoding: gzip, br" 2>&1)
    rps=$(echo "$out" | sed -n 's/.*finished in [^,]*, \([0-9.]*\) req\/s.*/\1/p')
    ok=$(echo "$out" | sed -n 's/.*status codes: \([0-9]*\) 2xx.*/\1/p')
    fail=$(echo "$out" | sed -n 's/.*requests: .* \([0-9]*\) failed.*/\1/p')
    mean=$(echo "$out" | awk '/time for request:/ {print $6}')
    p=$(echo "$out" | awk '/time for request:/ {print $4"/"$5}')
    printf '%-14s %-9s %3s conexões %7s req  %10s req/s  média %9s  min/max %s  2xx %6s  falhas %s\n' \
        "$label" "$name" "$conns" "$total" "$rps" "$mean" "$p" "$ok" "$fail"
}

run estatico "$static" 50 45000
run home / 20 2000
run post "$post" 20 2000
run home-1 / 1 200
run post-1 "$post" 1 200
