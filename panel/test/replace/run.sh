#!/bin/sh
# Sobe o container com systemd e roda o ciclo do modo replace.
set -u
name=olsbridge-replace-$$
MSYS_NO_PATHCONV=1 docker run -d --name "$name" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock \
    olsbridge-replace >/dev/null || exit 1
trap 'docker rm -f "$name" >/dev/null 2>&1' EXIT
for _ in $(seq 1 30); do
    docker exec "$name" systemctl is-system-running 2>/dev/null | grep -qE "running|degraded" && break
    sleep 1
done
MSYS_NO_PATHCONV=1 docker exec "$name" sh /test/cycle.sh
