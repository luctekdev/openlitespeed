#!/usr/bin/env python3
"""Testa as garantias que não têm equivalente direto no Apache.

Cada vhost de ols/policy.conf usa uma política diferente. O Apache não entra aqui:
estes casos verificam decisões nossas de segurança e os modos de operação.
"""

import argparse
import http.client
import os
import shutil
import sys

BASE = "policy-case"


def fetch(host, vhost, path):
    conn = http.client.HTTPConnection(host, 80, timeout=10)
    try:
        conn.request("GET", path, headers={"Host": vhost})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body, dict((k.lower(), v) for k, v in resp.getheaders())
    finally:
        conn.close()


class Runner:
    def __init__(self, host, www):
        self.host = host
        self.root = os.path.join(www, BASE)
        self.results = []
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root)
        os.chmod(self.root, 0o755)
        self.seq = 0

    def case(self, content, files=None):
        self.seq += 1
        name = f"c{self.seq}"
        path = os.path.join(self.root, name)
        os.makedirs(path)
        os.chmod(path, 0o755)
        for rel, text in {"page.txt": "page\n", ".htaccess": content, **(files or {})}.items():
            with open(os.path.join(path, rel), "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.chmod(os.path.join(path, rel), 0o644)
        return f"/{BASE}/{name}"

    def check(self, label, got, expected):
        ok = got == expected
        self.results.append(ok)
        print(f"[{'ok' if ok else 'FALHOU'}] {label}: {got!r}" + ("" if ok else f" esperado {expected!r}"))

    def status(self, vhost, uri, rel="page.txt"):
        return fetch(self.host, vhost, f"{uri}/{rel}")[0]

    def header(self, vhost, uri, name):
        return fetch(self.host, vhost, f"{uri}/page.txt")[2].get(name)


def post(host, vhost, path):
    conn = http.client.HTTPConnection(host, 80, timeout=10)
    try:
        conn.request("POST", path, body="log=a&pwd=b", headers={
            "Host": vhost, "Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        return resp.status, dict((k.lower(), v) for k, v in resp.getheaders())
    finally:
        conn.close()


def run_wordpress(r):
    uri = r.case("# vazio\n", {"wp-login.php": "login\n", "other.php": "x\n"})
    codes = [post(r.host, "wp", f"{uri}/wp-login.php")[0] for _ in range(5)]
    r.check("tentativas dentro do limite passam", 429 in codes, False)
    code, headers = post(r.host, "wp", f"{uri}/wp-login.php")
    r.check("além do limite é bloqueado", code, 429)
    r.check("resposta informa a espera", int(headers.get("retry-after", "0")) > 0, True)
    r.check("xmlrpc.php conta como login", post(r.host, "wp", f"{uri}/xmlrpc.php")[0], 429)
    r.check("outras páginas não são afetadas", post(r.host, "wp", f"{uri}/other.php")[0] != 429, True)
    r.check("GET no login continua liberado", r.status("wp", uri, "wp-login.php"), 200)
    r.check("sem a opção não há limite",
            any(post(r.host, "compat", f"{uri}/wp-login.php")[0] == 429 for _ in range(8)), False)


def run(r):
    uri = r.case("Header set X-Mode \"on\"\nFooBar 1\n")
    r.check("compat ignora diretiva desconhecida", r.status("compat", uri), 200)
    r.check("compat aplica o resto", r.header("compat", uri, "x-mode"), "on")
    r.check("strict responde 500 como o Apache", r.status("strict", uri), 500)

    uri = r.case("<Banana>\nHeader set X-A \"1\"\n</Banana>\n")
    r.check("compat ignora bloco desconhecido", r.status("compat", uri), 200)
    r.check("strict recusa bloco desconhecido", r.status("strict", uri), 500)

    uri = r.case("Require all denied\n")
    r.check("off ignora Require, como o OLS original", r.status("off", uri), 200)
    r.check("compat aplica Require", r.status("compat", uri), 403)

    uri = r.case("RewriteEngine On\nRewriteRule ^go$ page.txt [L]\n")
    r.check("off mantém o rewrite original", r.status("off", uri, "go"), 200)

    uri = r.case("Require bogus-provider x\n")
    r.check("regra de acesso inválida nega", r.status("compat", uri), 403)
    uri = r.case("Allow from 300.1.2.3\n")
    r.check("endereço inválido nega", r.status("compat", uri), 403)
    uri = r.case("<If \"%{FOO\">\nRequire all granted\n</If>\n")
    r.check("expressão inválida nega", r.status("compat", uri), 403)

    uri = r.case("AuthType Basic\nAuthName \"x\"\nAuthUserFile /etc/passwd\nRequire valid-user\n")
    r.check("AuthUserFile fora da vhost nega", r.status("compat", uri), 403)
    uri = r.case("Require valid-user\n")
    r.check("Require sem AuthType falha", r.status("compat", uri), 500)

    uri = r.case("RewriteEngine On\nRewriteRule ^go$ http://apache/ [P]\n")
    r.check("[P] bloqueado por padrão", r.status("compat", uri, "go"), 404)
    r.check("[P] liberado por configuração", r.status("proxy", uri, "go") != 404, True)

    uri = r.case("SetEnv modsecurity off\nHeader set X-M \"%{modsecurity}e\"\n")
    r.check("variável interna protegida", r.header("compat", uri, "x-m") in (None, "", "(null)"), True)

    uri = r.case("x" * (1024 * 1024 + 10))
    r.check("arquivo grande demais nega", r.status("compat", uri), 403)
    uri = r.case("<IfModule x>\n" * 40 + "</IfModule>\n" * 40)
    r.check("aninhamento excessivo responde 500", r.status("compat", uri), 500)

    uri = r.case("Header edit X-A \"a\" \"b\"\nHeader set X-Ok \"1\"\n")
    r.check("Header edit não gera cabeçalho inválido", r.header("compat", uri, "edit"), None)
    r.check("o restante do arquivo continua valendo", r.header("compat", uri, "x-ok"), "1")
    uri = r.case("<IfFile /tmp>\nRequire all denied\n</IfFile>\n")
    r.check("regra de acesso em bloco não suportado nega", r.status("compat", uri), 403)
    uri = r.case("<If \"true\">\nOrder Deny,Allow\nDeny from all\n</If>\n")
    r.check("regra de acesso não suportada em <If> nega", r.status("compat", uri), 403)

    # AllowOverride
    uri = r.case("Header set X-A \"1\"\nRequire all denied\n")
    r.check("AllowOverride None ignora o arquivo", r.status("none", uri), 200)
    r.check("AuthConfig aplica Require", r.status("authonly", uri), 403)
    uri = r.case("Header set X-A \"1\"\n")
    r.check("AuthConfig não aplica Header", r.header("authonly", uri, "x-a"), None)
    r.check("AllowOverride olha dentro de IfModule",
            r.header("authonly", r.case("<IfModule mod_headers.c>\nHeader set X-A \"1\"\n</IfModule>\n"), "x-a"),
            None)
    run_wordpress(r)
    return all(r.results)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="ols")
    parser.add_argument("--www", default="/www")
    args = parser.parse_args()
    return 0 if run(Runner(args.host, args.www)) else 1


if __name__ == "__main__":
    sys.exit(main())
