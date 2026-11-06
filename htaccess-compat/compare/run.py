#!/usr/bin/env python3
"""Compara as respostas do Apache e do OLS para o corpus de .htaccess.

Sai com código 0 mesmo havendo divergências: na Fase 0 o objetivo é medir, não
reprovar. Use --fail-on-diff para tratar divergências como falha no CI.
"""

import argparse
import hashlib
import http.client
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field

from corpus import CASES

# Variam entre execuções ou entre servidores sem significar incompatibilidade.
IGNORED_HEADERS = {
    "date", "server", "connection", "keep-alive", "accept-ranges", "etag",
    "last-modified", "transfer-encoding", "content-encoding", "vary",
    "x-powered-by",
}
# Comparados por faixa, porque dependem do horário exato.
TIME_HEADERS = {"expires"}


@dataclass
class Response:
    status: int
    headers: dict
    body_hash: str
    error: str = ""


@dataclass
class Result:
    case: str
    group: str
    request: str
    diffs: list = field(default_factory=list)
    skipped: str = ""


def fetch(host, method, path, headers):
    conn = http.client.HTTPConnection(host, 80, timeout=10)
    try:
        conn.request(method, path, headers={"Host": "compat.test", **headers})
        resp = conn.getresponse()
        body = resp.read()
    except (OSError, http.client.HTTPException) as exc:
        return Response(0, {}, "", f"{type(exc).__name__}: {exc}")
    finally:
        conn.close()

    merged = {}
    for name, value in resp.getheaders():
        key = name.lower()
        merged[key] = f"{merged[key]}, {value}" if key in merged else value
    return Response(resp.status, merged, hashlib.sha256(body).hexdigest()[:16])


def normalize_location(value):
    for prefix in ("http://compat.test", "https://compat.test"):
        if value.startswith(prefix):
            return value[len(prefix):] or "/"
    return value


def normalize_cache_control(value):
    return value.replace(" ", "").lower()


def compare(apache, ols, case):
    if apache.error or ols.error:
        return [f"erro de conexão: apache={apache.error!r} ols={ols.error!r}"]

    diffs = []
    if apache.status != ols.status:
        diffs.append(f"status: apache={apache.status} ols={ols.status}")
    if case.get("status_only"):
        return diffs

    names = (set(apache.headers) | set(ols.headers)) - IGNORED_HEADERS
    generated_page = apache.status >= 300 and not case.get("compare_error_body")
    if generated_page:
        # Páginas padrão de erro e redirecionamento diferem por natureza; só importam
        # os cabeçalhos que têm significado para o caso.
        names = {n for n in names
                 if n.startswith("x-") or n in ("www-authenticate", "location")}

    for name in sorted(names):
        left, right = apache.headers.get(name), ols.headers.get(name)
        if name == "location" and left and right:
            left, right = normalize_location(left), normalize_location(right)
        elif name == "cache-control" and left and right:
            left, right = normalize_cache_control(left), normalize_cache_control(right)
        elif name in TIME_HEADERS and left and right:
            continue
        if left != right:
            diffs.append(f"{name}: apache={left!r} ols={right!r}")

    if not generated_page and apache.body_hash != ols.body_hash:
        diffs.append("corpo diferente")
    return diffs


def materialize(www, run_id):
    root = os.path.join(www, run_id)
    os.makedirs(root)
    for case in CASES:
        base = f"/{run_id}/{case['name']}"
        path = os.path.join(root, case["name"])
        os.makedirs(path)
        files = {
            "index.html": "index\n",
            "page.txt": "page\n",
            ".htaccess": case["htaccess"],
            **case.get("files", {}),
        }
        for rel, content in files.items():
            target = os.path.join(path, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content.replace("{base}", base))
            os.chmod(target, 0o644)
    for dirpath, _, _ in os.walk(root):
        os.chmod(dirpath, 0o755)
    return root


def wait_ready(host, deadline=60):
    end = time.time() + deadline
    while time.time() < end:
        try:
            socket.create_connection((host, 80), timeout=2).close()
            return True
        except OSError:
            time.sleep(1)
    return False


def write_report(path, results):
    groups = {}
    for result in results:
        passed, total = groups.get(result.group, (0, 0))
        groups[result.group] = (passed + (not result.diffs), total + 1)
    passed = sum(1 for r in results if not r.diffs)

    lines = [
        "# Matriz de compatibilidade Apache x OLS",
        "",
        f"Requisições idênticas: **{passed} de {len(results)}**.",
        "",
        "| Grupo | Iguais | Total |",
        "|---|---|---|",
    ]
    lines += [f"| {g} | {p} | {t} |" for g, (p, t) in sorted(groups.items())]
    lines += ["", "## Divergências", ""]
    for result in results:
        if result.diffs:
            lines.append(f"### {result.case}: `{result.request}`")
            lines += [f"- {d}" for d in result.diffs]
            lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines))
    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--www", default="/www")
    parser.add_argument("--report", default="report.md")
    parser.add_argument("--apache", default="apache")
    parser.add_argument("--ols", default="ols")
    parser.add_argument("--fail-on-diff", action="store_true")
    args = parser.parse_args()

    for host in (args.apache, args.ols):
        if not wait_ready(host):
            print(f"servidor indisponível: {host}", file=sys.stderr)
            return 2

    run_id = f"c{int(time.time())}"
    root = materialize(args.www, run_id)
    results = []
    try:
        for case in CASES:
            base = f"/{run_id}/{case['name']}/"
            for method, rel, headers in case["requests"]:
                uri = base + rel
                apache = fetch(args.apache, method, uri, headers)
                ols = fetch(args.ols, method, uri, headers)
                label = f"{method} {rel or '/'}"
                if headers:
                    label += " " + " ".join(f"{k}={v}" for k, v in headers.items())
                diffs = compare(apache, ols, case)
                results.append(Result(case["name"], case["group"], label, diffs))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    passed = write_report(args.report, results)
    print(f"{passed}/{len(results)} requisições idênticas. Relatório: {args.report}")
    return 1 if args.fail_on_diff and passed != len(results) else 0


if __name__ == "__main__":
    sys.exit(main())
