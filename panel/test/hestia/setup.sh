#!/bin/sh
# Monta Apache e PHP-FPM com os templates do HestiaCP (fixtures/, da versão 3c52c6c).
set -eu
IP=$(hostname -i | awk '{print $1}')
F=/test/fixtures
export IP

# Base do Apache, como o instalador do HestiaCP deixa.
cp $F/apache2.conf /etc/apache2/apache2.conf
echo "# Powered by hestia" > /etc/apache2/ports.conf
rm -f /etc/apache2/sites-enabled/*
a2dismod -q -f mpm_prefork >/dev/null 2>&1 || true
a2enmod -q mpm_event rewrite ssl actions headers proxy_fcgi setenvif remoteip >/dev/null
cat > /etc/apache2/mods-available/remoteip.conf <<EOF
<IfModule mod_remoteip.c>
  RemoteIPHeader X-Real-IP
  RemoteIPInternalProxy 127.0.0.1
  RemoteIPInternalProxy $IP
</IfModule>
EOF
a2enmod -q remoteip >/dev/null
mkdir -p /etc/apache2/conf.d/domains /var/log/apache2/domains /var/www/html /var/www/document_errors
echo unassigned > /var/www/html/index.html
echo "erro 404 geral" > /var/www/document_errors/404.html

# Certificados: uma CA intermediária, para conferir que a cadeia é enviada.
mkdir -p /test/ca /usr/local/hestia/ssl
cd /test/ca
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=Teste Raiz" \
    -keyout root.key -out root.crt 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=Teste Intermediaria" \
    -keyout int.key -out int.csr 2>/dev/null
printf 'basicConstraints=critical,CA:true\nkeyUsage=keyCertSign,cRLSign\n' > int.ext
openssl x509 -req -in int.csr -CA root.crt -CAkey root.key -CAcreateserial -days 30 \
    -extfile int.ext -out int.crt 2>/dev/null
. /test/leaf.sh
leaf "$IP" /usr/local/hestia/ssl/certificate

# IP do servidor: Listen e vhosts "unassigned", como em v-add-sys-ip.
{
    echo "Listen $IP:8443"
    echo "Listen $IP:8080"
    sed -e "s/directIP/$IP/g" -e "s/directPORT/8080/g" -e "s/directSSLPORT/8443/g" $F/unassigned.conf
} > /etc/apache2/conf.d/$IP.conf

# Domínios: usuário, domínio, forçar HTTPS.
python3 - <<'EOF'
import os, shutil, subprocess
ip = os.environ["IP"]
F = "/test/fixtures"
domains = [("alice", "alpha.test", True), ("alice", "beta.test", False), ("bob", "gamma.test", False)]

def render(name, tokens):
    text = open(os.path.join(F, name)).read()
    for key, value in tokens.items():
        text = text.replace("%" + key + "%", value)
    return text

for user, domain, force in domains:
    subprocess.call(["useradd", "-m", "-d", "/home/" + user, "-s", "/bin/bash", user],
                    stderr=subprocess.DEVNULL)
    web = "/home/%s/web/%s" % (user, domain)
    conf = "/home/%s/conf/web/%s" % (user, domain)
    for d in ("public_html", "document_errors", "stats", "cgi-bin", "private"):
        os.makedirs(os.path.join(web, d), exist_ok=True)
    os.makedirs(conf + "/ssl", exist_ok=True)
    os.makedirs("/home/%s/tmp" % user, exist_ok=True)
    ssl = conf + "/ssl/" + domain
    subprocess.check_call(["sh", "-c", ". /test/leaf.sh; leaf %s %s" % (domain, ssl)])
    shutil.copy("/test/ca/int.crt", ssl + ".ca")
    tokens = {
        "ip": ip, "web_port": "8080", "web_ssl_port": "8443", "domain_idn": domain,
        "alias_string": "ServerAlias www." + domain, "email": "info@" + domain,
        "docroot": web + "/public_html", "sdocroot": web + "/public_html", "home": "/home",
        "user": user, "group": user, "domain": domain, "web_system": "apache2",
        "backend_lsnr": "unix:/run/php/php8.3-fpm-%s.sock" % domain,
        "ssl_crt": ssl + ".crt", "ssl_key": ssl + ".key", "ssl_ca_str": "", "ssl_ca": ssl + ".ca",
        "backend": domain, "backend_version": "8.3",
    }
    open(conf + "/apache2.conf", "w").write(render("web.tpl", tokens))
    open(conf + "/apache2.ssl.conf", "w").write(render("web.stpl", tokens))
    if force:
        open(conf + "/apache2.forcessl.conf", "w").write(
            "RewriteEngine On\nRewriteRule ^(.*)$ https://%{HTTP_HOST}$1 [R=301,L]\n")
    for suffix in ("", ".ssl"):
        link = "/etc/apache2/conf.d/domains/%s%s.conf" % (domain, suffix)
        if not os.path.lexists(link):
            os.symlink(conf + "/apache2%s.conf" % suffix, link)
    open("/etc/php/8.3/fpm/pool.d/%s.conf" % domain, "w").write(render("fpm.tpl", tokens))
    for src in os.listdir("/test/site"):
        dst = os.path.join(web, "public_html", src)
        if os.path.isdir(os.path.join("/test/site", src)):
            shutil.copytree(os.path.join("/test/site", src), dst, dirs_exist_ok=True)
        else:
            shutil.copy(os.path.join("/test/site", src), dst)
    open(os.path.join(web, "public_html", "who.txt"), "w").write(domain + "\n")
    for code in ("403", "404", "50x"):
        open(os.path.join(web, "document_errors", code + ".html"), "w").write(
            "%s de %s\n" % (code, domain))
    open(os.path.join(web, "stats", "index.html"), "w").write("stats de %s\n" % domain)
    subprocess.check_call(["chown", "-R", "%s:%s" % (user, user), "/home/" + user])
    subprocess.check_call(["chown", "-R", "root:root", "/home/%s/conf" % user])
    subprocess.check_call(["chmod", "-R", "a+rX", web, "/home/" + user])
EOF

rm -f /etc/php/8.3/fpm/pool.d/www.conf
apache2ctl -t
if [ -d /run/systemd/system ]; then
    # Com systemd, quem instala a ponte é o próprio teste.
    systemctl restart php8.3-fpm
    systemctl restart apache2
    exit 0
fi
mkdir -p /run/php
php-fpm8.3 -D
apache2ctl start

cat > /usr/local/lsws/conf/olsbridge.conf <<EOF
mode=offset
port_offset=10000
EOF
/usr/local/lsws/bin/olsbridge start
