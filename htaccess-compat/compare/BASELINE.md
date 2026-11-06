# Matriz de compatibilidade Apache x OLS

Requisições idênticas: **31 de 62**.

| Grupo | Iguais | Total |
|---|---|---|
| aplicações reais | 8 | 8 |
| core | 4 | 13 |
| mod_access_compat | 2 | 4 |
| mod_alias | 1 | 5 |
| mod_auth_basic | 2 | 4 |
| mod_authz | 1 | 3 |
| mod_autoindex | 1 | 1 |
| mod_dir | 0 | 1 |
| mod_env | 0 | 1 |
| mod_expires | 0 | 2 |
| mod_headers | 1 | 6 |
| mod_mime | 0 | 1 |
| mod_rewrite | 9 | 10 |
| mod_setenvif | 1 | 2 |
| segurança | 1 | 1 |

## Divergências

### rewrite-env-flag: `GET page.txt`
- x-foo: apache='bar' ols=None

### header-set: `GET page.txt`
- x-always: apache='1' ols=None
- x-frame-options: apache='SAMEORIGIN' ols=None
- x-list: apache='a, b' ols=None
- x-multi: apache='1, 2' ols=None

### header-set: `GET missing`
- x-always: apache='1' ols=None

### header-ifmodule: `GET page.txt`
- x-in-block: apache='yes' ols=None

### header-unset-merge: `GET page.txt`
- x-m: apache='a, b' ols=None

### header-env-condition: `GET page.txt User-Agent=curl/8`
- x-curl: apache='yes' ols=None

### expires-bytype: `GET page.txt`
- cache-control: apache='max-age=3600' ols=None
- expires: apache='Sat, 07 Nov 2026 00:11:52 GMT' ols=None

### expires-bytype: `GET /`
- cache-control: apache='max-age=600' ols=None
- expires: apache='Fri, 06 Nov 2026 23:21:52 GMT' ols=None

### redirect: `GET old`
- status: apache=301 ols=404
- location: apache='http://compat.test/c1794006712/redirect/new' ols=None

### redirect: `GET old/sub`
- status: apache=301 ols=404
- location: apache='http://compat.test/c1794006712/redirect/new/sub' ols=None

### redirect: `GET gone`
- status: apache=410 ols=404

### redirectmatch: `GET doc/a.html`
- status: apache=302 ols=404
- location: apache='http://compat.test/c1794006712/redirectmatch/v2/a' ols=None

### errordocument: `GET missing`
- cache-control: apache=None ols='private, no-cache, max-age=0'
- content-length: apache='11' ols='1249'
- pragma: apache=None ols='no-cache'
- corpo diferente

### errordocument-text: `GET x`
- cache-control: apache=None ols='private, no-cache, max-age=0'
- content-length: apache='6' ols='1240'
- content-type: apache='text/html; charset=iso-8859-1' ols='text/html'
- pragma: apache=None ols='no-cache'
- corpo diferente

### files-block: `GET page.txt`
- x-file: apache='yes' ols=None

### filesmatch-block: `GET page.txt`
- x-match: apache='yes' ols=None

### filesmatch-deny: `GET secret.txt`
- status: apache=403 ols=200

### if-expression: `GET page.txt User-Agent=a-bot`
- x-bot: apache='yes' ols=None

### forcetype: `GET data`
- content-type: apache='text/csv' ols=None

### adddefaultcharset: `GET page.txt`
- content-type: apache='text/plain; charset=UTF-8' ols='text/plain'

### options-noindexes: `GET sub/`
- status: apache=403 ols=404

### addtype: `GET a.foo`
- content-type: apache='application/x-custom' ols='application/octet-stream'

### directoryindex: `GET /`
- content-length: apache='5' ols='6'
- corpo diferente

### setenv: `GET page.txt`
- x-env: apache='production' ols=None

### browsermatch: `GET page.txt User-Agent=MSIE 6`
- x-old: apache='yes' ols=None

### require-all-denied: `GET page.txt`
- status: apache=403 ols=200

### require-ip: `GET page.txt`
- status: apache=403 ols=200

### order-deny: `GET page.txt`
- status: apache=403 ols=200

### limit-except: `POST page.txt`
- status: apache=403 ols=200

### basic-auth: `GET page.txt`
- status: apache=401 ols=200
- www-authenticate: apache='Basic realm="Area"' ols=None

### basic-auth-group: `GET page.txt`
- status: apache=401 ols=200
- www-authenticate: apache='Basic realm="Area"' ols=None
