# Plugin para HestiaCP e DirectAdmin

Status: em desenvolvimento, iniciado em 2026-11-07. Num HestiaCP de verdade (itzvpc2), o OLS assume nginx, Apache e
PHP-FPM, com o lsphp da mesma versão de cada pool. DirectAdmin não foi testado. Uso e testes:
[`panel/README.md`](../../panel/README.md).

### Performance e segurança no itzvpc2 (2026-11-07)

Medições completas e opções em [`panel/README.md`](../../panel/README.md#performance-e-segurança). Resumo:

- PageSpeed celular (mediana de 5): nginx + Apache 75, OLS 79, OLS com WebP 94, mais o plugin LiteSpeed Cache 96. O
  LCP foi de 7,2 s para 2,6 s, e a página de 2,8 MB para 525 KB. Uma rodada só do PageSpeed varia ±10 pontos: um
  print de 85 com nginx e 75 com OLS foi essa variação, não o servidor.
- Estático 140x mais rápido que o nginx do HestiaCP (que comprime a cada requisição); PHP sem cache igual (limitado
  pelo pool); com o LSCache, a home sai em 0,25 ms.
- WAF em `block` sem custo mensurável; WP admin, login e editor seguem funcionando.

Achados e correções desta rodada:

| Achado | Correção |
|---|---|
| `expires max` do nginx convertido para `text/*`, JSON e XML: HTML do WordPress, `wp-json` e sitemaps com cache de 10 anos | Só tipos estáticos |
| Voltar de `replace` para `offset` religava o nginx com o OLS ainda nas portas 80/443: sites fora até subir à mão | O OLS sai das portas antes |
| Cada start das unidades disfarçadas mandava SIGUSR1; em rajada, o systemd via o processo principal sair e reiniciava o serviço | Restart só quando a configuração muda, um por vez, esperando o processo novo |
| O `phpX.Y-litespeed` do Sury traz um serviço que sobe o lsphp como root num socket no boot | Mascarado |
| O teste do limite por IP usava rajadas, que o OLS limita sem banir | Teste com slowloris; o ban funciona |
| `/list/server/` mostrava apache2, nginx e php-fpm parados | Linha do `litespeed`, os outros como atendidos pelo OLS, PHP como lsphp |

### HestiaCP real: itzvpc2 (2026-11-07)

Ubuntu 26.04, HestiaCP 1.10.6, nginx nas portas 80/443 e Apache 2.4.66 em 8080/8443, PHP-FPM 7.4, 8.1, 8.2, 8.4 e 8.5
do Sury. São 4 domínios (um com webmail), phpMyAdmin e a vhost do IP.

Antes de trocar, o OLS rodou em modo `offset` nas portas 10080/10443, que o firewall do HestiaCP bloqueia por fora. O
`compare` contra o nginx real deu 0 diferenças de status, redirecionamento ou certificado em 112 requisições. O desafio
do Let's Encrypt saiu idêntico ao do nginx em 40 de 40. Num pool de 8 filhos, 40 requisições simultâneas a um PHP de
0,2 s deram 200, a mais lenta em 0,8 s. O phpMyAdmin, no pool `www` de 4 filhos, deu 30 de 30.

Depois, `install --mode replace`: só o OLS escuta nas portas 80 e 443, e `apache2`, `nginx` e os cinco `phpX.Y-fpm`
seguem ativos para o painel sem nenhum processo deles. O painel do HestiaCP (8083) tem nginx próprio e não muda. De
fora, pela internet:

- Os dois domínios com DNS têm certificado válido, HSTS onde o nginx tinha, HTTP redirecionado para HTTPS, `.env`
  com 404, phpMyAdmin com 200 e `libraries` com 403, e o desafio do Let's Encrypt.
- Pelo IP: webmail com o favicon do Roundcube, `README.md` do webmail com 404 e o teste22.

Pelo próprio HestiaCP (`v-change-web-domain-backend-tpl`, `v-restart-service`, `v-add-web-domain` e
`v-delete-web-domain`):

- trocar o PHP do teste22 de 8.4 para 8.2 e voltar;
- mudar `memory_limit`, `upload_max_filesize` e `max_execution_time` no `php.ini` e reiniciar o `php8.4-fpm`;
- criar um domínio, servido na hora pelo OLS com PHP 8.5 e o usuário certo, e apagá-lo.

Tudo chegou aos sites. Para voltar: `olsbridge uninstall`.

Integração no painel (`olsbridge hestia-integrate`): a seção Web Server de Configure Server ganha a linha
"OpenLiteSpeed", que leva a uma página para trocar entre Apache/nginx/PHP-FPM, teste lado a lado e OpenLiteSpeed, e
para reiniciar o OLS e o lsphp. O HestiaCP não troca de servidor web depois de instalado (os lápis da seção só editam o
arquivo de configuração de cada serviço), e um `WEB_SYSTEM=litespeed` exigiria mudar 23 scripts que comparam o servidor
web com `apache2`, `httpd` ou `nginx`, perdidos a cada atualização. A página roda pelos mesmos comandos `v-*` da linha
de comando, como `hestiaweb` via sudo; no itzvpc2 a troca deixou os sites 6 s fora, e cada ação foi para o log de
atividade.

O `check` e o `compare` acharam o que os templates do teste não tinham:

| Achado | Efeito no OLS | Correção |
|---|---|---|
| Cada vhost inclui `conf.d/*.inc`, e a ponte lia cada arquivo uma vez só | O `phpmyadmin.inc` só entrava na primeira vhost | O arquivo entra em toda vhost que o inclui; só a recursão é barrada |
| `<Directory>` do phpMyAdmin com PHP no pool `www` e pastas negadas | PHP no pool do usuário; `libraries`, `setup` e `vendor` abertos | Contexto por pasta, com PHP, índice e acesso; outro pool por um tipo MIME só daquele handler |
| `hestia-event.conf`: `.php` no pool `www` para o que não tem PHP próprio | Webmail e a vhost do IP sem PHP | PHP do servidor vale para as vhosts sem o seu |
| Webmail com `Alias /` e sem `DocumentRoot` | Vhost descartada | O `Alias /` vira a raiz; os Alias que ele cobre são ignorados |
| `ErrorDocument` do webmail aponta para um arquivo que não existe | 500 em vez de 404 | Página inexistente não vira `errorPage` |
| A ponte deixava a conexão com o PHP-FPM aberta | O pool `www` (4 processos) ficou preso no OLS, e o Apache esperou até estourar o tempo | Uma conexão por requisição, como o `mod_proxy_fcgi` |
| `RewriteRule ^favicon\.ico$ static.php/skins/...` do Roundcube | 404: o PHP recebia o PATH_INFO dentro do `SCRIPT_NAME` | Bug do OLS, também no upstream: o `SCRIPT_NAME` sai da URL servida, não da pedida |
| O nginx faz HTTPS forçado, HSTS, Let's Encrypt, esconde `.env` e `.git` e serve `/error/` do webmail | Sem o nginx, nada disso aconteceria | A ponte lê o nginx de cada domínio e gera as mesmas regras |
| O OLS desliga todo rewrite nas URLs do ACME | Sem HTTPS forçado e sem resposta ao desafio | Fork: só as regras do `.htaccess` deixam de rodar; as da vhost valem, como no Apache |
| Sem `CGIRLimit`, o OLS não configura o `lscgid` | Nenhum lsphp subia com o usuário do site (`cgidSuEXEC failed`) | A ponte gera o bloco |
| O lsphp ignora o `.user.ini` por padrão | `memory_limit` do site em 128M em vez de 768M | `LSPHP_ENABLE_USER_INI=on` |
| Um lsphp de antes do restart do OLS segue no mesmo socket | `php.ini` e pool mudados pelo painel não chegavam aos sites | O restart do `phpX.Y-fpm` reinicia o lsphp da versão, sem derrubar requisições |
| `persistConn 0` não valia para LSAPI, e cada processo do OLS prende um filho do lsphp | Com 8 processos e um pool de 2 filhos, requisições esperavam para sempre | Fork: com `persistConn 0` o LSAPI fecha a conexão ao fim da requisição |

### Teste em container do modo replace (2026-11-07)

O mesmo arranjo, em Ubuntu 26.04 com systemd e o PHP do Sury: 25 de 25 verificações. Os serviços `apache2`, `nginx` e
`php8.4-fpm` ficam ativos sem nenhum processo deles. O PHP sai pelo lsphp com o usuário, o `.user.ini` e o pool. Uma
mudança no `php.ini` ou num pool chega aos sites depois do `restart php8.4-fpm`. Num pool de 2 filhos, 60 requisições
passaram sem nenhuma lenta. A desinstalação devolve nginx, Apache e PHP-FPM. Com 45 restarts seguidos de `php8.4-fpm`,
`nginx` e `apache2`, nenhum foi recusado, e 314 de 314 requisições deram 200.

Achados do container:

- `apache2` e `php8.4-fpm` têm sandbox (`ProtectSystem=full`, `PrivateDevices`), que deixava `/usr/local/lsws` só
  leitura para a ponte. O drop-in só pede ao systemd para rodar `olsbridge-unit@<serviço>`, sem sandbox.
- A unidade disfarçada termina em milissegundos, e o limite do systemd (5 starts em 10 s) recusava os restarts que o
  painel faz em sequência. Os drop-ins desligam esse limite.
- A verificação de que o OLS subiu usava o `ss`, que nem toda instalação tem; agora conecta em cada porta.

### Resultados da Fase 1 (2026-11-07)

| Teste | Resultado |
|---|---|
| Apache e OLS lado a lado, templates do HestiaCP: 3 domínios, HTTP, HTTPS, PHP-FPM, WordPress, `.htaccess`, aliases, erros, cadeia SSL, phpMyAdmin e webmail | 71 de 71 respostas iguais (uma é um limite do modo PHP-FPM), nenhum aviso |
| Logs de acesso e de banda do domínio | Mesmo formato; banda (`%b`) igual. `%O` difere alguns bytes porque os cabeçalhos não são os mesmos |
| Troca do serviço `apache2` com systemd, domínio criado com `reload`, domínio removido sem `reload`, `configtest`, `restart`, `stop` e volta ao Apache | 16 de 16 |

Achados que mudaram o desenho:

- O OLS grava os logs com o usuário dos processos, e o Apache como root. A ponte passa os arquivos de log para esse
  usuário a cada geração, inclusive depois do logrotate, que chama o `reload`.
- O pacote do OLS sobe o servidor no boot pelo script `lsws`. O systemd recusa como serviço um processo que não viu
  nascer, então a ponte desliga esse script e para a instância antes de subir a sua.
- No modo `offset`, Apache e OLS no mesmo arquivo de log contariam a banda em dobro; o OLS grava em outra pasta.

## 1. Objetivo

Trocar o Apache, o nginx da frente e o PHP-FPM por este OpenLiteSpeed em servidores com HestiaCP ou DirectAdmin, sem
que o painel perceba. O painel continua gerando a configuração do Apache, do nginx e dos pools, e mandando recarregar
os serviços deles. Quem atende os sites é o OLS, com o lsphp da mesma versão de cada pool.

Referência: o plugin da LiteSpeed para cPanel e o [release log dos plugins](https://www.litespeedtech.com/products/litespeed-web-server/control-panel-support/release-log).

Fica de fora o que não se aplica: LiteSpeed Containers, licenças e QUIC.cloud.

## 2. Como funciona

```
painel ──escreve──▶ Apache, nginx e pools do PHP-FPM ──lidos pela──▶ ponte ──gera──▶ config do OLS e do lsphp
painel ──systemctl reload/restart apache2, nginx, phpX.Y-fpm──▶ drop-in ──▶ olsbridge-unit@ ──▶ gera e recarrega
painel ──apache2ctl configtest, nginx -t, php-fpm -t──▶ binários reais (continuam instalados, nunca são iniciados)
```

- **Ponte** (`panel/bridge`): lê a configuração do Apache com `Include`, `IncludeOptional`, `Define` e `<IfModule>`, o
  nginx de cada domínio e os pools do PHP-FPM, e gera listeners, vhosts, aliases, SSL, logs, regras do nginx e um lsphp
  por pool. O que não sabe converter vai para um relatório de avisos.
- **Serviços disfarçados**: drop-ins em `apache2`, `nginx` e `phpX.Y-fpm` trocam o programa por um recarregamento do
  OLS. O processo do OLS é do `olsbridge.service`, que sobe no boot.
- **Desfazer**: remover os drop-ins devolve os serviços originais. Nada do painel é alterado.

### Modos

| Modo | O que faz | Uso |
|---|---|---|
| `offset` | Tudo continua no ar, e o OLS sobe nas mesmas portas somadas a um deslocamento | Comparar os dois no mesmo servidor antes de trocar |
| `replace` | `apache2`, `nginx` e `phpX.Y-fpm` passam a ser o OLS | Produção |

Nos dois modos um timer regenera a configuração se os arquivos do Apache, do nginx ou dos pools mudarem sem um `reload`.

## 3. Painéis

| | HestiaCP | DirectAdmin |
|---|---|---|
| Config principal | `/etc/apache2/apache2.conf` | `/etc/httpd/conf/httpd.conf` |
| Domínios | `/etc/apache2/conf.d/domains/*.conf`, links para `/home/USER/conf/web/DOMÍNIO/apache2*.conf` | `/usr/local/directadmin/data/users/USER/httpd.conf` |
| Serviço | `apache2`, via `systemctl reload-or-restart` | `httpd` |
| Teste de config | `apache2ctl configtest` | `httpd -t` |
| PHP | PHP-FPM por domínio, `SetHandler "proxy:unix:/run/php/...sock\|fcgi://localhost"` | PHP-FPM, LSAPI, suPHP ou FastCGI, conforme o CustomBuild |
| Na frente | NGINX nas portas 80/443, Apache em 8080/8443 | Apache direto, ou NGINX na frente |
| Logs que o painel lê | `/var/log/apache2/domains/DOMÍNIO.log` e `.bytes` (banda) | `/var/log/httpd/domains/DOMÍNIO.log` e `.bytes` |
| Interface para plugin | Não tem sistema de plugins | Tem sistema de plugins |

O DirectAdmin também tem um modo OpenLiteSpeed nativo no CustomBuild, com templates próprios. Ele é uma alternativa ao
disfarce, mas o painel deixa de "achar que é Apache"; decidir no servidor de teste.

## 4. Recursos

| Recurso | Fase |
|---|---|
| Ponte, serviço disfarçado e modo `offset` | 1 (pronto) |
| nginx da frente e PHP-FPM assumidos pelo OLS, lsphp por pool | 1 (pronto) |
| Restart do PHP sem derrubar requisições (`olsbridge php-restart`) | 2 (pronto) |
| Restart e versão instalada | 2 |
| Atualização a partir das versões do fork | 2 |
| Encerrar o PHP de contas suspensas (hooks dos painéis) | 2 |
| Cache de WordPress: descoberta, ativação em massa, marcar sites, versão do plugin | 3 |
| Aviso no painel de todos os WordPress (Dash Notifier) | 3 |
| Pasta do cache por servidor e por domínio | 3 |
| Redis por usuário, com padrão por plano e opção de Valkey | 4 |
| Avisos ao administrador: erros da instalação e versão nova | 2 |
| HestiaCP: página OpenLiteSpeed em Configure Server, comandos `v-*` (CLI e API), sobrevive às atualizações | 5 (pronto) |
| Interface no DirectAdmin | 5 |

## 5. Cuidados

- A parte que roda como root deve ser mínima e tratar links simbólicos com cuidado. O plugin da LiteSpeed teve uma falha
  em que um usuário do cPanel virava root por symlink.
- O OLS precisa rodar com o mesmo usuário e grupo do Apache, ou não consegue usar os sockets do PHP-FPM.
- Antes de trocar, conferir se o Apache está instalado e se a configuração gerada sobe; se não subir, manter o Apache.
- `php_value` no `.htaccess` dá erro 500 no Apache com PHP-FPM. Com o lsphp ele funciona, como no Apache com
  `mod_php`. No modo PHP-FPM da ponte, o comportamento não foi testado.
- Uma versão nova do PHP instalada pelo painel depois do `install` fica no PHP-FPM real até o timer instalar o
  `phpX.Y-litespeed` e o `install --mode replace` rodar de novo para disfarçar o `phpX.Y-fpm` dela.

## 6. Pronto quando

- Fase 1: domínios gerados com os templates reais do HestiaCP respondem igual no Apache e no OLS, e o ciclo
  `reload` do painel funciona num container com systemd.
- Antes de produção: os dois servidores reais, HestiaCP e DirectAdmin, em modo `offset` com sites de verdade.
