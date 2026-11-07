#!/bin/sh
# Roda todas as verificações contra as imagens já geradas.
#   OLS_IMAGE e OLS_PHP_IMAGE escolhem as imagens (por exemplo, a de ASan).
#   LOAD_SECONDS define a duração do teste de carga (padrão: 20).
# Sai com erro se recarregamento, política, ou a contagem de requisições iguais ao
# Apache ficarem abaixo do registrado em LATEST.md e LATEST-PHP.md.
set -u
cd "$(dirname "$0")"
export MSYS_NO_PATHCONV=1
mkdir -p report
status=0

compose() {
    docker compose --profile php --profile policy "$@"
}

passed() {
    grep -oE '\*\*[0-9]+ de [0-9]+\*\*' "$1" | grep -oE '[0-9]+' | head -n 1
}

wait_ready() {
    for service in "$@"; do
        tries=0
        until compose run --rm -T runner python -B -c "
import socket, sys
socket.create_connection(('$service', 80), timeout=2)" >/dev/null 2>&1; do
            tries=$((tries + 1))
            [ "$tries" -lt 30 ] || { echo "$service não subiu"; return 1; }
        done
    done
}

compare() {
    runner=$1 report=$2 floor=$3
    compose run --rm -T "$runner" >/dev/null 2>&1
    now=$(passed "report/$report")
    min=$(passed "$floor")
    echo "$report: $now requisições iguais ao Apache (mínimo: $min)"
    [ "$now" -ge "$min" ] || status=1
}

compose up -d --force-recreate apache ols ols-policy apache-php ols-php >/dev/null 2>&1
wait_ready apache ols ols-policy apache-php ols-php || exit 1

compare runner report.md LATEST.md
compare runner-php report-php.md LATEST-PHP.md

echo "--- recarregamento"
compose run --rm -T runner python -B reload.py --load "${LOAD_SECONDS:-20}" 2>&1 \
    | grep -E 'FALHOU|^carga' || true
compose run --rm -T runner python -B reload.py >/dev/null 2>&1 || status=1

echo "--- política"
compose run --rm -T runner python -B policy.py --host ols-policy 2>&1 | grep -E 'FALHOU' || true
compose run --rm -T runner python -B policy.py --host ols-policy >/dev/null 2>&1 || status=1

for service in ols ols-policy ols-php; do
    count=$(compose exec -T "$service" sh -c 'ls /tmp/asan* 2>/dev/null | wc -l')
    if [ "${count:-0}" -gt 0 ]; then
        echo "ASan reportou erros em $service:"
        compose exec -T "$service" sh -c 'head -n 40 /tmp/asan*'
        status=1
    fi
done

compose down -v >/dev/null 2>&1
exit $status
