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
recarregar o OLS; o painel continua vendo os três ativos. O OLS é do `olsbridge.service`, que sobe no boot. O processo do serviço é a ponte
(`olsbridge supervise`), não o OLS: o processo principal do OLS muda a cada restart gracioso, e o systemd tomava a
troca por uma queda. A ponte sobe o OLS de novo se ele ficar 5 s sem processo principal.
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
| `olsbridge set OPÇÃO VALOR` | `cache`, `http3`, `webp`, `protection`, `waf` (abaixo) |
| `olsbridge stats` | Tempo real, IPs bloqueados e eventos do WAF, em JSON |
| `olsbridge webp` | Cria as cópias WebP que faltam (o timer roda a cada 10 minutos) |

A configuração gerada fica em `/usr/local/lsws/conf/bridge/`, com `warnings.txt` listando o que não foi convertido. Os
`.ini` dos pools e o script do Let's Encrypt ficam em `/usr/local/lsws/share/olsbridge/`, que o lsphp lê como o usuário
do site. Ajustes manuais do servidor vão em `/usr/local/lsws/conf/bridge/server-extra.conf`, que é preservado.

## No painel do HestiaCP

```bash
/usr/local/lsws/bin/olsbridge hestia-integrate
```

Acrescenta uma linha "OpenLiteSpeed" na seção Web Server de Configure Server, com o lápis para a página
`/edit/server/litespeed/`. Na página, só para o admin:

- escolher quem atende os sites: Apache, nginx e PHP-FPM; OpenLiteSpeed ao lado, para comparar; ou OpenLiteSpeed;
- reiniciar o OpenLiteSpeed, ou o lsphp de cada versão do PHP;
- ver o que da configuração não foi convertido.

A troca leva alguns segundos, e os sites ficam fora enquanto as portas mudam de dono (6 s no itzvpc2). Se o
OpenLiteSpeed não responder em todas as portas, a troca é desfeita.

A página usa comandos no padrão do HestiaCP, que valem também na linha de comando e na API dele. Saídas reais, o que
muda no HestiaCP e o que a API dele aceita estão em [`hestia/COMANDOS.md`](hestia/COMANDOS.md).

| Comando | O que faz |
|---|---|
| `v-list-sys-litespeed [json\|shell\|plain\|csv]` | Modo, versão, serviços assumidos, PHP de cada versão e avisos |
| `v-change-sys-web-litespeed replace\|offset\|off` | Troca quem atende os sites |
| `v-restart-sys-litespeed` | Regenera e reinicia o OpenLiteSpeed sem derrubar conexões |
| `v-restart-sys-litespeed-php [8.2]` | Reinicia o lsphp, de uma versão ou de todas |

Atalhos para a página: um raio na barra do topo, ao lado da engrenagem (só o admin vê), e o botão OpenLiteSpeed em
Server (`/list/server/`).

A lista de serviços em `/list/server/` mostra o OpenLiteSpeed como ele está:

| Linha | No modo replace |
|---|---|
| `litespeed` | Estado, CPU, memória e tempo de vida dos processos do OLS. Reiniciar e parar valem: `litespeed.service` é um alias do `olsbridge.service` |
| `apache2`, `nginx` | Ícone azul e linha apagada: "disabled, served by OpenLiteSpeed". O serviço segue ativo para o painel |
| `phpX.Y-fpm` | Aparece como `lsphpX.Y`, com os números do lsphp daquela versão. Editar, reiniciar e parar continuam no `phpX.Y-fpm` |

O `v-list-sys-services` passa a lista pelo `olsbridge hestia-services`; se a ponte falhar, a lista sai como sempre.

As ações vão para o log de atividade do HestiaCP. Os comandos e a página são arquivos novos, que as atualizações do
HestiaCP não tocam. Os trechos em arquivos do pacote do HestiaCP (`edit_server.php`, `panel.php`, `list_services.php`,
`v-list-sys-services`) ficam entre marcadores; o pacote substitui esses arquivos, e o gancho
`/etc/hestiacp/hooks/post_install.sh`, que ele roda depois de cada atualização, coloca os trechos de volta.
`hestia-integrate --remove` tira tudo e deixa os arquivos como eram.

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
requisição já usa a configuração nova. O pacote `phpX.Y-litespeed` traz um `phpX.Y-litespeed.service` que, no boot,
sobe o lsphp como root num socket em `/run/php`; a ponte mascara esse serviço, porque quem roda o PHP é o OLS, com o
usuário de cada pool. Trocar a versão de um domínio no painel muda o socket na configuração do Apache, e
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

## Performance e segurança

As opções valem para o servidor inteiro. Mudam pela página do OpenLiteSpeed no painel, por
`v-change-sys-litespeed-option OPÇÃO VALOR` ou por `olsbridge set OPÇÃO VALOR`.

| Opção | Padrão | O que faz |
|---|---|---|
| `cache` | `on` | LSCache no servidor. Guarda só o que a aplicação pede: no WordPress, com o plugin LiteSpeed Cache. Quem não pede não é afetado |
| `webp` | `on` | PNG e JPEG saem em WebP para o navegador que aceita (abaixo) |
| `http3` | `off` | HTTP/3 (QUIC). Ligar abre UDP 443 no firewall do HestiaCP (regra HTTP3) |
| `protection` | `standard` | `off`, `standard` ou `strict`: conexões por IP, reCAPTCHA sob ataque, login do WordPress e jails do fail2ban |
| `waf` | `off` | `off`, `detect` ou `block`: ModSecurity com o OWASP CRS 4 e as exclusões do WordPress. Aceita PUT, PATCH e DELETE (APIs REST, Laravel), que o CRS sozinho barra |

Fixos, sem opção: brotli e gzip com cache em memória dos estáticos comprimidos, cache de sessão TLS e tickets,
OCSP stapling, `expires` máximo nos estáticos como o `expires max` do nginx do HestiaCP. O `expires` do OLS vale
também para o que o PHP gera, por isso cobre só tipos de arquivo estático (imagem, fonte, CSS, JS, PDF...), nunca
HTML, JSON, XML ou texto.

**WebP.** `olsbridge webp` cria `imagem.png.webp` ao lado de cada PNG/JPEG das pastas dos sites. O `cwebp` (pacote
`webp`, instalado sozinho) roda com o usuário dono da imagem, e a cópia só fica se for ao menos 10% menor. Imagens de
root, links simbólicos e pastas ocultas ficam de fora. Uma regra de rewrite serve a cópia quando o navegador aceita
`image/webp`. Como a cópia está na mesma pasta, valem para ela as mesmas regras de acesso da original (`.htaccess`,
senha), e as respostas levam `Vary: Accept`. O plugin LiteSpeed Cache usa o mesmo nome de arquivo, então os dois
convivem. As cópias contam na cota e no backup do usuário.

**Limite por IP.** O OLS conta as conexões em cada processo (worker), por isso a ponte divide os limites pelo número
de workers. Acima do limite soft, o OLS desliga o keep-alive daquele IP: cada conexão fecha depois da resposta.
Bane o IP (por `banPeriod`) quem passar do hard, ou ficar acima do soft por `gracePeriod` segundos, ou seja, quem
**segura** conexões abertas (slowloris, uploads lentos, PHP lento sob ataque). Uma rajada de requisições completas
não bane, mas fica limitada ao soft. Os IPs do próprio servidor não têm limite. O ban aparece no `error.log`, e o
jail `olsbridge-limit` do fail2ban leva o IP para o firewall do HestiaCP.

| Nível | Conexões por IP (soft / hard, grace) | Ban | PHP por IP | reCAPTCHA a partir de | Login do WordPress |
|---|---|---|---|---|---|
| `standard` | 300 / 600, 15 s | 10 min | sem limite | 8.000 conexões no servidor | 10 tentativas em 5 min, bloqueio de 15 min |
| `strict` | 100 / 200, 10 s | 30 min | 30 req/s | 2.500 conexões no servidor | 5 tentativas em 5 min, bloqueio de 1 h |

Jails do fail2ban (ação `hestia[name=WEB]`, visíveis e removíveis em Server → Firewall → Banlist):
`olsbridge-limit` (limite por IP), `olsbridge-wp` (login do WordPress), `olsbridge-waf` (bloqueios do WAF) e
`olsbridge-scan` (varredura de `.env`, `.git`, `wp-config` e afins).

### Medições no itzvpc2 (2026-11-07)

PageSpeed (Lighthouse 13.6 local, celular com 4G simulado, mediana de 5 rodadas), site WordPress com Elementor:

| Servidor | Nota | LCP | Peso |
|---|---|---|---|
| nginx + Apache + PHP-FPM | 75 | 7,2 s | 2,8 MB |
| OLS | 79 | 5,3 s | 2,8 MB |
| OLS + WebP | 94 | 2,6 s | 525 KB |
| OLS + WebP + plugin LiteSpeed Cache | 96 | 2,6 s | 525 KB |

Com HTTP/3 desligado, o OLS fica em 75 (LCP 7,1 s): o HTTP/3 ajuda.

Capacidade (h2load no próprio servidor, HTTPS/HTTP2, 8 vCPU), `bench/bench2.sh`:

| Cenário | nginx + Apache + FPM | OLS + lsphp | OLS + LSCache |
|---|---|---|---|
| Estático (jQuery 87 KB, br/gzip), 50 conexões | 296 req/s | 41.444 req/s | 45.518 req/s |
| Home do WordPress sem cache, 20 conexões | 70 req/s, 282 ms | 73 req/s, 273 ms | 52.638 req/s, 0,25 ms |
| Post sem cache, 20 conexões | 73 req/s | 78 req/s | 33.938 req/s |
| Home, 1 usuário | 118 ms | 109 ms | 0,21 ms |
| TTFB remoto da home (São Paulo, mediana de 10) | 427 ms | — | 141 ms (104 ms são TCP+TLS) |

O nginx do HestiaCP comprime o arquivo a cada requisição (`gzip_static` sem os `.gz`); sem compressão ele faz 1.807
req/s. O OLS guarda a versão comprimida em memória. O PHP sem cache é limitado pelos 8 processos do pool nos dois. O
WAF em `block` não mudou os números do PHP.

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
faz 60 requisições num pool de 2 filhos, liga o WAF, testa o WebP (cópia com o dono da imagem, WebP só para quem
aceita) e o limite por IP (300 conexões slowloris de um segundo container: o IP é banido e os outros seguem
atendidos), confere PUT, PATCH e DELETE com o WAF ligado, reinicia o OLS como num boot e desinstala. São 40
verificações.

Os arquivos em `panel/test/hestia/fixtures/` e `panel/test/replace/fixtures/` vêm do
[HestiaCP](https://github.com/hestiacp/hestiacp), sob GPLv3: os templates de domínio do commit `3c52c6c`;
`hestia-event.conf`, `phpmyadmin.inc`, `mail.tpl` e `nginx.tpl` de uma instalação 1.10.6.
