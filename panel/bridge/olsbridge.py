#!/usr/bin/env python3
"""Serve com o OpenLiteSpeed a configuração que o painel gera para o Apache.

    olsbridge generate            gera a configuração do OLS e mostra os avisos
    olsbridge start|stop|reload   comandos usados pela unidade do systemd
    olsbridge sync                gera de novo e recarrega só se o Apache mudou
    olsbridge status
    olsbridge check               mostra o que seria gerado, sem escrever nada
    olsbridge php-restart [8.2..] reinicia o lsphp, sem derrubar requisições
    olsbridge php-install         instala o phpX.Y-litespeed de cada versão do PHP-FPM
    olsbridge compare [CAMINHO..] compara Apache e OLS no modo offset (só GET)
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
# O que o lsphp lê como o usuário do domínio: fora de conf/, que é só do lsadm.
PUB = os.path.join(LSWS, "share", "olsbridge")
PID_FILE = "/tmp/lshttpd/lshttpd.pid"
SYSTEMD = "/etc/systemd/system"

PHP_ROOT = "/etc/php"
HESTIA_CONF = "/usr/local/hestia/conf/hestia.conf"

DEFAULTS = {
    "mode": "replace",
    "port_offset": "10000",
    "php_max_conns": "10",
    # lsphp: cada pool do PHP-FPM vira um lsphp da mesma versão, com o usuário e os
    # valores do pool. fpm: o OLS usa os sockets do PHP-FPM.
    "php": "lsphp",
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
acceptpathinfo rpafenable rpafsethostname rpafproxy_ips rpafheader
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
                "php_max_conns", "panel", "php", "front", "front_service", "port_map", "units"]
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
        hestia = read_hestia_conf()
        # nginx na frente e Apache atrás: o OLS assume as portas do nginx.
        if hestia.get("PROXY_SYSTEM") == "nginx":
            found["front"] = "nginx"
            found["front_service"] = "nginx"
            pairs = [(hestia.get("WEB_PORT"), hestia.get("PROXY_PORT")),
                     (hestia.get("WEB_SSL_PORT"), hestia.get("PROXY_SSL_PORT"))]
            found["port_map"] = ",".join("%s:%s" % p for p in pairs if all(x and x.isdigit()
                                                                          for x in p))
    elif os.path.isdir("/usr/local/directadmin"):
        found["panel"] = "directadmin"
    return found


def read_hestia_conf(path=HESTIA_CONF):
    found = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.match(r"^\s*([A-Z_]+)='?([^']*)'?\s*$", line)
                if match:
                    found[match.group(1)] = match.group(2)
    except OSError:
        pass
    return found


def port_map(settings):
    result = {}
    for pair in settings.get("port_map", "").split(","):
        old, _, new = pair.partition(":")
        if old.strip().isdigit() and new.strip().isdigit():
            result[int(old)] = int(new)
    return result


# --------------------------------------------------------------------------
# Pools do PHP-FPM


class Pool(object):
    def __init__(self, name, version, path):
        self.name = name
        self.version = version
        self.path = path
        self.listen = None
        self.user = None
        self.group = None
        self.children = None
        self.max_requests = None
        self.idle = None
        self.ini = []            # (chave, valor, é flag)
        self.env = []            # (nome, valor)

    @property
    def lsphp(self):
        return "/usr/bin/lsphp%s" % self.version


def fpm_seconds(value):
    match = re.match(r"^(\d+)([smhd]?)$", value.strip())
    if not match:
        return None
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


def read_pools(root=None):
    """Pools de /etc/php/*/fpm/pool.d, pelo socket em que escutam."""
    root = root or PHP_ROOT
    pools = {}
    for path in sorted(glob.glob(os.path.join(root, "*", "fpm", "pool.d", "*.conf"))):
        version = path[len(root):].strip("/").split("/")[0]
        pool = None
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line[0] in ";#":
                continue
            match = re.match(r"^\[(.+)\]$", line)
            if match:
                pool = Pool(match.group(1), version, path)
                pools.setdefault(id(pool), pool)
                continue
            if pool is None or "=" not in line:
                continue
            key, value = [p.strip() for p in line.split("=", 1)]
            value = value.strip('"')
            match = re.match(r"^(php_admin_value|php_value|php_admin_flag|php_flag|env)\[(.+)\]$",
                             key)
            if match:
                kind, name = match.groups()
                if kind == "env":
                    pool.env.append((name, value))
                else:
                    pool.ini.append((name, value, kind.endswith("flag")))
            elif key == "listen":
                pool.listen = value
            elif key == "user":
                pool.user = value
            elif key == "group":
                pool.group = value
            elif key == "pm.max_children" and value.isdigit():
                pool.children = int(value)
            elif key == "pm.max_requests" and value.isdigit():
                pool.max_requests = int(value)
            elif key == "pm.process_idle_timeout":
                pool.idle = fpm_seconds(value)
    return dict((p.listen, p) for p in pools.values() if p.listen)


# --------------------------------------------------------------------------
# nginx na frente (HestiaCP): o que ele faz além de repassar para o Apache


def nginx_tokens(text):
    pos, size = 0, len(text)
    while pos < size:
        char = text[pos]
        if char.isspace():
            pos += 1
        elif char == "#":
            end = text.find("\n", pos)
            pos = size if end < 0 else end
        elif char in "{};":
            yield char
            pos += 1
        elif char in "\"'":
            end = pos + 1
            while end < size and text[end] != char:
                end += 2 if text[end] == "\\" else 1
            yield text[pos + 1:end]
            pos = end + 1
        else:
            end = pos
            while end < size and not text[end].isspace() and text[end] not in "{};":
                end += 1
            yield text[pos:end]
            pos = end


def nginx_parse(path, root="/etc/nginx", depth=0):
    """Lê um arquivo do nginx em nós (nome, args, filhos), expandindo os include."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            tokens = list(nginx_tokens(handle.read()))
    except OSError:
        return []
    top = []
    stack = [top]
    words = []
    for token in tokens:
        if token == ";":
            if words:
                name, args = words[0], words[1:]
                if name == "include" and args and depth < 8:
                    target = args[0] if os.path.isabs(args[0]) else os.path.join(root, args[0])
                    for name_ in sorted(glob.glob(target)):
                        node = Node("include", [name_], path, [])
                        node.children = nginx_parse(name_, root, depth + 1)
                        stack[-1].append(node)
                else:
                    stack[-1].append(Node(name, args, path))
            words = []
        elif token == "{":
            node = Node(words[0] if words else "", words[1:], path, [])
            stack[-1].append(node)
            stack.append(node.children)
            words = []
        elif token == "}":
            if len(stack) > 1:
                stack.pop()
            words = []
        else:
            words.append(token)
    return top


def nginx_flat(nodes):
    """Os filhos de um bloco, com o conteúdo dos include no lugar."""
    for node in nodes:
        if node.name == "include" and node.children is not None:
            for child in nginx_flat(node.children):
                yield child
        else:
            yield node


class Front(object):
    """O que o nginx de um domínio faz por conta própria."""

    def __init__(self):
        self.redirect = None     # https ou http: `return 301 <esquema>://...` do servidor
        self.hsts = None
        self.acme = None         # thumbprint da conta do Let's Encrypt
        self.deny = []           # (regex, status, sem maiúsculas)
        self.expires = False
        self.custom = []         # includes do usuário que não sabemos converter
        self.aliases = []        # (prefixo, pasta) de `location /x/ { alias ...; }`

    @property
    def forcessl(self):
        return self.redirect == "https"


ACME_RE = re.compile(r"^\$1\.([A-Za-z0-9_-]+)$")


def read_fronts(paths=None):
    """Servidores do nginx dos domínios, por (nome, SSL)."""
    if paths is None:
        paths = sorted(glob.glob("/etc/nginx/conf.d/domains/*.conf") +
                       glob.glob("/etc/nginx/conf.d/*.conf"))
    fronts = {}
    for path in paths:
        for server in nginx_parse(path):
            if server.name != "server" or server.children is None:
                continue
            front, names, ssl, default = Front(), [], False, False
            for node in nginx_flat(server.children):
                if node.name == "listen":
                    ssl = ssl or "ssl" in node.args or any(a.endswith(":443") for a in node.args)
                    default = default or "default_server" in node.args or "default" in node.args
                elif node.name == "server_name":
                    names += [n.lower() for n in node.args]
                elif node.name == "return" and node.args[:1] in (["301"], ["302"]) and \
                        len(node.args) > 1 and re.match(r"^https?://", node.args[1]):
                    front.redirect = node.args[1].split(":", 1)[0]
                elif node.name == "add_header" and node.args[:1] and \
                        node.args[0].lower() == "strict-transport-security" and len(node.args) > 1:
                    front.hsts = node.args[1]
                elif node.name == "location" and node.children is not None:
                    read_location(front, node)
            for node in server.children:
                if node.name == "include" and node.children and \
                        re.search(r"nginx(\.ssl)?\.conf_", node.args[0]) and \
                        not node.args[0].endswith("_letsencrypt"):
                    front.custom.append(node.args[0])
            if default:
                names.append("_default_")  # o que chega pelo IP ou com um nome desconhecido
            for name in names:
                fronts.setdefault((name, ssl), front)
    return fronts


def nginx_charset(path="/etc/nginx/nginx.conf"):
    """O `charset` do bloco http: o nginx o acrescenta ao Content-Type do texto."""
    for node in nginx_parse(path):
        if node.name == "http" and node.children is not None:
            for child in nginx_flat(node.children):
                if child.name == "charset" and child.args and child.args[0] != "off":
                    return child.args[0]
    return None


def read_location(front, node):
    args = node.args
    regex, nocase = None, False
    if len(args) == 2 and args[0] in ("~", "~*"):
        regex, nocase = args[1], args[0] == "~*"
    elif len(args) == 1 and args[0].startswith("/"):
        regex = "^" + re.escape(args[0])
    children = list(nginx_flat(node.children))
    keys = [c.name for c in children]
    if len(args) == 1 and args[0].startswith("/") and "alias" in keys:
        for child in children:
            if child.name == "alias" and child.args:
                front.aliases.append((args[0], child.args[0]))
        return
    for child in children:
        if child.name == "return" and child.args[:1] == ["200"] and len(child.args) > 1 and \
                "acme-challenge" in (regex or ""):
            match = ACME_RE.match(child.args[1])
            if match:
                front.acme = match.group(1)
            return
    if regex and "deny" in keys:
        status = 403
        for child in children:
            if child.name == "return" and child.args[:1] and child.args[0].isdigit():
                status = int(child.args[0])
        front.deny.append((regex, status, nocase))
        return
    for child in node.children:
        if child.name == "location" and child.children is not None:
            if any(c.name == "expires" for c in child.children):
                front.expires = True


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
        self.reading = []
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
        self.files = []
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
        # O Apache inclui o mesmo arquivo quantas vezes for pedido (o HestiaCP põe
        # conf.d/*.inc em cada vhost); só um arquivo que inclui a si mesmo é erro.
        path = os.path.normpath(path)
        if path in self.reading:
            self.warnings.append("%s: Include recursivo ignorado" % path)
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError as err:
            if required:
                raise SystemExit("não foi possível ler %s: %s" % (path, err))
            self.warnings.append("%s: não foi possível ler (%s)" % (path, err.strerror))
            return
        if path not in self.files:
            self.files.append(path)
        self.reading.append(path)
        try:
            self.read_lines(path, text, into)
        finally:
            self.reading.pop()

    def read_lines(self, path, text, into):
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
        self.docroot = None
        self.php = []            # (sufixos, endereço) de um <FilesMatch> fora das vhosts
        self.has_status = False  # há um <Location> de server-status


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


def files_php(node, warnings):
    """PHP de um bloco <Files>/<FilesMatch>: [(sufixos, endereço)], vazio se não houver."""
    found = []
    for child in node.children:
        if child.key == "sethandler" and child.args:
            address = proxy_address(child.args[0])
            if address:
                found.append((php_suffixes(" ".join(node.args), node.where, warnings), address))
        elif child.key == "addhandler" and len(child.args) > 1:
            address = proxy_address(child.args[0])
            if address:
                found.append(([s.lstrip(".") for s in child.args[1:]], address))
    return found


def access_rule(node):
    """Acesso de um <Directory>: None (sem regra), "grant", "deny" ou os IPs liberados.

    Cobre o `Require` do 2.4 e o `Order`/`Allow`/`Deny` do access_compat. Nomes de
    máquina não viram regra do OLS; sem IP nenhum, a pasta fica negada.
    """
    def ips(values):
        out = []
        for value in values:
            if value.lower() == "localhost":
                out += ["127.0.0.1", "::1"]
            elif re.match(r"^[0-9a-fA-F.:/]+$", value):
                out.append(value)
        return out or "deny"

    order, allows, denies, require, compat = None, [], [], None, False
    for child in node.children:
        args = [a.lower() for a in child.args]
        if child.key == "require" and args:
            if args[0] == "all":
                require = "deny" if args[1:2] == ["denied"] else []
            elif args[0] == "ip":
                require = (require if isinstance(require, list) else []) + child.args[1:]
            elif args[0] == "local":
                require = (require if isinstance(require, list) else []) + ["127.0.0.1", "::1"]
        elif child.key == "order" and args:
            order, compat = "".join(args).replace(" ", ""), True
        elif child.key == "allow" and args[:1] == ["from"]:
            allows, compat = allows + child.args[1:], True
        elif child.key == "deny" and args[:1] == ["from"]:
            denies, compat = denies + child.args[1:], True
    if require is not None:
        return "grant" if require == [] else (require if require == "deny" else ips(require))
    if not compat:
        return None
    allow_all = any(a.lower() == "all" for a in allows)
    deny_all = any(d.lower() == "all" for d in denies)
    if (order or "deny,allow") == "deny,allow":
        if not deny_all or allow_all:
            return "grant"  # nega alguns IPs e libera o resto: o OLS não precisa disso
        return ips([a for a in allows if a.lower() != "all"])
    # allow,deny: o padrão é negar, e um Deny que casa vence o Allow.
    if deny_all:
        return "deny"
    if allow_all:
        return "grant"
    return ips(allows)


def dir_info(node, warnings):
    info = {"index": None, "php": [], "access": access_rule(node)}
    for child in node.children:
        if child.key == "directoryindex":
            info["index"] = child.args
        elif child.children is not None and child.key in ("files", "filesmatch"):
            info["php"] += files_php(child, warnings)
    return info


def covers(prefix, url):
    """Se o Alias `prefix` casa com `url`: igual, ou seguido de um segmento."""
    base = prefix.rstrip("/")
    return url == prefix or base == "" or url.rstrip("/") == base or url.startswith(base + "/")


def build(config):
    server = Server()
    warnings = config.warnings
    for node in config.tree:
        apply_server(server, node, config, warnings)
    for vhost in server.vhosts:
        # O Apache testa os Alias na ordem, os da vhost antes dos do servidor, e o
        # primeiro que casa vence: um Alias depois de outro que o cobre nunca é usado.
        aliases = []
        for alias in vhost.alias + server.alias:
            if not any(covers(a[1], alias[1]) for a in aliases):
                aliases.append(alias)
        root = [a for a in aliases if a[0] == "alias" and a[1] == "/"]
        if root:
            # Webmail do HestiaCP: `Alias / /var/lib/roundcube/...` no lugar do DocumentRoot.
            aliases.remove(root[0])
            vhost.docroot = root[0][2].rstrip("/") or "/"
        elif not vhost.docroot and server.docroot:
            vhost.docroot = server.docroot
        vhost.alias = aliases
        if not vhost.php:
            vhost.php = list(server.php)
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
            if any(v.where == node.where for v in server.vhosts):
                # O HestiaCP inclui conf.d/domains/webmail.*.conf e depois *.conf: o
                # Apache vê a mesma vhost duas vezes e usa a primeira.
                return
            vhost = VHost(node)
            for child in node.children:
                apply_vhost(vhost, child, warnings)
            server.vhosts.append(vhost)
        elif key in ("directory", "directorymatch"):
            server.dirs.append((args[0] if args else "", key == "directorymatch" or
                                (len(args) > 1 and args[0] == "~"), node))
        elif key in ("files", "filesmatch") and args and args[-1] in ('^\\.ht', "^\\.ht", ".ht*"):
            pass  # o OLS já nega .ht*
        elif key in ("files", "filesmatch") and files_php(node, []):
            server.php += files_php(node, warnings)  # PHP padrão, se a vhost não tiver o seu
        elif key == "location" and any(c.key == "sethandler" and c.args and
                                       c.args[0].lower() in ("server-status", "server-info")
                                       for c in node.children):
            server.has_status = True  # status do Apache: o OLS tem o seu, no WebAdmin
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
    elif key == "documentroot" and args:
        server.docroot = args[0].rstrip("/") or "/"
    elif key in ("alias", "scriptalias") and len(args) > 1:
        server.alias.append((key, args[0], args[1]))
    elif key == "rpafproxy_ips":
        server.proxies += args  # mod_rpaf: mesmo papel do RemoteIPInternalProxy
    elif key == "rpafheader" and args and not server.proxy_header:
        server.proxy_header = args[0]
    elif key == "remoteipheader" and args:
        server.proxy_header = args[0]
    elif key in ("remoteipinternalproxy", "remoteiptrustedproxy"):
        server.proxies += args
    elif key not in IGNORED and key not in SERVER_IGNORED and not key.startswith("fcgid"):
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
            found = files_php(node, warnings)
            vhost.php += found
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
        self.front = settings.get("front") == "nginx"
        self.port_map = port_map(settings) if self.front else {}
        self.fronts = read_fronts() if self.front else {}
        self.charset = nginx_charset() if self.front else None
        self.pools = read_pools() if settings.get("php", "lsphp") == "lsphp" else {}
        self.kinds = {}          # processador -> fcgi ou lsapi
        self.pool_of = {}        # processador lsapi -> pool do PHP-FPM
        self.extra_files = {}    # php/<processador>/pool.ini, acme/<vhost>/index.php

    def processor(self, address):
        if address == "acme" and address not in self.processors:
            self.processors[address] = "lsphp_acme"
            self.kinds["lsphp_acme"] = "lsapi"
        if address not in self.processors:
            pool = self.pools.get(address[len("uds:/"):]) if address.startswith("uds:/") else None
            if pool is not None and not os.access(pool.lsphp, os.X_OK):
                self.warnings.append("PHP %s sem %s: o pool %s segue no PHP-FPM" %
                                     (pool.version, pool.lsphp, pool.name))
                pool = None
            if pool is not None and not (pool.user and SAFE_NAME.match(pool.user)):
                self.warnings.append("pool %s sem usuário válido: segue no PHP-FPM" % pool.name)
                pool = None
            if pool is not None:
                name = "lsphp%s_%s" % (pool.version.replace(".", ""),
                                       re.sub(r"[^A-Za-z0-9]+", "_", pool.name).strip("_"))
                kind = "lsapi"
            else:
                name = "fpm_" + re.sub(r"[^A-Za-z0-9]+", "_",
                                       address.replace("uds:/", "")).strip("_")
                kind = "fcgi"
            name = name[:80]
            self.processors[address] = name
            self.kinds[name] = kind
            if pool is not None:
                self.pool_of[name] = pool
        return self.processors[address]

    def handler(self, address):
        name = self.processor(address)
        return "%s:%s" % (self.kinds[name], name)

    def lsapi_processor(self, name, pool):
        """lsphp com o usuário, os limites e os valores do pool do PHP-FPM.

        O php.ini é o do PHP-FPM da mesma versão, que o painel edita, e as extensões
        são as de litespeed/conf.d, que o pacote mantém iguais às do PHP-FPM. Os
        php_admin_value e php_value do pool vão para um .ini lido depois deles.
        """
        if pool is None:  # desafio do Let's Encrypt: sem pool, com o usuário do servidor
            version = self.acme_version
            env = ["PHP_LSAPI_CHILDREN=2"]
            user = group = None
            children, extra = 2, []
        else:
            version = pool.version
            children = pool.children or 8
            ini_dir = os.path.join(PUB, "php", name)
            lines = ["; pool %s (%s)" % (pool.name, pool.path)]
            for key, value, flag in pool.ini:
                if not re.match(r"^[A-Za-z0-9_.]+$", key) or "\n" in value:
                    self.warnings.append("pool %s: valor %s ignorado" % (pool.name, key))
                    continue
                if flag:
                    lines.append("%s = %s" % (key, "On" if value.lower() in ("on", "1", "yes",
                                                                             "true") else "Off"))
                else:
                    lines.append('%s = "%s"' % (key, value.replace('"', "")))
            self.extra_files["php/%s/zz-pool.ini" % name] = "\n".join(lines) + "\n"
            env = ["PHP_LSAPI_CHILDREN=%d" % children,
                   "PHP_LSAPI_MAX_REQUESTS=%d" % (pool.max_requests or 500),
                   "LSAPI_MAX_IDLE=%d" % (pool.idle or 300),
                   "PHPRC=%s/%s/fpm" % (PHP_ROOT, version),
                   "PHP_INI_SCAN_DIR=%s/%s/litespeed/conf.d:%s" % (PHP_ROOT, version, ini_dir),
                   # O PHP-FPM lê o .user.ini de cada pasta; o lsphp só com isto.
                   "LSPHP_ENABLE_USER_INI=on"]
            env += ["%s=%s" % (k, v) for k, v in pool.env
                    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k) and "\n" not in v]
            user, group = pool.user, pool.group or pool.user
        out = ["extprocessor %s {" % name,
               "    type                  lsapi",
               "    address               uds://tmp/lshttpd/%s.sock" % name,
               "    maxConns              %d" % children]
        out += ["    env                   %s" % e for e in env]
        out += ["    initTimeout           60",
                "    retryTimeout          0",
                # Cada processo do OLS guarda as suas conexões, e uma conexão aberta
                # prende um filho do lsphp: com 8 processos e um pool de 2 filhos, as
                # requisições dos outros processos esperavam para sempre.
                "    persistConn           0",
                "    respBuffer            0",
                "    autoStart             2",
                "    path                  /usr/bin/lsphp%s" % version,
                "    backlog               100",
                "    instances             1",
                "    memSoftLimit          2047M",
                "    memHardLimit          2047M",
                "    procSoftLimit         1000",
                "    procHardLimit         1200"]
        if user:
            out += ["    extUser               %s" % user, "    extGroup              %s" % group]
        return out + ["}", ""]

    def port_of(self, port):
        """Porta do Apache -> porta em que o OLS escuta (a do nginx, se ele assume a frente)."""
        return self.port_map.get(port, port) + self.offset

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
        # A primeira vhost de cada endereço é a padrão, como no Apache.
        seen = set()
        for vhost in vhosts:
            vhost.is_default = bool(vhost.addrs) and vhost.addrs[0] not in seen
            seen.update(vhost.addrs)
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
                self.warnings.append("Listen %s:%d sem vhost, não convertido%s" % (
                    ip, port, " (deve ser o server-status do Apache, que o OLS não imita)"
                    if self.server.has_status else ""))
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
               "disableWebAdmin           1",  # o painel não usa o WebAdmin do OLS
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
               # Sem este bloco o OLS não configura o lscgid, e nenhum lsphp sobe com o
               # usuário do domínio ("cgidSuEXEC failed").
               "CGIRLimit {",
               "    maxCGIInstances       100",
               "    minUID                11",
               "    minGID                10",
               "    priority              0",
               "    CPUSoftLimit          10",
               "    CPUHardLimit          50",
               "    memSoftLimit          2047M",
               "    memHardLimit          2047M",
               "    procSoftLimit         400",
               "    procHardLimit         450",
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
            if self.kinds[name] == "lsapi":
                out += self.lsapi_processor(name, self.pool_of.get(name))
                continue
            out += ["extprocessor %s {" % name,
                    "    type                  fcgi",
                    "    address               %s" % address,
                    "    maxConns              %s" % conns,
                    "    initTimeout           60",
                    "    retryTimeout          0",
                    # Como o mod_proxy_fcgi: uma conexão por requisição. Persistente,
                    # cada conexão prende um processo do pool (o www do HestiaCP tem 4).
                    "    persistConn           0",
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
                              self.port_of(port))
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

    def url_path(self, vhost, url):
        """Arquivo que o Apache serviria para a URL: o primeiro Alias que casa, ou a raiz."""
        for _kind, prefix, target in vhost.alias:
            if covers(prefix, url):
                rest = url[len(prefix.rstrip("/")):].lstrip("/")
                return os.path.join(target, rest)
        return vhost.docroot.rstrip("/") + url

    def dir_uris(self, vhost, path):
        """URLs em que uma pasta aparece na vhost: pela raiz ou por um Alias."""
        path = path.rstrip("/") or "/"
        result = []
        bases = [("/", vhost.docroot)] + [(u, t) for k, u, t in vhost.alias if k == "alias"]
        for url, target in bases:
            base = target.rstrip("/") or "/"
            if path == base or path.startswith(base.rstrip("/") + "/"):
                result.append((url.rstrip("/") + path[len(base):] + "/", path + "/"))
        return result

    def effective(self, vhost, path):
        """Junta os <Directory> que cobrem o caminho, do mais curto ao mais longo."""
        path = path.rstrip("/") or "/"
        merged = {"index": None, "php": [], "access": None}
        dirs = [(p.rstrip("/") or "/", n) for p, rx, n in self.server.dirs + vhost.dirs if not rx]
        for base, node in sorted(dirs, key=lambda d: len(d[0])):
            if path == base or path.startswith(base.rstrip("/") + "/"):
                info = dir_info(node, [])
                for key in ("index", "access"):
                    if info[key] is not None:
                        merged[key] = info[key]
                if info["php"]:
                    merged["php"] = info["php"]
        if merged["access"] == "grant":
            merged["access"] = None
        return merged

    def contexts(self, vhost):
        found, order = {}, []

        def add(uri, location, kind):
            key = uri.rstrip("/") or "/"
            if key not in found:
                found[key] = {"uri": uri, "location": location, "kind": kind}
                order.append(key)

        for kind, url, path in vhost.alias:
            if not SAFE_PATH.match(path) or not SAFE_PATH.match(url):
                self.warnings.append("%s: Alias %s com caminho inválido" % (vhost.label, url))
                continue
            add(url, path, "cgi" if kind == "scriptalias" else "null")
        front = self.front_of(vhost)
        for url, path in (front.aliases if front is not None else []):
            # O nginx serve estes prefixos sem passar pelo Apache.
            if SAFE_PATH.match(url) and SAFE_PATH.match(path):
                found.pop(url.rstrip("/") or "/", None)
                if (url.rstrip("/") or "/") in order:
                    order.remove(url.rstrip("/") or "/")
                add(url, path, "null")
        for path, is_regex, node in self.server.dirs + vhost.dirs:
            info = dir_info(node, [])
            if is_regex or not (info["index"] or info["php"] or info["access"] is not None):
                continue
            for uri, location in self.dir_uris(vhost, path):
                if uri == "/":
                    continue
                if SAFE_PATH.match(uri) and SAFE_PATH.match(location):
                    add(uri, location, "null")
        root = self.effective(vhost, vhost.docroot)
        if root["access"] is not None:
            self.warnings.append("%s: restrição de acesso na raiz do site não convertida" %
                                 vhost.label)
        result = []
        for key in order:
            ctx = found[key]
            ctx.update(self.effective(vhost, ctx["location"]))
            result.append(ctx)
        return result

    def front_of(self, vhost):
        if not self.front:
            return None
        for name in [vhost.name] + vhost.aliases:
            found = self.fronts.get((name, vhost.ssl))
            if found is not None:
                return found
        if getattr(vhost, "is_default", False):
            return self.fronts.get(("_default_", vhost.ssl))
        return None

    @property
    def acme_version(self):
        found = sorted((p.version for p in self.pools.values() if os.access(p.lsphp, os.X_OK)),
                       key=version_tuple)
        return found[-1] if found else None

    def front_rules(self, vhost):
        """Regras do nginx do domínio, antes das do Apache. Devolve (regras, tem ACME)."""
        front = self.front_of(vhost)
        if front is None:
            return [], False
        rules = []
        # O return do forcessl vale para o servidor inteiro, antes de qualquer location.
        if front.redirect and (front.redirect == "https") != vhost.ssl:
            rules.append("RewriteRule ^(.*)$ %s://%%{HTTP_HOST}$1 [R=301,L]" % front.redirect)
        acme = False
        if front.acme:
            if self.acme_version:
                acme = True
                rules.append("RewriteRule ^/\\.well-known/acme-challenge/([A-Za-z0-9_-]+)$ "
                             "/olsbridge-acme/index.php?t=$1 [L]")
                self.extra_files["acme/%s/index.php" % vhost.ols_name] = (
                    "<?php\n// Let's Encrypt sem estado, como o nginx.conf_letsencrypt do HestiaCP.\n"
                    "$t = isset($_GET['t']) ? $_GET['t'] : '';\n"
                    "if (!preg_match('/^[A-Za-z0-9_-]+$/', $t)) { http_response_code(404); exit; }\n"
                    "header('Content-Type: text/plain');\n"
                    "echo $t, '.%s';\n" % front.acme)
            else:
                self.warnings.append("%s: desafio do Let's Encrypt precisa de um lsphp" %
                                     vhost.label)
        for regex, status, nocase in front.deny:
            if re.search(r"\s", regex):
                self.warnings.append("%s: regra do nginx com espaço não convertida: %s" %
                                     (vhost.label, regex))
                continue
            flags = ["F"] if status == 403 else ["R=%d" % status]
            rules.append("RewriteRule %s - [%s]" % (regex, ",".join(flags + ["NC"] * nocase + ["L"])))
        if front.hsts and vhost.ssl:
            vhost.headers.append('Header always set Strict-Transport-Security "%s"' % front.hsts)
        for path in front.custom:
            self.warnings.append("%s: include do nginx não convertido: %s" % (vhost.label, path))
        return rules, acme

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
            if not url.startswith("/"):
                continue
            # Página que não existe: o Apache mostra a padrão; o OLS daria 500 ao
            # procurá-la (o webmail do HestiaCP aponta /error/ para dentro do Roundcube).
            if not os.path.isfile(self.url_path(vhost, url.split("?")[0])):
                continue
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
        front_rules, acme = self.front_rules(vhost)
        out += ["rewrite {", "    enable                1", "    autoLoadHtaccess      %d" %
                (0 if allow.lower() == "none" else 1)]
        if allow.lower() not in ("all", "none"):
            out.append("    htaccessAllowOverride %s" % allow)
        rules = front_rules + (vhost.rewrite if vhost.rewrite_on else [])
        if rules:
            out.append("    rules                 <<<END_rules")
            out += rules
            out.append("    END_rules")
        out += ["}", ""]
        front = self.front_of(vhost)
        if front is not None and front.expires:
            # nginx: `expires max` nos arquivos estáticos que ele mesmo serve.
            types = ["image/*", "text/*", "font/*", "audio/*", "video/*", "application/javascript",
                     "application/x-javascript", "application/json", "application/xml",
                     "application/pdf", "application/zip", "application/wasm",
                     "application/vnd.ms-fontobject", "application/font-woff"]
            out += ["expires {", "    enableExpires         1",
                    "    expiresByType         %s" % ", ".join("%s=A315360000" % t for t in types),
                    "}", ""]
        handlers = ["    add                   %s %s" % (self.handler(address), ", ".join(suffixes))
                    for suffixes, address in vhost.php]
        if acme:
            handlers.append("    add                   %s olsbacme" % self.handler("acme"))
        own = dict((s, a) for suffixes, a in vhost.php for s in suffixes)
        contexts = self.contexts(vhost)
        pseudo = {}
        for ctx in contexts:
            # PHP de outro pool numa pasta (o phpMyAdmin no www.sock, por exemplo): o
            # contexto aponta o sufixo para um tipo MIME que só aquele handler atende.
            ctx["mime"] = []
            for suffixes, address in ctx["php"]:
                for suffix in suffixes:
                    if own.get(suffix) == address:
                        continue
                    if address not in pseudo:
                        pseudo[address] = "olsbphp%d" % (len(pseudo) + 1)
                        handlers.append("    add                   %s %s" %
                                        (self.handler(address), pseudo[address]))
                    ctx["mime"].append("application/x-httpd-%s %s" % (pseudo[address], suffix))
        if handlers:
            out += ["scripthandler {"] + handlers + ["}", ""]
        for ctx in contexts:
            sub_allow, sub_index = directory_settings(self.server, vhost,
                                                      ctx["location"].rstrip("/"))
            access = ctx["access"]
            out += ["context %s {" % ctx["uri"],
                    "    type                  %s" % ctx["kind"],
                    "    location              %s" % ctx["location"],
                    "    allowBrowse           %d" % (0 if access == "deny" else 1)]
            if isinstance(access, list):
                out += ["    accessControl {", "        allow             %s" % ", ".join(access),
                        "        deny              ALL", "    }"]
            if ctx["kind"] != "cgi":
                out += ["    indexFiles            %s" % ", ".join(ctx["index"] or vhost.index or
                                                                  ["index.html"]),
                        "    autoIndex             %d" % sub_index]
                if ctx["mime"]:
                    out.append("    addMIMEType           %s" % ", ".join(ctx["mime"]))
                if sub_allow.lower() != "none":
                    out += ["    rewrite {", "        enable            1", "    }"]
            out += ["}", ""]
        if acme:
            out += ["context /olsbridge-acme/ {", "    type                  null",
                    "    location              %s/acme/%s/" % (PUB, vhost.ols_name),
                    "    allowBrowse           1",
                    "    addMIMEType           application/x-httpd-olsbacme php", "}", ""]
        if vhost.headers or self.charset:
            out += ["context / {", "    location              %s/" % vhost.docroot.rstrip("/"),
                    "    allowBrowse           1"]
            if self.charset and re.match(r"^[A-Za-z0-9_-]+$", self.charset):
                # nginx: `charset utf-8` no texto que sai sem charset.
                out += ["    addDefaultCharset     on",
                        "    defaultCharsetCustomized %s" % self.charset]
            if vhost.headers:
                out.append("    extraHeaders          <<<END_headers")
                for line in vhost.headers:
                    out.append(re.sub(r"(?i)^header\s+(always\s+)?", "", line))
                out.append("    END_headers")
            out += ["}", ""]
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
    public = generator.extra_files

    digest = hashlib.sha256()
    for prefix, group in (("", files), ("pub/", public)):
        for name in sorted(group):
            digest.update((prefix + name).encode() + b"\0" + group[name].encode("utf-8"))
    digest = digest.hexdigest()
    stamp = os.path.join(OUT, ".digest")
    try:
        with open(stamp) as handle:
            changed = handle.read().strip() != digest
    except OSError:
        changed = True

    prepare_logs(server, generator.log_files)
    if changed:
        # Primeiro o que o lsphp lê, depois a configuração que aponta para lá.
        swap_dir(PUB, public, lambda _name: 0o644, dir_mode=0o755)
        keep = os.path.join(OUT, "server-extra.conf")
        if os.path.exists(keep):
            with open(keep, encoding="utf-8") as handle:
                files["server-extra.conf"] = handle.read()
        files[".digest"] = digest + "\n"
        swap_dir(OUT, files, lambda name: 0o600 if name.startswith("ssl/") else 0o644)
        link_server_conf()
    if not quiet:
        print("%d vhosts, %d avisos%s." % (len([v for v in server.vhosts if v.ols_name]),
                                          len(config.warnings),
                                          "" if changed else ", nada mudou"))
    return changed, config.warnings


def swap_dir(target, files, mode_of, dir_mode=None):
    """Escreve numa pasta nova e troca de uma vez: o OLS nunca lê metade de uma geração."""
    new, old = target + ".new", target + ".old"
    shutil.rmtree(new, ignore_errors=True)
    os.makedirs(new)
    for name, body in files.items():
        path = os.path.join(new, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(path, mode_of(name))
    if dir_mode is not None:
        for base, dirs, _files in os.walk(new):
            os.chmod(base, dir_mode)
    shutil.rmtree(old, ignore_errors=True)
    if os.path.exists(target):
        os.rename(target, old)
    os.rename(new, target)
    shutil.rmtree(old, ignore_errors=True)


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
    if changed:
        restart_lsphp()  # um pool pode ter mudado de usuário, limites ou valores
    return 0


def status_info(settings):
    """Estado para a página do HestiaCP e para a API (v-list-sys-litespeed)."""
    pid = ols_pid()
    installed = os.path.exists(os.path.join(SYSTEMD, "olsbridge.service"))
    units = [u for u in settings.get("units", "").split(",") if u]
    mode = settings.get("mode", "offset") if installed else "off"
    if mode == "replace" and not units:
        mode = "offset"
    code, out = run([os.path.join(LSWS, "bin", "openlitespeed"), "-v"])
    version = out.splitlines()[0].split(" (")[0].strip() if code == 0 and out else ""
    lsphp_on = settings.get("php", "lsphp") == "lsphp"
    php = [{"version": v, "lsphp": os.access("/usr/bin/lsphp%s" % v, os.X_OK),
            "served_by": "lsphp" if lsphp_on and os.access("/usr/bin/lsphp%s" % v, os.X_OK)
            else "php-fpm"} for v in fpm_versions()]
    warnings = []
    try:
        with open(os.path.join(OUT, "warnings.txt"), encoding="utf-8") as handle:
            warnings = [l for l in handle.read().splitlines() if l]
    except OSError:
        pass
    if mode == "off":
        label = "off"
    elif mode == "offset":
        label = "testing beside the current servers (ports +%s)" % settings.get("port_offset")
    else:
        names = {"apache2": "Apache", "httpd": "Apache", "nginx": "nginx"}
        replaced = [names[u] for u in units if u in names]
        if any(re.match(r"^php.*-fpm$", u) for u in units):
            replaced.append("PHP-FPM")
        label = "active, in place of %s" % " + ".join(dict.fromkeys(replaced))
    if mode != "off" and not pid:
        label += " (stopped)"
    return {"mode": mode, "label": label, "running": bool(pid), "pid": pid or 0,
            "version": version, "units": units, "front": settings.get("front", ""),
            "port_offset": settings.get("port_offset"), "php": php, "warnings": warnings}


HESTIA = "/usr/local/hestia"
HESTIA_ADDON = os.path.join(LSWS, "add-ons", "olsbridge-hestia")
HESTIA_HOOK = "/etc/hestiacp/hooks/post_install.sh"
HESTIA_FILES = [
    "bin/v-list-sys-litespeed",
    "bin/v-change-sys-web-litespeed",
    "bin/v-restart-sys-litespeed",
    "bin/v-restart-sys-litespeed-php",
    "web/edit/server/litespeed/index.php",
    "web/templates/pages/edit_server_litespeed.php",
]
HESTIA_ANCHOR = '<?php if (!empty($_SESSION["WEB_BACKEND"])) { ?>'
HESTIA_ROW = """<!-- olsbridge:begin -->
\t\t\t\t\t<?php exec(HESTIA_CMD . "v-list-sys-litespeed json", $olsb_out); $olsb = json_decode(implode("", $olsb_out), true); unset($olsb_out); ?>
\t\t\t\t\t<p>
\t\t\t\t\t\tOpenLiteSpeed:
\t\t\t\t\t\t<span class="u-ml5"><?= tohtml(is_array($olsb) ? $olsb["label"] : _("unavailable")) ?></span>
\t\t\t\t\t\t<a href="/edit/server/litespeed/" class="u-ml5"><i class="fas fa-pencil icon-orange"></i></a>
\t\t\t\t\t</p>
\t\t\t\t\t<!-- olsbridge:end -->
\t\t\t\t\t"""
HOOK_BLOCK = """# olsbridge:begin
# Depois de cada atualização do HestiaCP: a linha do OpenLiteSpeed na tela Web Server.
[ -x %s/bin/olsbridge ] && %s/bin/olsbridge hestia-integrate >/dev/null 2>&1
# olsbridge:end
""" % (LSWS, LSWS)


def strip_block(text, begin, end):
    start = text.find(begin)
    if start < 0:
        return text
    stop = text.find(end, start)
    if stop < 0:
        return text
    stop += len(end)
    while stop < len(text) and text[stop] in " \t\n":
        stop += 1
    return text[:start] + text[stop:]


def hestia_integrate(remove=False):
    """Página do OpenLiteSpeed no HestiaCP, os comandos v-* e a linha na tela Web Server.

    Os arquivos novos não são do pacote do HestiaCP e sobrevivem às atualizações; a
    linha na tela é reaplicada pelo gancho post_install.sh que o pacote roda.
    """
    if not os.path.isdir(HESTIA):
        print("HestiaCP não encontrado em %s." % HESTIA)
        return 1
    page = os.path.join(HESTIA, "web/templates/pages/edit_server.php")
    try:
        with open(page, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as err:
        print("não foi possível ler %s: %s" % (page, err.strerror))
        return 1
    text = strip_block(text, "<!-- olsbridge:begin -->", "<!-- olsbridge:end -->")
    hook = ""
    if os.path.exists(HESTIA_HOOK):
        with open(HESTIA_HOOK, encoding="utf-8") as handle:
            hook = strip_block(handle.read(), "# olsbridge:begin", "# olsbridge:end")
    if remove:
        for name in HESTIA_FILES:
            path = os.path.join(HESTIA, name)
            if os.path.exists(path):
                os.remove(path)
        try:
            os.rmdir(os.path.join(HESTIA, "web/edit/server/litespeed"))
        except OSError:
            pass
    else:
        for name in HESTIA_FILES:
            src = os.path.join(HESTIA_ADDON, name)
            if not os.path.exists(src):
                print("faltou %s; reinstale o pacote." % src)
                return 1
            dst = os.path.join(HESTIA, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o755 if name.startswith("bin/") else 0o644)
        if HESTIA_ANCHOR not in text:
            print("aviso: a tela Web Server mudou nesta versão do HestiaCP; a página continua em "
                  "/edit/server/litespeed/")
        else:
            text = text.replace(HESTIA_ANCHOR, HESTIA_ROW + HESTIA_ANCHOR, 1)
        if not hook.strip():
            hook = "#!/bin/sh\n"
        hook = hook.rstrip("\n") + "\n" + HOOK_BLOCK
    write_keep(page, text)
    if remove and hook.strip() in ("", "#!/bin/sh"):
        if os.path.exists(HESTIA_HOOK):
            os.remove(HESTIA_HOOK)
    elif hook:
        write(HESTIA_HOOK, hook, 0o755)
    print("Integração com o HestiaCP %s." % ("removida" if remove else "instalada"))
    return 0


def write_keep(path, text):
    """Reescreve mantendo dono e permissões do arquivo original."""
    st = os.stat(path)
    tmp = path + ".olsbridge"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    os.rename(tmp, path)


def cmd_status(settings, as_json=False):
    if as_json:
        import json
        print(json.dumps(status_info(settings), indent=1))
        return 0
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


def analyze(settings):
    """Lê o Apache e monta a configuração do OLS só em memória."""
    config = ApacheConfig(settings)
    config.parse()
    server = build(config)
    generator = Generator(server, settings, config.warnings)
    files = generator.generate()
    return server, generator, files, config.warnings


def cmd_check(settings):
    """Mostra o que seria gerado, sem escrever nada nem tocar em logs."""
    server, generator, files, warnings = analyze(settings)
    print("Apache: %s (painel: %s)" % (settings["apache_conf"], settings.get("panel")))
    print("usuário: %s:%s" % (server.user, server.group))
    print("portas: %s" % ", ".join("%s:%d" % l for l in dict.fromkeys(server.listens)))
    if server.proxy_header:
        print("IP real: %s, de %s" % (server.proxy_header, " ".join(server.proxies) or "?"))
    for vhost in server.vhosts:
        if not vhost.ols_name:
            continue
        allow = directory_settings(server, vhost, vhost.docroot)[0]
        print("- %s%s %s" % (vhost.label, " SSL" if vhost.ssl else "",
                             " ".join(vhost.aliases)))
        print("    raiz %s, .htaccess: %s" % (vhost.docroot, allow))
        for suffixes, address in vhost.php:
            name = generator.processors.get(address)
            pool = generator.pool_of.get(name)
            target = ("lsphp %s, pool %s, usuário %s" % (pool.version, pool.name, pool.user)
                      if pool else address)
            print("    PHP .%s -> %s" % (" .".join(suffixes), target))
        front = generator.front_of(vhost)
        if front is not None:
            parts = (["HTTPS forçado"] * (front.forcessl and not vhost.ssl) +
                     ["HSTS"] * bool(front.hsts and vhost.ssl) +
                     ["Let's Encrypt"] * bool(front.acme) +
                     ["%d regras de bloqueio" % len(front.deny)] * bool(front.deny) +
                     ["expires"] * front.expires)
            print("    nginx: %s" % (", ".join(parts) or "só repassa"))
    if generator.port_map:
        print("portas do nginx: %s" % ", ".join("%d -> %d" % kv for kv in
                                                 sorted(generator.port_map.items())))
    print("%d vhosts, %d processadores PHP, %d avisos" % (
        len([v for v in server.vhosts if v.ols_name]), len(generator.processors), len(warnings)))
    for line in warnings:
        print("  aviso: " + line)
    return 0


def fetch(ip, port, host, path, tls, timeout=15):
    """GET direto no backend, com SNI. Devolve (status, cabeçalhos, corpo, certificado)."""
    import http.client
    import ssl
    sock = socket.create_connection((ip, port), timeout=timeout)
    cert = None
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        sock = context.wrap_socket(sock, server_hostname=host)
        cert = hashlib.sha256(sock.getpeercert(binary_form=True) or b"").hexdigest()[:16]
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    conn.sock = sock
    try:
        conn.request("GET", path, headers={"Host": host, "User-Agent": "olsbridge-compare",
                                           "Accept-Encoding": "identity"})
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body, cert
    finally:
        conn.close()


def describe(result):
    if isinstance(result, Exception):
        return "erro: %s" % result
    status, headers, body, cert = result
    parts = [str(status)]
    if headers.get("location"):
        parts.append("-> " + headers["location"])
    parts.append(headers.get("content-type", "-").replace(" ", ""))
    if cert:
        parts.append("cert " + cert)
    return " ".join(parts)


def essential(result):
    """O que conta para o visitante: status, para onde redireciona e o certificado."""
    if isinstance(result, Exception):
        return ("erro", str(result))
    status, headers, _body, cert = result
    return (status, headers.get("location"), cert)


def cmd_compare(settings, extra_paths, offset):
    """Compara as respostas do Apache e do OLS (modo offset) para cada domínio."""
    offset = offset or int(settings.get("port_offset", 10000))
    settings = Settings(settings)
    settings["mode"] = "offset"
    server, generator, _files, _warnings = analyze(settings)
    # Com o nginx na frente, o OLS imita os dois: compara com o nginx, que é o que o
    # visitante vê hoje.
    front = generator.port_map
    paths = ["/", "/robots.txt", "/favicon.ico", "/.htaccess", "/.user.ini", "/.env",
             "/.git/config", "/.well-known/acme-challenge/olsbridge-teste",
             "/olsbridge-nao-existe-%d" % os.getpid()] + list(extra_paths)
    print("comparando %s com o OLS (+%d)" % ("o nginx" if front else "o Apache", offset))
    same = differ = body_only = type_only = 0
    for vhost in server.vhosts:
        if not vhost.ols_name or not vhost.addrs:
            continue
        ip, port = vhost.addrs[0]
        if ip in ("*", "_default_", ""):
            ip = "127.0.0.1"
        port = front.get(port, port)
        for path in paths:
            results = []
            for p in (port, port + offset):
                try:
                    results.append(fetch(ip, p, vhost.name, path, vhost.ssl))
                except Exception as err:  # noqa: BLE001 - qualquer falha vira diferença
                    results.append(err)
            a, b = (describe(r) for r in results)
            label = "%s %s://%s%s" % ("SSL" if vhost.ssl else "   ",
                                      "https" if vhost.ssl else "http", vhost.name, path)
            statuses = set(r[0] for r in results if not isinstance(r, Exception))
            if path.startswith("/.ht") and len(results) == 2 and statuses <= {403, 404} \
                    and not any(isinstance(r, Exception) for r in results):
                # O Apache nega .ht* mesmo sem o arquivo (403); o OLS nega os que
                # existem e responde 404 para os outros. Nenhum dos dois entrega nada.
                same += 1
                print("[igual]     %s: %s (.ht* negado)" % (label, " / ".join(
                    str(r[0]) for r in results)))
                continue
            if a != b and essential(results[0]) == essential(results[1]):
                # Mesmo status, destino e certificado: muda só o charset ou o tipo,
                # quase sempre numa página de erro.
                type_only += 1
                print("[tipo]      %s: %s | ols: %s" % (label, a, b.split(" ", 1)[-1]))
            elif a != b:
                differ += 1
                print("[DIFERENTE] %s\n    apache: %s\n    ols:    %s" % (label, a, b))
            elif not isinstance(results[0], Exception) and results[0][2] != results[1][2]:
                body_only += 1
                print("[corpo]     %s: %s (%d e %d bytes)" % (label, a, len(results[0][2]),
                                                               len(results[1][2])))
            else:
                same += 1
                print("[igual]     %s: %s" % (label, a))
    print("\n%d iguais, %d com corpo diferente (páginas dinâmicas mudam a cada acesso), "
          "%d só com outro Content-Type, %d diferentes" % (same, body_only, type_only, differ))
    return 1 if differ else 0


# O serviço que o painel comanda (apache2, nginx, phpX.Y-fpm) deixa de iniciar o
# programa dele: start, restart e reload só recarregam o OLS. Quem é dono do
# processo do OLS é sempre o olsbridge.service.
DROPIN = """[Unit]
Description=%(unit)s servido pelo OpenLiteSpeed (olsbridge)
Wants=olsbridge.service
After=olsbridge.service
# O painel reinicia estes serviços em sequência, e aqui cada restart leva milissegundos:
# o limite do systemd (5 em 10 s) recusaria os seguintes.
StartLimitIntervalSec=0

# A ponte não roda aqui: estes serviços têm sandbox (ProtectSystem, PrivateDevices)
# que deixariam /usr/local/lsws só leitura. Quem faz o trabalho é olsbridge-unit@.
[Service]
Type=oneshot
RemainAfterExit=yes
PIDFile=
User=
Group=
PrivateTmp=no
ProtectSystem=no
ProtectHome=no
PrivateDevices=no
RestrictAddressFamilies=
Environment=
EnvironmentFile=
ExecStartPre=
ExecStart=
ExecStart=/bin/systemctl start olsbridge-unit@%(unit)s.service
ExecStartPost=
ExecReload=
ExecReload=/bin/systemctl start olsbridge-unit@%(unit)s.service
ExecStop=
ExecStop=/bin/true
ExecStopPost=
Restart=no
"""

UNIT_TEMPLATE = """[Unit]
Description=Recarrega o OpenLiteSpeed pelo serviço %%i (olsbridge)
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=%(bridge)s unit-reload %%i
"""

OLS_UNIT = """[Unit]
Description=OpenLiteSpeed com a configuração do Apache (olsbridge, modo %(mode)s)
After=network.target

[Service]
Type=forking
PIDFile=%(pid)s
PrivateTmp=false
ExecStart=%(bridge)s start
ExecReload=%(bridge)s reload
ExecStop=%(bridge)s stop
Restart=on-failure
RestartSec=2

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


def fpm_versions(root=None):
    """Versões do PHP com PHP-FPM e pelo menos um pool."""
    root = root or PHP_ROOT
    found = []
    for path in sorted(glob.glob(os.path.join(root, "*", "fpm", "pool.d"))):
        version = path[len(root):].strip("/").split("/")[0]
        if re.match(r"^\d+\.\d+$", version) and glob.glob(os.path.join(path, "*.conf")):
            found.append(version)
    return found


def ensure_lsphp(once=False):
    """Instala o phpX.Y-litespeed de cada versão do PHP-FPM, do mesmo repositório.

    É o mesmo PHP, com as mesmas extensões (mods-available é comum aos dois). Com
    once, cada versão é tentada uma vez por dia: o timer chama isto a cada 30 s.
    """
    missing = [v for v in fpm_versions() if not os.access("/usr/bin/lsphp%s" % v, os.X_OK)]
    if not missing or not shutil.which("apt-get"):
        return missing
    todo = []
    for version in missing:
        stamp = os.path.join(LSWS, "conf", ".lsphp-%s-tentado" % version)
        if once and os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 86400:
            continue
        open(stamp, "w").close()
        todo.append(version)
    if todo:
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        code, out = run(["apt-get", "install", "-y", "-q"] +
                        ["php%s-litespeed" % v for v in todo], env=env)
        print("lsphp %s: %s" % (", ".join(todo), "instalado" if code == 0 else
                                "falhou\n" + out[-2000:]))
    return [v for v in missing if not os.access("/usr/bin/lsphp%s" % v, os.X_OK)]


def served_units(settings):
    """Serviços que o OLS assume no modo replace."""
    units = [settings["service"]]
    if settings.get("front") and settings.get("front_service"):
        units.append(settings["front_service"])
    if settings.get("php", "lsphp") == "lsphp":
        units += ["php%s-fpm" % v for v in fpm_versions()
                  if os.access("/usr/bin/lsphp%s" % v, os.X_OK)]
    return units


def dropin_path(unit):
    return os.path.join(SYSTEMD, unit + ".service.d", "zz-olsbridge.conf")


def remove_dropins(units):
    for unit in units:
        path = dropin_path(unit)
        if os.path.exists(path):
            os.remove(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass


def ols_listening(seconds=10):
    """O OLS subiu e escuta em todas as portas da configuração gerada."""
    # Os serviços antigos já pararam: quem aceitar conexão nessas portas é o OLS.
    wanted = []
    try:
        with open(os.path.join(OUT, "server.conf"), encoding="utf-8") as handle:
            for match in re.finditer(r"^\s*address\s+(\S+):(\d+)\s*$", handle.read(), re.M):
                host = match.group(1).strip("[]")
                wanted.append(("127.0.0.1" if host in ("*", "0.0.0.0") else host,
                               int(match.group(2))))
    except OSError:
        return False
    deadline = time.time() + seconds
    while time.time() < deadline:
        missing = []
        for host, port in wanted:
            try:
                socket.create_connection((host, port), timeout=1).close()
            except OSError:
                missing.append("%s:%d" % (host, port))
        if ols_pid() and not missing:
            return True
        time.sleep(0.5)
    print("sem resposta em: %s" % ", ".join(missing))
    return False


def cmd_install(settings, mode, offset):
    if not settings.get("apache_conf"):
        print("Apache não encontrado: nada a fazer.")
        return 1
    if not os.path.exists(os.path.join(LSWS, "bin", "lswsctrl")):
        print("OpenLiteSpeed não encontrado em %s." % LSWS)
        return 1
    previous = [u for u in settings.get("units", "").split(",") if u]
    settings["mode"] = mode
    if offset:
        settings["port_offset"] = str(offset)
    bridge = os.path.join(LSWS, "bin", "olsbridge")
    if os.path.realpath(sys.argv[0]) != os.path.realpath(bridge):
        shutil.copy2(os.path.realpath(sys.argv[0]), bridge)
        os.chmod(bridge, 0o755)
    values = {"bridge": bridge, "pid": PID_FILE, "mode": mode}

    # O pacote do OLS sobe o servidor sozinho no boot; quem manda agora é a ponte.
    for unit in ("lshttpd", "lsws", "openlitespeed"):
        systemctl("disable", "--now", unit)

    code, out = run([settings["apache_ctl"], "-t"])
    if code != 0:
        print("A configuração atual do Apache tem erros; corrija antes de instalar:\n" + out)
        return 1
    if settings.get("php", "lsphp") == "lsphp":
        for version in ensure_lsphp():
            print("  aviso: PHP %s sem lsphp; os sites dessa versão seguem no PHP-FPM" % version)
    units = served_units(settings) if mode == "replace" else []
    settings["units"] = ",".join(units)
    settings.save()
    changed, warnings = generate(settings)
    for line in warnings[:20]:
        print("  aviso: " + line)

    write(os.path.join(SYSTEMD, "olsbridge-sync.service"), SYNC_SERVICE % values)
    write(os.path.join(SYSTEMD, "olsbridge-sync.timer"), SYNC_TIMER)
    write(os.path.join(SYSTEMD, "olsbridge.service"), OLS_UNIT % values)
    write(os.path.join(SYSTEMD, "olsbridge-unit@.service"), UNIT_TEMPLATE % values)
    if mode == "offset":
        # Voltando do replace: os serviços do painel voltam a ser os programas deles.
        if previous:
            remove_dropins(previous)
            systemctl("daemon-reload")
            systemctl("restart", *previous)
        systemctl("daemon-reload")
        systemctl("enable", "olsbridge.service")
        code, out = systemctl("restart", "olsbridge.service")
    else:
        print("Parando %s; o OLS assume as portas deles." % ", ".join(units))
        systemctl("stop", "olsbridge.service")
        systemctl("stop", *units)
        for unit in units:
            write(dropin_path(unit), DROPIN % dict(values, unit=unit))
        systemctl("daemon-reload")
        systemctl("enable", "olsbridge.service")
        code, out = systemctl("start", "olsbridge.service")
        if code != 0 or not ols_listening():
            print("O OLS não subiu em todas as portas; voltando para %s.\n%s" %
                  (", ".join(units), out))
            systemctl("stop", "olsbridge.service")
            systemctl("disable", "olsbridge.service")
            remove_dropins(units)
            settings["units"] = ""
            settings["mode"] = "offset"
            settings.save()
            systemctl("daemon-reload")
            systemctl("start", *units)
            return 1
        # Os serviços do painel ficam ativos, como o painel espera.
        code, out = systemctl("start", *units)
    systemctl("enable", "--now", "olsbridge-sync.timer")
    print("Instalado no modo %s. Avisos em %s/warnings.txt." % (mode, OUT))
    return 0 if code == 0 else 1


def cmd_uninstall(settings):
    units = [u for u in settings.get("units", "").split(",") if u]
    # Instalações antigas só tinham o drop-in do Apache.
    service = settings.get("service", "apache2")
    if os.path.exists(dropin_path(service)) and service not in units:
        units.append(service)
    systemctl("disable", "--now", "olsbridge-sync.timer")
    systemctl("disable", "--now", "olsbridge.service")
    remove_dropins(units)
    for name in ("olsbridge.service", "olsbridge-sync.service", "olsbridge-sync.timer",
                 "olsbridge-unit@.service"):
        path = os.path.join(SYSTEMD, name)
        if os.path.exists(path):
            os.remove(path)
    systemctl("daemon-reload")
    if units:
        code, out = systemctl("restart", *units)
        print("%s %s." % (", ".join(units), "de volta" if code == 0 else "não subiram:\n" + out))
    settings["units"] = ""
    settings["mode"] = "offset"
    settings.save()
    print("olsbridge removido. A configuração gerada continua em %s." % OUT)
    return 0


def lsphp_parents(versions=None):
    """Processos pai do lsphp (os que o lscgid iniciou), por versão."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            exe = os.readlink("/proc/%s/exe" % entry)
            with open("/proc/%s/stat" % entry) as handle:
                ppid = handle.read().rsplit(")", 1)[1].split()[1]
            parent = os.readlink("/proc/%s/exe" % ppid)
        except (OSError, IndexError):
            continue
        match = re.match(r"^/usr/bin/lsphp(\d+\.\d+)$", exe)
        if match and not parent.startswith("/usr/bin/lsphp") and \
                (versions is None or match.group(1) in versions):
            found.append(int(entry))
    return found


def restart_lsphp(versions=None):
    """Reinício gracioso do PHP: o pai para de aceitar, os filhos terminam o que estão
    atendendo e saem. Na próxima requisição o OLS sobe um lsphp com o php.ini e o pool
    atuais. Sem isso, um lsphp de antes do restart do OLS segue no mesmo socket com a
    configuração antiga."""
    count = 0
    for pid in lsphp_parents(versions):
        try:
            os.kill(pid, signal.SIGUSR1)
            count += 1
        except OSError:
            pass
    return count


def cmd_unit_reload(settings, unit):
    """O que apache2, nginx e phpX.Y-fpm fazem no start, restart e reload."""
    changed, _warnings = generate(settings, quiet=True)
    pid = ols_pid()
    if not pid:
        systemctl("start", "--no-block", "olsbridge.service")
        return 0
    os.kill(pid, signal.SIGUSR1)
    match = re.match(r"^php(\d+\.\d+)-fpm$", unit)
    if match:
        # O painel reinicia o phpX.Y-fpm depois de mudar o php.ini ou um pool.
        restart_lsphp([match.group(1)])
    elif changed:
        restart_lsphp()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command")
    for name in ("generate", "start", "stop", "reload", "sync", "uninstall", "check",
                 "php-install"):
        sub.add_parser(name)
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    hestia = sub.add_parser("hestia-integrate", help="página e atalho no painel do HestiaCP")
    hestia.add_argument("--remove", action="store_true")
    unit = sub.add_parser("unit-reload")
    unit.add_argument("unit")
    restart = sub.add_parser("php-restart")
    restart.add_argument("versions", nargs="*", help="8.2 8.4...; sem nada, todas")
    install = sub.add_parser("install")
    install.add_argument("--mode", choices=("replace", "offset"), default="offset")
    install.add_argument("--offset", type=int)
    compare = sub.add_parser("compare")
    compare.add_argument("paths", nargs="*", help="caminhos extras, além dos padrões")
    compare.add_argument("--offset", type=int)
    args = parser.parse_args(argv)
    settings = Settings.load()
    if args.command not in ("status", "hestia-integrate") and not settings.get("apache_conf"):
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
        if settings.get("php", "lsphp") == "lsphp":
            ensure_lsphp(once=True)
        return cmd_reload(settings, only_if_changed=True)
    if args.command == "unit-reload":
        return cmd_unit_reload(settings, args.unit)
    if args.command == "php-restart":
        print("%d lsphp reiniciados" % restart_lsphp(args.versions or None))
        return 0
    if args.command == "php-install":
        missing = ensure_lsphp()
        print("sem lsphp: %s" % (", ".join(missing) or "nenhuma versão"))
        return 1 if missing else 0
    if args.command == "status":
        return cmd_status(settings, args.json)
    if args.command == "hestia-integrate":
        return hestia_integrate(args.remove)
    if args.command == "check":
        return cmd_check(settings)
    if args.command == "compare":
        return cmd_compare(settings, args.paths, args.offset)
    if args.command == "install":
        return cmd_install(settings, args.mode, args.offset)
    if args.command == "uninstall":
        return cmd_uninstall(settings)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
