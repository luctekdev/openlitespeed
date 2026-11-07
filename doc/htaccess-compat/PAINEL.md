# Plugin para HestiaCP e DirectAdmin

Status: em desenvolvimento, iniciado em 2026-11-07. A Fase 1 funciona com os templates do HestiaCP em container; nada
foi testado com os painéis instalados. Uso e testes: [`panel/README.md`](../../panel/README.md).

### Resultados da Fase 1 (2026-11-07)

| Teste | Resultado |
|---|---|
| Apache e OLS lado a lado, templates do HestiaCP: 3 domínios, HTTP, HTTPS, PHP-FPM, WordPress, `.htaccess`, aliases, erros e cadeia SSL | 43 de 43 respostas iguais, nenhum aviso |
| Logs de acesso e de banda do domínio | Mesmo formato; banda (`%b`) igual. `%O` difere alguns bytes porque os cabeçalhos não são os mesmos |
| Troca do serviço `apache2` com systemd, domínio criado com `reload`, domínio removido sem `reload`, `configtest`, `restart`, `stop` e volta ao Apache | Tudo certo |

Achados que mudaram o desenho:

- O OLS grava os logs com o usuário dos processos, e o Apache como root. A ponte passa os arquivos de log para esse
  usuário a cada geração, inclusive depois do logrotate, que chama o `reload`.
- O pacote do OLS sobe o servidor no boot pelo script `lsws`. O systemd recusa como serviço um processo que não viu
  nascer, então a ponte desliga esse script e para a instância antes de subir a sua.
- No modo `offset`, Apache e OLS no mesmo arquivo de log contariam a banda em dobro; o OLS grava em outra pasta.

## 1. Objetivo

Trocar o Apache por este OpenLiteSpeed em servidores com HestiaCP ou DirectAdmin sem que o painel perceba. O painel
continua gerando a configuração do Apache e mandando recarregar o serviço do Apache. Quem atende os sites é o OLS.

Referência: o plugin da LiteSpeed para cPanel e o [release log dos plugins](https://www.litespeedtech.com/products/litespeed-web-server/control-panel-support/release-log).

Fica de fora o que não se aplica: LiteSpeed Containers, licenças e QUIC.cloud.

## 2. Como funciona

```
painel ──escreve──▶ config do Apache ──lida pela──▶ ponte ──gera──▶ config do OLS
painel ──systemctl reload apache2/httpd──▶ unidade da ponte ──▶ gera de novo e faz restart gracioso do OLS
painel ──apache2ctl configtest──▶ binário real do Apache (continua instalado, nunca é iniciado)
```

- **Ponte** (`panel/bridge`): lê a configuração do Apache com `Include`, `IncludeOptional`, `Define` e `<IfModule>`, e gera
  listeners, vhosts, aliases, SSL, logs e o PHP de cada domínio. O que não sabe converter vai para um relatório de avisos.
- **Serviço disfarçado**: uma unidade em `/etc/systemd/system` com o nome do serviço do Apache (`apache2` ou `httpd`),
  que tem precedência sobre a do pacote. `start`, `stop`, `reload` e `restart` passam a comandar o OLS.
- **Desfazer**: remover a unidade devolve o Apache original. Nada do painel é alterado.

### Modos

| Modo | O que faz | Uso |
|---|---|---|
| `offset` | O Apache continua no ar e o OLS sobe nas mesmas portas somadas a um deslocamento | Comparar os dois no mesmo servidor antes de trocar |
| `replace` | O serviço do Apache passa a ser o OLS | Produção |

Nos dois modos um timer regenera a configuração se os arquivos do Apache mudarem sem um `reload`.

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
| Ponte, serviço disfarçado e modo `offset` | 1 |
| Restart, restart do PHP e versão instalada | 2 |
| Atualização a partir das versões do fork | 2 |
| Encerrar o PHP de contas suspensas (hooks dos painéis) | 2 |
| Cache de WordPress: descoberta, ativação em massa, marcar sites, versão do plugin | 3 |
| Aviso no painel de todos os WordPress (Dash Notifier) | 3 |
| Pasta do cache por servidor e por domínio | 3 |
| Redis por usuário, com padrão por plano e opção de Valkey | 4 |
| Avisos ao administrador: erros da instalação e versão nova | 2 |
| Interface no DirectAdmin; no HestiaCP, comandos e uma página própria | 5 |

## 5. Cuidados

- A parte que roda como root deve ser mínima e tratar links simbólicos com cuidado. O plugin da LiteSpeed teve uma falha
  em que um usuário do cPanel virava root por symlink.
- O OLS precisa rodar com o mesmo usuário e grupo do Apache, ou não consegue usar os sockets do PHP-FPM.
- Antes de trocar, conferir se o Apache está instalado e se a configuração gerada sobe; se não subir, manter o Apache.
- `php_value` no `.htaccess` dá erro 500 no Apache com PHP-FPM. O OLS deve se comportar igual.

## 6. Pronto quando

- Fase 1: domínios gerados com os templates reais do HestiaCP respondem igual no Apache e no OLS, e o ciclo
  `reload` do painel funciona num container com systemd.
- Antes de produção: os dois servidores reais, HestiaCP e DirectAdmin, em modo `offset` com sites de verdade.
