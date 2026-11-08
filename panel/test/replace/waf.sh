#!/bin/sh
# WAF por site (ESPEC-WAF-PONTE do ERP): eventos, exclusões, modo, persistência,
# recusas e dados sensíveis. Roda depois de cycle.sh, com o OLS no lugar.
set -u
IP=$(hostname -i | awk '{print $1}')
B=/usr/local/lsws/bin/olsbridge
status=0
ok() { echo "[ok] $1"; }
fail() { echo "[FALHOU] $1"; status=1; }
expect() {
    if [ "$3" = "$2" ]; then ok "$1: $3"; else fail "$1: '$3', esperado '$2'"; fi
}
code() {  # host caminho [opções do curl]
    h=$1 p=$2; shift 2
    curl -s -o /dev/null -w '%{http_code}' -H "Host: $h" "$@" "http://$IP$p"
}
passes() {  # passa = não barrado pelo WAF
    c=$(code "$@"); [ "$c" != 403 ] && echo passa || echo "barrado ($c)"
}
json() {  # domínio expressão-python sobre d
    $B waf-site show "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print($2)"
}
ATK="/?id=1%27%20OR%20%271%27=%271"

$B set waf block >/dev/null 2>&1 || fail "set waf block"
sleep 2

# 1. Ataque bloqueado e listado no site, com a regra 942xxx.
expect "1. ataque em alpha.test" 403 "$(code alpha.test "$ATK")"
sleep 1
expect "1. evento listado com 942xxx e blocked" True "$(json alpha.test \
    "any(e['blocked'] and any(r['id'].startswith('942') for r in e['rules']) for e in d['events'])")"
expect "1. regras de pontuação fora da lista" False "$(json alpha.test \
    "any(r['id'][:3] in ('949','980') for e in d['events'] for r in e['rules'])")"
expect "1. evento de alpha não aparece em beta" 0 "$(json beta.test "len(d['events'])")"

# 2. Exclusão de 942100 só em /api, em alpha.test.
id=$($B waf-site add alpha.test 942100 /api "Webhook de teste")
case "$id" in ex_????????) ok "2. id da exclusão: $id" ;; *) fail "2. id: '$id'" ;; esac
expect "2. ataque em alpha.test/api/x" passa "$(passes alpha.test "/api/x?id=1%27%20OR%20%271%27=%271")"
expect "2. ataque em www.alpha.test/api/x (alias)" passa \
    "$(passes www.alpha.test "/api/x?id=1%27%20OR%20%271%27=%271")"
expect "2. ataque em alpha.test/outra" 403 "$(code alpha.test "/outra?id=1%27%20OR%20%271%27=%271")"
expect "2. ataque em alpha.test/apiary (não é /api)" 403 \
    "$(code alpha.test "/apiary?id=1%27%20OR%20%271%27=%271")"
expect "2. ataque em /api/../outra" 403 \
    "$(code alpha.test "/api/../outra?id=1%27%20OR%20%271%27=%271" --path-as-is)"
expect "2. ataque em beta.test/api/x" 403 "$(code beta.test "/api/x?id=1%27%20OR%20%271%27=%271")"
expect "2. exclusão listada" "942100 /api Webhook de teste" "$(json alpha.test \
    "' '.join([d['exclusions'][0][k] for k in ('rule','path','note')])")"

# 3. alpha.test em detect: registra sem bloquear; beta.test segue bloqueando.
$B waf-site mode alpha.test detect || fail "3. mode detect"
expect "3. ataque em alpha.test/outra (detect)" passa \
    "$(passes alpha.test "/outra3?id=1%27%20OR%20%271%27=%271")"
sleep 1
expect "3. evento com blocked false" False "$(json alpha.test \
    "[e['blocked'] for e in d['events'] if e['uri'].startswith('/outra3')][0]")"
expect "3. modo e efetivo" "detect detect block" "$(json alpha.test \
    "d['mode']+' '+d['effective']+' '+d['server_mode']")"
expect "3. beta.test segue bloqueando" 403 "$(code beta.test "$ATK")"

# 4. Sobrevive a sync e a restart; some ao apagar o domínio.
$B sync >/dev/null 2>&1
systemctl restart olsbridge
sleep 3
expect "4. depois de sync e restart" "detect $id" "$(json alpha.test \
    "d['mode']+' '+d['exclusions'][0]['id']")"
expect "4. detect segue valendo" passa "$(passes alpha.test "/outra4?id=1%27%20OR%20%271%27=%271")"

# 5. Recusas com o código do painel.
$B waf-site add alpha.test 949110 >/dev/null 2>&1; expect "5. regra 949110" 2 $?
$B waf-site add alpha.test 901100 >/dev/null 2>&1; expect "5. regra 901100" 2 $?
$B waf-site add alpha.test 12345 >/dev/null 2>&1; expect "5. regra fora da faixa" 2 $?
$B waf-site add alpha.test 942100 "/a;b" >/dev/null 2>&1; expect "5. caminho com ;" 2 $?
$B waf-site add alpha.test 942100 "/a b" >/dev/null 2>&1; expect "5. caminho com espaço" 2 $?
$B waf-site add alpha.test 942100 /x 'nota "com" aspas' >/dev/null 2>&1; expect "5. motivo com aspas" 2 $?
$B waf-site add alpha.test 942100 /api >/dev/null 2>&1; expect "5. exclusão repetida" 4 $?
$B waf-site add gamma.test 942100 >/dev/null 2>&1; expect "5. domínio que não existe" 3 $?
$B waf-site delete alpha.test ex_00000000 >/dev/null 2>&1; expect "5. id que não existe" 3 $?
$B waf-site mode alpha.test bloquear >/dev/null 2>&1; expect "5. modo inválido" 2 $?
$B waf-site show alpha.test --hours 169 >/dev/null 2>&1; expect "5. 169 horas" 2 $?

# 6. Nada de corpo, cookie ou Authorization na saída, nem com o ataque na senha.
$B waf-site mode alpha.test block >/dev/null
code alpha.test /login -X POST -H "Authorization: Bearer xyzsegredo" -H "Cookie: sess=biscoito" \
    --data "password=1%27%20OR%20%271%27=%271%20segredo&q=1%27%20OR%20%271%27=%271" >/dev/null
code alpha.test "/conta?token=tok%27%20OR%20%271%27=%271&id=1%27%20OR%20%271%27=%271" >/dev/null
sleep 1
out=$($B waf-site show alpha.test; $B waf-log)
for secret in segredo xyzsegredo biscoito "tok'"; do
    if echo "$out" | grep -q "$secret"; then fail "6. '$secret' apareceu na saída"
    else ok "6. '$secret' fora da saída"; fi
done
expect "6. evento do POST listado" True "$(json alpha.test \
    "any(e['method']=='POST' and e['uri']=='/login' for e in d['events'])")"
expect "6. token da URL mascarado" True "$(json alpha.test \
    "any(e['uri'].startswith('/conta?token=***') for e in d['events'])")"

# Servidor inteiro: o domínio em cada evento e o resumo por hora.
expect "waf-log com domínio e por hora" True "$($B waf-log | python3 -c "
import json,sys; d=json.load(sys.stdin)
print({'alpha.test','beta.test'} <= {e['domain'] for e in d['events']} and bool(d['summary']['by_hour']))")"

# Exclusão: apagar devolve o bloqueio.
$B waf-site delete alpha.test "$id" || fail "delete"
expect "exclusão apagada: /api/x volta a ser barrado" 403 \
    "$(code alpha.test "/api/x?id=1%27%20OR%20%271%27=%271")"

# Limite de 50 exclusões por site (beta.test).
n=0
for r in $(seq 941100 941149); do
    $B waf-site add beta.test "$r" >/dev/null 2>&1 && n=$((n + 1))
done
expect "50 exclusões aceitas" 50 "$n"
$B waf-site add beta.test 941150 >/dev/null 2>&1; expect "51ª exclusão" 8 $?

# Domínio apagado do painel: o JSON dele some no sync.
rm -f /etc/apache2/sites-enabled/beta.test.conf /etc/nginx/conf.d/domains/beta.test.conf
$B sync >/dev/null 2>&1
expect "domínio apagado: JSON removido" no "$([ -e /usr/local/lsws/conf/waf-sites/beta.test.json ] && echo yes || echo no)"

$B waf-site mode alpha.test inherit >/dev/null
$B set waf off >/dev/null 2>&1
exit $status
