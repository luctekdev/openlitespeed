#!/usr/bin/env python3
"""Envia URLs de diretórios inexistentes para checar crescimento da árvore de contextos.

Roda dentro da rede do compose. Medir a memória do processo é tarefa de quem chama:
    python memprobe.py ols 100000
    python memprobe.py ols 100000 --fixed     (controle: sempre a mesma URL)
"""

import http.client
import sys
import uuid


def main():
    host, total = sys.argv[1], int(sys.argv[2])
    fixed = "--fixed" in sys.argv
    conn = http.client.HTTPConnection(host, 80, timeout=10)
    statuses = {}
    for _ in range(total):
        name = "fixed-missing-dir" if fixed else uuid.uuid4().hex
        conn.request("GET", f"/{name}/x", headers={"Host": "compat.test"})
        resp = conn.getresponse()
        resp.read()
        statuses[resp.status] = statuses.get(resp.status, 0) + 1
    print(statuses)


if __name__ == "__main__":
    main()
