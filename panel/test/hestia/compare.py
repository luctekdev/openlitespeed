"""Faz as mesmas requisições ao Apache e ao OLS e mostra o que difere."""
import base64
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import sys

IP = os.environ["IP"]
OFFSET = int(os.environ.get("OFFSET", "10000"))
DOMAINS = ["alpha.test", "beta.test", "gamma.test"]
AUTH = "Basic " + base64.b64encode(b"ana:senha").decode()
PROXY = {"X-Real-IP": "203.0.113.9", "X-Forwarded-For": "203.0.113.9"}


def fetch(port, scheme, host, method, path, headers, body):
    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(IP, port, timeout=10, context=ctx)
        # SNI com o nome do domínio, conectando no IP: com sock pronto, http.client não reconecta.
        conn.sock = ctx.wrap_socket(socket.create_connection((IP, port), 10),
                                    server_hostname=host)
    else:
        conn = http.client.HTTPConnection(IP, port, timeout=10)
    hdrs = {"Host": host}
    hdrs.update(headers)
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", "replace")
    conn.close()
    return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data


def summary(status, headers, body):
    out = {"status": status}
    if "location" in headers:
        out["location"] = headers["location"]
    out["type"] = headers.get("content-type", "").split(";")[0]
    if out["type"] == "application/json":
        out["body"] = json.loads(body)
    elif status < 300 or status >= 400:
        out["body"] = body.strip() if len(body) < 300 else "<%d bytes>" % len(body)
    return out


def chain_length(port, host):
    proc = subprocess.run(["openssl", "s_client", "-connect", "%s:%d" % (IP, port),
                           "-servername", host, "-showcerts"], input="", capture_output=True,
                          text=True, timeout=15)
    return len(re.findall("BEGIN CERTIFICATE", proc.stdout)), \
        (re.search(r"subject=.*?CN\s*=\s*([^\n,/]+)", proc.stdout) or [None, "?"])[1]


def cases():
    yield "http", "desconhecido.test", "GET", "/", {}, None
    for d in DOMAINS:
        yield "http", d, "GET", "/", {}, None
        yield "http", "www." + d, "GET", "/who.txt", {}, None
        for scheme in ("https",):
            yield scheme, d, "GET", "/", {}, None
            yield scheme, d, "GET", "/who.txt", {}, None
            yield scheme, d, "GET", "/info.php?x=1", dict(PROXY, Authorization=AUTH), None
            yield scheme, d, "POST", "/info.php", {"Content-Type": "text/plain"}, "corpo"
            yield scheme, d, "GET", "/info.php/a/b", {}, None
            yield scheme, d, "GET", "/wp/2026/11/post/", {}, None
            yield scheme, d, "GET", "/wp/", {}, None
            yield scheme, d, "GET", "/private/secret.txt", {}, None
            yield scheme, d, "GET", "/nada.html", {}, None
            yield scheme, d, "GET", "/vstats/", {}, None
            yield scheme, d, "GET", "/wp/.htaccess", {}, None


def main():
    same = total = 0
    for scheme, host, method, path, headers, body in cases():
        port = 8443 if scheme == "https" else 8080
        results = []
        for p in (port, port + OFFSET):
            try:
                results.append(summary(*fetch(p, scheme, host, method, path, headers, body)))
            except Exception as err:  # noqa
                results.append({"erro": str(err)})
        total += 1
        label = "%s %s://%s%s" % (method, scheme, host, path)
        if results[0] == results[1]:
            same += 1
            print("[igual] %s -> %s" % (label, results[0].get("status")))
        else:
            print("[DIFERE] %s" % label)
            print("   apache: %s" % json.dumps(results[0], ensure_ascii=False, sort_keys=True))
            print("   ols:    %s" % json.dumps(results[1], ensure_ascii=False, sort_keys=True))
    for d in DOMAINS:
        total += 1
        a, o = chain_length(8443, d), chain_length(8443 + OFFSET, d)
        if a == o:
            same += 1
            print("[igual] cadeia SSL de %s: %d certificados, CN %s" % (d, a[0], a[1]))
        else:
            print("[DIFERE] cadeia SSL de %s: apache %s, ols %s" % (d, a, o))
    print("%d de %d iguais" % (same, total))
    return 0 if same == total else 1


if __name__ == "__main__":
    sys.exit(main())
