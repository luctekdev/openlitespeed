# Matriz de compatibilidade Apache x OLS

Requisições idênticas: **99 de 100**.

| Grupo | Iguais | Total |
|---|---|---|
| aplicações reais | 14 | 14 |
| core | 24 | 25 |
| mod_access_compat | 5 | 5 |
| mod_alias | 8 | 8 |
| mod_auth_basic | 6 | 6 |
| mod_authz | 7 | 7 |
| mod_autoindex | 1 | 1 |
| mod_dir | 1 | 1 |
| mod_env | 2 | 2 |
| mod_expires | 2 | 2 |
| mod_headers | 9 | 9 |
| mod_mime | 1 | 1 |
| mod_rewrite | 10 | 10 |
| mod_setenvif | 2 | 2 |
| segurança | 7 | 7 |

## Divergências

### errordocument-text: `GET x`
- content-type: apache='text/html; charset=iso-8859-1' ols='text/html'
