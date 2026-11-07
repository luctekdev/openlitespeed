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
    # LSCache: só guarda o que a aplicação pede (o plugin LiteSpeed Cache do WordPress,
    # por exemplo), então ligado não muda nenhum site que não o use.
    "cache": "on",
    # HTTP/3 precisa de UDP 443 aberto no firewall; o painel abre ao ligar.
    "http3": "off",
    # PNG e JPEG em WebP para quem aceita: uma cópia imagem.png.webp ao lado de cada
    # imagem, feita com o usuário dono do site (olsbridge webp, timer de 10 minutos).
    "webp": "on",
    # off, standard ou strict: limite por IP, reCAPTCHA sob ataque e login do WordPress.
    "protection": "standard",
    # off, detect (só registra) ou block: ModSecurity com as regras OWASP CRS.
    "waf": "off",
}

# Níveis de proteção. Conexões simultâneas por IP: acima de soft por grace segundos,
# ou acima de hard, o IP é bloqueado por ban segundos. Requisições dinâmicas por
# segundo por IP passam a esperar acima de dyn (0: sem limite). O reCAPTCHA aparece
# para todos, menos buscadores, quando o servidor passa de captcha conexões. O login do
# WordPress bloqueia o IP depois de wp tentativas em wp_window segundos, por wp_block.
PROTECTION = {
    "off": None,
    "standard": {"soft": 300, "hard": 600, "grace": 15, "ban": 600, "dyn": 0,
                 "captcha": 8000, "wp": 10, "wp_window": 300, "wp_block": 900},
    "strict": {"soft": 100, "hard": 200, "grace": 10, "ban": 1800, "dyn": 30,
               "captcha": 2500, "wp": 5, "wp_window": 300, "wp_block": 3600},
}
BOT_WHITELIST = ["Googlebot", "bingbot", "DuckDuckBot", "YandexBot", "Baiduspider",
                 "Applebot", "facebookexternalhit", "Twitterbot", "LinkedInBot",
                 "Slackbot", "WhatsApp", "TelegramBot", "UptimeRobot", "Pingdom"]

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
                "php_max_conns", "panel", "php", "front", "front_service", "port_map", "units",
                "cache", "http3", "protection", "waf"]
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
        # O mesmo número que o OLS usaria sozinho, fixo para os limites por processo.
        self.workers = max(1, min(os.cpu_count() or 1, 8))
        self.pools = read_pools() if settings.get("php", "lsphp") == "lsphp" else {}
        self.kinds = {}          # processador -> fcgi ou lsapi
        self.pool_of = {}        # processador lsapi -> pool do PHP-FPM
        self.extra_files = {}    # php/<processador>/pool.ini, acme/<vhost>/index.php
        self.webp = settings.get("webp", DEFAULTS["webp"]) == "on"

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
        # Filhos que ficam vivos e aceitam conexões direto do socket, como os do PHP-FPM.
        # Sem isto, com uma conexão por requisição, cada requisição criava um processo.
        env.append("LSAPI_AVOID_FORK=200M")
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
                "    procHardLimit         1200",
                # O OLS rodaria o PHP com nice 1; o PHP-FPM roda com 0.
                "    priority              0"]
        if user:
            out += ["    extUser               %s" % user, "    extGroup              %s" % group]
        return out + ["}", ""]

    def waf_mode(self):
        mode = self.settings.get("waf", "off")
        if mode == "off":
            return None
        if not (os.path.exists(os.path.join(LSWS, "modules", "mod_security.so")) and
                os.path.isdir(waf_root("crs"))):
            self.warnings.append("WAF %s pedido, mas o mod_security ou as regras faltam: "
                                 "olsbridge set waf %s" % (mode, mode))
            return None
        waf_write(mode)
        return mode

    def protection_conf(self):
        level = PROTECTION.get(self.settings.get("protection", "standard"))
        if not level:
            return []
        # Cada processo do OLS conta as conexões de um IP só para si (não há memória
        # compartilhada), e o kernel espalha as conexões de um IP entre os processos.
        # Os limites do nível são do IP inteiro: cada processo fica com a sua parte.
        share = lambda n: max(1, -(-n // self.workers)) if n else 0
        return ["perClientConnLimit {",
                "    staticReqPerSec       0",
                "    dynReqPerSec          %d" % share(level["dyn"]),
                "    outBandwidth          0",
                "    inBandwidth           0",
                "    softLimit             %d" % share(level["soft"]),
                "    hardLimit             %d" % share(level["hard"]),
                "    gracePeriod           %d" % level["grace"],
                "    banPeriod             %d" % level["ban"],
                "}", "",
                # Sob ataque (conexões no servidor acima do limite), o visitante passa
                # por um reCAPTCHA; buscadores e monitores passam direto.
                "lsrecaptcha {",
                "    enabled               1",
                "    type                  1",
                "    maxTries              3",
                "    regConnLimit          %d" % share(level["captcha"]),
                "    sslConnLimit          %d" % share(level["captcha"]),
                "    allowedRobotHits      3",
                "    botWhiteList          <<<END_bots",
                ] + BOT_WHITELIST + ["END_bots", "}", ""]

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
               "httpdWorkers              %d" % self.workers,
               "autoRestart               1",
               "gracefulRestartTimeout    300",
               "mime                      conf/mime.properties",
               "showVersionNumber         0",
               "indexFiles                %s" % ", ".join(s.index or ["index.html"]),
               "disableWebAdmin           1",  # o painel não usa o WebAdmin do OLS
               "",
               "errorlog logs/error.log {",
               # NOTICE: é onde o OLS registra os IPs que bloqueia (o fail2ban lê daqui).
               "    logLevel              NOTICE",
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
               # Compressão: Brotli e gzip pré-comprimidos para estáticos, gzip no dinâmico.
               "    enableGzipCompress    1",
               "    enableDynGzipCompress 1",
               "    enableBrCompress      4",
               "    gzipCompressLevel     6",
               "    gzipStaticCompressLevel 6",
               "    brStaticCompressLevel 6",
               "    gzipAutoUpdateStatic  1",
               "    gzipMinFileSize       300",
               "    gzipMaxFileSize       10M",
               "    compressibleTypes     default",
               # Arquivos pequenos ficam em memória, os médios mapeados.
               "    maxCachedFileSize     4096",
               "    totalInMemCacheSize   20M",
               "    maxMMapFileSize       256K",
               "    totalMMapCacheSize    40M",
               "    useSendfile           1",
               "    sslSessionCache       1",
               "    sslSessionCacheSize   1000000",
               "    sslSessionCacheTimeout 3600",
               "    sslSessionTickets     1",
               "    quicEnable            %d" % (1 if self.settings.get("http3") == "on" else 0),
               "    quicShmDir            /dev/shm/ols",
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
        # Com o OLS no lugar do nginx não há proxy na frente: os IPs que o Apache
        # aceitava (o nginx local) não devem ser tratados como proxy.
        if s.proxy_header and not self.front:
            if s.proxy_header.lower() not in ("x-forwarded-for", "x-real-ip"):
                self.warnings.append("RemoteIPHeader %s: o OLS só usa X-Forwarded-For" %
                                     s.proxy_header)
            out.append("useIpInProxyHeader        2")
            allow += ["%sT" % p for p in s.proxies if re.match(r"^[0-9a-fA-F.:/]+$", p)]
        out += ["accessControl {", "    allow                 %s" % ", ".join(allow),
                "    deny", "}", ""]
        out += self.protection_conf()
        if self.waf_mode():
            out += ["module mod_security {", "    ls_enabled            1",
                    "    modsecurity           on",
                    "    modsecurity_rules_file %s/main.conf" % MODSEC_DIR, "}", ""]
            self.log_files.append(MODSEC_AUDIT)
        if self.settings.get("cache", "on") == "on":
            # Só guarda o que a aplicação pede por cabeçalho (enableCache 0).
            out += ["module cache {", "    ls_enabled            1",
                    "    checkPrivateCache     1", "    checkPublicCache      1",
                    "    maxCacheObjSize       10000000", "    maxStaleAge           200",
                    "    qsCache               1", "    reqCookieCache        1",
                    "    respCookieCache       1", "    ignoreReqCacheCtrl    1",
                    "    ignoreRespCacheCtrl   0", "    enableCache           0",
                    "    expireInSeconds       3600", "    enablePrivateCache    0",
                    "    privateExpireInSeconds 3600",
                    "    storagePath           %s/cachedata" % LSWS, "}", ""]

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
                out += ["    sslSessionCache       1", "    sslSessionTickets     1",
                        "    enableQuic            %d" % (1 if self.settings.get("http3") == "on"
                                                         else 0)]
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
        rules = front_rules + (WEBP_RULES if self.webp else []) + (
            vhost.rewrite if vhost.rewrite_on else [])
        if rules:
            out.append("    rules                 <<<END_rules")
            out += rules
            out.append("    END_rules")
        out += ["}", ""]
        level = PROTECTION.get(self.settings.get("protection", "standard"))
        if level:
            # Login do WordPress: wp-login.php e xmlrpc.php, por IP.
            out += ["wpProtectLimit            %d" % level["wp"],
                    "wpProtectWindow           %d" % level["wp_window"],
                    "wpProtectBlock            %d" % level["wp_block"], ""]
        if self.settings.get("cache", "on") == "on":
            out += ["module cache {", "    ls_enabled            1",
                    # Uma pasta por domínio: HTTP e HTTPS limpam o mesmo cache.
                    "    storagePath           %s/cachedata/%s" % (LSWS, vhost.name), "}", ""]
        front = self.front_of(vhost)
        if front is not None and front.expires:
            # nginx: `expires max` nos arquivos estáticos que ele mesmo serve. O expires do
            # OLS vale também para o que o PHP gera, então ficam de fora os tipos que o
            # PHP responde: HTML, JSON (wp-json), XML (feeds, sitemaps) e texto (robots.txt).
            types = ["image/*", "font/*", "audio/*", "video/*", "text/css", "text/javascript",
                     "application/javascript", "application/x-javascript", "application/pdf",
                     "application/zip", "application/wasm", "application/vnd.ms-fontobject",
                     "application/font-woff", "application/font-woff2", "application/x-font-ttf"]
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
        headers = [re.sub(r"(?i)^header\s+(always\s+)?", "", line) for line in vhost.headers]
        if self.webp:
            # A mesma URL sai em PNG ou em WebP conforme o Accept: caches e CDNs precisam saber.
            headers.append("append Vary Accept")
        if headers or self.charset:
            out += ["context / {", "    location              %s/" % vhost.docroot.rstrip("/"),
                    "    allowBrowse           1"]
            if self.charset and re.match(r"^[A-Za-z0-9_-]+$", self.charset):
                # nginx: `charset utf-8` no texto que sai sem charset.
                out += ["    addDefaultCharset     on",
                        "    defaultCharsetCustomized %s" % self.charset]
            if headers:
                out.append("    extraHeaders          <<<END_headers")
                out += headers
                out.append("    END_headers")
            out += ["}", ""]
        if vhost.env:
            self.warnings.append("%s: SetEnv não convertido" % vhost.label)
        if vhost.ssl:
            pair = self.ssl_pair(vhost)
            if pair:
                out += ["vhssl {", "    keyFile               %s" % pair[0],
                        "    certFile              %s" % pair[1],
                        "    certChain             %d" % (1 if pair[2] else 0),
                        # O navegador não precisa consultar a CA a cada visita.
                        "    enableStapling        1",
                        "    ocspRespMaxAge        86400",
                        "    sslSessionCache       1",
                        "    sslSessionTickets     1", "}", ""]
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
    prepare_cache(server, settings)
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


def prepare_cache(server, settings):
    """O LSCache grava com o usuário dos processos do OLS; o pacote deixa a pasta com nobody."""
    if settings.get("cache", "on") != "on" or os.geteuid() != 0:
        return
    try:
        import grp
        import pwd
        uid = pwd.getpwnam(server.user).pw_uid
        gid = grp.getgrnam(server.group).gr_gid
    except (ImportError, KeyError):
        return
    path = os.path.join(LSWS, "cachedata")
    os.makedirs(path, exist_ok=True)
    st = os.stat(path)
    if (st.st_uid, st.st_gid) != (uid, gid):
        os.chown(path, uid, gid)
    os.chmod(path, 0o750)


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


def bridge_lock():
    """Uma geração e um restart por vez: o painel reinicia apache2, nginx e phpX.Y-fpm
    juntos, e o timer do sync roda a cada 30 s. Fechar o arquivo devolve a trava."""
    import fcntl
    handle = open(os.path.join(LSWS, "conf", ".olsbridge.lock"), "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def graceful_restart(pid):
    """Restart gracioso: o OLS sobe um processo principal novo, que grava o PID, e o
    antigo sai depois de terminar as conexões. Espera o novo assumir: um segundo sinal
    antes disso faz o systemd ver o processo principal sair sem PID novo no arquivo e
    reiniciar o serviço inteiro, com os sites fora por alguns segundos."""
    try:
        os.kill(pid, signal.SIGUSR1)
    except OSError:
        return False
    for _ in range(100):
        time.sleep(0.1)
        new = ols_pid()
        if new and new != pid:
            return True
    return False


def cmd_reload(settings, only_if_changed=False):
    with bridge_lock():
        changed, _warnings = generate(settings, quiet=only_if_changed)
        pid = ols_pid()
        if not pid:
            return cmd_start(settings)
        if changed or not only_if_changed:
            graceful_restart(pid)  # novas conexões já usam a config nova
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
    options = dict((k, settings.get(k, DEFAULTS.get(k, "off"))) for k in OPTIONS)
    options["waf_available"] = os.path.exists(os.path.join(LSWS, "modules", "mod_security.so"))
    webp = webp_state()
    if webp.get("files"):
        options["webp_stats"] = "%d images in WebP, %s saved (last pass %s)" % (
            webp["files"], human_bytes(webp.get("saved", 0)),
            time.strftime("%Y-%m-%d %H:%M", time.localtime(webp.get("at", 0))))
    return {"mode": mode, "label": label, "running": bool(pid), "pid": pid or 0,
            "options": options,
            "version": version, "units": units, "front": settings.get("front", ""),
            "port_offset": settings.get("port_offset"), "php": php, "warnings": warnings}


# WAF: ModSecurity v3 com o OWASP CRS e as exclusões do WordPress. Versões fixadas com
# o SHA-256 do arquivo publicado; outra versão é outro par de valores aqui.
MODSEC_DIR = os.path.join(LSWS, "conf", "modsec")
MODSEC_AUDIT = os.path.join(LSWS, "logs", "modsec_audit.log")
WAF_SOURCES = {
    "crs": ("4.30.0", "https://github.com/coreruleset/coreruleset/releases/download/v4.30.0/"
            "coreruleset-4.30.0-minimal.tar.gz",
            "3d678a41fd5aade34760127fef5dd64fd7a77848913fc0f70dde0cf467c94427"),
    "wordpress": ("1.2.0", "https://github.com/coreruleset/wordpress-rule-exclusions-plugin/"
                  "archive/refs/tags/v1.2.0.tar.gz",
                  "8a1fc80dcf6030b13f856dcaa72dcac898b4375fe5a41689ed96d6e895929439"),
}

MODSEC_BASE = """# Gerado por olsbridge. ModSecurity v3, com o OWASP CRS.
SecRuleEngine %(engine)s
SecRequestBodyAccess On
SecRequestBodyLimit 134217728
SecRequestBodyNoFilesLimit 1048576
SecRequestBodyLimitAction ProcessPartial
SecRequestBodyJsonDepthLimit 512
SecArgumentsLimit 2000
SecResponseBodyAccess Off
SecPcreMatchLimit 100000
SecPcreMatchLimitRecursion 100000
SecTmpDir /tmp/
SecDataDir /tmp/
SecArgumentSeparator &
SecCookieFormat 0
SecStatusEngine Off
# Uma linha JSON por transação com regra disparada: o painel conta, o fail2ban bane.
SecAuditEngine RelevantOnly
SecAuditLogRelevantStatus "^(?:5|4(?!04))"
SecAuditLogParts ABHZ
SecAuditLogFormat JSON
SecAuditLogType Serial
SecAuditLog %(audit)s
"""


def waf_root(name):
    version = WAF_SOURCES[name][0]
    return os.path.join(MODSEC_DIR, "%s-%s" % (name, version))


def fetch_verified(url, sha256, dest_dir):
    """Baixa, confere o SHA-256 e extrai um .tar.gz sem deixar sair da pasta."""
    import tarfile
    import tempfile
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "olsbridge"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    got = hashlib.sha256(data).hexdigest()
    if got != sha256:
        raise RuntimeError("SHA-256 diferente para %s: %s" % (url, got))
    tmp = tempfile.mkdtemp(dir=MODSEC_DIR)
    try:
        path = os.path.join(tmp, "a.tar.gz")
        with open(path, "wb") as handle:
            handle.write(data)
        with tarfile.open(path) as tar:
            members = []
            for member in tar.getmembers():
                target = os.path.realpath(os.path.join(tmp, "x", member.name))
                if not target.startswith(os.path.realpath(os.path.join(tmp, "x")) + os.sep) or \
                        member.issym() or member.islnk() or member.isdev():
                    continue
                members.append(member)
            tar.extractall(os.path.join(tmp, "x"), members=members)
        tops = os.listdir(os.path.join(tmp, "x"))
        if len(tops) != 1:
            raise RuntimeError("arquivo inesperado: %s" % url)
        os.rename(os.path.join(tmp, "x", tops[0]), dest_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def waf_install():
    """Biblioteca do ModSecurity (apt) e as regras, uma vez por versão."""
    so = os.path.join(LSWS, "modules", "mod_security.so")
    if not os.path.exists(so):
        raise RuntimeError("este OpenLiteSpeed não tem o mod_security")
    code, out = run(["ldd", so])
    if "not found" in out:
        if not shutil.which("apt-get"):
            raise RuntimeError("falta a libmodsecurity: " + out)
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        for package in ("libmodsecurity3t64", "libmodsecurity3"):
            code, out = run(APT_INSTALL + [package], env=env)
            if code == 0:
                break
        code, out = run(["ldd", so])
        if "not found" in out:
            raise RuntimeError("a libmodsecurity não ficou disponível:\n" + out)
    os.makedirs(MODSEC_DIR, exist_ok=True)
    os.chmod(MODSEC_DIR, 0o755)
    for name, (version, url, sha) in WAF_SOURCES.items():
        root = waf_root(name)
        if not os.path.isdir(root):
            fetch_verified(url, sha, root)
    setup = os.path.join(waf_root("crs"), "crs-setup.conf")
    if not os.path.exists(setup):
        shutil.copyfile(setup + ".example", setup)


def waf_write(mode):
    """Configuração do ModSecurity para o modo: detect só registra, block bloqueia."""
    crs, wpx = waf_root("crs"), waf_root("wordpress")
    write(os.path.join(MODSEC_DIR, "modsecurity.conf"), MODSEC_BASE % {
        "engine": "On" if mode == "block" else "DetectionOnly", "audit": MODSEC_AUDIT})
    # O ModSecurity v3 recusa um Include com curinga que não acha nada: cada arquivo
    # entra pelo nome, na ordem que o CRS pede.
    def each(pattern):
        return ["Include %s" % p for p in sorted(glob.glob(pattern))]

    # O CRS só aceita GET, HEAD, POST e OPTIONS (regra 911100): APIs REST (Laravel,
    # WordPress, apps) usam PUT, PATCH e DELETE. Vem antes do REQUEST-901, que só põe o
    # padrão se a variável estiver vazia.
    write(os.path.join(MODSEC_DIR, "olsbridge-setup.conf"),
          "# Gerado por olsbridge.\n"
          "SecAction \"id:1009200,phase:1,pass,t:none,nolog,"
          "setvar:'tx.allowed_methods=GET HEAD POST OPTIONS PUT PATCH DELETE'\"\n")
    lines = ["# Gerado por olsbridge.", "Include %s/modsecurity.conf" % MODSEC_DIR,
             "Include %s/crs-setup.conf" % crs,
             "Include %s/olsbridge-setup.conf" % MODSEC_DIR]
    for stage in ("config", "before"):
        lines += each("%s/plugins/*-%s.conf" % (wpx, stage))
        lines += each("%s/plugins/*-%s.conf" % (crs, stage))
    lines += each("%s/rules/*.conf" % crs)
    lines += each("%s/plugins/*-after.conf" % wpx) + each("%s/plugins/*-after.conf" % crs)
    write(os.path.join(MODSEC_DIR, "main.conf"), "\n".join(lines) + "\n")


# Outro apt (o timer do WebP, uma atualização automática) pode estar com a trava do
# dpkg: espera em vez de falhar.
APT_INSTALL = ["apt-get", "-o", "DPkg::Lock::Timeout=300", "install", "-y", "-q"]

WEBP_RULES = [
    # Navegador que aceita WebP e cópia pronta ao lado da imagem. A cópia fica na mesma
    # pasta, então valem para ela as mesmas regras de acesso (.htaccess, senha).
    "RewriteCond %{HTTP_ACCEPT} image/webp",
    "RewriteCond %{DOCUMENT_ROOT}%{REQUEST_URI} -f",
    "RewriteCond %{DOCUMENT_ROOT}%{REQUEST_URI}.webp -f",
    "RewriteRule ^(.+\\.(?:png|jpe?g))$ $1.webp [NC,L]",
]
WEBP_STATE = os.path.join(LSWS, "conf", "webp-state.json")
WEBP_SERVICE = """[Unit]
Description=Cópias WebP das imagens dos sites (olsbridge)

[Service]
Type=oneshot
ExecStart=%(bridge)s webp
Nice=15
IOSchedulingClass=idle
"""
WEBP_TIMER = """[Unit]
Description=Cópias WebP das imagens novas a cada 10 minutos

[Timer]
OnBootSec=5min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
"""
# Tudo o que grava na pasta do site roda com o usuário dono da imagem: converte num
# arquivo temporário e só fica com o WebP se ele for ao menos 10% menor.
WEBP_SCRIPT = (
    '"$5" -quiet -mt -m 4 -q "$4" -alpha_q 100 -sharp_yuv -metadata none "$1" -o "$3" '
    '2>/dev/null || { rm -f "$3"; exit 2; }; '
    's=$(stat -c %s "$3"); if [ "$s" -lt "$2" ]; then mv -f "$3" "$1.webp"; '
    'else rm -f "$3"; exit 3; fi')


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (n, unit)
        n /= 1024.0


def webp_state():
    import json
    try:
        with open(WEBP_STATE, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def ensure_cwebp():
    tool = shutil.which("cwebp")
    if tool or not shutil.which("apt-get"):
        return tool
    stamp = os.path.join(LSWS, "conf", ".webp-tentado")
    if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < 86400:
        return None
    open(stamp, "w").close()
    run(APT_INSTALL + ["webp"],
        env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    return shutil.which("cwebp")


def webp_docroots(settings):
    config = ApacheConfig(settings)
    config.parse()
    server = build(config)
    # Pastas de root (o padrão do Apache, o Roundcube) ficam de fora em cmd_webp.
    return sorted(set(v.docroot.rstrip("/") for v in server.vhosts
                      if v.docroot and os.path.isdir(v.docroot)))


def as_user(uid, gid):
    def demote():
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
        os.umask(0o022)
    return demote


def cmd_webp(settings, budget=300):
    """Cria imagem.png.webp ao lado de cada PNG/JPEG dos sites, com o dono da imagem.

    Percorre as pastas dos sites inteiras (sem seguir links), converte o que é novo ou
    mudou até o limite de tempo e deixa o resto para a próxima vez. Imagens de root,
    links simbólicos e cópias que não ficam menores ficam de fora.
    """
    import json
    import stat as st_mod
    if settings.get("webp", DEFAULTS["webp"]) != "on":
        return 0
    if os.geteuid() != 0:
        print("rode como root")
        return 1
    tool = ensure_cwebp()
    if not tool:
        print("cwebp não encontrado (pacote webp)")
        return 1
    state = webp_state()
    old_skip = state.get("skip", {})
    skip = {}
    start = time.time()
    files = saved = converted = pending = failed = 0
    for root in webp_docroots(settings):
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules"]
            for name in names:
                if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue
                src = os.path.join(base, name)
                try:
                    info = os.lstat(src)
                except OSError:
                    continue
                if not st_mod.S_ISREG(info.st_mode) or info.st_uid == 0 or \
                        not 1024 <= info.st_size <= 25 << 20:
                    continue
                try:
                    copy = os.lstat(src + ".webp")
                except OSError:
                    copy = None
                if copy is not None:
                    if not st_mod.S_ISREG(copy.st_mode):
                        continue
                    if copy.st_mtime >= info.st_mtime:
                        files += 1
                        saved += max(0, info.st_size - copy.st_size)
                        continue
                key = [int(info.st_mtime), info.st_size]
                if old_skip.get(src) == key:
                    skip[src] = key
                    continue
                if time.time() - start > budget:
                    pending += 1
                    continue
                quality = "85" if name.lower().endswith(".png") else "82"
                tmp = "%s.webp.olsb%d" % (src, os.getpid())
                try:
                    proc = subprocess.run(
                        ["sh", "-c", WEBP_SCRIPT, "sh", src, str(info.st_size * 9 // 10), tmp,
                         quality, tool], preexec_fn=as_user(info.st_uid, info.st_gid),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
                    code = proc.returncode
                except (subprocess.TimeoutExpired, OSError):
                    code = 2
                if code == 0:
                    converted += 1
                    files += 1
                    try:
                        saved += max(0, info.st_size - os.lstat(src + ".webp").st_size)
                    except OSError:
                        pass
                else:
                    failed += code == 2
                    skip[src] = key
    state = {"at": int(time.time()), "files": files, "saved": saved, "pending": pending,
             "skip": skip}
    tmp = WEBP_STATE + ".new"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.rename(tmp, WEBP_STATE)
    print("WebP: %d novas, %d no total, %s economizados, %d para a próxima vez, %d com erro" %
          (converted, files, human_bytes(saved), pending, failed))
    return 0


def ensure_webp_timer(settings):
    """Liga ou desliga o timer do WebP conforme a opção; barato o bastante para o sync."""
    service = os.path.join(SYSTEMD, "olsbridge-webp.service")
    timer = os.path.join(SYSTEMD, "olsbridge-webp.timer")
    want = settings.get("webp", DEFAULTS["webp"]) == "on" and \
        os.path.exists(os.path.join(SYSTEMD, "olsbridge.service"))
    if want and not os.path.exists(timer):
        values = {"bridge": os.path.join(LSWS, "bin", "olsbridge")}
        write(service, WEBP_SERVICE % values)
        write(timer, WEBP_TIMER)
        systemctl("daemon-reload")
        systemctl("enable", "--now", "olsbridge-webp.timer")
        systemctl("start", "--no-block", "olsbridge-webp.service")
    elif not want and os.path.exists(timer):
        systemctl("disable", "--now", "olsbridge-webp.timer")
        for path in (service, timer):
            os.remove(path)
        systemctl("daemon-reload")


OPTIONS = {
    "cache": ("on", "off"),
    "http3": ("on", "off"),
    "webp": ("on", "off"),
    "protection": ("off", "standard", "strict"),
    "waf": ("off", "detect", "block"),
}


def hestia_http3_rule(enable):
    """HTTP/3 é UDP 443: abre ou fecha no firewall do HestiaCP, com a regra visível no painel."""
    tool = os.path.join(HESTIA, "bin")
    if not os.path.exists(os.path.join(tool, "v-list-firewall")):
        return None
    import json
    code, out = run([os.path.join(tool, "v-list-firewall"), "json"])
    try:
        rules = json.loads(out) if code == 0 else {}
    except ValueError:
        rules = {}
    mine = [k for k, v in rules.items() if v.get("COMMENT") == "HTTP3"]
    if enable and not mine:
        return run([os.path.join(tool, "v-add-firewall-rule"), "ACCEPT", "0.0.0.0/0", "443",
                    "UDP", "HTTP3"])
    if not enable:
        for rule in mine:
            run([os.path.join(tool, "v-delete-firewall-rule"), rule])
    return (0, "")


F2B_FILTERS = {
    "olsbridge-limit": r"""[Definition]
# OpenLiteSpeed fechou conexões de um IP acima do limite por cliente (olsbridge).
failregex = \[NOTICE\] \[\d+\] \[<HOST>\] [Rr]eached per client (?:soft|hard) connection limit
ignoreregex =
""",
    "olsbridge-wp": r"""[Definition]
# Login do WordPress bloqueado pelo OpenLiteSpeed: tentativas demais (olsbridge).
# O OLS registra o cliente como [IP:porta:H2-1#vhost].
failregex = \[NOTICE\] \[\d+\] \[<HOST>:[^\]]*\] \[WP\] Too many login attempts
ignoreregex =
""",
    "olsbridge-waf": r"""[Definition]
# Requisições bloqueadas pelo WAF (ModSecurity, modo block): uma linha JSON por transação.
failregex = "client_ip":"<HOST>".*"http_code":403
datepattern = {NONE}
ignoreregex =
""",
    "olsbridge-scan": r"""[Definition]
# Pedidos de arquivos que só um varredor procura, respondidos com 403 ou 404 (olsbridge).
# O fail2ban tira a data da linha antes de aplicar o padrão.
failregex = ^<HOST> \S+ \S+ [^"]*"(?:GET|POST|HEAD|PUT|PROPFIND) [^"]*(?:/\.env|/\.git/|/\.aws/|/\.ssh/|/\.svn/|/\.hg/|/\.DS_Store|/\.vscode/|/\.idea/|/wp-config\.php\.(?:bak|old|orig|save|swp|txt|1)|/wp-config\.bak|/phpinfo\.php|/vendor/phpunit/|/actuator/|/backup\.(?:sql|zip|tar\.gz)|/dump\.sql|/database\.sql|/config\.php\.bak|/\.htpasswd)[^"]* HTTP/[0-9.]+" (?:403|404)
ignoreregex =
""",
}

F2B_JAIL = """# Gerado por olsbridge: bloqueios do OpenLiteSpeed escalam para o firewall do HestiaCP.
[olsbridge-limit]
enabled  = true
filter   = olsbridge-limit
action   = hestia[name=WEB]
logpath  = %(lsws)s/logs/error.log
maxretry = 3
findtime = 600
bantime  = 3600

[olsbridge-wp]
enabled  = true
filter   = olsbridge-wp
action   = hestia[name=WEB]
logpath  = %(lsws)s/logs/error.log
maxretry = 2
findtime = 3600
bantime  = 3600

%(waf)s[olsbridge-scan]
enabled  = true
filter   = olsbridge-scan
action   = hestia[name=WEB]
logpath  = %(logs)s
maxretry = 3
findtime = 600
bantime  = 3600
"""


def apply_fail2ban(settings):
    """Liga os jails do olsbridge quando há proteção, fail2ban e a ação do HestiaCP."""
    base = "/etc/fail2ban"
    jail = os.path.join(base, "jail.d", "olsbridge.conf")
    if not os.path.exists(os.path.join(base, "action.d", "hestia.conf")) or \
            not shutil.which("fail2ban-client"):
        return False
    want = settings.get("protection", "standard") != "off" and \
        os.path.exists(os.path.join(SYSTEMD, "olsbridge.service"))
    logs = "/var/log/apache2/domains/*.log" if os.path.isdir("/var/log/apache2/domains") \
        else "/var/log/httpd/domains/*.log"
    if want:
        for name, body in F2B_FILTERS.items():
            write(os.path.join(base, "filter.d", name + ".conf"), body)
        waf = ""
        if settings.get("waf") == "block":
            waf = ("[olsbridge-waf]\nenabled  = true\nfilter   = olsbridge-waf\n"
                   "action   = hestia[name=WEB]\nlogpath  = %s\nmaxretry = 5\n"
                   "findtime = 600\nbantime  = 3600\n\n" % MODSEC_AUDIT)
        text = F2B_JAIL % {"lsws": LSWS, "logs": logs, "waf": waf}
        old = None
        if os.path.exists(jail):
            with open(jail, encoding="utf-8") as handle:
                old = handle.read()
        if old == text:
            return True
        write(jail, text)
    elif os.path.exists(jail):
        os.remove(jail)
        for name in F2B_FILTERS:
            path = os.path.join(base, "filter.d", name + ".conf")
            if os.path.exists(path):
                os.remove(path)
    else:
        return False
    run(["fail2ban-client", "reload"])
    return True


def read_rtreport(pattern="/tmp/lshttpd/.rtreport*"):
    """Soma os relatórios de cada processo do OLS; HTTP e HTTPS de um domínio juntos."""
    total = {"uptime": "", "conns": 0, "ssl_conns": 0, "idle_conns": 0, "bps_in": 0,
             "bps_out": 0, "req_per_sec": 0.0, "req_processing": 0, "total_reqs": 0,
             "cache_hits_per_sec": 0.0, "total_cache_hits": 0, "static_per_sec": 0.0}
    sites, apps, blocked = {}, {}, set()

    def numbers(text):
        found = {}
        for key, value in re.findall(r"([A-Z_]+): ([0-9.]+)", text):
            found[key] = float(value)
        return found

    for path in sorted(glob.glob(pattern)):
        if not re.search(r"\.rtreport(\.\d+)?$", path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for line in lines:
            if line.startswith("UPTIME:") and not total["uptime"]:
                total["uptime"] = line.split(":", 1)[1].strip()
            elif line.startswith("BPS_IN"):
                n = numbers(line)
                total["bps_in"] += n.get("BPS_IN", 0) + n.get("SSL_BPS_IN", 0)
                total["bps_out"] += n.get("BPS_OUT", 0) + n.get("SSL_BPS_OUT", 0)
            elif line.startswith("MAXCONN"):
                n = numbers(line)
                total["conns"] += int(n.get("PLAINCONN", 0))
                total["ssl_conns"] += int(n.get("SSLCONN", 0))
                total["idle_conns"] += int(n.get("IDLECONN", 0))
            elif line.startswith("REQ_RATE ["):
                name = line[len("REQ_RATE ["):line.index("]")]
                n = numbers(line[line.index("]"):])
                if not name:
                    total["req_per_sec"] += n.get("REQ_PER_SEC", 0)
                    total["req_processing"] += int(n.get("REQ_PROCESSING", 0))
                    total["total_reqs"] += int(n.get("TOT_REQS", 0))
                    total["cache_hits_per_sec"] += n.get("PUB_CACHE_HITS_PER_SEC", 0) + \
                        n.get("PRIVATE_CACHE_HITS_PER_SEC", 0)
                    total["total_cache_hits"] += int(n.get("TOTAL_PUB_CACHE_HITS", 0) +
                                                     n.get("TOTAL_PRIVATE_CACHE_HITS", 0))
                    total["static_per_sec"] += n.get("STATIC_HITS_PER_SEC", 0)
                    continue
                domain = re.sub(r"-ssl$", "", name)
                site = sites.setdefault(domain, {"domain": domain, "req_per_sec": 0.0,
                                                 "total_reqs": 0, "cache_hits": 0,
                                                 "processing": 0})
                site["req_per_sec"] += n.get("REQ_PER_SEC", 0)
                site["total_reqs"] += int(n.get("TOT_REQS", 0))
                site["processing"] += int(n.get("REQ_PROCESSING", 0))
                site["cache_hits"] += int(n.get("TOTAL_PUB_CACHE_HITS", 0) +
                                          n.get("TOTAL_PRIVATE_CACHE_HITS", 0))
            elif line.startswith("EXTAPP ["):
                parts = re.findall(r"\[([^\]]*)\]", line.split(":", 1)[0])
                name = parts[-1] if parts else "?"
                n = numbers(line.split(":", 1)[1])
                app = apps.setdefault(name, {"name": name, "in_use": 0, "idle": 0,
                                             "waiting": 0, "req_per_sec": 0.0, "total_reqs": 0})
                app["in_use"] += int(n.get("INUSE_CONN", 0))
                app["idle"] += int(n.get("IDLE_CONN", 0))
                app["waiting"] += int(n.get("WAITQUE_DEPTH", 0))
                app["req_per_sec"] += n.get("REQ_PER_SEC", 0)
                app["total_reqs"] += int(n.get("TOT_REQS", 0))
            elif line.startswith("BLOCKED_IP:"):
                blocked.update(ip.strip() for ip in line.split(":", 1)[1].split(",")
                               if ip.strip())
    total["cache_ratio"] = round(100.0 * total["total_cache_hits"] / total["total_reqs"], 1) \
        if total["total_reqs"] else 0.0
    for value in list(total):
        if isinstance(total[value], float):
            total[value] = round(total[value], 1)
    for site in sites.values():
        site["req_per_sec"] = round(site["req_per_sec"], 1)
    return total, sorted(sites.values(), key=lambda s: -s["total_reqs"]), \
        sorted(apps.values(), key=lambda a: a["name"]), sorted(blocked)


def waf_events(hours=24, limit=4 * 1024 * 1024):
    """Ataques no log de auditoria do WAF: total recente, regras e IPs mais frequentes."""
    import json
    events, rules, ips = 0, {}, {}
    try:
        with open(MODSEC_AUDIT, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            data = handle.read().decode("utf-8", "replace")
    except OSError:
        return {"events": 0, "blocked": 0, "rules": [], "ips": []}
    cutoff = time.time() - hours * 3600
    blocked = 0
    for line in data.splitlines():
        if not line.startswith("{"):
            continue
        try:
            tx = json.loads(line).get("transaction", {})
        except ValueError:
            continue
        stamp = tx.get("time_stamp", "")
        try:
            when = time.mktime(time.strptime(stamp, "%a %b %d %H:%M:%S %Y"))
        except ValueError:
            when = time.time()
        if when < cutoff:
            continue
        events += 1
        if tx.get("response", {}).get("http_code") == 403:
            blocked += 1
        ip = tx.get("client_ip", "?")
        ips[ip] = ips.get(ip, 0) + 1
        for message in tx.get("messages", []):
            rule = message.get("details", {}).get("ruleId", "")
            if rule and not rule.startswith("949") and not rule.startswith("980"):
                key = "%s %s" % (rule, message.get("message", "")[:60])
                rules[key] = rules.get(key, 0) + 1
    top = lambda d: [{"name": k, "count": v} for k, v in
                     sorted(d.items(), key=lambda kv: -kv[1])[:8]]
    return {"events": events, "blocked": blocked, "rules": top(rules), "ips": top(ips)}


def firewall_bans():
    """IPs banidos no firewall do HestiaCP (fail2ban e manuais), com a cadeia."""
    import json
    tool = os.path.join(HESTIA, "bin", "v-list-firewall-ban")
    if not os.path.exists(tool):
        return []
    code, out = run([tool, "json"])
    try:
        data = json.loads(out) if code == 0 else {}
    except ValueError:
        return []
    bans = [{"ip": ip, "chain": v.get("CHAIN", ""), "date": "%s %s" % (v.get("DATE", ""),
                                                                    v.get("TIME", ""))}
            for ip, v in (data.items() if isinstance(data, dict) else [])]
    return sorted(bans, key=lambda b: b["date"], reverse=True)


def cmd_stats(as_json=True):
    import json
    total, sites, apps, blocked = read_rtreport()
    stats = {"total": total, "sites": sites, "php": apps, "ols_blocked": blocked,
             "firewall_bans": firewall_bans(), "waf": waf_events(), "time": int(time.time())}
    print(json.dumps(stats, indent=1))
    return 0


def cmd_set(settings, key, value):
    if key not in OPTIONS or value not in OPTIONS[key]:
        print("opções: %s" % "; ".join("%s=%s" % (k, "|".join(v)) for k, v in OPTIONS.items()))
        return 2
    if key == "waf" and value != "off" and not os.path.exists(
            os.path.join(LSWS, "modules", "mod_security.so")):
        print("WAF indisponível: este OpenLiteSpeed não tem o mod_security.")
        return 1
    if key == "waf" and value != "off":
        try:
            waf_install()
        except (RuntimeError, OSError) as err:
            print("WAF: %s" % err)
            return 1
    if key == "http3":
        result = hestia_http3_rule(value == "on")
        if result and result[0] != 0:
            print("não foi possível abrir UDP 443 no firewall:\n" + result[1])
            return 1
    settings[key] = value
    settings.save()
    if key in ("protection", "waf"):
        apply_fail2ban(settings)
    if key == "webp":
        ensure_webp_timer(settings)
    if ols_pid():
        return cmd_reload(settings)
    generate(settings)
    return 0


HESTIA = "/usr/local/hestia"
HESTIA_ADDON = os.path.join(LSWS, "add-ons", "olsbridge-hestia")
HESTIA_HOOK = "/etc/hestiacp/hooks/post_install.sh"
HESTIA_FILES = [
    "bin/v-list-sys-litespeed",
    "bin/v-change-sys-web-litespeed",
    "bin/v-restart-sys-litespeed",
    "bin/v-restart-sys-litespeed-php",
    "bin/v-change-sys-litespeed-option",
    "bin/v-list-sys-litespeed-stats",
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
# Atalho na barra do topo, ao lado da engrenagem, só para o admin.
HESTIA_TOPBAR = """<!-- olsbridge:begin -->
\t\t\t\t\t\t\t<?php if ($_SESSION["userContext"] === "admin" && empty($_SESSION["look"]) && file_exists(dirname(__DIR__, 2) . "/edit/server/litespeed/index.php")) { ?>
\t\t\t\t\t\t\t\t<li class="top-bar-menu-item">
\t\t\t\t\t\t\t\t\t<a title="OpenLiteSpeed" class="top-bar-menu-link <?php if (strpos($_SERVER["REQUEST_URI"], "/edit/server/litespeed/") === 0) echo "active"; ?>" href="/edit/server/litespeed/">
\t\t\t\t\t\t\t\t\t\t<i class="fas fa-bolt"></i>
\t\t\t\t\t\t\t\t\t\t<span class="top-bar-menu-link-label u-hide-desktop">OpenLiteSpeed</span>
\t\t\t\t\t\t\t\t\t</a>
\t\t\t\t\t\t\t\t</li>
\t\t\t\t\t\t\t<?php } ?>
\t\t\t\t\t\t\t<!-- olsbridge:end -->
\t\t\t\t\t\t\t"""
HESTIA_SERVICES_BUTTON = """<!-- olsbridge:begin -->
\t\t\t<a href="/edit/server/litespeed/" class="button button-secondary">
\t\t\t\t<i class="fas fa-bolt icon-orange"></i>OpenLiteSpeed
\t\t\t</a>
\t\t\t<!-- olsbridge:end -->
\t\t\t"""
# Na lista: apache2 e nginx aparecem desativados (atendidos pelo OLS), e os phpX.Y-fpm
# com o nome do lsphp. As ações continuam nos nomes reais dos serviços.
HESTIA_SERVICES_ROW = """// olsbridge:begin
\t\t\t\t$olsb_name = null;
\t\t\t\tif (preg_match('/^php(\\d+\\.\\d+)-fpm$/', $key, $olsb_m) && strpos($data[$key]["SYSTEM"], "lsphp") !== false) {
\t\t\t\t\t$olsb_name = "lsphp" . $olsb_m[1];
\t\t\t\t}
\t\t\t\tif (strpos($data[$key]["SYSTEM"], "served by OpenLiteSpeed") !== false) {
\t\t\t\t\t$status = 'suspended';
\t\t\t\t\t$state_icon = 'fa-circle-right icon-blue';
\t\t\t\t}
\t\t\t\t// olsbridge:end
\t\t\t\t"""
HESTIA_SERVICES_NAME = ("\t\t\t\t\t\t<?= tohtml($key) ?>\n\t\t\t\t\t</a>",
                        "\t\t\t\t\t\t<?= tohtml($olsb_name ?? $key) /* olsbridge */ ?>\n"
                        "\t\t\t\t\t</a>")
HESTIA_SERVICES_DATA = """# olsbridge:begin
# OpenLiteSpeed no lugar de apache2, nginx e PHP-FPM (olsbridge hestia-services).
if [ -x %s/bin/olsbridge ]; then
\tolsb_data=$(echo -e "$data" | %s/bin/olsbridge hestia-services 2>/dev/null) && [ -n "$olsb_data" ] && data="$olsb_data"
fi
# olsbridge:end
""" % (LSWS, LSWS)
# (arquivo, âncora, bloco inserido antes da âncora)
HESTIA_PATCHES = [
    ("web/templates/pages/edit_server.php", HESTIA_ANCHOR, HESTIA_ROW),
    ("web/templates/includes/panel.php", "<!-- Server Settings -->", HESTIA_TOPBAR),
    ("web/templates/pages/list_services.php", '<a href="/list/rrd/"', HESTIA_SERVICES_BUTTON),
    ("web/templates/pages/list_services.php", "$cpu = $data[$key]['CPU'] / 10;",
     HESTIA_SERVICES_ROW),
    ("bin/v-list-sys-services", "# Listing data\n", HESTIA_SERVICES_DATA),
]
MARKERS = (("<!-- olsbridge:begin -->", "<!-- olsbridge:end -->"),
           ("// olsbridge:begin", "// olsbridge:end"),
           ("# olsbridge:begin", "# olsbridge:end"))
HOOK_BLOCK = """# olsbridge:begin
# Depois de cada atualização do HestiaCP: atalhos, a linha na tela Web Server e a lista
# de serviços com o OpenLiteSpeed.
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


def strip_patches(text):
    for begin, end in MARKERS:
        while True:
            stripped = strip_block(text, begin, end)
            if stripped == text:
                break
            text = stripped
    return text.replace(HESTIA_SERVICES_NAME[1], HESTIA_SERVICES_NAME[0])


def patch_hestia_files(remove):
    """Aplica (ou tira) os trechos nos arquivos do pacote do HestiaCP."""
    files = {}
    for name, _anchor, _block in HESTIA_PATCHES:
        if name in files:
            continue
        path = os.path.join(HESTIA, name)
        try:
            with open(path, encoding="utf-8") as handle:
                files[name] = strip_patches(handle.read())
        except OSError as err:
            print("aviso: não foi possível ler %s: %s" % (path, err.strerror))
    if not remove:
        for name, anchor, block in HESTIA_PATCHES:
            if name not in files:
                continue
            if anchor not in files[name]:
                print("aviso: %s mudou nesta versão do HestiaCP; trecho do OpenLiteSpeed "
                      "não aplicado ali (a página continua em /edit/server/litespeed/)" % name)
                continue
            files[name] = files[name].replace(anchor, block + anchor, 1)
        name = "web/templates/pages/list_services.php"
        if name in files and HESTIA_SERVICES_NAME[0] in files[name]:
            files[name] = files[name].replace(*HESTIA_SERVICES_NAME, 1)
    for name, text in files.items():
        write_keep(os.path.join(HESTIA, name), text)


def ensure_ols_alias(settings):
    """Instalações de antes do Alias=litespeed.service ganham o nome novo."""
    unit = os.path.join(SYSTEMD, "olsbridge.service")
    try:
        with open(unit, encoding="utf-8") as handle:
            current = handle.read()
    except OSError:
        return
    if "Alias=litespeed.service" in current:
        return
    values = {"bridge": os.path.join(LSWS, "bin", "olsbridge"), "pid": PID_FILE,
              "mode": settings.get("mode", "offset")}
    write(unit, OLS_UNIT % values)
    systemctl("daemon-reload")
    systemctl("enable", "olsbridge.service")


def hestia_integrate(remove=False, settings=None):
    """Página do OpenLiteSpeed no HestiaCP, os comandos v-* e os atalhos no painel.

    Os arquivos novos não são do pacote do HestiaCP e sobrevivem às atualizações; os
    trechos nos arquivos do pacote (linha na tela Web Server, ícone na barra do topo,
    botão e lista de serviços) são reaplicados pelo gancho post_install.sh que o pacote
    roda depois de cada atualização.
    """
    if not os.path.isdir(HESTIA):
        print("HestiaCP não encontrado em %s." % HESTIA)
        return 1
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
        if not hook.strip():
            hook = "#!/bin/sh\n"
        hook = hook.rstrip("\n") + "\n" + HOOK_BLOCK
        if settings is not None:
            ensure_ols_alias(settings)
    patch_hestia_files(remove)
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


def process_table():
    """pid, ppid, %cpu, memória (KB, como o HestiaCP soma), segundos de vida e nome."""
    code, out = run(["ps", "-eo", "pid=,ppid=,pcpu=,size=,etimes=,comm="])
    table = []
    for line in out.splitlines() if code == 0 else []:
        parts = line.split(None, 5)
        if len(parts) == 6:
            try:
                table.append((int(parts[0]), int(parts[1]), float(parts[2]), int(parts[3]),
                              int(parts[4]), parts[5].strip()))
            except ValueError:
                pass
    return table


def service_row(name, system, state, procs, rtime=None):
    cpu = sum(p[2] for p in procs)
    mem = sum(p[3] for p in procs) // 1024
    if procs:
        rtime = max(p[4] for p in procs) // 60
    return "NAME='%s' SYSTEM='%s' STATE='%s' CPU='%s' MEM='%d' RTIME='%d'" % (
        name, system, state, ("%.1f" % cpu).rstrip("0").rstrip("."), mem, rtime or 0)


def hestia_services(settings, lines):
    """A lista de v-list-sys-services com o OpenLiteSpeed: uma linha dele e, no modo
    replace, apache2, nginx e phpX.Y-fpm como atendidos por ele (o lsphp de cada
    versão sobe sob demanda, com os números dos processos dele)."""
    procs = process_table()
    pid = ols_pid()
    ols = [p for p in procs if p[5] == "litespeed"]
    if pid:
        main = [p for p in ols if p[0] == pid]
        ols = main + [p for p in ols if p[0] != pid]
    state = "running" if pid else "stopped"
    uptime = ols[0][4] // 60 if ols else 0
    replace = settings.get("mode") == "replace"
    units = [u for u in settings.get("units", "").split(",") if u] if replace else []
    out = []
    for line in lines:
        fields = dict(re.findall(r"(\w+)='([^']*)'", line))
        name = fields.get("NAME", "")
        if not name or name == "litespeed":
            if line.strip() and not name:
                out.append(line)
            continue
        if name not in units:
            out.append(line)
            continue
        match = re.match(r"^php(\d+\.\d+)-fpm$", name)
        if match:
            version = match.group(1)
            mine = [p for p in procs if p[5] == "lsphp" + version]
            # Sem requisição recente o lsphp não tem processo: vale o tempo do OLS.
            out.append(service_row(name, "php interpreter · lsphp%s (LiteSpeed)" % version,
                                   state, mine, uptime))
        else:
            out.append(service_row(name, "%s · disabled, served by OpenLiteSpeed" %
                                   fields.get("SYSTEM", "web server"), state, [], uptime))
    if replace:
        names = [u for u in units if not u.startswith("php")]
        if any(u.startswith("php") for u in units):
            names.append("PHP-FPM")
        label = "web server · OpenLiteSpeed" + (
            " (in place of %s)" % " + ".join(names) if names else "")
    else:
        label = "web server · OpenLiteSpeed (port offset %s)" % settings.get("port_offset", "")
    out.insert(0, service_row("litespeed", label, state, ols))
    return out


def cmd_hestia_services(settings):
    lines = sys.stdin.read().splitlines()
    try:
        if os.path.exists(os.path.join(SYSTEMD, "olsbridge.service")):
            lines = hestia_services(settings, lines)
    except Exception:  # a lista do painel nunca pode quebrar por causa da ponte
        pass
    print("\n".join(lines))
    return 0


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
# O nome que o HestiaCP mostra na lista de serviços e usa para reiniciar e parar.
Alias=litespeed.service
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
    mask_lsphp_daemons()
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
        code, out = run(APT_INSTALL +
                        ["php%s-litespeed" % v for v in todo], env=env)
        print("lsphp %s: %s" % (", ".join(todo), "instalado" if code == 0 else
                                "falhou\n" + out[-2000:]))
    missing = [v for v in missing if not os.access("/usr/bin/lsphp%s" % v, os.X_OK)]
    if todo:
        mask_lsphp_daemons()
    return missing


def mask_lsphp_daemons():
    """O phpX.Y-litespeed traz um serviço que sobe o lsphp como root, ouvindo em
    /run/php/lsphpX.Y.sock, no boot. Quem roda o PHP aqui é o OLS, com o usuário de cada
    pool; esse daemon só abriria um PHP de root no socket. Fica mascarado, o que também
    vale para as atualizações do pacote."""
    masked = []
    names = set()
    for root in ("/lib/systemd/system", "/usr/lib/systemd/system"):
        names.update(os.path.basename(p) for p in
                     glob.glob(os.path.join(root, "php*-litespeed.service")))
    for name in sorted(names):
        link = os.path.join(SYSTEMD, name)
        if os.path.islink(link) and os.readlink(link) == "/dev/null":
            continue
        systemctl("disable", "--now", name)
        systemctl("mask", name)
        masked.append(name)
    return masked


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
        # Voltando do replace: os serviços do painel voltam a ser os programas deles. O
        # OLS sai das portas antes: o nginx e o Apache não sobem com elas ocupadas.
        systemctl("daemon-reload")
        systemctl("enable", "olsbridge.service")
        if previous:
            systemctl("stop", "olsbridge.service")
            remove_dropins(previous)
            systemctl("daemon-reload")
            systemctl("stop", *previous)
            code, out = systemctl("start", *previous)
            if code != 0:
                print("aviso: %s não subiram:\n%s" % (", ".join(previous), out))
            code, out = systemctl("start", "olsbridge.service")
        else:
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
    ensure_webp_timer(settings)
    apply_fail2ban(settings)
    print("Instalado no modo %s. Avisos em %s/warnings.txt." % (mode, OUT))
    return 0 if code == 0 else 1


def cmd_uninstall(settings):
    units = [u for u in settings.get("units", "").split(",") if u]
    # Instalações antigas só tinham o drop-in do Apache.
    service = settings.get("service", "apache2")
    if os.path.exists(dropin_path(service)) and service not in units:
        units.append(service)
    systemctl("disable", "--now", "olsbridge-sync.timer")
    systemctl("disable", "--now", "olsbridge-webp.timer")
    systemctl("disable", "--now", "olsbridge.service")
    remove_dropins(units)
    for name in ("olsbridge.service", "olsbridge-sync.service", "olsbridge-sync.timer",
                 "olsbridge-webp.service", "olsbridge-webp.timer",
                 "olsbridge-unit@.service"):
        path = os.path.join(SYSTEMD, name)
        if os.path.exists(path):
            os.remove(path)
    systemctl("daemon-reload")
    apply_fail2ban(settings)
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
    """O que apache2, nginx e phpX.Y-fpm fazem no start, restart e reload.

    O OLS só reinicia se a configuração gerada mudou: na instalação e no boot as
    unidades sobem juntas, logo depois do OLS, que já está com a configuração atual."""
    with bridge_lock():
        changed, _warnings = generate(settings, quiet=True)
        pid = ols_pid()
        if not pid:
            systemctl("start", "--no-block", "olsbridge.service")
            return 0
        if changed:
            graceful_restart(pid)
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
    sub.add_parser("stats", help="tempo real, bloqueios e WAF, em JSON")
    webp = sub.add_parser("webp", help="cópias WebP das imagens dos sites")
    webp.add_argument("--budget", type=int, default=300, help="segundos de conversão")
    setter = sub.add_parser("set", help="cache, http3, protection, waf")
    setter.add_argument("key")
    setter.add_argument("value")
    hestia = sub.add_parser("hestia-integrate", help="página e atalho no painel do HestiaCP")
    hestia.add_argument("--remove", action="store_true")
    sub.add_parser("hestia-services", help="filtro da lista de serviços do HestiaCP (stdin)")
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
    if args.command not in ("status", "hestia-integrate", "stats", "hestia-services") and \
            not settings.get("apache_conf"):
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
        ensure_webp_timer(settings)
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
        return hestia_integrate(args.remove, settings)
    if args.command == "hestia-services":
        return cmd_hestia_services(settings)
    if args.command == "set":
        return cmd_set(settings, args.key, args.value)
    if args.command == "stats":
        return cmd_stats()
    if args.command == "webp":
        return cmd_webp(settings, args.budget)
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
