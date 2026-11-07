"""Casos que dependem de PHP, no mesmo formato de corpus.py."""

from corpus import get

INFO = r"""<?php
header('Content-Type: text/plain');
$keys = ['APP_ENV', 'HTTP_X_REQ', 'HTTP_AUTHORIZATION', 'REQUEST_URI', 'SCRIPT_NAME', 'QUERY_STRING',
         'PATH_INFO'];
foreach ($keys as $key) {
    echo $key, '=', $_SERVER[$key] ?? '-', "\n";
}
foreach (['memory_limit', 'max_input_vars', 'display_errors'] as $key) {
    echo $key, '=', ini_get($key), "\n";
}
// O valor padrão depende de como cada PHP foi compilado.
if (strpos(ini_get('include_path'), 'compat') !== false) {
    echo ini_get('include_path'), "\n";
}
"""

FILES = {"t.php": INFO}

CASES = [
    dict(name="php-value", group="php_value",
         htaccess="php_value memory_limit 64M\nphp_value max_input_vars 1234\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-flag", group="php_value",
         htaccess="php_flag display_errors On\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-value-quoted", group="php_value",
         htaccess="php_value include_path \".:/tmp/compat\"\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-inherit", group="php_value",
         htaccess="php_value memory_limit 64M\nphp_value max_input_vars 1234\n",
         files={**FILES, "sub/t.php": INFO, "sub/.htaccess": "php_value memory_limit 96M\n"},
         requests=[get("sub/t.php")]),
    dict(name="php-ifmodule", group="php_value",
         htaccess="<IfModule mod_php.c>\nphp_value memory_limit 64M\n</IfModule>\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-setenv", group="ambiente",
         htaccess="SetEnv APP_ENV production\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-request-header", group="ambiente",
         htaccess="RequestHeader set X-Req \"v\"\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-auth-env", group="ambiente",
         htaccess="RewriteEngine On\nRewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]\n",
         files=FILES,
         requests=[("GET", "t.php", {"Authorization": "Bearer abc"})]),
    dict(name="php-front-controller", group="aplicações reais",
         htaccess=("RewriteEngine On\nRewriteBase {base}/\n"
                   "RewriteRule ^t\\.php$ - [L]\n"
                   "RewriteCond %{REQUEST_FILENAME} !-f\n"
                   "RewriteCond %{REQUEST_FILENAME} !-d\n"
                   "RewriteRule . {base}/t.php [L]\n"),
         files=FILES, requests=[get("2026/11/post/?x=1"), get("t.php")]),
    # Roundcube 1.6: favicon.ico vira static.php/skins/...; CodeIgniter e Yii usam
    # index.php/$1. O PATH_INFO vem do rewrite, não da URL pedida.
    dict(name="php-rewrite-pathinfo", group="aplicações reais",
         htaccess="RewriteEngine On\nRewriteRule ^favicon\\.ico$ t.php/skins/elastic/favicon.ico\n",
         files=FILES, requests=[get("favicon.ico"), get("t.php/a/b")]),
    dict(name="php-index-pathinfo", group="aplicações reais",
         htaccess=("RewriteEngine On\nRewriteBase {base}/\n"
                   "RewriteCond %{REQUEST_FILENAME} !-f\n"
                   "RewriteCond %{REQUEST_FILENAME} !-d\n"
                   "RewriteRule ^(.*)$ t.php/$1 [L]\n"),
         files=FILES, requests=[get("users/42/edit?x=1")]),
    dict(name="php-directoryindex", group="handlers",
         htaccess="DirectoryIndex t.php\n",
         files=FILES, requests=[get("")]),
    dict(name="php-addhandler", group="handlers",
         htaccess="AddHandler application/x-httpd-php .html\n",
         files={"p.html": "<?php echo 'run'; ?>\n"}, requests=[get("p.html")]),
    dict(name="php-addtype", group="handlers",
         htaccess="AddType application/x-httpd-php .foo\n",
         files={"p.foo": "<?php echo 'run'; ?>\n"}, requests=[get("p.foo")]),
    dict(name="php-sethandler-files", group="handlers",
         htaccess="<Files \"p.data\">\nSetHandler application/x-httpd-php\n</Files>\n",
         files={"p.data": "<?php echo 'run'; ?>\n"}, requests=[get("p.data")]),
    dict(name="php-forcetype", group="handlers",
         htaccess="<Files \"p\">\nForceType application/x-httpd-php\n</Files>\n",
         files={"p": "<?php echo 'run'; ?>\n"}, requests=[get("p")]),
    dict(name="php-removehandler", group="handlers",
         htaccess="<Files \"raw.php\">\nSetHandler default-handler\n</Files>\n",
         files={"raw.php": "<?php echo 'run'; ?>\n"}, requests=[get("raw.php")],
         status_only=True),
    dict(name="php-headers", group="mod_headers",
         htaccess="Header set X-Added \"1\"\nHeader unset X-From-App\n",
         files={"h.php": "<?php header('X-From-App: 1'); header('Content-Type: text/plain'); echo 'ok';"},
         requests=[get("h.php")]),
    dict(name="php-header-on-redirect", group="mod_headers",
         htaccess="Header set X-Added \"1\"\nHeader always set X-Always \"1\"\n",
         files={"r.php": "<?php header('Location: /x', true, 302);"},
         requests=[get("r.php")]),
    dict(name="php-error-status", group="mod_headers",
         htaccess="Header set X-Added \"1\"\nHeader always set X-Always \"1\"\n",
         files={"e.php": "<?php http_response_code(500); echo 'bad';"},
         requests=[get("e.php")]),
    dict(name="php-require", group="segurança",
         htaccess="<Files \"t.php\">\nRequire all denied\n</Files>\n",
         files=FILES, requests=[get("t.php")]),
    dict(name="php-errordocument", group="core",
         htaccess="ErrorDocument 404 {base}/t.php\n",
         files=FILES, requests=[get("missing")], compare_error_body=True, status_only=True),
]
