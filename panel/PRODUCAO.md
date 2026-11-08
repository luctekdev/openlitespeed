# Prontidão para produção: hospedagem compartilhada

Avaliação de 2026-11-08, feita no itzvpc2 (HestiaCP 1.10.6, Ubuntu 26.04, 8 vCPU, 7,4 GB) e na suíte em
contêiner (`panel/test/replace`). Uso e medições: [`README.md`](README.md).

**Resumo:** o OpenLiteSpeed com a ponte está pronto para um piloto em produção, com clientes reais em um
servidor, migrando poucos sites por vez. Ainda não está pronto para migrar um parque inteiro de uma vez: falta
tempo de uso real (abaixo). O isolamento de recursos por cliente, que faltava, existe desde 2026-11-08.

## Pronto e testado

| Área | O que existe | Como foi validado |
|---|---|---|
| Compatibilidade | `.htaccess` do Apache no OLS: rewrite, `Files`/`FilesMatch`, `Require`/`Allow`, `IfVersion`, `Header`, `SetEnv`, `ErrorDocument`, `Redirect*`, `php_value` | 109 de 111 requisições iguais ao Apache 2.4; WordPress, Laravel, PrestaShop e Magento 2.4 conferidos |
| PHP | lsphp por pool do PHP-FPM, com o usuário, o `php.ini`, o `open_basedir` e o `.user.ini` do site; troca de versão pelo painel | Fluxos do painel no itzvpc2 (versão, `php.ini`, pool, criar e apagar domínio) |
| Painel | Comandos `v-*`, página OpenLiteSpeed, lista de serviços, tela de status, ícone na barra; voltam sozinhos depois de atualizações do HestiaCP | itzvpc2 |
| Desempenho | LSCache, Brotli, HTTP/3, WebP automático, cache de estáticos em memória pela RAM | PageSpeed mobile 75 → 96 (mediana de 5); estático 296 → 41.000 req/s; home com LSCache 52.000 req/s |
| Segurança web | WAF (OWASP CRS 4, com exclusões por site), limite de conexões por IP, proteção do login do WordPress, reCAPTCHA sob ataque, jails do fail2ban no firewall do HestiaCP | Ataques bloqueados; slowloris banido; WP admin e APIs REST (PUT/PATCH/DELETE) funcionando com o WAF ligado |
| Isolamento entre clientes | Link simbólico só é seguido quando o dono do link é o dono do destino; pastas de sistema negadas; PHP com o usuário do site; serviço do lsphp como root do pacote mascarado | Link de um cliente para o arquivo de outro e para `/etc/passwd`: 403. Link do próprio dono (storage do Laravel): funciona |
| Robustez | Restart gracioso sem queda; o serviço vigia a ponte, não o PID do OLS; OLS reerguido em 5 s se cair; logs reabertos depois do logrotate; volta ao Apache com um comando | 80 reloads sob carga sem falha; `kill -9` volta em 5 s; logrotate do HestiaCP simulado no itzvpc2 |
| PHP travado | Requisição PHP com tempo máximo de relógio (`php_timeout`, padrão 300 s) | Script de 90 s cortado em 33 s com a opção em 30 s |
| Recursos por cliente | Como o LVE do CloudLinux: o lsphp de cada usuário nasce na fatia dele (cgroups v2), com teto de CPU, memória, swap, processos e disco; valores do plano do HestiaCP ou padrões da ponte; página e `olsbridge limits` com o consumo | itzvpc2: página de outro usuário 86 → 131 ms com um cliente queimando CPU sem limites, 87 → 83 ms com; disco do cliente em 28 MB/s (outro: 1,9 GB/s); 4 GB pedidos com teto de 2 GB: só aquele processo morto, em 2,9 s. Contêiner (`limits.sh`): página do vizinho 26 → 159 ms sem limites, 26 → 26 ms com |
| Memória com muitos sites | lsphp de site ocioso sai (`php_idle`, padrão 30 min) e volta na próxima visita | Saída e volta conferidas no itzvpc2 |

## Ressalvas

- **Recursos por cliente: o que fica de fora.** O limite vale para o PHP, o SSH e o cron do usuário. As consultas
  que o site faz ao MySQL contam no `mysql.service` (o LVE também precisa do MySQL Governor para isso), e o OLS, que
  serve os estáticos, é um só para todos. Os padrões (`limit_cpu 200%`, `limit_mem 2G`, `limit_io 50M`) são um ponto de
  partida: ajuste pelo plano de cada cliente. Precisa de cgroups v2 (não há no AlmaLinux 8).
- **Pouco tempo de uso real.** O itzvpc2 roda assim desde 2026-11-07, com sites de teste. Recomendado: piloto de 2 a
  4 semanas num servidor com clientes reais, com monitoramento externo (uptime e tempo de resposta), antes de migrar
  os demais.
- **Manutenção das dependências.** Conferido em 2026-11-08: o fork está na última versão do OLS (1.9.4, sem commits
  novos no original), o CRS na 4.30.0 e as exclusões do WordPress na 1.2.0. A libmodsecurity do Ubuntu parou na
  3.0.14, sem as correções da 3.0.15 à 3.0.17 (estouros de memória no multipart e no `hex_decode`, desvios do WAF);
  o pacote agora traz a 3.0.17, compilada para o Ubuntu 24.04 e para o 26.04, em `/usr/local/lsws/modsec/lib`. O
  `error.log` do OLS ainda anuncia "libmodsecurity v3.0.15": é texto fixo do código original. A versão carregada se
  vê em `grep libmodsecurity /proc/$(cat /tmp/lshttpd/lshttpd.pid)/maps`. A cada nova versão do OLS, do CRS ou da
  libmodsecurity: trazer, recompilar (`htaccess-compat/docker/ols.Dockerfile`, versão e SHA-256 no topo), rodar
  `htaccess-compat/compare/all.sh` e `panel/test/replace/run.sh` e atualizar com `install.sh --upgrade`. Quatro
  correções nossas aguardam revisão no projeto original (PRs 548 a 551).
- **DirectAdmin** não foi testado.
- **Saída para a internet bloqueada.** Na subida, o OLS baixa a lista de IPs do QUIC.cloud e espera até 10 s se não
  conseguir; a instalação já espera 30 s por isso.
- **Diferenças conhecidas do Apache:** charset em `AddType`, `Options FollowSymLinks`/`MultiViews` ignorados,
  `php_admin_value` em `.htaccess` ignorado (o Apache dá 500). Lista completa em
  [`../doc/htaccess-compat/DESIGN.md`](../doc/htaccess-compat/DESIGN.md).
- **Configure Server → Web Server do HestiaCP:** trocar ali o servidor web enquanto a ponte está ativa não é suportado;
  use a página OpenLiteSpeed.

## Antes de pôr clientes

1. Instalar no modo `offset`, rodar `olsbridge compare` com os caminhos importantes dos sites e só então `replace`.
2. Ligar `waf` em `detect` por alguns dias, olhar os eventos (`v-list-sys-litespeed-waf`) e só então `block`.
3. Conferir que o firewall do HestiaCP tem a regra HTTP3 (UDP 443) se `http3` estiver ligado.
4. Conferir os limites de cada plano (CPU, memória) e olhar `olsbridge limits` nos primeiros dias: muita CPU
   freada ou mortes por falta de memória num cliente pedem plano maior para ele.
5. Monitorar por fora: tempo de resposta e disponibilidade de alguns sites, e o `journalctl -u olsbridge`.
6. Plano de volta: `olsbridge uninstall` devolve Apache, nginx e PHP-FPM em segundos.
