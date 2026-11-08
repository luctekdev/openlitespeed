#!/bin/sh
# Um domínio como o HestiaCP monta: pool do PHP-FPM com o usuário do site, Apache em
# 8080 e nginx em 80 com o template do HestiaCP.
set -eu
IP=$(hostname -i | awk '{print $1}')
D=/home/alice/web/alpha.test
mkdir -p /usr/local/hestia/conf
cat > /usr/local/hestia/conf/hestia.conf <<EOF
WEB_SYSTEM='apache2'
WEB_PORT='8080'
WEB_SSL_PORT='8443'
PROXY_SYSTEM='nginx'
PROXY_PORT='80'
PROXY_SSL_PORT='443'
EOF

id alice >/dev/null 2>&1 || useradd -m -s /bin/bash alice
mkdir -p $D/public_html $D/document_errors /home/alice/tmp /home/alice/conf/web/alpha.test
cat > $D/public_html/info.php <<'EOF'
<?php
echo php_sapi_name(), " ", PHP_VERSION, " ", posix_getpwuid(posix_geteuid())["name"], " ",
     ini_get("memory_limit"), " ", ini_get("upload_max_filesize"), " ",
     ini_get("open_basedir") ? "basedir" : "sem-basedir", "\n";
EOF
echo "upload_max_filesize = 33M" > $D/public_html/.user.ini
echo "SEGREDO=1" > $D/public_html/.env
echo "alpha" > $D/public_html/who.txt
echo "404 de alpha" > $D/document_errors/404.html
chown -R alice:alice /home/alice
chmod 711 /home/alice

rm -f /etc/php/8.4/fpm/pool.d/www.conf
cat > /etc/php/8.4/fpm/pool.d/alpha.test.conf <<EOF
[alpha.test]
listen = /run/php/php8.4-fpm-alpha.test.sock
listen.owner = alice
listen.group = www-data
listen.mode = 0660
user = alice
group = alice
pm = ondemand
pm.max_children = 4
pm.max_requests = 4000
pm.process_idle_timeout = 10s
php_admin_value[upload_tmp_dir] = /home/alice/tmp
php_admin_value[session.save_path] = /home/alice/tmp
php_admin_value[open_basedir] = $D/public_html:/home/alice/tmp:/tmp
env[TMP] = /home/alice/tmp
EOF

echo "Listen $IP:8080" > /etc/apache2/ports.conf
rm -f /etc/apache2/sites-enabled/*
a2enmod -q proxy_fcgi rewrite setenvif >/dev/null
cat > /etc/apache2/sites-enabled/alpha.test.conf <<EOF
<VirtualHost $IP:8080>
    ServerName alpha.test
    DocumentRoot $D/public_html
    Alias /error/ $D/document_errors/
    ErrorDocument 404 /error/404.html
    <Directory $D/public_html>
        AllowOverride All
        Require all granted
    </Directory>
    <Directory $D/document_errors>
        Require all granted
    </Directory>
    <FilesMatch \.php$>
        SetHandler "proxy:unix:/run/php/php8.4-fpm-alpha.test.sock|fcgi://localhost"
    </FilesMatch>
</VirtualHost>
EOF

rm -f /etc/nginx/sites-enabled/default
mkdir -p /etc/nginx/conf.d/domains /var/log/apache2/domains
# Como no nginx.conf do HestiaCP.
printf '%s
' "log_format bytes '\$body_bytes_sent';" 'include /etc/nginx/conf.d/domains/*.conf;'     > /etc/nginx/conf.d/domains.conf
sed -e "s/%ip%/$IP/g" -e "s/%proxy_port%/80/g" -e "s/%web_port%/8080/g" \
    -e "s/%domain_idn%/alpha.test/g" -e "s/%alias_idn%/www.alpha.test/g" -e "s/%domain%/alpha.test/g" \
    -e "s|%home%|/home|g" -e "s/%user%/alice/g" -e "s/%web_system%/apache2/g" \
    -e "s|%docroot%|$D/public_html|g" -e "s/%proxy_extensions%/css|js|png|txt/g" \
    /test/fixtures/nginx.tpl > /etc/nginx/conf.d/domains/alpha.test.conf
sed -i "s/^    ServerName alpha.test$/    ServerName alpha.test\n    ServerAlias www.alpha.test/" \
    /etc/apache2/sites-enabled/alpha.test.conf

# Um segundo site, só estático, para o WAF por site (waf.sh).
E=/home/bob/web/beta.test
id bob >/dev/null 2>&1 || useradd -m -s /bin/bash bob
mkdir -p $E/public_html
echo "beta" > $E/public_html/who.txt
chown -R bob:bob /home/bob
chmod 711 /home/bob
cat > /etc/apache2/sites-enabled/beta.test.conf <<EOF
<VirtualHost $IP:8080>
    ServerName beta.test
    DocumentRoot $E/public_html
    <Directory $E/public_html>
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
EOF
sed -e "s/%ip%/$IP/g" -e "s/%proxy_port%/80/g" -e "s/%web_port%/8080/g" \
    -e "s/%domain_idn%/beta.test/g" -e "s/%alias_idn%//g" -e "s/%domain%/beta.test/g" \
    -e "s|%home%|/home|g" -e "s/%user%/bob/g" -e "s/%web_system%/apache2/g" \
    -e "s|%docroot%|$E/public_html|g" -e "s/%proxy_extensions%/css|js|png|txt/g" \
    /test/fixtures/nginx.tpl > /etc/nginx/conf.d/domains/beta.test.conf

# Lista de IPs do QUIC.cloud já baixada: sem ela, o OLS a baixa na subida e espera até
# 10 s se quic.cloud não responder, e o teste dependeria da internet.
mkdir -p /usr/local/lsws/tmp && touch /usr/local/lsws/tmp/download-quic-cloud-ips

systemctl restart php8.4-fpm apache2 nginx
