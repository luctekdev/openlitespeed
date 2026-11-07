# Ponte Apache → OpenLiteSpeed para painéis

Faz o OpenLiteSpeed deste fork atender os sites que o HestiaCP ou o DirectAdmin configuram para o Apache, sem mudar
nada no painel. Plano e estado: [`doc/htaccess-compat/PAINEL.md`](../doc/htaccess-compat/PAINEL.md).

## Uso

Com o OLS do fork instalado em `/usr/local/lsws`, como root:

```bash
python3 panel/bridge/olsbridge.py install --mode offset
```

O modo `offset` deixa o Apache no ar e sobe o OLS nas mesmas portas somadas a 10000 (8080 vira 18080). Os logs do OLS
vão para `/usr/local/lsws/logs/offset/`, para não contar a banda em dobro. Depois de comparar os sites:

```bash
/usr/local/lsws/bin/olsbridge uninstall
```

```bash
/usr/local/lsws/bin/olsbridge install --mode replace
```

No modo `replace`, o serviço do Apache (`apache2` ou `httpd`) passa a iniciar, recarregar e parar o OLS, por um
drop-in em `/etc/systemd/system/<serviço>.service.d/zz-olsbridge.conf`. Se o OLS não subir, a instalação volta para o
Apache. `uninstall` remove o drop-in e religa o Apache.

Outros comandos: `olsbridge status`, `olsbridge generate` (gera e lista os avisos, sem recarregar) e `olsbridge sync`
(o que o timer roda a cada 30 segundos).

A configuração gerada fica em `/usr/local/lsws/conf/bridge/`, com `warnings.txt` listando o que não foi convertido.
Ajustes manuais do servidor vão em `/usr/local/lsws/conf/bridge/server-extra.conf`, que é preservado.

## O que é convertido

`Listen`, `User`, `Group`, `VirtualHost`, `ServerName`, `ServerAlias`, `DocumentRoot`, `Alias`, `ScriptAlias`,
`DirectoryIndex`, `ErrorDocument`, `AllowOverride` e `Options Indexes` do `<Directory>`, SSL com cadeia separada,
`RewriteRule` da vhost, `Header`, `CustomLog` e `ErrorLog` com o `LogFormat` do Apache, PHP-FPM por `SetHandler` ou
`AddHandler` `proxy:unix:` e `proxy:fcgi://`, e `RemoteIPHeader` com os proxies confiáveis. `Include`,
`IncludeOptional`, `Define`, `<IfModule>`, `<IfDefine>`, `<IfFile>` e `<IfVersion>` são avaliados como no Apache.

## Testes

```bash
docker build -f panel/test/hestia/Dockerfile -t olsbridge-hestia .
docker run --rm olsbridge-hestia
```

Monta Apache e PHP-FPM com os templates do HestiaCP e compara 43 respostas do Apache e do OLS lado a lado: HTTP,
HTTPS com SNI e cadeia, PHP, WordPress, `.htaccess`, aliases, páginas de erro e logs.

```bash
docker build -f panel/test/systemd/Dockerfile -t olsbridge-systemd .
sh panel/test/systemd/run.sh
```

Com systemd: troca o serviço `apache2`, cria e remove domínios como o painel faz, e volta para o Apache.

Os arquivos em `panel/test/hestia/fixtures/` vêm do [HestiaCP](https://github.com/hestiacp/hestiacp), commit
`3c52c6c`, sob GPLv3.
