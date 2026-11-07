# Ponte Apache → OpenLiteSpeed para painéis

Faz o OpenLiteSpeed deste fork atender os sites que o HestiaCP ou o DirectAdmin configuram para o Apache, sem mudar
nada no painel. No modo `replace`, o OLS também assume o nginx que fica na frente e o PHP-FPM: os três serviços
continuam existindo para o painel, mas quem atende é o OLS, com o lsphp da mesma versão de cada pool. Plano e estado:
[`doc/htaccess-compat/PAINEL.md`](../doc/htaccess-compat/PAINEL.md).

## Instalação num servidor

O pacote leva o `/usr/local/lsws` da imagem de teste, a ponte e o instalador. Na máquina de desenvolvimento:

```bash
docker build -f htaccess-compat/docker/ols.Dockerfile --target runtime -t ols-htaccess:runtime .
```

```bash
sh panel/package.sh
```

O pacote sai em `panel/dist/olsbridge-<commit>-<sistema>.tgz`. A imagem é Ubuntu 24.04, e o binário roda em sistemas
com glibc 2.38 ou mais nova (Ubuntu 24.04 e 26.04, Debian 13). Para Debian 12 ou Ubuntu 22.04, gere a imagem a partir
de um desses sistemas. No servidor, como root:

```bash
tar xzf olsbridge-*.tgz && sh olsbridge-*/install.sh
```

O instalador confere se o binário roda no sistema, cria o usuário `lsadm` e gera a chave do WebAdmin no próprio servidor.
Ele não baixa nada e não inicia nada, e o WebAdmin do OLS fica desligado. Para trocar só o binário de uma instalação
existente, use `install.sh --upgrade`.

## Uso

Antes de instalar, veja o que seria gerado. Este comando não escreve nada:

```bash
/usr/local/lsws/bin/olsbridge check
```

```bash
/usr/local/lsws/bin/olsbridge install --mode offset
```

O modo `offset` deixa tudo como está e sobe o OLS nas mesmas portas somadas a 10000. Com o nginx na frente (HestiaCP), o
OLS escuta nas portas do nginx somadas a 10000 (80 vira 10080) e já faz o que o nginx faz; sem nginx, nas do Apache (8080
vira 18080). Os logs do OLS vão para `/usr/local/lsws/logs/offset/`, para não contar a banda em dobro. Confira se o
firewall bloqueia essas portas por fora; o do HestiaCP bloqueia. Para comparar os sites com o que o visitante vê hoje:

```bash
/usr/local/lsws/bin/olsbridge compare /phpmyadmin/ /wp-login.php
```

O `compare` faz só GET, no nginx (ou no Apache) e no OLS, para cada domínio: os caminhos padrão e os que forem passados.
Diferenças de status, redirecionamento ou certificado aparecem como `[DIFERENTE]`; `[tipo]` é o mesmo resultado com
outro `Content-Type`, em geral só o charset de uma página de erro. Depois de comparar:

```bash
/usr/local/lsws/bin/olsbridge install --mode replace
```

No modo `replace`, `apache2` (ou `httpd`), `nginx` e cada `phpX.Y-fpm` com lsphp ganham um drop-in em
`/etc/systemd/system/<serviço>.service.d/zz-olsbridge.conf`. Eles param de iniciar os programas deles e passam a só
recarregar o OLS; o painel continua vendo os três ativos. O processo do OLS é do `olsbridge.service`, que sobe no boot.
Parar um dos serviços do painel não derruba os sites; quem para o OLS é `systemctl stop olsbridge`. Se o OLS não
responder em todas as portas, a instalação volta para os serviços originais.

```bash
/usr/local/lsws/bin/olsbridge uninstall
```

`uninstall` remove os drop-ins e religa nginx, Apache e PHP-FPM.

Outros comandos:

| Comando | O que faz |
|---|---|
| `olsbridge status` | Estado do OLS e do modo |
| `olsbridge generate` | Gera e lista os avisos, sem recarregar |
| `olsbridge sync` | O que o timer roda a cada 30 segundos: gera de novo e recarrega se algo mudou |
| `olsbridge php-restart [8.2 ...]` | Reinicia o lsphp sem derrubar requisições |
| `olsbridge php-install` | Instala o `phpX.Y-litespeed` de cada versão do PHP-FPM |

A configuração gerada fica em `/usr/local/lsws/conf/bridge/`, com `warnings.txt` listando o que não foi convertido. Os
`.ini` dos pools e o script do Let's Encrypt ficam em `/usr/local/lsws/share/olsbridge/`, que o lsphp lê como o usuário
do site. Ajustes manuais do servidor vão em `/usr/local/lsws/conf/bridge/server-extra.conf`, que é preservado.

## PHP

Cada pool do PHP-FPM (`/etc/php/X.Y/fpm/pool.d/*.conf`) vira um lsphp da mesma versão, do pacote `phpX.Y-litespeed`
do mesmo repositório (o Sury, no HestiaCP). É o mesmo PHP, com as mesmas extensões: `mods-available` é comum ao FPM e
ao LiteSpeed. A instalação baixa o pacote que faltar; o timer tenta de novo uma vez por dia para versões novas. Uma
versão sem lsphp continua no PHP-FPM.

| Do pool | No lsphp |
|---|---|
| `user`, `group` | O lsphp roda com esse usuário (`extUser`, pelo `lscgid`) |
| `pm.max_children`, `pm.max_requests`, `pm.process_idle_timeout` | `PHP_LSAPI_CHILDREN`, `PHP_LSAPI_MAX_REQUESTS`, `LSAPI_MAX_IDLE` |
| `php_admin_value`, `php_value`, `php_admin_flag`, `php_flag` | Um `.ini` lido depois dos outros |
| `env[...]` | Variáveis de ambiente do lsphp |

O `php.ini` é o do PHP-FPM da versão (`PHPRC`), que é o que o painel edita, e o `.user.ini` dos sites vale como no
FPM (`LSPHP_ENABLE_USER_INI`). Quando o painel reinicia o `phpX.Y-fpm` depois de mudar o `php.ini` ou um pool, o lsphp
daquela versão reinicia: o processo pai para de aceitar, os filhos terminam o que estão atendendo, e a próxima
requisição já usa a configuração nova. Trocar a versão de um domínio no painel muda o socket na configuração do Apache, e
o domínio passa para o lsphp da versão nova. Cada requisição usa uma conexão com o lsphp, como o PHP-FPM com o
`mod_proxy_fcgi`, e quem passa do limite do pool espera na fila.

## nginx na frente

Com o HestiaCP em nginx + Apache, a ponte lê `/etc/nginx/conf.d/domains/*.conf` e `/etc/nginx/conf.d/*.conf`, além do
`charset` do `nginx.conf`, e o OLS assume as portas do nginx (`port_map`, tirado do `hestia.conf`). De cada servidor do
nginx, vira regra do OLS:

| No nginx | No OLS |
|---|---|
| `return 301 https://...` (HTTPS forçado) ou `http://` (o HTTPS do IP) | Rewrite 301 |
| `add_header Strict-Transport-Security` | Cabeçalho HSTS |
| `location ~ regex { deny all; return 404; }` (`.env`, `.git`, os do webmail) | Rewrite com o mesmo regex e status |
| `location /error/ { alias ...; }` | Contexto |
| Desafio do Let's Encrypt sem estado (`nginx.conf_letsencrypt`) | Rewrite para um script que responde `token.thumbprint` |
| `expires max` nos estáticos | `expiresByType` |
| `charset utf-8` | `addDefaultCharset` |
| `server_name _` com `default_server` | Regras da vhost padrão do IP |

Um `nginx.conf_*` do usuário vira aviso: o OLS não lê nginx.

## O que é convertido do Apache

`Listen`, `User`, `Group`, `VirtualHost`, `ServerName`, `ServerAlias`, `DocumentRoot`, `Alias`, `ScriptAlias`,
`DirectoryIndex`, `ErrorDocument`, `AllowOverride` e `Options Indexes` do `<Directory>`, SSL com cadeia separada,
`RewriteRule` da vhost, `Header`, `CustomLog` e `ErrorLog` com o `LogFormat` do Apache, PHP-FPM por `SetHandler` ou
`AddHandler` `proxy:unix:` e `proxy:fcgi://`, e `RemoteIPHeader` (ou o `RPAFproxy_ips` do mod_rpaf) com os proxies
confiáveis. `Include`, `IncludeOptional`, `Define`, `<IfModule>`, `<IfDefine>`, `<IfFile>` e `<IfVersion>` são
avaliados como no Apache, e um arquivo incluído em várias vhosts entra em todas.

Como o Apache faz:

- Um `<FilesMatch>` de PHP fora das vhosts vale para as que não têm o seu, como o pool `www` do HestiaCP.
- Um `<Directory>` dentro do site ou de um `Alias` vira um contexto do OLS, com o `DirectoryIndex`, o PHP
  e o acesso daquela pasta. O phpMyAdmin roda no pool `www` e as pastas `libraries`, `setup` e `vendor` ficam negadas.
  O acesso vem de `Require all denied|granted`, `Require ip`, `Require local` e `Order`/`Allow`/`Deny`. Um `Deny`
  que só bloqueia alguns IPs não é convertido.
- `Alias /` no lugar do `DocumentRoot` (o webmail do HestiaCP) vira a raiz. Um `Alias` coberto por outro anterior
  é ignorado, porque o Apache usa o primeiro que casa.
- `ErrorDocument` para um arquivo que não existe é ignorado: o Apache mostra a página padrão, e o OLS daria 500.
- No modo PHP-FPM (`php=fpm` no `olsbridge.conf`), cada requisição abre uma conexão com o pool, como o
  `mod_proxy_fcgi`. Um rewrite que gera PATH_INFO (o favicon do Roundcube) não funciona nesse modo: o OLS manda
  `REDIRECT_URL`, e o PHP-FPM procura o script por ele.

## Testes

```bash
docker build -f panel/test/hestia/Dockerfile -t olsbridge-hestia .
docker run --rm olsbridge-hestia
```

Monta Apache e PHP-FPM com os templates do HestiaCP e compara 71 respostas do Apache e do OLS lado a lado: HTTP,
HTTPS com SNI e cadeia, PHP, WordPress, `.htaccess`, aliases, páginas de erro, phpMyAdmin, webmail e logs. Usa o
PHP-FPM, porque o Ubuntu 24.04 não tem `php-litespeed`.

```bash
docker build -f panel/test/systemd/Dockerfile -t olsbridge-systemd .
sh panel/test/systemd/run.sh
```

Com systemd e só o Apache: troca o serviço `apache2`, cria e remove domínios como o painel faz, e volta para o Apache.

```bash
sh panel/package.sh
docker build -f panel/test/replace/Dockerfile -t olsbridge-replace .
sh panel/test/replace/run.sh
```

O arranjo do itzvpc2, em Ubuntu 26.04 com o PHP do Sury: nginx na frente, Apache e PHP-FPM. Troca os três, confere o PHP
pelo lsphp com o usuário, o `.user.ini` e o pool, muda o `php.ini` e um pool e reinicia o `php8.4-fpm` como o painel,
faz 60 requisições num pool de 2 filhos, reinicia o OLS como num boot e desinstala.

Os arquivos em `panel/test/hestia/fixtures/` e `panel/test/replace/fixtures/` vêm do
[HestiaCP](https://github.com/hestiacp/hestiacp), sob GPLv3: os templates de domínio do commit `3c52c6c`;
`hestia-event.conf`, `phpmyadmin.inc`, `mail.tpl` e `nginx.tpl` de uma instalação 1.10.6.
