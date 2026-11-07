# Compatibilidade nativa com `.htaccess` no OpenLiteSpeed

Status: Fases 0 a 3 implementadas, Fases 4 e 5 parciais; ver a seção 11. Base: `master` em `031b2c8` (2026-11-01).

## 1. Objetivo

Fazer um site vindo do Apache funcionar no OpenLiteSpeed (OLS) sem converter regras e
sem reiniciar o servidor quando um `.htaccess` mudar.

### O que "100%" significa aqui

Compatibilidade literal com todo o Apache não é alcançável: o `.htaccess` aceita diretivas de
qualquer módulo carregado, inclusive de terceiros. Nem o LiteSpeed Enterprise promete isso; a
própria LiteSpeed fala em "majority of Apache directives".

A meta verificável é:

- cobrir todas as diretivas permitidas em `.htaccess` nos módulos padrão do Apache 2.4 usados
  por aplicações reais;
- publicar uma matriz de compatibilidade gerada pelos testes;
- medir a compatibilidade comparando respostas com um Apache real (seção 6).

## 2. O que o código faz hoje

| Fato | Onde |
|---|---|
| `.htaccess` só é lido quando o contexto do diretório nasce. Não há nova leitura depois. | `src/http/httpvhost.cpp:748` |
| O da raiz é carregado na configuração da vhost, não por requisição. | `src/http/httpvhost.cpp:2997` |
| O parser só entende `RewriteCond`, `RewriteRule`, `RewriteBase`, `RewriteEngine`, `RewriteFile` e `CacheKeyModify`. O resto vira log `Invalid rewrite directive`. | `src/http/rewriteengine.cpp:225`, `:310` |
| Blocos `<IfModule mod_headers...>` são pulados de propósito. | `src/http/rewriteengine.cpp:191` |
| Diretório inexistente vira contexto nulo permanente. Se for criado depois, seu `.htaccess` não é lido. | `src/http/httpvhost.cpp:732`, `:684` |
| Rewrite de contexto roda **antes** da autenticação. No Apache, o rewrite por diretório roda depois. | `src/http/httpsession.cpp:6226` em diante |

### Boa notícia: o destino já existe

`HttpContext` já guarda quase tudo o que o Apache define por diretório (`src/http/httpcontext.h:184`):
autenticação, ACL, índice, charset, `ForceType`, MIME, PHP, páginas de erro, `FilesMatch`,
operações de cabeçalho e `Expires`. `releaseHTAConf()` também já existe (`httpcontext.cpp:156`).

`php_value` chega ao LSAPI pelo contexto (`src/extensions/lsapi/lsapireq.cpp:148`). Basta preencher
`PHPConfig` a partir do `.htaccess`; não é preciso reinventar o transporte até o PHP.

## 3. Decisão: núcleo, não módulo LSIAPI puro

A API de módulos (`include/ls.h`) permite alterar cabeçalhos, ambiente, URI, status e MIME, e tem
hooks úteis. Ela **não** permite trocar as regras de rewrite de um contexto nem o `PHPConfig`
usado na montagem da requisição LSAPI.

Um módulo puro teria de reimplementar rewrite, autenticação e ACL por fora, e `php_value` ficaria
de fora. A documentação da CyberPanel aponta na mesma direção: o `cyberpanel_ols.so` exige o
binário próprio deles.

**Proposta:** código novo em `src/htaccess/`, ligado ao núcleo por um patch pequeno e isolado, para
facilitar o rebase sobre o upstream. Recursos independentes, como proteção contra força bruta,
ficam como módulos LSIAPI normais.

## 4. Arquitetura

```
.htaccess --> Lexer/Parser --> AST --> Aplicador --> HttpContext
                 (puro)       (cache)   (por tipo)     (já existente)
                                           ^
Monitor de mudança (stat + TTL) -----------+
```

### 4.1 Parser

- Isolado do servidor e testável sem subir o OLS.
- Sem `ConfigCtx` em tempo de execução: o parse acontece com requisições em andamento.
- Trata continuação de linha, aspas, comentários, blocos aninhados e `<IfModule>`, `<IfDefine>`,
  `<IfVersion>`, `<Files>`, `<FilesMatch>`, `<Limit>`, `<LimitExcept>` e `<If>`.
- Limites de tamanho e profundidade: o arquivo é controlado pelo inquilino.

### 4.2 Recarregamento a quente

É a parte mais arriscada do projeto.

**Detecção.** Em cada requisição, cada diretório do caminho faz `stat` no `.htaccess`, com TTL
configurável (padrão proposto: 1 s). A comparação usa `mtime`, tamanho e inode, e detecta arquivo
criado, alterado e removido. Cada worker tem sua árvore de contextos, então cada um detecta sozinho.

**Propagação.** Atualizar só o contexto alterado está errado, por três motivos:

1. Sem `BIT_CTXINT`, o filho aponta para o `m_pInternal` do pai (`httpcontext.cpp:607`). Se o pai
   trocar de bloco ou liberar dados, o filho fica desatualizado ou com ponteiro inválido.
2. `inherit()` não é idempotente: `PHPConfig`, cabeçalhos, MIME e `FilesMatch` fazem merge sobre o
   próprio estado (`httpcontext.cpp:659`, `:686`). Chamar de novo acumula valores.
3. Os ponteiros herdados são copiados por valor, então os descendentes também precisam ser refeitos.

Regra adotada: a AST é a fonte da verdade. Na mudança, percorre-se a subárvore de cima para baixo:
limpar o que o `.htaccess` definiu, reaplicar a AST e executar `inherit()`.

**Tempo de vida.** Requisições assíncronas seguram `const HttpContext *`, e PHP e autenticação
externa atravessam eventos. O `HttpContext` nunca é destruído; os dados antigos vão para uma lista
de descarte, liberada depois do maior tempo de vida de uma requisição, ou por contagem de referência.

**Erro de sintaxe.** O Apache devolve 500 no diretório. Isso vira modo configurável:

| Modo | Comportamento | Uso |
|---|---|---|
| `compat` (padrão) | Diretiva desconhecida gera aviso e é ignorada | Migração plug and play |
| `strict` | Igual ao Apache: 500 | Paridade total |
| `off` | Comportamento original do OLS | Reverter por vhost |

### 4.3 Segurança

O `.htaccess` é entrada não confiável em hospedagem compartilhada.

- Implementar `AllowOverride` e `AllowOverrideList` de verdade, por vhost.
- Recusar `php_admin_value` e `php_admin_flag` no `.htaccess`, como o Apache e o PHP fazem. Aceitá-los
  deixaria o inquilino sobrescrever limites do administrador.
- `AuthUserFile`: abrir o arquivo com as permissões do usuário da vhost, nunca como root.
- `RewriteRule [P]`: desativado por padrão no `.htaccess`, para evitar SSRF.
- Manter a proteção existente das variáveis internas, como `modsecurity`
  (`src/http/rewriteengine.cpp:1281`).
- Impedir que `.htaccess` e `.htpasswd` sejam servidos.

## 5. Cobertura de diretivas

**Só mapeamento, o destino já existe:** `Rewrite*`, `Header`, `RequestHeader`, `php_value`, `php_flag`,
`Expires*`, `ErrorDocument`, `DirectoryIndex`, `AddType`, `ForceType`, `AddDefaultCharset`,
`AuthType Basic`, `AuthName`, `AuthUserFile`, `Satisfy`, `<Files>`, `<FilesMatch>`, `FileETag`,
`Options Indexes` e `Includes`, `XBitHack`.

**Exige código novo:** `SetEnv`, `SetEnvIf`, `BrowserMatch`, `Redirect`, `RedirectMatch`, semântica de
`Order`/`Allow`/`Deny`, `Require` 2.4 (`all`, `ip`, `host`, `not`, `RequireAny`, `RequireAll`), `<Limit>`,
`<LimitExcept>`, `<If>`, `AddHandler`, `SetHandler`, `RemoveType`, `AddEncoding`, `AddOutputFilterByType`,
`Options MultiViews` e `FollowSymLinks`, `IndexIgnore`, `AuthGroupFile`.

A lista precisa ser confirmada na Fase 2: a existência do destino não garante paridade fina.

**Atenção a mudança de comportamento:** blocos `<IfModule mod_headers.c>` hoje ignorados passarão a
valer. Sites que rodam bem no OLS podem mudar de comportamento, por isso existe o modo `off`.

## 6. Testes: comparação com Apache real

O critério de pronto não é "parece funcionar".

1. Um contêiner com Apache httpd 2.4 e outro com o OLS modificado usam o mesmo docroot.
2. Um corpus de `.htaccess` reais (WordPress, Laravel, Drupal, Joomla, Nextcloud, HTML5 Boilerplate,
   WooCommerce) e casos sintéticos por diretiva é executado nos dois.
3. Status, `Location`, cabeçalhos e corpo são comparados. As diferenças viram a matriz de compatibilidade.

Também entram:

- testes unitários do parser, no padrão de `test/http/`;
- testes de hot reload sob carga com ASan e TSan (já há suporte em `test/asan_run` e
  `thread_sanitizer_*`);
- benchmark contra o OLS original, para medir o custo do `stat`.

## 7. Fases

| Fase | Entrega | Critério de pronto |
|---|---|---|
| 0. Fundação | Branch, ambiente Linux, build e testes de base, harness Apache x OLS | OLS original compila e o harness roda |
| 1. Hot reload | Monitor e propagação, ainda só com rewrite | Criar, editar e apagar `.htaccess` funciona sob carga, sem erro de ASan |
| 2. Parser e mapeamento | Parser completo e diretivas com destino existente, incluindo `php_value` | WordPress e Laravel passam na comparação |
| 3. Diretivas novas | `Require`, `Redirect`, `SetEnvIf`, `<If>` etc. | Corpus inteiro com divergências documentadas |
| 4. Drop-in Apache | Importar `httpd.conf` e vhosts, ferramenta de migração, integração com painéis | Site migra sem edição manual |
| 5. Recursos "Pro" | Força bruta, ESI, handshake SSL assíncrono, cache para outras plataformas, upgrade sem downtime | Avaliados um a um |

A Fase 1 vem antes do parser porque concentra o maior risco. Se a propagação não ficar estável,
é melhor descobrir antes de escrever centenas de diretivas sobre ela.

A Fase 5 é grande por si só: ESI, por exemplo, muda o módulo de cache. Cada item deve ter design próprio.

## 8. Licença

- O OLS é GPLv3: quem distribuir o binário modificado precisa oferecer o código-fonte.
- Código do Apache httpd (Apache-2.0) pode ser aproveitado em GPLv3, mantendo os avisos de autoria.
- Não usar engenharia reversa do `cyberpanel_ols.so` nem do LiteSpeed Enterprise. Documentação pública
  serve de referência de recursos; o código é nosso.
- Dar nome próprio ao projeto, para não sugerir vínculo com as marcas LiteSpeed ou CyberPanel.

O uso previsto é em servidores próprios, sem venda, com o repositório público. Rodar em servidores
próprios não obriga a publicar nada, e deixar o código aberto já atende à GPLv3 para quem baixar.

Este item não é aconselhamento jurídico.

## 9. Repositório e atualização a partir do upstream

Fork público de `litespeedtech/openlitespeed`, com estes papéis:

| Branch ou referência | Papel | Regra |
|---|---|---|
| `master` | Espelho do `master` do upstream | Nunca recebe commit nosso |
| `v1_9` | Espelho da linha estável do upstream | Nunca recebe commit nosso |
| `feature/htaccess-compat` | Nosso desenvolvimento | Branch padrão do fork |
| Tags `vX.Y.Z-htaccess.N` | Versões que vão para os servidores | Só depois do CI passar |

Fluxo diário, via GitHub Actions:

1. Buscar o upstream e atualizar os espelhos.
2. Se houver tag nova, criar `sync/<tag>` e fazer merge sobre o nosso código.
3. Compilar, rodar os testes e a comparação com o Apache.
4. Com tudo verde, abrir PR marcado como pronto. Com conflito ou falha, abrir issue com o log.

Decisões:

- **Merge, não rebase.** O repositório é público: rebase reescreve o histórico de quem clonou.
- **Seguir tags, não `master`.** O `master` recebe mudanças ainda não lançadas; para produção, tags
  estáveis são mais seguras. Isso é um ponto a validar observando o ritmo do upstream. Hoje o nosso
  branch parte de `031b2c8`, 45 commits depois de `v1.9.4`, com `master` e `v1_9` idênticos.
- **Atualização automática até o PR.** A sincronização nunca publica nos servidores sozinha. Correção de
  segurança do upstream deve receber prioridade, porque o nosso binário só a recebe depois do merge.
- **Nosso código isolado.** Quanto menor o patch nos arquivos do upstream, menos conflitos.

Cuidados do GitHub Actions: em fork, os workflows precisam ser ativados manualmente; agendamentos só
rodam a partir do branch padrão; e em repositório público ficam desativados após 60 dias sem atividade.

## 10. Resultado da Fase 0

Ambiente: Docker sobre WSL 2, Ubuntu 24.04, GCC 13, OLS 1.9.4 (`031b2c8`). ModSecurity, Lua e PageSpeed
ficaram fora do build. Comandos em [`htaccess-compat/README.md`](../../htaccess-compat/README.md).

**Testes do upstream:** `ols_unittest` e `extensions_cgidcopytest` passaram em build Debug com ASan.
Os testes de Node não entraram, porque a imagem não tem Node. O upstream desativa a detecção de vazamentos
(`detect_leaks=0`) nesses testes.

**Comparação com o Apache:** 31 de 62 requisições idênticas, em 36 casos. Relatório em
[`htaccess-compat/compare/BASELINE.md`](../../htaccess-compat/compare/BASELINE.md).

### Risco de segurança na migração

Regras de acesso do Apache são ignoradas em silêncio pelo OLS original. Arquivos protegidos ficam públicos:

| Diretiva no `.htaccess` | Apache | OLS original |
|---|---|---|
| `Require all denied`, `Require ip` | 403 | 200 |
| `Order Deny,Allow` + `Deny from all` | 403 | 200 |
| `<FilesMatch>` com `Require all denied` | 403 | 200 |
| `<LimitExcept GET>` em `POST` | 403 | 200 |
| `AuthType Basic` | 401 | 200 |

Isso muda a prioridade: controle de acesso deve vir antes de cabeçalhos e demais diretivas nas Fases 2 e 3.
Enquanto não estiver pronto, o modo `compat` não pode ignorar essas diretivas: precisa negar o acesso.

### Vazamento de memória no OLS original

`HttpVHost::bestMatch` cria um contexto permanente para cada diretório inexistente pedido, e esse caminho
não depende de `autoLoadHtaccess` (`src/http/httpvhost.cpp:724`). Medição com 8 workers:

| Carga | Memória total |
|---|---|
| 100 mil URLs aleatórias, diretórios inexistentes | +68 MB, crescimento linear (cerca de 680 bytes por requisição) |
| 100 mil requisições à mesma URL inexistente | +4 KB |

Qualquer cliente anônimo pode provocar isso. A Fase 1 precisa limitar esses contextos, porque o recarregamento
usa a mesma árvore. A correção também interessa ao upstream.

### Demais pendências

- `configRewriteRule` usa `ConfigCtx::getCurConfigCtx()` durante requisições (`httpcontext.cpp:1307`). Não houve
  falha nos casos executados, mas o parser novo não deve depender disso.
- Rodar a comparação com PHP, necessária para `php_value` e `RequestHeader`, que não podem ser observados só
  com arquivos estáticos.
- Links simbólicos: no Windows, o Git os grava como texto. O Dockerfile os restaura a partir do índice.
- `build.sh` usa `CUR_PATH` sem defini-lo. O Dockerfile define a variável; o defeito é do upstream.

## 11. Estado atual

### Resultado

| Verificação | Resultado |
|---|---|
| Arquivos estáticos | 99 de 100 requisições iguais ao Apache (OLS original: 31 de 62, num corpus menor) |
| PHP | 21 de 21 iguais ao Apache com mod_php |
| Recarregamento | Todos os cenários, sob carga e sem erros |
| Política | 33 de 33 |
| Memória | 100 mil URLs inexistentes: 0,5 MB, contra 68 MB no OLS original |
| ASan | Nenhum erro, incluindo a liberação de contextos antigos sob carga |

A divergência restante é o `charset` da página gerada por `ErrorDocument` com texto.

Na comparação, `a,b` e `a, b` contam como iguais, assim como `public,max-age=60` e `max-age=60`.

Desempenho: cerca de 26 mil req/s com `off`, `compat` e verificação a cada requisição, sem diferença mensurável. O
cliente `ab` pode ser o limite nesse patamar, então isso não prova ausência de custo em cargas maiores.

### Como ficou diferente do plano

- **Contextos são substituídos, não atualizados.** O plano previa reaplicar a AST sobre os contextos. Foi mais simples
  e seguro descartar os contextos dinâmicos afetados e recriá-los na requisição seguinte.
- **Contextos configurados não são alterados.** Um contexto dinâmico com a mesma URI os sobrepõe, então apagar o
  `.htaccess` restaura a configuração original.
- **Ordem do rewrite:** o acesso é verificado antes dele, como no Apache. O redirecionamento de `Redirect` ocorre depois.

### Limitações conhecidas

| Tema | Comportamento |
|---|---|
| Nomes de host em `Allow`, `Deny` e `Require host` | Não são resolvidos, pois isso bloquearia o servidor. Nunca casam, e o log avisa. **`Deny from dominio` não bloqueia ninguém**: use endereços |
| Vários `<Files>` no mesmo arquivo | Todos precisam permitir. O Apache deixa o último vencer, então aqui é mais restritivo |
| `php_admin_value`, `php_admin_flag` | Ignorados com aviso. O Apache responde 500 |
| `Header edit`, `echo`, `note`, `setifempty` | Ignorados com aviso |
| `AuthType Digest`, `Require expr` | Sem suporte: o acesso é negado ou responde 500 |
| `<If>` | Só `Require`, `Header`, `SetEnv*`, `BrowserMatch*` e `Redirect*`, com subconjunto das expressões |
| `AddHandler` | Só tipos PHP |
| `Options` | Só `Indexes`. `FollowSymLinks`, `MultiViews` e `ExecCGI` são ignorados |
| `AddOutputFilter*`, `Deflate*` | Ignorados, pois o OLS comprime por conta própria |
| Contexto configurado abaixo de um `.htaccess` | Não herda esse `.htaccess` |
| `lsphp` no contêiner de teste | Roda à parte, porque o `cgid` não conseguiu iniciá-lo no Docker. Não foi investigado |

### Estado das Fases 4 e 5

| Item | Estado |
|---|---|
| Conversor de vhosts do Apache (`htaccess-compat/tools/apache2ols.py`) | Pronto para domínios, alias, SSL, rewrite da vhost, `DirectoryIndex`, `ErrorDocument` e `AllowOverride`. O resto vai para `avisos.txt`. Testado num OLS real com um exemplo; SSL não foi testado com certificado real |
| Proteção contra força bruta no WordPress | Pronta: `wpProtectLimit`, `wpProtectWindow`, `wpProtectBlock`. Bloqueia com 429; não há atraso progressivo nem lista de IPs confiáveis |
| Leitura direta do `httpd.conf` em execução | Não feita. O conversor gera a configuração uma vez, sem acompanhar mudanças |
| Plugins para cPanel e Plesk | Não feitos |
| ESI | Não feito. Exige mudanças no módulo de cache |
| Handshake SSL assíncrono | Não feito |
| Cache para Magento, PrestaShop e Shopware | Não feito |
| Atualização do servidor sem downtime | Não feita |

### Pendências

- Corpus maior, com arquivos `.htaccess` reais completos de plugins e aplicações.
- Teste em servidor real, com tráfego real, antes de usar em produção.
- A sincronização com o upstream só foi exercitada sem versão nova: o caminho de merge, PR e conflito ainda não rodou.
- Acompanhar o que foi enviado à LiteSpeed em 2026-11-07: o `env=` ignorado em `Header` está na
  [PR 548](https://github.com/litespeedtech/openlitespeed/pull/548), e o vazamento de contextos foi relatado em privado.
  Regras de `Files` puladas pelo cache de URL não foram enviadas: sem o suporte a `.htaccess` deste fork, o OLS só tem
  regras de `Files` que negam tudo ou forçam o tipo, então um arquivo bloqueado nunca entra no cache.
