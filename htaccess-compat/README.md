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

## Comparar com o Apache

Sobe Apache e OLS sobre o mesmo docroot, executa o corpus de [`compare/corpus.py`](compare/corpus.py) e grava
a matriz em `compare/report/report.md`.

```bash
cd htaccess-compat/compare && mkdir -p report && docker compose run --rm runner
```

Para encerrar os contêineres:

```bash
docker compose down -v
```

[`compare/BASELINE.md`](compare/BASELINE.md) guarda o resultado do OLS original, para acompanhar a evolução.

## Medir memória com diretórios inexistentes

`compare/memprobe.py` envia URLs aleatórias, ou uma URL fixa com `--fixed`, como controle.
