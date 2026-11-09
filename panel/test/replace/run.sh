#!/bin/sh
# Sobe o container com systemd e roda o ciclo do modo replace. O limite por IP é
# testado de um segundo container: o OLS libera os IPs do próprio servidor.
set -u
image=${OLSB_IMAGE:-olsbridge-replace}  # outra tag: OLSB_IMAGE=olsbridge-replace-x sh run.sh
name=olsbridge-replace-$$
client=olsbridge-client-$$
export MSYS_NO_PATHCONV=1
# cgroup próprio: o systemd do container fica na raiz de /sys/fs/cgroup, como num
# servidor, e o lscgid acha /sys/fs/cgroup/user.slice (limits.sh). Com o do host, a
# árvore do container ficava em /docker/<id>/ e o lscgid não punha ninguém nas fatias.
docker run -d --name "$name" --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock \
    "$image" >/dev/null || exit 1
trap 'docker rm -f "$name" "$client" >/dev/null 2>&1' EXIT
for _ in $(seq 1 30); do
    docker exec "$name" systemctl is-system-running 2>/dev/null | grep -qE "running|degraded" && break
    sleep 1
done
docker exec "$name" sh /test/cycle.sh

status=0
ok() { echo "[ok] $1"; }
fail() { echo "[FALHOU] $1"; status=1; }
expect() {
    if [ "$3" = "$2" ]; then ok "$1: $3"; else fail "$1: '$3', esperado '$2'"; fi
}
SIP=$(docker exec "$name" hostname -i | awk '{print $1}')
get_code() {
    docker exec "$1" curl -s -m 5 -o /dev/null -w '%{http_code}' -H "Host: alpha.test" \
        "http://$SIP/who.txt"
}
docker exec "$name" /usr/local/lsws/bin/olsbridge set protection strict >/dev/null
sleep 2
docker run -d --name "$client" --entrypoint sleep "$image" 300 >/dev/null
expect "outro IP antes do ataque" 200 "$(get_code "$client")"
# Slowloris: 400 conexões com o cabeçalho pela metade, acima do limite de 200 por IP do
# nível strict. Uma rajada de requisições completas não serve de teste: acima do limite
# soft o OLS desliga o keep-alive e fecha cada conexão depois da resposta, e uma conexão
# sem dados nem chega ao OLS (deferred accept).
docker exec "$client" python3 -c "
import socket, time
socks = []
for i in range(400):
    try:
        s = socket.create_connection(('$SIP', 80), timeout=3)
        s.sendall(b'GET /who.txt HTTP/1.1\r\nHost: alpha.test\r\n')
        socks.append(s)
    except OSError:
        break
time.sleep(3)
"
docker exec "$name" grep -q "Reached per client hard connection limit" \
    /usr/local/lsws/logs/error.log && ok "limite por IP registrado no error.log" \
    || fail "limite por IP não registrado"
expect "IP do ataque bloqueado" 000 "$(get_code "$client")"
expect "o próprio servidor segue atendido" 200 "$(get_code "$name")"
docker exec "$name" /usr/local/lsws/bin/olsbridge set protection standard >/dev/null
docker exec "$name" systemctl restart olsbridge
sleep 2
expect "bloqueio some com o restart" 200 "$(get_code "$client")"
docker rm -f "$client" >/dev/null

docker exec "$name" sh /test/prod.sh || status=1
docker exec "$name" sh /test/limits.sh || status=1
docker exec "$name" sh /test/waf.sh || status=1
docker exec "$name" sh /test/erp.sh || status=1
docker exec "$name" sh /test/finish.sh || status=1
exit $status
