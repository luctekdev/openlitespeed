#!/usr/bin/env python3
"""Testa se mudanças no .htaccess valem sem reiniciar o servidor.

Precisa de htaccessCheckInterval 0 na vhost. Os cenários rodam contra o OLS; com
--load, várias threads fazem requisições enquanto o arquivo é editado.
"""

import argparse
import http.client
import os
import shutil
import sys
import threading
import time

RULE = "RewriteEngine On\nRewriteRule ^go$ {target} [L]\n"


def fetch(host, path):
    conn = http.client.HTTPConnection(host, 80, timeout=10)
    try:
        conn.request("GET", path, headers={"Host": "compat.test"})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body, dict((k.lower(), v) for k, v in resp.getheaders())
    finally:
        conn.close()


def status(host, path):
    return fetch(host, path)[:2]


def write(path, content):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


class Scenario:
    def __init__(self, host, www, name):
        self.host = host
        self.www = www
        self.uri = f"/{name}"
        self.dir = os.path.join(www, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        os.chmod(self.dir, 0o755)
        write(os.path.join(self.dir, "a.txt"), "A\n")
        write(os.path.join(self.dir, "b.txt"), "B\n")

    def body(self, rel="go"):
        return status(self.host, f"{self.uri}/{rel}")


def check(results, label, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"[{'ok' if ok else 'FALHOU'}] {label}: {got!r}" + ("" if ok else f" esperado {expected!r}"))


def run_scenarios(host, www):
    results = []
    ht = lambda s, target: write(os.path.join(s.dir, ".htaccess"), RULE.format(target=target))

    s = Scenario(host, www, "r-edit")
    check(results, "sem .htaccess", s.body()[0], 404)
    ht(s, "a.txt")
    check(results, "criado depois do primeiro acesso", s.body(), (200, b"A\n"))
    ht(s, "b.txt")
    check(results, "editado", s.body(), (200, b"B\n"))
    os.remove(os.path.join(s.dir, ".htaccess"))
    check(results, "removido", s.body()[0], 404)

    s = Scenario(host, www, "r-parent")
    sub = os.path.join(s.dir, "sub")
    os.makedirs(sub)
    os.chmod(sub, 0o755)
    write(os.path.join(sub, "c.txt"), "C\n")
    check(results, "subdiretório antes", s.body("sub/c.txt")[0], 200)
    write(os.path.join(s.dir, ".htaccess"), "RewriteEngine On\nRewriteRule ^sub/ - [F]\n")
    check(results, "regra do pai vale no filho já visitado", s.body("sub/c.txt")[0], 403)
    os.remove(os.path.join(s.dir, ".htaccess"))
    check(results, "pai removido libera o filho", s.body("sub/c.txt")[0], 200)

    s = Scenario(host, www, "r-late")
    late = os.path.join(s.dir, "late")
    check(results, "diretório inexistente", s.body("late/go")[0], 404)
    os.makedirs(late)
    os.chmod(late, 0o755)
    write(os.path.join(late, "a.txt"), "L\n")
    write(os.path.join(late, ".htaccess"), RULE.format(target="a.txt"))
    check(results, "diretório criado depois", s.body("late/go"), (200, b"L\n"))

    # Arquivos estáticos pequenos ficam em cache na memória do servidor.
    s = Scenario(host, www, "r-static")
    page = f"{s.uri}/a.txt"
    conf = os.path.join(s.dir, ".htaccess")
    for _ in range(3):
        fetch(host, page)
    write(conf, 'Header set X-V "1"\n')
    check(results, "cabeçalho criado em arquivo em cache", fetch(host, page)[2].get("x-v"), "1")
    for _ in range(3):
        fetch(host, page)
    write(conf, 'Header set X-V "2"\n')
    check(results, "cabeçalho editado", fetch(host, page)[2].get("x-v"), "2")
    write(conf, 'Require all denied\n')
    check(results, "negado depois de cache", fetch(host, page)[0], 403)
    write(conf, 'Require all granted\n')
    check(results, "liberado depois de negar", fetch(host, page)[0], 200)
    write(conf, '<Files "a.txt">\nRequire all denied\n</Files>\n')
    check(results, "<Files> negado depois de cache", fetch(host, page)[0], 403)
    write(conf, 'ExpiresActive On\nExpiresByType text/plain "access plus 1 hour"\n')
    check(results, "Expires aplicado", "max-age=3600" in fetch(host, page)[2].get("cache-control", ""), True)
    write(conf, '<IfModule\n')
    check(results, "erro de sintaxe vira 500", fetch(host, page)[0], 500)
    write(conf, '# corrigido\n')
    check(results, "correção vale de imediato", fetch(host, page)[0], 200)

    root = os.path.join(www, ".htaccess")
    write(os.path.join(www, "rootpage.txt"), "ROOT\n")
    check(results, "raiz sem .htaccess", status(host, "/rootgo")[0], 404)
    write(root, "RewriteEngine On\nRewriteRule ^rootgo$ rootpage.txt [L]\n")
    check(results, ".htaccess criado na raiz", status(host, "/rootgo"), (200, b"ROOT\n"))
    write(root, "RewriteEngine On\nRewriteRule ^rootgo$ - [F]\n")
    check(results, ".htaccess da raiz editado", status(host, "/rootgo")[0], 403)
    os.remove(root)
    check(results, ".htaccess da raiz removido", status(host, "/rootgo")[0], 404)
    return all(results)


def run_load(host, www, seconds):
    s = Scenario(host, www, "r-load")
    sub = os.path.join(s.dir, "sub")
    os.makedirs(sub)
    os.chmod(sub, 0o755)
    write(os.path.join(sub, "c.txt"), "C\n")
    stop = time.time() + seconds
    bad, total = [], [0]

    def hammer():
        while time.time() < stop:
            for path in (f"{s.uri}/go", f"{s.uri}/sub/c.txt", f"{s.uri}/a.txt"):
                try:
                    code, _ = status(host, path)
                except OSError as exc:
                    bad.append(repr(exc))
                    continue
                total[0] += 1
                if code not in (200, 404):
                    bad.append(f"{path} -> {code}")

    threads = [threading.Thread(target=hammer) for _ in range(16)]
    for thread in threads:
        thread.start()
    target, edits = 0, 0
    while time.time() < stop:
        write(os.path.join(s.dir, ".htaccess"),
              RULE.format(target="ab"[target % 2] + ".txt"))
        target += 1
        edits += 1
        if edits % 7 == 0:
            os.remove(os.path.join(s.dir, ".htaccess"))
        time.sleep(0.02)
    for thread in threads:
        thread.join()
    print(f"carga: {total[0]} requisições, {edits} edições, {len(bad)} erros")
    for line in bad[:5]:
        print("  ", line)
    return not bad


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="ols")
    parser.add_argument("--www", default="/www")
    parser.add_argument("--load", type=int, default=0, help="segundos de carga")
    args = parser.parse_args()
    ok = run_scenarios(args.host, args.www)
    if args.load:
        ok = run_load(args.host, args.www, args.load) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
