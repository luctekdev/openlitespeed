#!/usr/bin/env python3
"""Converte as vhosts de uma configuração do Apache para o OpenLiteSpeed.

    python apache2ols.py /etc/apache2/apache2.conf --out /tmp/ols

Gera `httpd_vhosts.conf`, para incluir no httpd_config.conf, e um `vhconf.conf`
por vhost em `vhosts/<nome>/`. Nada é aplicado ao servidor. O que não tem
conversão vai para `avisos.txt`, para ser revisto antes da migração.
"""

import argparse
import glob
import os
import re
import sys


def read_lines(path, server_root, seen):
    """Lê um arquivo e expande Include, IncludeOptional e continuações de linha."""
    path = os.path.normpath(path)
    if path in seen:
        return []
    seen.add(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return []

    lines = []
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"(?i)^include(optional)?\s+(.+)$", stripped)
        if match:
            target = match.group(2).strip().strip('"')
            if not os.path.isabs(target):
                target = os.path.join(server_root, target)
            pattern = os.path.join(target, "*") if os.path.isdir(target) else target
            for name in sorted(glob.glob(pattern)):
                if os.path.isfile(name):
                    lines += read_lines(name, server_root, seen)
            continue
        lines.append(stripped)
    return lines


def split_args(text):
    return [a or b or c for a, b, c in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', text)]


class VHost:
    def __init__(self, address):
        self.port = int(address.rsplit(":", 1)[1]) if ":" in address else 80
        self.ssl = self.port == 443
        self.name = None
        self.aliases = []
        self.docroot = None
        self.cert = None
        self.key = None
        self.chain = None
        self.index = []
        self.rewrite = []
        self.errors = []
        self.allow_override = None
        self.autoindex = None


def parse(lines):
    vhosts = []
    warnings = []
    current = None
    directory = None
    for line in lines:
        if current is None:
            match = re.match(r"(?i)^<VirtualHost\s+([^>]+)>", line)
            if match:
                current = VHost(match.group(1).split()[0])
            continue
        if re.match(r"(?i)^</VirtualHost>", line):
            vhosts.append(current)
            current = None
            continue
        match = re.match(r"(?i)^<Directory\s+\"?([^\">]+)\"?>", line)
        if match:
            directory = match.group(1)
            continue
        if re.match(r"(?i)^</Directory>", line):
            directory = None
            continue

        args = split_args(line)
        name = args[0].lower()
        values = args[1:]
        label = current.name or "?"
        if directory is not None:
            if name == "allowoverride":
                if directory.rstrip("/") == (current.docroot or "").rstrip("/") \
                        or current.allow_override is None:
                    current.allow_override = " ".join(values)
            elif name == "options":
                if any(v.lstrip("+").lower() == "indexes" and not v.startswith("-") for v in values):
                    current.autoindex = 1
                elif "-indexes" in [v.lower() for v in values]:
                    current.autoindex = 0
            elif name in ("require", "order", "allow", "deny"):
                if " ".join(values).lower() not in ("all granted", "from all", "allow,deny"):
                    warnings.append(f"{label}: regra de acesso em <Directory> não convertida: {line}")
            continue

        if name == "servername" and values:
            current.name = values[0].split(":")[0]
        elif name == "serveralias":
            current.aliases += values
        elif name == "documentroot" and values:
            current.docroot = values[0].rstrip("/")
        elif name == "sslengine" and values:
            current.ssl = values[0].lower() == "on"
        elif name == "sslcertificatefile" and values:
            current.cert = values[0]
        elif name == "sslcertificatekeyfile" and values:
            current.key = values[0]
        elif name == "sslcertificatechainfile" and values:
            current.chain = values[0]
        elif name == "directoryindex":
            current.index = values
        elif name == "errordocument" and len(values) > 1:
            current.errors.append((values[0], values[1]))
        elif name in ("rewriteengine", "rewritecond", "rewriterule", "rewritebase"):
            current.rewrite.append(line)
        elif name in ("errorlog", "customlog", "serveradmin", "sslprotocol", "sslciphersuite",
                      "transferlog", "loglevel", "sslhonorcipherorder"):
            pass
        else:
            warnings.append(f"{label}: diretiva não convertida: {line}")
    return vhosts, warnings


def merge_by_name(vhosts):
    """A mesma vhost costuma aparecer em :80 e :443. No OLS ela é uma só."""
    merged = {}
    for vhost in vhosts:
        if not vhost.name or not vhost.docroot:
            continue
        item = merged.setdefault(vhost.name, {"main": vhost, "http": False, "https": False,
                                              "aliases": []})
        item["https" if vhost.ssl else "http"] = True
        item["aliases"] += [a for a in vhost.aliases if a not in item["aliases"]]
        if vhost.ssl:
            item["ssl"] = vhost
        if vhost.rewrite and not item["main"].rewrite:
            item["main"].rewrite = vhost.rewrite
    return merged


def vhconf(vhost, ssl):
    out = [f"docRoot                 {vhost.docroot}/", "",
           "index {", "    useServer           0",
           f"    indexFiles          {', '.join(vhost.index) or 'index.html, index.php'}",
           f"    autoIndex           {vhost.autoindex or 0}", "}", ""]
    for code, url in vhost.errors:
        out += [f"errorPage {code} {{", f"    url                 {url}", "}", ""]
    out += ["rewrite {", "    enable              1", "    autoLoadHtaccess    1"]
    if vhost.allow_override and vhost.allow_override.lower() != "all":
        out.append(f"    htaccessAllowOverride {vhost.allow_override}")
    if vhost.rewrite:
        out.append("    rules                <<<END_rules")
        out += vhost.rewrite
        out.append("END_rules")
    out += ["}", ""]
    if ssl and ssl.cert:
        out += ["vhssl {", f"    keyFile             {ssl.key or ssl.cert}",
                f"    certFile            {ssl.cert}"]
        if ssl.chain:
            out.append(f"    CACertFile          {ssl.chain}")
        out += ["}", ""]
    return "\n".join(out)


def server_conf(merged):
    out = []
    http_maps, https_maps = [], []
    first_ssl = None
    for name, item in merged.items():
        vhost = item["main"]
        out += [f"virtualhost {name} {{",
                f"    vhRoot              {os.path.dirname(vhost.docroot)}/",
                f"    configFile          conf/vhosts/{name}/vhconf.conf",
                "    allowSymbolLink     1", "    enableScript        1",
                "    restrained          1", "}", ""]
        domains = ", ".join([name] + item["aliases"])
        if item["http"]:
            http_maps.append(f"    map                 {name} {domains}")
        if item["https"]:
            https_maps.append(f"    map                 {name} {domains}")
            first_ssl = first_ssl or item.get("ssl")
    if http_maps:
        out += ["listener http {", "    address             *:80", "    secure              0"]
        out += http_maps + ["}", ""]
    if https_maps and first_ssl and first_ssl.cert:
        out += ["listener https {", "    address             *:443", "    secure              1",
                f"    keyFile             {first_ssl.key or first_ssl.cert}",
                f"    certFile            {first_ssl.cert}"]
        out += https_maps + ["}", ""]
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("config", help="arquivo principal do Apache")
    parser.add_argument("--out", required=True, help="pasta de saída")
    parser.add_argument("--server-root", help="base dos Include relativos")
    args = parser.parse_args()

    root = args.server_root or os.path.dirname(os.path.abspath(args.config))
    vhosts, warnings = parse(read_lines(args.config, root, set()))
    merged = merge_by_name(vhosts)
    if not merged:
        print("Nenhuma vhost com ServerName e DocumentRoot encontrada.", file=sys.stderr)
        return 1

    for name, item in merged.items():
        folder = os.path.join(args.out, "vhosts", name)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "vhconf.conf"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(vhconf(item["main"], item.get("ssl")))
    with open(os.path.join(args.out, "httpd_vhosts.conf"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(server_conf(merged))
    with open(os.path.join(args.out, "avisos.txt"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(warnings) + "\n")
    print(f"{len(merged)} vhosts convertidas, {len(warnings)} avisos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
