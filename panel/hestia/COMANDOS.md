# OpenLiteSpeed no HestiaCP: o que está instalado

Registro do que a integração (`olsbridge hestia-integrate`) põe no HestiaCP, com as saídas reais do itzvpc2 em
2026-11-07. Uso geral e medições: [`../README.md`](../README.md).

## Comandos v-*

Ficam em `/usr/local/hestia/bin/`, no padrão do HestiaCP: rodam como root via sudo, aceitam `json`, `plain`, `csv`
e `shell` onde listam, e registram as ações no log do painel. São arquivos novos; as atualizações do HestiaCP não
os tocam.

| Comando | Argumentos | O que faz |
|---|---|---|
| `v-list-sys-litespeed` | `[FORMAT]` | Modo, estado, versão, opções, serviços assumidos, PHP de cada versão, avisos |
| `v-list-sys-litespeed-stats` | `[FORMAT]` | Tempo real (req/s, conexões, cache), por site, filas do PHP, IPs bloqueados, WAF das últimas 24 h |
| `v-change-sys-web-litespeed` | `replace\|offset\|off` | Troca quem atende os sites. Leva uns 10 s, com os sites fora nesse tempo |
| `v-change-sys-litespeed-option` | `OPÇÃO VALOR` | Muda uma opção (tabela abaixo) |
| `v-restart-sys-litespeed` | — | Regenera a configuração e reinicia o OLS sem derrubar conexões |
| `v-restart-sys-litespeed-php` | `[VERSÃO]` | Reinicia o lsphp de uma versão (`8.2`) ou de todas |
| `v-delete-web-domain-litespeed-cache` | `USER DOMAIN [CAMINHO]` | Limpa o LSCache do site, ou só de CAMINHO e abaixo dele. Imprime quantas entradas saíram |
| `v-list-user-litespeed-limits` | `USER [FORMAT]` | Limites e consumo de uma conta, com a última hora (JSON) |
| `v-change-user-package-litespeed-limits` | `PACOTE CPU MEMÓRIA [TASKS] [IO] [IOPS]` | Limites de um plano, aplicados na hora a todas as contas dele |

Opções de `v-change-sys-litespeed-option`:

| Opção | Valores | No itzvpc2 |
|---|---|---|
| `cache` | `on`, `off` | `on` |
| `http3` | `on`, `off` (abre/fecha UDP 443 no firewall, regra HTTP3) | `on` |
| `webp` | `on`, `off` | `on` |
| `protection` | `off`, `standard`, `strict` | `standard` |
| `waf` | `off`, `detect`, `block` | `block` |
| `limits` | `on`, `off` (CPU, memória e disco por usuário, cgroups v2) | `on` |
| `limit_cpu` | `50%`, `100%`, `200%`, `400%`, `off` | `200%` |
| `limit_mem` | `512M`, `1G`, `2G`, `4G`, `off` | `2G` |
| `limit_tasks` | `100`, `200`, `400`, `1000`, `off` | `400` |
| `limit_io` | `10M`, `25M`, `50M`, `100M`, `off` | `50M` |
| `limit_iops` | `500`, `1000`, `2000`, `off` | `1000` |

Os `limit_*` são o padrão; os campos de recursos do plano (`CPU_QUOTA`, `CPU_QUOTA_PERIOD`, `MEMORY_LIMIT`,
`SWAP_LIMIT`), quando não são `unlimited`, valem no lugar deles. `v-list-sys-litespeed json` traz em `limits` uma
linha por usuário (limites, de onde vieram, CPU usada e freada, memória, processos, mortes por falta de memória), e
`v-list-sys-litespeed-stats json` traz o mesmo em `users`, que a página usa para a CPU de agora.

Códigos de saída: os do HestiaCP (`0` ok; `1` argumentos; `2` valor inválido; `3` não existe, ou seja, olsbridge
não instalado; `20` falha ao aplicar).

### `v-list-sys-litespeed json`

```json
{
 "mode": "replace",
 "label": "active, in place of Apache + nginx + PHP-FPM",
 "running": true,
 "pid": 2084931,
 "options": {
  "cache": "on",
  "http3": "on",
  "webp": "on",
  "protection": "standard",
  "waf": "block",
  "waf_available": true,
  "webp_stats": "244 images in WebP, 46.3 MB saved (last pass 2026-11-07 19:55)"
 },
 "version": "LiteSpeed/1.9.4 Open",
 "units": ["apache2", "nginx", "php7.4-fpm", "php8.1-fpm", "php8.2-fpm", "php8.4-fpm", "php8.5-fpm"],
 "front": "nginx",
 "port_offset": "10000",
 "php": [
  {"version": "7.4", "lsphp": true, "served_by": "lsphp"},
  {"version": "8.2", "lsphp": true, "served_by": "lsphp"}
 ],
 "warnings": ["Listen 127.0.0.1:8081 sem vhost, não convertido (...)"]
}
```

- `mode`: `replace` (OLS no lugar), `offset` (OLS ao lado, portas +10000) ou `off`.
- `options.webp_stats` só aparece depois da primeira passada do WebP.
- `php[].served_by`: `lsphp`, ou `php-fpm` quando falta o lsphp daquela versão.
- Em `plain`, a saída é uma linha separada por tabs: modo, label, rodando (`yes`/`no`), versão, units, PHP
  (`7.4:lsphp ...`) e número de avisos.

### `v-list-sys-litespeed-stats json`

Chaves: `total`, `sites`, `php`, `ols_blocked`, `firewall_bans`, `waf` e `time`.

```json
{
 "total": {"uptime": "00:28:12", "conns": 0, "ssl_conns": 0, "idle_conns": 0, "bps_in": 0.0, "bps_out": 0.0,
           "req_per_sec": 0.0, "req_processing": 0, "total_reqs": 131571, "cache_hits_per_sec": 0.0,
           "total_cache_hits": 85255, "static_per_sec": 0.0, "cache_ratio": 64.8},
 "sites": [{"domain": "teste-hestia.intenza.com.br", "req_per_sec": 0.0, "total_reqs": 326, "cache_hits": 13,
            "processing": 0}],
 "php": [{"name": "lsphp82_teste_hestia_intenza_com_br", "in_use": 0, "idle": 6, "waiting": 0,
          "req_per_sec": 0.0, "total_reqs": 43}],
 "ols_blocked": [],
 "firewall_bans": [{"ip": "45.170.228.66", "chain": "RECIDIVE", "date": "2026-11-07 19:11:03"}],
 "waf": {"events": 45, "blocked": 30,
         "rules": [{"name": "942100 SQL Injection Attack Detected via libinjection", "count": 7}]}
}
```

- Os contadores (`total_reqs`, `total_cache_hits`, `uptime`) zeram a cada restart do OLS, incluindo os graciosos
  feitos quando a configuração muda.
- `ols_blocked` lista os IPs banidos pelo próprio OLS (limite por IP), que somem no restart.
- `firewall_bans` é a banlist do firewall do HestiaCP, onde caem também os jails `olsbridge-*` do fail2ban.
  Desbloqueio: `v-delete-firewall-ban IP CHAIN`.

## WAF por site

Feito a partir de `intenza-erp/docs/ESPEC-WAF-PONTE.md`. O cliente vê por que uma requisição do site dele foi
barrada e libera uma regra só no site dele. Tudo vale para o domínio e os aliases dele.

| Comando | Argumentos | O que faz |
|---|---|---|
| `v-list-web-domain-litespeed-waf` | `USER DOMAIN [HORAS] [FORMAT]` | Eventos do site (até 200, o mais novo primeiro), resumo por regra e por hora, modo e exclusões. HORAS: 1 a 168, padrão 24 |
| `v-add-web-domain-litespeed-waf-exclusion` | `USER DOMAIN REGRA [CAMINHO] [MOTIVO]` | Desliga a regra no site, ou só em CAMINHO e abaixo dele. Imprime o id (`ex_` + 8 hex) |
| `v-delete-web-domain-litespeed-waf-exclusion` | `USER DOMAIN ID` | Remove a exclusão |
| `v-change-web-domain-litespeed-waf` | `USER DOMAIN MODO` | `inherit` (o do servidor), `block`, `detect` ou `off` |
| `v-list-sys-litespeed-waf` | `[HORAS] [FORMAT]` | Eventos do servidor inteiro, com `domain` em cada um |

Os comandos de site conferem o usuário e que o domínio é dele (`is_object_valid 'web'`), registram a ação no log
do HestiaCP e aplicam com restart gracioso. A saída de lista é sempre JSON.

Códigos de saída:

| Código | Quando |
|---|---|
| `2` | Regra fora de 900000–999999, ou 901xxx, 949xxx, 959xxx, 980xxx (configuração e pontuação do CRS: liberar desligaria o WAF do site); caminho que não começa com `/`, com mais de 200 caracteres, espaço, aspas, `{}`, `;`, `` ` ``, `$`, `\`, `<>`, `|`; motivo com mais de 120 caracteres ou fora de letras, números, espaço e `.,_-()/:`; modo ou horas inválidos |
| `3` | Usuário ou domínio não existe, o domínio não é do usuário, id de exclusão que não existe, ponte ou mod_security não instalados |
| `4` | Já existe a mesma regra no mesmo caminho |
| `8` | O site já tem 50 exclusões |
| `20` | Falhou ao instalar as regras ou recarregar o OpenLiteSpeed |

`v-list-web-domain-litespeed-waf USER DOMAIN json`:

```json
{
 "domain": "alpha.test",
 "mode": "inherit",
 "effective": "block",
 "server_mode": "block",
 "events": [
  {
   "id": "177051234567.123456",
   "time": "2026-11-07T20:11:55-03:00",
   "ip": "203.0.113.9",
   "method": "GET",
   "uri": "/conta?token=***&id=1' OR '1'='1",
   "status": 403,
   "blocked": true,
   "rules": [
    {"id": "942100", "message": "SQL Injection Attack Detected via libinjection", "severity": "CRITICAL",
     "data": "Matched Data: s&sos found within ARGS:id: 1' OR '1'='1"}
   ]
  }
 ],
 "summary": {"events": 12, "blocked": 9,
             "rules": [{"id": "942100", "message": "SQL Injection Attack Detected via libinjection", "count": 7}],
             "by_hour": [{"hour": "2026-11-07T20:00", "events": 3, "blocked": 2}]},
 "exclusions": [{"id": "ex_a1b2c3d4", "rule": "942100", "path": "/api/webhook", "note": "Webhook do Mercado Pago",
                 "created": "2026-11-07T21:30:00-03:00"}]
}
```

`v-list-sys-litespeed-waf` tem `server_mode`, `events` (com `domain` logo depois de `id`) e `summary`.

Detalhes:

- **`blocked`**: `true` quando a resposta foi 403 e a regra de pontuação (949xxx/959xxx) disparou. Em `detect` o
  evento aparece com `blocked: false`. As regras de pontuação (949, 959, 980) não aparecem em `rules`.
- **Dados sensíveis**: nunca sai corpo, cookie ou cabeçalho. `data` sai cortado em 200 caracteres e `uri` em 300.
  Valores de parâmetros cujo nome contém `pass`, `senha`, `token`, `secret`, `key`, `auth`, `cookie`, `session`,
  `xsrf` ou `csrf` viram `***`, na URI e no `data`, e também o trecho que bateu num desses parâmetros, num cookie
  ou nos cabeçalhos `Authorization`, `Cookie` e `X-XSRF-TOKEN`.
- **Caminho**: vale para o caminho e o que está abaixo dele (`/api` cobre `/api` e `/api/x`, não `/apiary`). O
  caminho da requisição é decodificado e normalizado antes da comparação: `/api/../admin` não cai na exclusão.
  Sem caminho, vale para o site inteiro.
- **Modo**: `block` e `detect` funcionam mesmo com o WAF do servidor em `off`: o módulo é carregado em modo
  `DetectionOnly` e só os sites que pediram WAF são avaliados.
- **Persistência**: um JSON por domínio em `/usr/local/lsws/conf/waf-sites/` (root, 0600). Sobrevive ao `sync`, a
  restart e a mudanças do domínio no painel; quando o domínio some da configuração do Apache, o `sync` apaga o
  JSON. As regras saem em `/usr/local/lsws/conf/modsec/olsbridge-sites.conf`, antes do CRS, com ids a partir de
  1010000, estáveis por exclusão.
- **Histórico**: o log de auditoria gira todo dia e guarda 8 dias (`/etc/logrotate.d/olsbridge-modsec`), o que
  cobre as 168 h.
- **Cache**: adicionar ou apagar uma exclusão e mudar o modo limpam o LSCache do site. Uma página servida do
  cache não passa pelo WAF, então sem isso a mudança só valeria quando a página expirasse.
- Na ponte, o mesmo pela linha de comando: `olsbridge waf-site show|add|delete|mode DOMAIN ...` e
  `olsbridge waf-log --hours N`.

## Cache e limites por conta e por plano

Pedidos novos de `ESPEC-WAF-PONTE.md`.

### `v-delete-web-domain-litespeed-cache USER DOMAIN [CAMINHO]`

Apaga as páginas guardadas do domínio e dos aliases (HTTP e HTTPS). Com CAMINHO, só as daquele caminho e abaixo
dele: `/blog` limpa `/blog` e `/blog/post`, não `/blog2`; a query não conta, então `/busca` limpa também
`/busca?q=x`. Imprime o número de entradas apagadas (`0` quando nada estava guardado). A próxima visita é
atendida pelo site e guardada de novo. Saída `3` quando o usuário ou o domínio não existe ou o domínio não é do
usuário; `2` para caminho inválido (as mesmas regras do caminho do WAF). Ponte: `olsbridge cache-purge DOMAIN
[CAMINHO]`.

### `v-list-user-litespeed-limits USER json`

A mesma linha de `limits` de `v-list-sys-litespeed json`, de um usuário só. Saída `3` quando o usuário não
existe.

```json
{
 "user": "testehes",
 "package": "default",
 "uid": 1003,
 "cpu": "200%", "mem": "2G", "tasks": "400", "io": "50M", "iops": "1000",
 "from": {"cpu": "default", "mem": "default", "tasks": "default", "io": "default", "iops": "default"},
 "active": true,
 "memory": 41943040, "tasks_now": 7, "processes": 3,
 "cpu_usec": 18234000, "throttled_usec": 0, "memory_high_events": 0, "oom_kills": 0,
 "last_hour": {"seconds": 3600, "cpu_usec": 2410000, "throttled_usec": 0, "memory_high_events": 0,
               "oom_kills": 0},
 "last_oom": null,
 "limits": "on"
}
```

- `from`: `package` quando o valor veio do plano, `default` quando veio de `limit_*` do servidor.
- `cpu_usec`, `throttled_usec` (tempo em que a CPU foi freada pelo limite), `memory_high_events` e `oom_kills`
  contam desde que a fatia do usuário subiu (boot ou primeira requisição PHP). `last_hour` traz os mesmos quatro
  na última hora: a ponte guarda uma amostra a cada 5 minutos no ciclo de 30 s, em
  `/usr/local/lsws/conf/limits-history.json`. `seconds` diz quantos segundos a conta cobre (menos de 3600 na
  primeira hora depois de instalar); `last_hour` é `null` enquanto não há amostra. Um contador que voltou a zero (fatia reiniciada)
  conta a partir do zero.
- `last_oom`: data da última morte por falta de memória que a ponte viu, ou `null`. Fica guardada no histórico,
  mesmo depois de a fatia reiniciar.
- Sem fatia (usuário sem PHP rodando, ou `limits` em `off`): `active: false`, os limites que valeriam e consumo `0`.

### Limites de um plano

`v-change-user-package-litespeed-limits PACOTE CPU MEMÓRIA [TASKS] [IO] [IOPS]`, por exemplo
`v-change-user-package-litespeed-limits default 200% 2G 400 50M 1000`:

- `CPU`: porcentagem de um núcleo (`100%` = 1 núcleo) ou `unlimited`. `MEMÓRIA`: `512M`, `2G` ou `unlimited`.
  `unlimited` quer dizer o padrão do servidor (`limit_cpu`, `limit_mem`).
- CPU e memória vão para o plano do HestiaCP (`CPU_QUOTA`, `MEMORY_LIMIT`, e `SWAP_LIMIT` com um quarto da
  memória) e o comando roda `v-update-user-package PACOTE`, que copia o plano para o `user.conf` de cada conta.
- `TASKS`, `IO` e `IOPS` o HestiaCP não tem; a ponte guarda por plano em `/usr/local/lsws/conf/limit-plans/`.
  Omitidos, ficam como estão; `default` volta ao padrão do servidor.
- Aplica na hora em todas as contas do plano, sem reiniciar o PHP delas e sem esperar o ciclo de 30 s.
- Saída `2` valor inválido, `3` plano que não existe, `20` falha ao aplicar.

Pelo HestiaCP puro, o mesmo para CPU e memória: `v-add-user-package ARQUIVO PACOTE yes` (reescreve o `.pkg`)
seguido de `v-update-user-package PACOTE`. Aí a ponte aplica no próximo ciclo, em até 30 s. Ponte:
`olsbridge plan-limits PACOTE [--tasks N] [--io 50M] [--iops N]`.

## Mudanças em comandos e telas do HestiaCP

Estes trechos ficam entre marcadores `olsbridge:begin`/`olsbridge:end`. O gancho
`/etc/hestiacp/hooks/post_install.sh` os reaplica depois de cada atualização do HestiaCP, e
`olsbridge hestia-integrate --remove` os tira.

- **`v-list-sys-services`:** em modo `replace` a lista muda (exemplo do itzvpc2):

  ```json
  "litespeed":  {"SYSTEM": "web server · OpenLiteSpeed (in place of apache2 + nginx + PHP-FPM)", "STATE": "running", "CPU": "1.5", "MEM": "408", "RTIME": "28"},
  "apache2":    {"SYSTEM": "web server · disabled, served by OpenLiteSpeed", "STATE": "running", "CPU": "0", "MEM": "0", "RTIME": "28"},
  "nginx":      {"SYSTEM": "reverse proxy · disabled, served by OpenLiteSpeed", "STATE": "running", "CPU": "0", "MEM": "0", "RTIME": "28"},
  "php8.2-fpm": {"SYSTEM": "php interpreter · lsphp8.2 (LiteSpeed)", "STATE": "running", "CPU": "0", "MEM": "0", "RTIME": "28"}
  ```

  - `litespeed` é uma entrada nova. Ela vale para `v-restart-service`, `v-stop-service` e `v-start-service`,
    porque `litespeed.service` é um alias do `olsbridge.service`.
  - As chaves dos PHP continuam `phpX.Y-fpm`; a tela é que mostra `lsphpX.Y`.
  - `RTIME` está em minutos desde o último restart do OLS.
- **`/list/server/`:** mostra a linha `litespeed`, apache2 e nginx com ícone azul e linha apagada, e o PHP com o
  nome do lsphp. Ganha também um botão "OpenLiteSpeed".
- **Barra do topo:** ícone de raio para `/edit/server/litespeed/`, visível só para o admin.
- **Configure Server → Web Server:** linha "OpenLiteSpeed" com o lápis para a página.
- **Página `/edit/server/litespeed/`** (só para o admin): modo, opções, restart, restart do PHP, IPs bloqueados com
  desbloqueio, e estatísticas ao vivo por `/edit/server/litespeed/?stats=1` (o mesmo JSON do
  `v-list-sys-litespeed-stats`, atualizado a cada 5 s).

O painel segue escrevendo a configuração do Apache, do nginx e do PHP-FPM como sempre: domínios, versão do PHP,
`php.ini`, pools e SSL. A ponte converte essa configuração a cada 30 s e logo depois de cada restart que o painel
faz.

## API do HestiaCP no itzvpc2

O que se viu em `/usr/local/hestia/web/api/index.php` (HestiaCP 1.10.6) e na configuração:

- `API='yes'`, `API_SYSTEM='1'`, `API_ALLOWED_IP='187.17.236.53'`: só esse IP chega na API.
- **Formato antigo** (`hash`, ou `user` + `password`, com `cmd` e `arg1..arg13`): roda qualquer comando `v-*`
  existente, inclusive os `v-*-litespeed`.
- **Chaves de acesso** (`access_key` + `secret_key`): o `v-check-access-key` confere o `cmd` contra os comandos
  liberados para a chave. Os perfis de `/usr/local/hestia/data/api/` (`billing`, `mail-accounts`,
  `phpmyadmin-sso`, `purge-nginx-cache`, `sync-dns-cluster`, `update-dns-records`) não incluem os comandos do
  OpenLiteSpeed.
- `returncode=yes` devolve só o código de saída; sem isso, a saída do comando (o JSON acima).

## Arquivos e serviços no servidor

| Caminho | O que é |
|---|---|
| `/usr/local/lsws/bin/olsbridge` | A ponte; os `v-*` chamam esta |
| `/usr/local/lsws/conf/olsbridge.conf` | Modo e opções |
| `/usr/local/lsws/conf/bridge/` | Configuração gerada do OLS; `warnings.txt` lista o que não foi convertido |
| `/usr/local/lsws/conf/webp-state.json` | Total da última passada do WebP |
| `/usr/local/lsws/logs/error.log`, `modsec_audit.log` | Log do OLS (bans por IP, login do WordPress) e do WAF (JSON) |
| `/usr/local/lsws/add-ons/olsbridge-hestia/` | Cópia dos arquivos do HestiaCP, para reaplicar |
| `olsbridge.service` (alias `litespeed.service`) | Sobe no boot. O processo principal é a ponte (`olsbridge supervise`), que sobe o OLS, o reinicia se ele cair (5 s) e o para no stop. `systemctl reload` faz restart gracioso |
| `olsbridge-sync.timer` | A cada 30 s, regenera e recarrega se algo mudou |
| `olsbridge-webp.timer` | A cada 10 min, cria as cópias WebP que faltam |
| `olsbridge-unit@.service` | Usado pelos drop-ins de `apache2`, `nginx` e `phpX.Y-fpm` no modo replace |
| `phpX.Y-litespeed.service` | Mascarados (o serviço do pacote rodava o lsphp como root) |

No itzvpc2, o plugin LiteSpeed Cache está ativo em `teste-hestia.intenza.com.br` e em
`wpbench.teste-hestia.intenza.com.br`.
