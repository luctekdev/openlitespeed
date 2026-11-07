#!/usr/bin/env python3
"""Serve com o OpenLiteSpeed a configuração que o painel gera para o Apache.

    olsbridge generate            gera a configuração do OLS e mostra os avisos
    olsbridge start|stop|reload   comandos usados pela unidade do systemd
    olsbridge sync                gera de novo e recarrega só se o Apache mudou
    olsbridge status
    olsbridge install --mode replace|offset [--offset N]
    olsbridge uninstall

O painel continua escrevendo a configuração do Apache e chamando o serviço do
Apache. A unidade instalada faz esse serviço comandar o OLS. O binário do Apache
continua instalado: o painel o usa para testar a configuração.

Compatível com Python 3.6, que é o do AlmaLinux 8.
"""

import argparse
import glob
import hashlib
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

LSWS = os.environ.get("LSWS_HOME", "/usr/local/lsws")
SETTINGS = os.path.join(LSWS, "conf", "olsbridge.conf")
OUT = os.path.join(LSWS, "conf", "bridge")
PID_FILE = "/tmp/lshttpd/lshttpd.pid"
SYSTEMD = "/etc/systemd/system"

DEFAULTS = {
    "mode": "replace",
    "port_offset": "10000",
    "php_max_conns": "10",
}

# Diretivas que não mudam o que o OLS precisa servir.
IGNORED = set("""
serveradmin servertokens serversignature traceenable timeout keepalive maxkeepaliverequests
keepalivetimeout hostnamelookups loglevel defaulttype pidfile mutex startservers minspareservers
maxspareservers serverlimit maxclients maxrequestworkers maxrequestsperchild maxconnectionsperchild
minsparethreads maxsparethreads threadlimit threadsperchild threadstacksize listenbacklog
enablesendfile enablemmap fileetag usecanonicalname protocols sslverifyclient sslprotocol
sslciphersuite sslhonorcipherorder sslcompression sslsessiontickets sslusestapling
sslstaplingcache sslsessioncache sslsessioncachetimeout sslrandomseed sslpassphrasedialog
sslcryptodevice sslstaplingresponderTimeout sslstaplingreturnrespondererrors sslengine
sslcertificatefile sslcertificatekeyfile sslcertificatechainfile sslrequiressl
suexecusergroup assignuserid ruidugid ruidgroups accessfilename addoutputfilterbytype
browsermatch browsermatchnocase adddefaultcharset indexoptions indexignore addicon addiconbyencoding
addiconbytype defaulticon readmename headername adddescription addlanguage languagepriority
forcelanguagepriority addcharset addencoding gracefulshutdowntimeout errorlogformat
scriptsock scriptlog extendedstatus servername serveralias documentroot directoryindex alias
scriptalias aliasmatch scriptaliasmatch errordocument customlog transferlog errorlog
rewriteengine rewritecond rewriterule rewritebase rewriteoptions logformat user group listen
loadmodule loadfile define undefine serverroot include includeoptional remoteipheader
remoteipinternalproxy remoteiptrustedproxy remoteipproxiesheader setenvif setenvifnocase options
allowoverride allowoverridelist require order allow deny satisfy sethandler addhandler
""".split())

# No nível do servidor, os padrões do Debian e do RHEL que o OLS já cobre.
SERVER_IGNORED = set("""
typesconfig addtype removetype addencoding requestreadtimeout proxystatus mimemagicfile
deflatecompressionlevel sslprotocol sslciphersuite
""".split())

SAFE_NAME = re.compile(r"^[A-Za-z0-9._*-]+$")
SAFE_PATH = re.compile(r"^/[^\s{}\"'<>|;`$\\]*$")


class Settings(dict):
    """Arquivo simples `chave=valor`, com padrões detectados no servidor."""

    @classmethod
    def load(cls, path=SETTINGS):
        values = cls(DEFAULTS)
        values.update(detect())
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.split("#", 1)[0].strip()
                    if "=" in line:
                        key, value = line.split("=", 1)
                        values[key.strip()] = value.strip()
        return values

    def save(self, path=SETTINGS):
        keys = ["mode", "port_offset", "apache_conf", "apache_root", "apache_ctl", "service",
                "php_max_conns", "panel"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("# Gerado por olsbridge install; pode ser editado.\n")
            for key in keys:
                if key in self:
                    handle.write("%s=%s\n" % (key, self[key]))


def detect():
    if os.path.exists("/etc/apache2/apache2.conf"):
        found = {"apache_conf": "/etc/apache2/apache2.conf", "apache_root": "/etc/apache2",
                 "apache_ctl": "apache2ctl", "service": "apache2", "panel": "generic"}
    elif os.path.exists("/etc/httpd/conf/httpd.conf"):
        found = {"apache_conf": "/etc/httpd/conf/httpd.conf", "apache_root": "/etc/httpd",
                 "apache_ctl": "httpd", "service": "httpd", "panel": "generic"}
    else:
        return {}
    if os.path.isdir("/usr/local/hestia"):
        found["panel"] = "hestia"
    elif os.path.isdir("/usr/local/directadmin"):
        found["panel"] = "directadmin"
    return found


def run(cmd, env=None):
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, env=env)
        out = proc.communicate()[0]
        return proc.returncode, out
    except OSError as err:
        return 127, str(err)


# --------------------------------------------------------------------------
# Leitura da configuração do Apache


class Node(object):
    __slots__ = ("name", "args", "children", "where")

    def __init__(self, name, args, where, children=None):
        self.name = name
        self.args = args
        self.where = where
        self.children = children

    @property
    def key(self):
        return self.name.lower()

    def line(self):
        return " ".join([self.name] + [quote(a) for a in self.args])


def quote(value):
    return '"%s"' % value if (not value or re.search(r"\s", value)) else value


def split_args(text):
    """Separa argumentos como o Apache: aspas duplas ou simples e escapes."""
    args = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        if text[i] in "\"'":
            q = text[i]
            i += 1
            buf = []
            while i < n and text[i] != q:
                if text[i] == "\\" and i + 1 < n and text[i + 1] in (q, "\\"):
                    i += 1
                buf.append(text[i])
                i += 1
            i += 1
            args.append("".join(buf))
        else:
            start = i
            while i < n and not text[i].isspace():
                i += 1
            args.append(text[start:i])
    return args


def module_id(name):
    """`mod_rewrite.c`, `rewrite_module` e `mod_rewrite` viram `rewrite_module`."""
    name = name.strip()
    if name.endswith("_module"):
        return name
    special = {"prefork.c": "mpm_prefork_module", "worker.c": "mpm_worker_module",
               "event.c": "mpm_event_module", "sapi_apache2.c": "php_module"}
    if name in special:
        return special[name]
    if name.endswith(".c"):
        name = name[:-2]
    if name.startswith("mod_"):
        name = name[4:]
    return name + "_module"


def version_tuple(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


class ApacheConfig(object):
    """Lê a configuração na ordem do Apache, avaliando condições ao abrir cada bloco."""

    def __init__(self, settings):
        self.settings = settings
        self.root = settings.get("apache_root", "/etc/apache2")
        self.defines = set()
        self.variables = dict(self.envvars())
        self.modules = self.loaded_modules()
        self.version = self.apache_version()
        self.warnings = []
        self.files = []
        self.tree = []

    def envvars(self):
        path = os.path.join(self.root, "envvars")
        if not os.path.exists(path):
            return {}
        code, out = run(["sh", "-c", ". \"$1\" >/dev/null 2>&1; env", "sh", path],
                        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        found = {}
        for line in out.splitlines():
            if line.startswith("APACHE_") and "=" in line:
                key, value = line.split("=", 1)
                found[key] = value
        return found

    def loaded_modules(self):
        code, out = run([self.settings.get("apache_ctl", "apache2ctl"), "-M"])
        found = set(re.findall(r"^\s*(\w+_module)\s", out, re.M)) if code == 0 else set()
        if not found:
            found = {"core_module", "so_module", "http_module", "log_config_module",
                     "logio_module", "version_module", "unixd_module"}
        return found

    def apache_version(self):
        code, out = run([self.settings.get("apache_ctl", "apache2ctl"), "-v"])
        match = re.search(r"Apache/([\d.]+)", out)
        return version_tuple(match.group(1)) if match else (2, 4, 0)

    def parse(self):
        path = self.settings["apache_conf"]
        self.tree = []
        self.read(path, self.tree, required=True)
        return self.tree

    def expand(self, text, where):
        def repl(match):
            name = match.group(1)
            if name in self.variables:
                return self.variables[name]
            self.warnings.append("%s: variável ${%s} não definida" % (where, name))
            return match.group(0)
        return re.sub(r"\$\{(\w+)\}", repl, text)

    def resolve(self, path):
        return path if os.path.isabs(path) else os.path.join(self.root, path)

    def read(self, path, into, required=False):
        path = os.path.normpath(path)
        if path in self.files:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as err:
            if required:
                raise SystemExit("não foi possível ler %s: %s" % (path, err))
            self.warnings.append("%s: não foi possível ler (%s)" % (path, err.strerror))
            return
        self.files.append(path)
        lines = re.sub(r"\\[ \t]*\r?\n", " ", text).splitlines()
        stack = [into]
        skip = 0
        for number, raw in enumerate(lines, 1):
            where = "%s:%d" % (path, number)
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if skip:
                if re.match(r"^</", line):
                    skip -= 1
                elif re.match(r"^<[A-Za-z]", line):
                    skip += 1
                continue
            line = self.expand(line, where)
            if line.startswith("</"):
                if len(stack) > 1:
                    stack.pop()
                continue
            match = re.match(r"^<(\w+)\s*(.*?)\s*>$", line)
            if match:
                name, rest = match.group(1), match.group(2)
                args = split_args(rest)
                if not self.condition(name.lower(), args):
                    skip = 1
                    continue
                if name.lower() in ("ifmodule", "ifdefine", "iffile", "ifversion", "ifdirective",
                                    "ifsection"):
                    stack.append(stack[-1])
                    continue
                node = Node(name, args, where, [])
                stack[-1].append(node)
                stack.append(node.children)
                continue
            args = split_args(line)
            name, values = args[0], args[1:]
            key = name.lower()
            if key in ("include", "includeoptional"):
                if values:
                    self.include(values[0], stack[-1], key == "include", where)
                continue
            if key == "define" and values:
                self.defines.add(values[0])
                if len(values) > 1:
                    self.variables[values[0]] = values[1]
                continue
            if key == "undefine" and values:
                self.defines.discard(values[0])
                self.variables.pop(values[0], None)
                continue
            if key == "loadmodule" and values:
                self.modules.add(values[0])
            if key == "serverroot" and values:
                self.root = values[0]
            stack[-1].append(Node(name, values, where))

    def include(self, target, into, required, where):
        target = self.resolve(target)
        if os.path.isdir(target):
            names = []
            for base, _dirs, files in os.walk(target):
                names += [os.path.join(base, f) for f in files]
        else:
            names = glob.glob(target)
        names = sorted(n for n in names if os.path.isfile(n))
        if not names and required and not glob.has_magic(target):
            self.warnings.append("%s: Include de %s não encontrou arquivos" % (where, target))
        for name in names:
            self.read(name, into)

    def condition(self, kind, args):
        arg = args[0] if args else ""
        negate = arg.startswith("!")
        arg = arg[1:] if negate else arg
        if kind == "ifmodule":
            result = module_id(arg) in self.modules
        elif kind == "ifdefine":
            result = arg in self.defines
        elif kind == "iffile":
            result = os.path.exists(self.resolve(arg))
        elif kind == "ifversion":
            result = self.version_matches(args)
            negate = False
        else:
            return True
        return result != negate

    def version_matches(self, args):
        if not args:
            return True
        op, value = ("=", args[0]) if len(args) == 1 else (args[0], args[1])
        negate = op.startswith("!")
        op = op.lstrip("!")
        if op in ("~", "=") and value.startswith("/"):
            result = re.search(value.strip("/"), ".".join(map(str, self.version))) is not None
        else:
            want = version_tuple(value)
            have = self.version[:len(want)]
            result = {"=": have == want, "==": have == want, ">": have > want,
                      ">=": have >= want, "<": have < want, "<=": have <= want}.get(op, True)
        return result != negate


# --------------------------------------------------------------------------
# Modelo


class VHost(object):
    def __init__(self, node):
        self.where = node.where
        self.addrs = []
        for addr in node.args:
            host, _, port = addr.rpartition(":")
            if not host:
                host, port = addr, "80"
            self.addrs.append((host.strip("[]"), int(port) if port.isdigit() else 80))
        self.name = None
        self.aliases = []
        self.docroot = None
        self.ssl = False
        self.cert = self.key = self.chain = None
        self.alias = []          # (tipo, url, caminho)
        self.dirs = []           # (caminho, nós)
        self.php = []            # (sufixos, endereço)
        self.rewrite = []
        self.rewrite_on = False
        self.errors = {}
        self.index = None
        self.logs = []           # (caminho, formato)
        self.errorlog = None
        self.headers = []
        self.env = []
        self.ols_name = None

    @property
    def label(self):
        return "%s:%s" % (self.name or "?", self.addrs[0][1] if self.addrs else "?")


class Server(object):
    def __init__(self):
        self.listens = []        # (ip, porta)
        self.user = "www-data"
        self.group = "www-data"
        self.formats = {"common": '%h %l %u %t "%r" %>s %b',
                        "combined": '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"'}
        self.errors = {}
        self.index = None
        self.dirs = []
        self.alias = []
        self.proxy_header = None
        self.proxies = []
        self.vhosts = []


def php_suffixes(pattern, where, warnings):
    match = re.search(r"\\\.\(?\??:?([A-Za-z0-9|]+)\)?\$?", pattern)
    if match:
        return [s for s in match.group(1).split("|") if s]
    warnings.append("%s: padrão de PHP não reconhecido (%s), usando .php" % (where, pattern))
    return ["php"]


def proxy_address(handler):
    """`proxy:unix:/run/php.sock|fcgi://localhost` ou `proxy:fcgi://127.0.0.1:9000`."""
    match = re.match(r"(?i)^proxy:unix:([^|]+)\|fcgi://", handler)
    if match:
        return "uds:/" + match.group(1)
    match = re.match(r"(?i)^proxy:fcgi://([^/]+)/?", handler)
    if match:
        return match.group(1)
    return None


def build(config):
    server = Server()
    warnings = config.warnings
    for node in config.tree:
        apply_server(server, node, config, warnings)
    for vhost in server.vhosts:
        for kind, url, path in server.alias:
            if not any(a[1] == url for a in vhost.alias):
                vhost.alias.append((kind, url, path))
        merged = dict(server.errors)
        merged.update(vhost.errors)
        vhost.errors = merged
        if vhost.index is None:
            vhost.index = server.index
    return server


def apply_server(server, node, config, warnings):
    key = node.key
    args = node.args
    if node.children is not None:
        if key == "virtualhost":
            vhost = VHost(node)
            for child in node.children:
                apply_vhost(vhost, child, warnings)
            server.vhosts.append(vhost)
        elif key in ("directory", "directorymatch"):
            server.dirs.append((args[0] if args else "", key == "directorymatch" or
                                (len(args) > 1 and args[0] == "~"), node))
        elif key in ("files", "filesmatch") and args and args[-1] in ('^\\.ht', "^\\.ht", ".ht*"):
            pass  # o OLS já nega .ht*
        elif key == "location" and any(c.key == "sethandler" and c.args and
                                       c.args[0].lower() in ("server-status", "server-info")
                                       for c in node.children):
            pass  # status do Apache: o OLS tem o seu, no WebAdmin
        else:
            warnings.append("%s: bloco <%s> no nível do servidor não convertido" %
                            (node.where, node.name))
        return
    if key == "listen" and args:
        ip, _, port = args[0].rpartition(":")
        if port.isdigit():
            server.listens.append((ip.strip("[]") or "*", int(port)))
    elif key == "user" and args:
        server.user = args[0]
    elif key == "group" and args:
        server.group = args[0]
    elif key == "logformat" and len(args) > 1:
        server.formats[args[1]] = args[0]
    elif key == "errordocument" and len(args) > 1:
        server.errors[args[0]] = " ".join(args[1:])
    elif key == "directoryindex":
        server.index = args
    elif key in ("alias", "scriptalias") and len(args) > 1:
        server.alias.append((key, args[0], args[1]))
    elif key == "remoteipheader" and args:
        server.proxy_header = args[0]
    elif key in ("remoteipinternalproxy", "remoteiptrustedproxy"):
        server.proxies += args
    elif key not in IGNORED and key not in SERVER_IGNORED:
        warnings.append("%s: diretiva não convertida: %s" % (node.where, node.line()))


def apply_vhost(vhost, node, warnings):
    key = node.key
    args = node.args
    if node.children is not None:
        if key in ("directory", "directorymatch"):
            vhost.dirs.append((args[0] if args else "", key == "directorymatch", node))
            for child in node.children:
                if child.key in ("rewriterule", "rewritecond"):
                    warnings.append("%s: RewriteRule dentro de <Directory> não convertida" %
                                    child.where)
                    break
        elif key in ("files", "filesmatch"):
            found = False
            for child in node.children:
                if child.key == "sethandler" and child.args:
                    address = proxy_address(child.args[0])
                    if address:
                        vhost.php.append((php_suffixes(" ".join(args), node.where, warnings),
                                          address))
                        found = True
                elif child.key == "addhandler" and len(child.args) > 1:
                    address = proxy_address(child.args[0])
                    if address:
                        vhost.php.append(([s.lstrip(".") for s in child.args[1:]], address))
                        found = True
            if not found:
                warnings.append("%s: bloco <%s> não convertido" % (node.where, node.name))
        else:
            warnings.append("%s: bloco <%s> não convertido" % (node.where, node.name))
        return
    if key == "servername" and args:
        vhost.name = args[0].split("://")[-1].split(":")[0].lower()
    elif key == "serveralias":
        vhost.aliases += [a.lower() for a in args]
    elif key == "documentroot" and args:
        vhost.docroot = args[0].rstrip("/") or "/"
    elif key == "sslengine" and args:
        vhost.ssl = args[0].lower() == "on"
    elif key == "sslcertificatefile" and args:
        vhost.cert = args[0]
    elif key == "sslcertificatekeyfile" and args:
        vhost.key = args[0]
    elif key == "sslcertificatechainfile" and args:
        vhost.chain = args[0]
    elif key in ("alias", "scriptalias") and len(args) > 1:
        vhost.alias.append((key, args[0], args[1]))
    elif key == "errordocument" and len(args) > 1:
        vhost.errors[args[0]] = " ".join(args[1:])
    elif key == "directoryindex":
        vhost.index = args
    elif key == "rewriteengine" and args:
        vhost.rewrite_on = args[0].lower() == "on"
    elif key in ("rewritecond", "rewriterule", "rewritebase", "rewriteoptions"):
        vhost.rewrite.append(node.line())
    elif key == "customlog" and len(args) > 1:
        vhost.logs.append((args[0], args[1]))
    elif key == "errorlog" and args:
        vhost.errorlog = args[0]
    elif key == "header":
        vhost.headers.append(node.line())
    elif key == "setenv" and args:
        vhost.env.append(args)
    elif key == "setenvif" and args and args[0].lower() == "authorization":
        pass  # o OLS repassa Authorization ao PHP
    elif key not in IGNORED:
        warnings.append("%s: diretiva não convertida: %s" % (node.where, node.line()))


# --------------------------------------------------------------------------
# Geração da configuração do OLS


def directory_settings(server, vhost, path):
    """AllowOverride e Indexes do <Directory> mais específico que cobre o caminho."""
    allow, indexes, best = "None", 0, -1
    for dirs in (server.dirs, vhost.dirs if vhost else []):
        for dir_path, is_regex, node in dirs:
            if is_regex:
                continue
            base = dir_path.rstrip("/") or "/"
            covers = path == base or path.startswith(base.rstrip("/") + "/") or base == "/"
            if not covers or len(base) < best:
                continue
            best = len(base)
            for child in node.children:
                if child.key == "allowoverride" and child.args:
                    allow = " ".join(child.args)
                elif child.key == "options":
                    for opt in child.args:
                        name = opt.lstrip("+-").lower()
                        if name in ("indexes", "all"):
                            indexes = 0 if opt.startswith("-") else 1
                        elif name == "none" and not opt.startswith(("+", "-")):
                            indexes = 0
    return allow, indexes


def log_format(server, fmt):
    return server.formats.get(fmt, fmt)


class Generator(object):
    def __init__(self, server, settings, warnings):
        self.server = server
        self.settings = settings
        self.warnings = warnings
        self.offset = int(settings.get("port_offset", 0)) if settings.get("mode") == "offset" else 0
        self.processors = {}
        self.ssl_files = {}
        self.log_files = []

    def processor(self, address):
        if address not in self.processors:
            name = "fpm_" + re.sub(r"[^A-Za-z0-9]+", "_", address.replace("uds:/", "")).strip("_")
            self.processors[address] = name[:80]
        return self.processors[address]

    def valid(self, vhost):
        if not vhost.name or not vhost.docroot:
            self.warnings.append("%s: vhost sem ServerName ou DocumentRoot ignorada" % vhost.where)
            return False
        names = [vhost.name] + vhost.aliases
        if not all(SAFE_NAME.match(n) for n in names) or not SAFE_PATH.match(vhost.docroot):
            self.warnings.append("%s: nome ou caminho com caracteres inválidos, vhost ignorada" %
                                 vhost.where)
            return False
        return True

    def generate(self):
        vhosts = [v for v in self.server.vhosts if self.valid(v)]
        used = set()
        for vhost in vhosts:
            base = vhost.name + ("-ssl" if vhost.ssl else "")
            name, n = base, 1
            while name in used:
                n += 1
                name = "%s-%d" % (base, n)
            used.add(name)
            vhost.ols_name = name
        files = {}
        for vhost in vhosts:
            files["vhosts/%s.conf" % vhost.ols_name] = self.vhconf(vhost)
        files["server.conf"] = self.server_conf(vhosts)
        return files

    def listeners(self, vhosts):
        result = []
        listens = list(dict.fromkeys(self.server.listens))
        for ip, port in listens:
            mapped = [v for v in vhosts if any(p == port and (ip == "*" or h in ("*", ip,
                      "_default_")) for h, p in v.addrs)]
            if not mapped:
                self.warnings.append("Listen %s:%d sem vhost, não convertido" % (ip, port))
                continue
            ssl = [v for v in mapped if v.ssl]
            if ssl and len(ssl) != len(mapped):
                self.warnings.append("Listen %s:%d mistura vhosts com e sem SSL" % (ip, port))
            result.append((ip, port, bool(ssl), mapped))
        return result

    def ssl_pair(self, vhost):
        """O OLS lê a cadeia junto com o certificado; o Apache aceita os dois separados."""
        if not vhost.cert:
            return None
        if not vhost.chain:
            return vhost.key or vhost.cert, vhost.cert, False
        name = "ssl/%s.crt" % vhost.ols_name
        try:
            with open(vhost.cert, encoding="utf-8") as cert, open(vhost.chain, encoding="utf-8") as chain:
                body = cert.read().rstrip("\n") + "\n" + chain.read()
        except OSError as err:
            self.warnings.append("%s: certificado ilegível (%s)" % (vhost.label, err.strerror))
            return vhost.key or vhost.cert, vhost.cert, False
        self.ssl_files[name] = body
        return vhost.key or vhost.cert, os.path.join(OUT, name), True

    def server_conf(self, vhosts):
        s = self.server
        out = ["# Gerado por olsbridge a partir de %s. Não edite." % self.settings["apache_conf"],
               "serverName                %s" % socket.gethostname(),
               "user                      %s" % s.user,
               "group                     %s" % s.group,
               "priority                  0",
               "autoRestart               1",
               "gracefulRestartTimeout    300",
               "mime                      conf/mime.properties",
               "showVersionNumber         0",
               "indexFiles                %s" % ", ".join(s.index or ["index.html"]),
               "disableWebAdmin           0",
               "",
               "errorlog logs/error.log {",
               "    logLevel              WARN",
               "    debugLevel            0",
               "    rollingSize           10M",
               "    enableStderrLog       1",
               "}",
               "",
               "accessLog logs/access.log {",
               "    rollingSize           10M",
               "    keepDays              10",
               "    compressArchive       1",
               "}",
               "",
               "tuning {",
               "    maxConnections        10000",
               "    maxSSLConnections     10000",
               "    connTimeout           300",
               "    maxKeepAliveReq       10000",
               "    keepAliveTimeout      5",
               "    maxReqURLLen          32768",
               "    maxReqHeaderSize      65536",
               "    maxReqBodySize        2047M",
               "    maxDynRespHeaderSize  32768",
               "    maxDynRespSize        2047M",
               "    enableGzipCompress    1",
               "    enableDynGzipCompress 1",
               "    quicEnable            0",
               "}",
               "",
               "fileAccessControl {",
               "    followSymbolLink      1",
               "    checkSymbolLink       0",
               "    requiredPermissionMask 000",
               "    restrictedPermissionMask 000",
               "}",
               ""]
        allow = ["ALL"]
        if s.proxy_header:
            if s.proxy_header.lower() not in ("x-forwarded-for", "x-real-ip"):
                self.warnings.append("RemoteIPHeader %s: o OLS só usa X-Forwarded-For" %
                                     s.proxy_header)
            out.append("useIpInProxyHeader        2")
            allow += ["%sT" % p for p in s.proxies if re.match(r"^[0-9a-fA-F.:/]+$", p)]
        out += ["accessControl {", "    allow                 %s" % ", ".join(allow),
                "    deny", "}", ""]

        conns = self.settings.get("php_max_conns", "10")
        for vhost in vhosts:
            for _suffixes, address in vhost.php:
                self.processor(address)
        for address, name in sorted(self.processors.items(), key=lambda x: x[1]):
            out += ["extprocessor %s {" % name,
                    "    type                  fcgi",
                    "    address               %s" % address,
                    "    maxConns              %s" % conns,
                    "    initTimeout           60",
                    "    retryTimeout          0",
                    "    persistConn           1",
                    "    respBuffer            0",
                    "    autoStart             0",
                    "}", ""]

        for vhost in vhosts:
            out += ["virtualhost %s {" % vhost.ols_name,
                    "    vhRoot                %s/" % self.vh_root(vhost),
                    "    configFile            %s/vhosts/%s.conf" % (OUT, vhost.ols_name),
                    "    allowSymbolLink       1",
                    "    enableScript          1",
                    "    restrained            1",
                    "}", ""]

        for ip, port, ssl, mapped in self.listeners(vhosts):
            addr = "%s:%d" % ("*" if ip == "*" else ("[%s]" % ip if ":" in ip else ip),
                              port + self.offset)
            out += ["listener %s {" % re.sub(r"[^A-Za-z0-9]", "_", "l_%s_%d" % (ip, port)),
                    "    address               %s" % addr,
                    "    secure                %d" % (1 if ssl else 0)]
            if ssl:
                first = next((v for v in mapped if v.cert), None)
                pair = self.ssl_pair(first) if first else None
                if pair:
                    out += ["    keyFile               %s" % pair[0],
                            "    certFile              %s" % pair[1],
                            "    certChain             %d" % (1 if pair[2] else 0)]
            out.append("    map                   %s *" % mapped[0].ols_name)
            seen = set()
            for vhost in mapped:
                names = [n for n in [vhost.name] + vhost.aliases if n not in seen]
                seen.update(names)
                if names:
                    out.append("    map                   %s %s" % (vhost.ols_name,
                                                                  ", ".join(names)))
            out += ["}", ""]
        out += ["include %s/server-extra.conf" % OUT] if os.path.exists(
            os.path.join(OUT, "server-extra.conf")) else []
        return "\n".join(out) + "\n"

    def log_path(self, path):
        """Lado a lado com o Apache, os dois no mesmo arquivo contariam a banda em dobro."""
        if self.offset:
            path = os.path.join(LSWS, "logs", "offset", os.path.basename(path))
        self.log_files.append(path)
        return path

    def vh_root(self, vhost):
        paths = [vhost.docroot] + [p for _k, _u, p in vhost.alias if p.startswith("/")]
        root = os.path.commonpath([p.rstrip("/") or "/" for p in paths])
        if root in ("/", ""):
            root = os.path.dirname(vhost.docroot.rstrip("/")) or "/"
        return root.rstrip("/")

    def vhconf(self, vhost):
        allow, indexes = directory_settings(self.server, vhost, vhost.docroot)
        out = ["# %s (%s)" % (vhost.label, vhost.where),
               "docRoot                   %s/" % vhost.docroot.rstrip("/"),
               "enableGzip                1",
               "",
               "index {",
               "    useServer             0",
               "    indexFiles            %s" % ", ".join(vhost.index or ["index.html"]),
               "    autoIndex             %d" % indexes,
               "}",
               ""]
        for code, url in sorted(vhost.errors.items()):
            if url.startswith("/"):
                out += ["errorPage %s {" % code, "    url                   %s" % url, "}", ""]
        for path, fmt in vhost.logs:
            if path.startswith("|"):
                self.warnings.append("%s: CustomLog com pipe não convertido" % vhost.label)
                continue
            path = self.log_path(path)
            out += ["accessLog %s {" % path, "    useServer             0",
                    '    logFormat             %s' % log_format(self.server, fmt),
                    "    rollingSize           0", "    keepDays              0",
                    "    compressArchive       0", "}", ""]
        if vhost.errorlog and vhost.errorlog.startswith("/"):
            out += ["errorlog %s {" % self.log_path(vhost.errorlog), "    useServer             0",
                    "    logLevel              ERROR", "    rollingSize           0", "}", ""]
        out += ["rewrite {", "    enable                1", "    autoLoadHtaccess      %d" %
                (0 if allow.lower() == "none" else 1)]
        if allow.lower() not in ("all", "none"):
            out.append("    htaccessAllowOverride %s" % allow)
        if vhost.rewrite_on and vhost.rewrite:
            out.append("    rules                 <<<END_rules")
            out += vhost.rewrite
            out.append("    END_rules")
        out += ["}", ""]
        if vhost.php:
            handlers = []
            for suffixes, address in vhost.php:
                handlers.append("    add                   fcgi:%s %s" %
                                (self.processor(address), ", ".join(suffixes)))
            out += ["scripthandler {"] + handlers + ["}", ""]
        for kind, url, path in vhost.alias:
            if not SAFE_PATH.match(path):
                self.warnings.append("%s: Alias %s com caminho inválido" % (vhost.label, url))
                continue
            sub_allow, sub_index = directory_settings(self.server, vhost, path.rstrip("/"))
            out += ["context %s {" % url,
                    "    type                  %s" % ("cgi" if kind == "scriptalias" else "null"),
                    "    location              %s" % path,
                    "    allowBrowse           1"]
            if kind != "scriptalias":
                out += ["    indexFiles            %s" % ", ".join(vhost.index or ["index.html"]),
                        "    autoIndex             %d" % sub_index]
                if sub_allow.lower() != "none":
                    out += ["    rewrite {", "        enable            1", "    }"]
            out += ["}", ""]
        if vhost.headers:
            out += ["context / {", "    location              %s/" % vhost.docroot.rstrip("/"),
                    "    allowBrowse           1", "    extraHeaders          <<<END_headers"]
            for line in vhost.headers:
                out.append(re.sub(r"(?i)^header\s+(always\s+)?", "", line))
            out += ["    END_headers", "}", ""]
        if vhost.env:
            self.warnings.append("%s: SetEnv não convertido" % vhost.label)
        if vhost.ssl:
            pair = self.ssl_pair(vhost)
            if pair:
                out += ["vhssl {", "    keyFile               %s" % pair[0],
                        "    certFile              %s" % pair[1],
                        "    certChain             %d" % (1 if pair[2] else 0), "}", ""]
            else:
                self.warnings.append("%s: SSL sem certificado" % vhost.label)
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Comandos


def generate(settings, quiet=False):
    """Gera numa pasta nova e troca de uma vez. Devolve (mudou, avisos)."""
    config = ApacheConfig(settings)
    config.parse()
    server = build(config)
    generator = Generator(server, settings, config.warnings)
    files = generator.generate()
    files.update(generator.ssl_files)
    files["warnings.txt"] = "\n".join(config.warnings) + ("\n" if config.warnings else "")

    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0" + files[name].encode("utf-8"))
    digest = digest.hexdigest()
    stamp = os.path.join(OUT, ".digest")
    try:
        with open(stamp) as handle:
            changed = handle.read().strip() != digest
    except OSError:
        changed = True

    prepare_logs(server, generator.log_files)
    if changed:
        new = OUT + ".new"
        shutil.rmtree(new, ignore_errors=True)
        for name, body in files.items():
            path = os.path.join(new, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(body)
            os.chmod(path, 0o600 if name.startswith("ssl/") else 0o644)
        extra = os.path.join(OUT, "server-extra.conf")
        if os.path.exists(extra):
            shutil.copy2(extra, os.path.join(new, "server-extra.conf"))
        with open(os.path.join(new, ".digest"), "w") as handle:
            handle.write(digest + "\n")
        old = OUT + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(OUT):
            os.rename(OUT, old)
        os.rename(new, OUT)
        shutil.rmtree(old, ignore_errors=True)
        link_server_conf()
    if not quiet:
        print("%d vhosts, %d avisos%s." % (len([v for v in server.vhosts if v.ols_name]),
                                          len(config.warnings),
                                          "" if changed else ", nada mudou"))
    return changed, config.warnings


def prepare_logs(server, paths):
    """O Apache abre os logs como root; o OLS escreve com o usuário dos processos.

    Os arquivos passam a ser desse usuário, e as pastas acima precisam deixá-lo
    passar. O logrotate recria os arquivos como root e depois chama o reload, que
    passa por aqui de novo.
    """
    if os.geteuid() != 0:
        return
    try:
        import grp
        import pwd
        uid = pwd.getpwnam(server.user).pw_uid
        gid = grp.getgrnam(server.group).gr_gid
    except (ImportError, KeyError):
        return
    logs = os.path.join(LSWS, "logs")
    if os.path.isdir(logs):
        os.chown(logs, 0, gid)
        os.chmod(logs, 0o770)
    for path in paths:
        folder = os.path.dirname(path)
        try:
            os.makedirs(folder, exist_ok=True)
            if not os.path.exists(path):
                open(path, "a").close()
                os.chmod(path, 0o640)
            st = os.stat(path)
            if st.st_uid != uid:
                os.chown(path, uid, st.st_gid)
            while folder and folder != "/":
                mode = os.stat(folder).st_mode
                if not mode & 0o001:
                    os.chmod(folder, mode | 0o001)
                folder = os.path.dirname(folder)
        except OSError as err:
            print("aviso: log %s: %s" % (path, err.strerror), file=sys.stderr)


def link_server_conf():
    """O httpd_config.conf do OLS passa a ser só um include do que a ponte gera."""
    conf = os.path.join(LSWS, "conf", "httpd_config.conf")
    wanted = "# Gerenciado por olsbridge.\ninclude %s/server.conf\n" % OUT
    try:
        with open(conf, encoding="utf-8") as handle:
            if handle.read() == wanted:
                return
    except OSError:
        pass
    if os.path.exists(conf) and not os.path.exists(conf + ".before-olsbridge"):
        shutil.copy2(conf, conf + ".before-olsbridge")
    with open(conf, "w", encoding="utf-8") as handle:
        handle.write(wanted)


def ols_pid():
    try:
        with open(PID_FILE) as handle:
            pid = int(handle.read().strip())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def lswsctrl(action):
    """O OLS vira daemon herdando a saída; ler dela travaria até ele parar."""
    try:
        return subprocess.call([os.path.join(LSWS, "bin", "lswsctrl"), action],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return 127


def wait_stopped(seconds=15):
    for _ in range(seconds * 10):
        if not ols_pid():
            return True
        time.sleep(0.1)
    return False


def cmd_start(settings):
    generate(settings)
    if ols_pid():
        # Iniciado por fora (o script lsws do pacote, por exemplo): o systemd só
        # aceita como serviço um processo que ele mesmo viu nascer.
        lswsctrl("stop")
        if not wait_stopped():
            print("um OpenLiteSpeed já rodando não parou", file=sys.stderr)
            return 1
    code = lswsctrl("start")
    for _ in range(50):
        if ols_pid():
            return 0
        time.sleep(0.1)
    return code or 1


def cmd_stop(_settings):
    lswsctrl("stop")
    wait_stopped()
    return 0


def cmd_reload(settings, only_if_changed=False):
    changed, _warnings = generate(settings, quiet=only_if_changed)
    pid = ols_pid()
    if not pid:
        return cmd_start(settings)
    if changed or not only_if_changed:
        os.kill(pid, signal.SIGUSR1)  # restart gracioso: novas conexões já usam a config nova
    return 0


def cmd_status(settings):
    pid = ols_pid()
    print("OLS: %s" % ("rodando (pid %d)" % pid if pid else "parado"))
    print("modo: %s%s" % (settings.get("mode"), " (+%s)" % settings.get("port_offset")
                          if settings.get("mode") == "offset" else ""))
    print("Apache: %s" % settings.get("apache_conf"))
    path = os.path.join(OUT, "warnings.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            lines = [l for l in handle.read().splitlines() if l]
        print("avisos: %d (%s)" % (len(lines), path))
    return 0 if pid else 3


DROPIN = """[Unit]
Description=OpenLiteSpeed no lugar do Apache (olsbridge)

[Service]
Type=forking
PIDFile=%(pid)s
PrivateTmp=false
Environment=
EnvironmentFile=
ExecStartPre=
ExecStart=
ExecStart=%(bridge)s start
ExecReload=
ExecReload=%(bridge)s reload
ExecStop=
ExecStop=%(bridge)s stop
ExecStopPost=
"""

OFFSET_UNIT = """[Unit]
Description=OpenLiteSpeed ao lado do Apache (olsbridge, portas +%(offset)s)
After=network.target

[Service]
Type=forking
PIDFile=%(pid)s
ExecStart=%(bridge)s start
ExecReload=%(bridge)s reload
ExecStop=%(bridge)s stop
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

SYNC_SERVICE = """[Unit]
Description=Regenera a configuração do OLS se a do Apache mudou

[Service]
Type=oneshot
ExecStart=%(bridge)s sync
"""

SYNC_TIMER = """[Unit]
Description=Verifica a configuração do Apache a cada 30 segundos

[Timer]
OnBootSec=60
OnUnitActiveSec=30

[Install]
WantedBy=timers.target
"""


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, mode)


def systemctl(*args):
    return run(["systemctl"] + list(args))


def cmd_install(settings, mode, offset):
    if not settings.get("apache_conf"):
        print("Apache não encontrado: nada a fazer.")
        return 1
    if not os.path.exists(os.path.join(LSWS, "bin", "lswsctrl")):
        print("OpenLiteSpeed não encontrado em %s." % LSWS)
        return 1
    settings["mode"] = mode
    if offset:
        settings["port_offset"] = str(offset)
    settings.save()
    bridge = os.path.join(LSWS, "bin", "olsbridge")
    if os.path.realpath(sys.argv[0]) != os.path.realpath(bridge):
        shutil.copy2(os.path.realpath(sys.argv[0]), bridge)
        os.chmod(bridge, 0o755)
    values = {"bridge": bridge, "pid": PID_FILE, "offset": settings["port_offset"]}

    # O pacote do OLS sobe o servidor sozinho no boot; quem manda agora é a ponte.
    for unit in ("lshttpd", "lsws", "openlitespeed"):
        systemctl("disable", "--now", unit)
    lswsctrl("stop")
    wait_stopped()

    code, out = run([settings["apache_ctl"], "-t"])
    if code != 0:
        print("A configuração atual do Apache tem erros; corrija antes de instalar:\n" + out)
        return 1
    changed, warnings = generate(settings)
    for line in warnings[:20]:
        print("  aviso: " + line)

    service = settings["service"]
    write(os.path.join(SYSTEMD, "olsbridge-sync.service"), SYNC_SERVICE % values)
    write(os.path.join(SYSTEMD, "olsbridge-sync.timer"), SYNC_TIMER)
    if mode == "offset":
        write(os.path.join(SYSTEMD, "olsbridge.service"), OFFSET_UNIT % values)
        systemctl("daemon-reload")
        code, out = systemctl("enable", "--now", "olsbridge.service")
    else:
        systemctl("stop", service)
        write(os.path.join(SYSTEMD, service + ".service.d", "zz-olsbridge.conf"), DROPIN % values)
        systemctl("daemon-reload")
        code, out = systemctl("start", service)
        if code != 0:
            print("O OLS não subiu; voltando para o Apache.\n" + out)
            os.remove(os.path.join(SYSTEMD, service + ".service.d", "zz-olsbridge.conf"))
            systemctl("daemon-reload")
            systemctl("start", service)
            return 1
    systemctl("enable", "--now", "olsbridge-sync.timer")
    print("Instalado no modo %s. Avisos em %s/warnings.txt." % (mode, OUT))
    return 0 if code == 0 else 1


def cmd_uninstall(settings):
    service = settings.get("service", "apache2")
    systemctl("disable", "--now", "olsbridge-sync.timer")
    systemctl("disable", "--now", "olsbridge.service")
    dropin = os.path.join(SYSTEMD, service + ".service.d", "zz-olsbridge.conf")
    replaced = os.path.exists(dropin)
    if replaced:
        systemctl("stop", service)
        os.remove(dropin)
    for name in ("olsbridge.service", "olsbridge-sync.service", "olsbridge-sync.timer"):
        path = os.path.join(SYSTEMD, name)
        if os.path.exists(path):
            os.remove(path)
    systemctl("daemon-reload")
    if replaced:
        code, out = systemctl("start", service)
        print("Apache %s." % ("de volta" if code == 0 else "não subiu:\n" + out))
    print("olsbridge removido. A configuração gerada continua em %s." % OUT)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")
    for name in ("generate", "start", "stop", "reload", "sync", "status", "uninstall"):
        sub.add_parser(name)
    install = sub.add_parser("install")
    install.add_argument("--mode", choices=("replace", "offset"), default="offset")
    install.add_argument("--offset", type=int)
    args = parser.parse_args(argv)
    settings = Settings.load()
    if args.command != "status" and not settings.get("apache_conf"):
        print("Configuração do Apache não encontrada.")
        return 1
    if args.command == "generate":
        changed, warnings = generate(settings)
        for line in warnings:
            print("  " + line)
        return 0
    if args.command == "start":
        return cmd_start(settings)
    if args.command == "stop":
        return cmd_stop(settings)
    if args.command == "reload":
        return cmd_reload(settings)
    if args.command == "sync":
        return cmd_reload(settings, only_if_changed=True)
    if args.command == "status":
        return cmd_status(settings)
    if args.command == "install":
        return cmd_install(settings, args.mode, args.offset)
    if args.command == "uninstall":
        return cmd_uninstall(settings)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
