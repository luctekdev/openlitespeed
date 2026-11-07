"""Casos da comparação Apache x OLS.

Cada caso é materializado em um diretório próprio de /www, com um nome novo a cada
execução, para que o OLS original leia o .htaccess sem precisar reiniciar.

Campos do caso:
  name      identificador
  group     grupo mostrado na matriz de compatibilidade
  htaccess  conteúdo do .htaccess
  files     dict caminho -> conteúdo (index.html e page.txt são criados por padrão)
  requests  lista de (método, caminho relativo ao caso, cabeçalhos extras)

{base} no .htaccess e nos arquivos vira o caminho do caso, por exemplo /c123/rewrite-basic.
"""

HTPASSWD = "alice:{SHA}qUqP5cyxm6YcTAhz05Hph5gvu9M=\n"  # senha: test
ALICE = {"Authorization": "Basic YWxpY2U6dGVzdA=="}  # alice:test
HTGROUP = "admins: alice\n"


def get(path="", **headers):
    return ("GET", path, {k.replace("_", "-"): v for k, v in headers.items()})


CASES = [
    # --- mod_rewrite: o OLS original já lê estas diretivas ---
    dict(name="rewrite-basic", group="mod_rewrite",
         htaccess="RewriteEngine On\nRewriteBase {base}/\nRewriteRule ^old$ page.txt [L]\n",
         requests=[get("old"), get("page.txt"), get("missing")]),
    dict(name="rewrite-redirect", group="mod_rewrite",
         htaccess="RewriteEngine On\nRewriteRule ^a$ {base}/b [R=301,L]\nRewriteRule ^c$ - [F]\nRewriteRule ^d$ - [G]\n",
         requests=[get("a"), get("c"), get("d")]),
    dict(name="rewrite-conditions", group="mod_rewrite",
         htaccess=("RewriteEngine On\n"
                   "RewriteCond %{HTTP_USER_AGENT} bot [NC]\n"
                   "RewriteRule ^ - [F]\n"
                   "RewriteCond %{QUERY_STRING} ^id=(\\d+)$\n"
                   "RewriteRule ^item$ {base}/page.txt?n=%1 [R=302,L]\n"),
         requests=[get("page.txt", User_Agent="GoodBot"), get("page.txt"), get("item?id=7")]),
    dict(name="rewrite-env-flag", group="mod_rewrite",
         htaccess=("RewriteEngine On\nRewriteRule ^ - [E=FOO:bar]\n"
                   "Header set X-Foo \"%{FOO}e\"\n"),
         requests=[get("page.txt")]),
    dict(name="wordpress", group="aplicações reais",
         htaccess=("# BEGIN WordPress\n<IfModule mod_rewrite.c>\nRewriteEngine On\n"
                   "RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]\n"
                   "RewriteBase {base}/\nRewriteRule ^index\\.html$ - [L]\n"
                   "RewriteCond %{REQUEST_FILENAME} !-f\n"
                   "RewriteCond %{REQUEST_FILENAME} !-d\n"
                   "RewriteRule . {base}/index.html [L]\n</IfModule>\n# END WordPress\n"),
         requests=[get(""), get("page.txt"), get("2026/11/post/"), get("wp-json/x")]),
    dict(name="laravel", group="aplicações reais",
         htaccess=("<IfModule mod_rewrite.c>\n<IfModule mod_negotiation.c>\n"
                   "Options -MultiViews -Indexes\n</IfModule>\nRewriteEngine On\n"
                   "RewriteCond %{HTTP:Authorization} .\n"
                   "RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]\n"
                   "RewriteCond %{REQUEST_FILENAME} !-d\n"
                   "RewriteCond %{REQUEST_URI} (.+)/$\n"
                   "RewriteRule ^ %1 [L,R=301]\n"
                   "RewriteCond %{REQUEST_FILENAME} !-d\n"
                   "RewriteCond %{REQUEST_FILENAME} !-f\n"
                   "RewriteRule ^ index.html [L]\n</IfModule>\n"),
         requests=[get(""), get("page.txt"), get("users/5"), get("users/5/")]),

    # --- mod_headers ---
    dict(name="header-set", group="mod_headers",
         htaccess=("Header set X-Frame-Options \"SAMEORIGIN\"\n"
                   "Header always set X-Always \"1\"\n"
                   "Header append X-List \"a\"\nHeader append X-List \"b\"\n"
                   "Header add X-Multi \"1\"\nHeader add X-Multi \"2\"\n"),
         requests=[get("page.txt"), get("missing")]),
    dict(name="header-ifmodule", group="mod_headers",
         htaccess="<IfModule mod_headers.c>\nHeader set X-In-Block \"yes\"\n</IfModule>\n",
         requests=[get("page.txt")]),
    dict(name="header-unset-merge", group="mod_headers",
         htaccess=("Header set X-A \"1\"\nHeader unset X-A\n"
                   "Header merge X-M \"a\"\nHeader merge X-M \"a\"\nHeader merge X-M \"b\"\n"),
         requests=[get("page.txt")]),
    dict(name="header-env-condition", group="mod_headers",
         htaccess=("SetEnvIf User-Agent \"curl\" IS_CURL\n"
                   "Header set X-Curl \"yes\" env=IS_CURL\n"),
         requests=[get("page.txt", User_Agent="curl/8"), get("page.txt", User_Agent="firefox")]),

    # --- mod_expires ---
    dict(name="expires-bytype", group="mod_expires",
         htaccess=("ExpiresActive On\nExpiresByType text/plain \"access plus 1 hour\"\n"
                   "ExpiresDefault \"access plus 10 minutes\"\n"),
         requests=[get("page.txt"), get("")]),

    # --- mod_alias ---
    dict(name="redirect", group="mod_alias",
         htaccess="Redirect 301 {base}/old {base}/new\nRedirect gone {base}/gone\n",
         requests=[get("old"), get("old/sub"), get("gone")]),
    dict(name="redirectmatch", group="mod_alias",
         htaccess="RedirectMatch 302 ^{base}/doc/(.*)\\.html$ {base}/v2/$1\n",
         requests=[get("doc/a.html"), get("doc/a.txt")]),

    # --- core ---
    dict(name="errordocument", group="core",
         htaccess="ErrorDocument 404 {base}/custom404.html\n",
         files={"custom404.html": "custom 404\n"},
         requests=[get("missing")], compare_error_body=True),
    dict(name="errordocument-text", group="core",
         htaccess="ErrorDocument 403 \"negado\"\nRewriteEngine On\nRewriteRule ^x$ - [F]\n",
         requests=[get("x")], compare_error_body=True),
    dict(name="files-block", group="core",
         htaccess="<Files \"page.txt\">\nHeader set X-File \"yes\"\n</Files>\n",
         files={"other.txt": "other\n"},
         requests=[get("page.txt"), get("other.txt")]),
    dict(name="filesmatch-block", group="core",
         htaccess="<FilesMatch \"\\.(txt|md)$\">\nHeader set X-Match \"yes\"\n</FilesMatch>\n",
         requests=[get("page.txt"), get("")]),
    dict(name="filesmatch-deny", group="core",
         htaccess="<FilesMatch \"^secret\">\nRequire all denied\n</FilesMatch>\n",
         files={"secret.txt": "secret\n"},
         requests=[get("secret.txt"), get("page.txt")]),
    dict(name="if-expression", group="core",
         htaccess="<If \"%{HTTP_USER_AGENT} =~ /bot/\">\nHeader set X-Bot \"yes\"\n</If>\n",
         requests=[get("page.txt", User_Agent="a-bot"), get("page.txt", User_Agent="human")]),
    dict(name="forcetype", group="core",
         htaccess="<Files \"data\">\nForceType text/csv\n</Files>\n",
         files={"data": "a,b\n"},
         requests=[get("data")]),
    dict(name="adddefaultcharset", group="core",
         htaccess="AddDefaultCharset UTF-8\n",
         requests=[get("page.txt")]),
    dict(name="options-noindexes", group="core",
         htaccess="Options -Indexes\n",
         files={"sub/file.txt": "x\n"},
         requests=[get("sub/")]),

    # --- mod_mime / mod_dir / mod_autoindex ---
    dict(name="addtype", group="mod_mime",
         htaccess="AddType application/x-custom .foo\n",
         files={"a.foo": "foo\n"},
         requests=[get("a.foo")]),
    dict(name="directoryindex", group="mod_dir",
         htaccess="DirectoryIndex home.html\n",
         files={"home.html": "home\n"},
         requests=[get("")]),
    dict(name="options-indexes", group="mod_autoindex",
         htaccess="Options +Indexes\nDirectoryIndex none.html\n",
         requests=[get("")], status_only=True),

    # --- mod_env / mod_setenvif ---
    dict(name="setenv", group="mod_env",
         htaccess="SetEnv APP_ENV production\nHeader set X-Env \"%{APP_ENV}e\"\n",
         requests=[get("page.txt")]),
    dict(name="browsermatch", group="mod_setenvif",
         htaccess=("BrowserMatch \"MSIE\" OLD_IE\nHeader set X-Old \"yes\" env=OLD_IE\n"),
         requests=[get("page.txt", User_Agent="MSIE 6"), get("page.txt", User_Agent="Chrome")]),

    # --- controle de acesso ---
    dict(name="require-all-denied", group="mod_authz",
         htaccess="Require all denied\n",
         requests=[get("page.txt")]),
    dict(name="require-ip", group="mod_authz",
         htaccess="Require ip 10.255.255.1\n",
         requests=[get("page.txt")]),
    dict(name="require-not", group="mod_authz",
         htaccess="<RequireAll>\nRequire all granted\nRequire not ip 10.255.255.1\n</RequireAll>\n",
         requests=[get("page.txt")]),
    dict(name="order-deny", group="mod_access_compat",
         htaccess="Order Deny,Allow\nDeny from all\n",
         requests=[get("page.txt")]),
    dict(name="order-allow", group="mod_access_compat",
         htaccess="Order Allow,Deny\nAllow from all\nDeny from 10.255.255.1\n",
         requests=[get("page.txt")]),
    dict(name="limit-except", group="mod_access_compat",
         htaccess="<LimitExcept GET>\nRequire all denied\n</LimitExcept>\n",
         requests=[get("page.txt"), ("POST", "page.txt", {})]),
    dict(name="basic-auth", group="mod_auth_basic",
         htaccess=("AuthType Basic\nAuthName \"Area\"\nAuthUserFile /www{base}/.htpasswd\n"
                   "Require valid-user\n"),
         files={".htpasswd": HTPASSWD},
         requests=[get("page.txt"), ("GET", "page.txt", ALICE)]),
    dict(name="basic-auth-group", group="mod_auth_basic",
         htaccess=("AuthType Basic\nAuthName \"Area\"\nAuthUserFile /www{base}/.htpasswd\n"
                   "AuthGroupFile /www{base}/.htgroup\nRequire group admins\n"),
         files={".htpasswd": HTPASSWD, ".htgroup": HTGROUP},
         requests=[get("page.txt"), ("GET", "page.txt", ALICE)]),
    dict(name="require-mixed", group="mod_authz",
         htaccess=("AuthType Basic\nAuthName \"Area\"\nAuthUserFile /www{base}/.htpasswd\n"
                   "<RequireAny>\nRequire ip 10.255.255.1\nRequire valid-user\n</RequireAny>\n"),
         files={".htpasswd": HTPASSWD},
         requests=[get("page.txt"), ("GET", "page.txt", ALICE)]),
    dict(name="require-none", group="mod_authz",
         htaccess=("<RequireAll>\nRequire all granted\n<RequireNone>\n"
                   "Require method POST\n</RequireNone>\n</RequireAll>\n"),
         requests=[get("page.txt"), ("POST", "page.txt", {})]),
    dict(name="require-user-wrong", group="mod_auth_basic",
         htaccess=("AuthType Basic\nAuthName \"Area\"\nAuthUserFile /www{base}/.htpasswd\n"
                   "Require user bob\n"),
         files={".htpasswd": HTPASSWD},
         requests=[("GET", "page.txt", ALICE)]),
    dict(name="auth-wrong-password", group="mod_auth_basic",
         htaccess=("AuthType Basic\nAuthName \"Area\"\nAuthUserFile /www{base}/.htpasswd\n"
                   "Require valid-user\n"),
         files={".htpasswd": HTPASSWD},
         requests=[("GET", "page.txt", {"Authorization": "Basic YWxpY2U6ZXJyYWRh"})]),
    dict(name="satisfy-any", group="mod_access_compat",
         htaccess=("Order Deny,Allow\nDeny from all\nAllow from 172.16.0.0/12\n"
                   "AuthType Basic\nAuthName \"Area\"\nAuthUserFile /www{base}/.htpasswd\n"
                   "Require valid-user\nSatisfy Any\n"),
         files={".htpasswd": HTPASSWD},
         requests=[get("page.txt")]),
    dict(name="rewrite-cannot-bypass-deny", group="segurança",
         htaccess="Require all denied\nRewriteEngine On\nRewriteRule ^x$ ../rewrite-basic/page.txt [L]\n",
         requests=[get("x")]),
    dict(name="child-inherits-deny", group="segurança",
         htaccess="Require all denied\n",
         files={"sub/file.txt": "x\n"},
         requests=[get("sub/file.txt")]),
    dict(name="child-overrides-deny", group="segurança",
         htaccess="Require all denied\n",
         files={"sub/file.txt": "x\n", "sub/.htaccess": "Require all granted\n"},
         requests=[get("sub/file.txt"), get("page.txt")]),
    dict(name="child-inherits-header", group="core",
         htaccess="Header set X-Parent \"1\"\n",
         files={"sub/file.txt": "x\n", "sub/.htaccess": "Header set X-Child \"2\"\n"},
         requests=[get("sub/file.txt")]),
    dict(name="child-inherits-filesmatch", group="core",
         htaccess="<FilesMatch \"\\.txt$\">\nHeader set X-Match \"yes\"\n</FilesMatch>\n",
         files={"sub/file.txt": "x\n"},
         requests=[get("sub/file.txt")]),
    dict(name="if-else", group="core",
         htaccess=("<If \"%{QUERY_STRING} == 'a=1'\">\nHeader set X-Branch \"if\"\n</If>\n"
                   "<ElseIf \"%{QUERY_STRING} =~ /^b=/\">\nHeader set X-Branch \"elseif\"\n</ElseIf>\n"
                   "<Else>\nHeader set X-Branch \"else\"\n</Else>\n"),
         requests=[get("page.txt?a=1"), get("page.txt?b=2"), get("page.txt")]),
    dict(name="if-require", group="core",
         htaccess="<If \"%{HTTP:X-Block} == 'yes'\">\nRequire all denied\n</If>\n",
         requests=[("GET", "page.txt", {"X-Block": "yes"}), get("page.txt")]),
    dict(name="if-redirect", group="core",
         htaccess="<If \"%{HTTP_HOST} == 'compat.test'\">\nRedirect 302 {base}/a {base}/b\n</If>\n",
         requests=[get("a")]),
    dict(name="ifversion", group="core",
         htaccess=("<IfVersion >= 2.4>\nHeader set X-New \"1\"\n</IfVersion>\n"
                   "<IfVersion < 2.4>\nHeader set X-Old \"1\"\n</IfVersion>\n"),
         requests=[get("page.txt")]),
    dict(name="ifmodule-negated", group="core",
         htaccess=("<IfModule !mod_rewrite.c>\nHeader set X-Off \"1\"\n</IfModule>\n"
                   "<IfModule mod_nonexistent.c>\nHeader set X-None \"1\"\n</IfModule>\n"),
         requests=[get("page.txt")]),
    dict(name="line-continuation", group="core",
         htaccess="Header set X-Long \\\n  \"value\"\n",
         requests=[get("page.txt")]),
    dict(name="header-on-redirect", group="mod_headers",
         htaccess=("Header set X-Ok \"1\"\nHeader always set X-Always \"1\"\n"
                   "Redirect 302 {base}/a {base}/b\n"),
         requests=[get("a"), get("page.txt")]),
    dict(name="redirect-querystring", group="mod_alias",
         htaccess="Redirect 301 {base}/old {base}/new\n",
         requests=[get("old?x=1")]),
    dict(name="redirect-permanent", group="mod_alias",
         htaccess="RedirectPermanent {base}/old {base}/new\nRedirectTemp {base}/tmp {base}/t\n",
         requests=[get("old"), get("tmp")]),
    dict(name="unsetenv", group="mod_env",
         htaccess=("SetEnv A one\nSetEnv B two\nUnsetEnv B\n"
                   "Header set X-A \"%{A}e\"\nHeader set X-B \"set\" env=B\n"),
         requests=[get("page.txt")]),
    dict(name="env-condition-empty", group="mod_headers",
         htaccess=("SetEnv EMPTY \"\"\n"
                   "Header set X-Has \"yes\" env=EMPTY\n"
                   "Header set X-Not \"yes\" env=!EMPTY\n"),
         requests=[get("page.txt")]),
    dict(name="html5-boilerplate", group="aplicações reais",
         htaccess=("<IfModule mod_headers.c>\n"
                   "Header always set X-Content-Type-Options \"nosniff\"\n"
                   "Header always set Referrer-Policy \"strict-origin-when-cross-origin\"\n"
                   "<FilesMatch \"\\.(txt|md)$\">\n"
                   "Header set Cache-Control \"max-age=60\"\n</FilesMatch>\n</IfModule>\n"
                   "<IfModule mod_expires.c>\nExpiresActive on\n"
                   "ExpiresByType text/html \"access plus 0 seconds\"\n</IfModule>\n"
                   "<IfModule mod_autoindex.c>\nOptions -Indexes\n</IfModule>\n"
                   "<FilesMatch \"(^#.*#|\\.(bak|conf|ini|log|sh|sql)|~)$\">\n"
                   "Require all denied\n</FilesMatch>\n"),
         files={"db.sql": "secret\n"},
         requests=[get("page.txt"), get(""), get("db.sql")]),
    dict(name="wp-protect-config", group="aplicações reais",
         htaccess=("<Files wp-config.php>\nOrder allow,deny\nDeny from all\n</Files>\n"
                   "<Files xmlrpc.php>\n<RequireAll>\nRequire all denied\n</RequireAll>\n</Files>\n"),
         files={"wp-config.php": "secret\n", "xmlrpc.php": "x\n"},
         requests=[get("wp-config.php"), get("xmlrpc.php"), get("page.txt")]),
    dict(name="two-files-blocks", group="segurança",
         htaccess=("<FilesMatch \"\\.txt$\">\nHeader set X-Txt \"1\"\n</FilesMatch>\n"
                   "<Files \"secret.txt\">\nRequire all denied\n</Files>\n"),
         files={"secret.txt": "secret\n"},
         requests=[get("secret.txt"), get("page.txt")]),
    dict(name="two-files-headers", group="core",
         htaccess=("<FilesMatch \"\\.txt$\">\nHeader set X-A \"1\"\n</FilesMatch>\n"
                   "<Files \"page.txt\">\nHeader set X-B \"2\"\n</Files>\n"),
         requests=[get("page.txt")]),
    dict(name="protect-htaccess", group="segurança",
         htaccess="# vazio\n",
         requests=[get(".htaccess")], status_only=True),
]
