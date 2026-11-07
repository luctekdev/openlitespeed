#!/bin/sh
# Mede um site com h2load: um arquivo estático, a home e um post (WordPress), em
# HTTPS com HTTP/2, depois de aquecer o PHP. Roda no próprio servidor, então mede a
# capacidade do servidor web e do PHP, sem a rede no meio; o h2load usa 2 threads da CPU.
#   sh bench.sh DOMÍNIO IP [rótulo]
# Saída: uma linha por cenário, com requisições por segundo e latência média.
set -u
host=$1 ip=$2 label=${3:-}
static=${STATIC:-/wp-content/themes/twentytwentyfive/style.css}
post=${POST:-/post-de-teste-7/}
dur=${DURATION:-20}

run() {
    name=$1 path=$2 conns=$3
    # Aquecimento: opcache, cache de página e conexões com o PHP.
    h2load -t 2 -c "$conns" -D 5 "https://$ip$path" -H "Host: $host" -H "accept-encoding: gzip, br" \
        >/dev/null 2>&1
    out=$(h2load -t 2 -c "$conns" -D "$dur" "https://$ip$path" -H "Host: $host" \
        -H "accept-encoding: gzip, br" 2>&1)
    rps=$(echo "$out" | sed -n 's/.*finished in [^,]*, \([0-9.]*\) req\/s.*/\1/p')
    ok=$(echo "$out" | sed -n 's/.*status codes: \([0-9]*\) 2xx.*/\1/p')
    fail=$(echo "$out" | sed -n 's/.*requests: .* \([0-9]*\) failed.*/\1/p')
    mean=$(echo "$out" | awk '/time for request:/ {print $6}')
    printf '%-10s %-8s %6s conexões  %10s req/s  média %8s  2xx %8s  falhas %s\n' \
        "$label" "$name" "$conns" "$rps" "$mean" "$ok" "$fail"
}

run estatico "$static" 50
run home / 20
run post "$post" 20
