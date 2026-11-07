#!/bin/sh
# Sobe o container com systemd e roda o ciclo de troca dentro dele.
set -u
export MSYS_NO_PATHCONV=1
name=olsbridge-systemd
docker rm -f "$name" >/dev/null 2>&1
docker run -d --name "$name" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    olsbridge-systemd >/dev/null || exit 1
tries=0
until docker exec "$name" systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded'; do
    tries=$((tries + 1))
    [ "$tries" -lt 60 ] || { echo "systemd não subiu"; docker logs "$name" | tail; exit 1; }
    sleep 1
done
docker exec "$name" sh /test/cycle.sh
status=$?
docker rm -f "$name" >/dev/null
exit $status
