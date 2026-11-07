# Ambiente de desenvolvimento do suporte a `.htaccess`

Design e resultados: [`doc/htaccess-compat/DESIGN.md`](../doc/htaccess-compat/DESIGN.md).

Requer Docker. Execute a partir da raiz do repositório.

## Compilar o OLS

```bash
docker build -f htaccess-compat/docker/ols.Dockerfile --target runtime -t ols-htaccess:runtime .
```

## Rodar os testes do upstream

Build Debug com ASan.

```bash
docker build -f htaccess-compat/docker/ols.Dockerfile --target test -t ols-htaccess:test .
```

```bash
docker run --rm --cap-add SYS_PTRACE ols-htaccess:test
```

## Rodar todas as verificações

Gere as imagens `runtime` e `php`:

```bash
docker build -f htaccess-compat/docker/ols.Dockerfile --target runtime -t ols-htaccess:runtime .
```

```bash
docker build -f htaccess-compat/docker/ols.Dockerfile --target php -t ols-htaccess:php .
```

Depois execute:

```bash
sh htaccess-compat/compare/all.sh
```

| Verificação | Arquivo | O que faz |
|---|---|---|
| Comparação estática | [`compare/corpus.py`](compare/corpus.py) | Mesmas requisições no Apache e no OLS |
| Comparação com PHP | [`compare/corpus_php.py`](compare/corpus_php.py) | Idem, com mod_php e LSAPI |
| Recarregamento | [`compare/reload.py`](compare/reload.py) | Edita arquivos em uso, também sob carga |
| Política | [`compare/policy.py`](compare/policy.py) | Modos, `AllowOverride` e negação em caso de dúvida |

`all.sh` falha se o número de requisições iguais ao Apache cair abaixo de
[`compare/LATEST.md`](compare/LATEST.md) e [`compare/LATEST-PHP.md`](compare/LATEST-PHP.md). Ao melhorar a
compatibilidade, atualize esses arquivos. [`compare/BASELINE.md`](compare/BASELINE.md) guarda o resultado do OLS
original, para referência.

## Rodar com ASan

```bash
docker build -f htaccess-compat/docker/ols.Dockerfile --target asan -t ols-htaccess:asan .
```

```bash
OLS_IMAGE=ols-htaccess:asan LS_HTA_GRAVE_TICKS=1 sh htaccess-compat/compare/all.sh
```

`LS_HTA_GRAVE_TICKS=1` libera os contextos antigos em até 1 minuto, em vez de até 1 hora. Só existe no build Debug.

## Medir memória com diretórios inexistentes

`compare/memprobe.py` envia URLs aleatórias, ou uma URL fixa com `--fixed`, como controle.

## Configuração

No bloco `rewrite` da vhost:

| Chave | Valores | Padrão | Efeito |
|---|---|---|---|
| `autoLoadHtaccess` | `0`, `1` | `0` | Liga a leitura de `.htaccess` |
| `htaccessMode` | `compat`, `strict`, `off` | `compat` | `strict` responde 500 a diretivas desconhecidas; `off` mantém o comportamento original |
| `htaccessCheckInterval` | segundos, `0` a `3600` | `1` | Intervalo mínimo entre verificações de cada diretório |
| `htaccessAllowOverride` | `All`, `None` ou lista | `All` | Como o `AllowOverride` do Apache |
| `htaccessAllowProxy` | `0`, `1` | `0` | Permite `RewriteRule [P]` no `.htaccess` |
