#!/bin/sh
# Pedidos do portal (ESPEC-WAF-PONTE, "Pedidos novos"): limpar o LSCache de um site ou
# de um caminho, e depois de mudar o WAF; os limites de uma conta só, com a última hora;
# processos, disco e IOPS por plano, aplicados na hora. Roda depois de waf.sh.
set -u
IP=$(hostname -i | awk '{print $1}')
B=/usr/local/lsws/bin/olsbridge
A=/home/alice/web/alpha.test/public_html
status=0
ok() { echo "[ok] $1"; }
fail() { echo "[FALHOU] $1"; status=1; }
expect() {
    if [ "$3" = "$2" ]; then ok "$1: $3"; else fail "$1: '$3', esperado '$2'"; fi
}
# O cabeçalho X-LiteSpeed-Cache da resposta: hit quando veio do cache.
hit() {
    curl -s -o /dev/null -D - -H "Host: alpha.test" "http://$IP$1" | tr -d '\r' |
        awk -F': ' 'tolower($1) == "x-litespeed-cache" {print $2}'
}
warm() { curl -s -o /dev/null -H "Host: alpha.test" "http://$IP$1"; sleep 1; }
field() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
slice() { echo "/sys/fs/cgroup/user.slice/user-$(id -u "$1").slice"; }

# --- A. Limpar o cache -------------------------------------------------------------
mkdir -p $A/blog
for f in $A/c1.php $A/blog/c2.php $A/blog2.php; do
    cat > "$f" <<'EOF'
<?php header('X-LiteSpeed-Cache-Control: public, max-age=600'); echo uniqid();
EOF
done
chown -R alice:alice $A/blog $A/c1.php $A/blog2.php
$B waf-site mode alpha.test off >/dev/null
for u in /c1.php /blog/c2.php /blog2.php; do warm $u; done
expect "A. /c1.php do cache" hit "$(hit /c1.php)"
expect "A. /blog/c2.php do cache" hit "$(hit /blog/c2.php)"
expect "A. limpar /blog: uma entrada" 1 "$($B cache-purge alpha.test /blog)"
expect "A. /blog/c2.php saiu do cache" miss "$(hit /blog/c2.php)"
expect "A. /blog2.php ficou (não está abaixo de /blog)" hit "$(hit /blog2.php)"
expect "A. /c1.php ficou" hit "$(hit /c1.php)"
warm /blog/c2.php
expect "A. limpar o site todo" 3 "$($B cache-purge alpha.test)"
expect "A. /c1.php saiu" miss "$(hit /c1.php)"
$B cache-purge nao-existe.test >/dev/null 2>&1; expect "A. domínio que não existe" 3 $?
$B cache-purge alpha.test "/a b" >/dev/null 2>&1; expect "A. caminho inválido" 2 $?

# Mudar o WAF limpa o cache do site (um acerto do cache não passa pelo WAF).
warm /c1.php
expect "A. de novo no cache" hit "$(hit /c1.php)"
$B waf-site mode alpha.test detect >/dev/null
expect "A. modo do WAF limpou o cache" miss "$(hit /c1.php)"
warm /c1.php
id=$($B waf-site add alpha.test 942100 /api "teste do cache")
expect "A. liberação nova limpou o cache" miss "$(hit /c1.php)"
warm /c1.php
$B waf-site delete alpha.test "$id" >/dev/null
expect "A. liberação apagada limpou o cache" miss "$(hit /c1.php)"
$B waf-site mode alpha.test off >/dev/null

# --- B e C. Limites de uma conta, com a última hora ---------------------------------
$B sync >/dev/null
row=$($B limits --user alice)
expect "B. usuário" alice "$(echo "$row" | field 'd["user"]')"
expect "B. com o limite de CPU" yes "$(echo "$row" | field '"yes" if d["cpu"] else "no"')"
expect "B. limits on" on "$(echo "$row" | field 'd["limits"]')"
expect "C. last_hour com os quatro contadores" yes "$(echo "$row" | field \
    '"yes" if all(k in d["last_hour"] for k in ("cpu_usec", "throttled_usec", "memory_high_events", "oom_kills")) else "no"')"
expect "C. last_oom de alice" None "$(echo "$row" | field 'd["last_oom"]')"
$B limits --user ninguem-aqui >/dev/null 2>&1; expect "B. usuário que não existe" 3 $?
expect "C. histórico gravado" yes \
    "$([ -s /usr/local/lsws/conf/limits-history.json ] && echo yes || echo no)"

# --- D. Processos, disco e IOPS de um plano -----------------------------------------
mkdir -p /usr/local/hestia/data/users/alice /usr/local/hestia/data/packages
echo "PACKAGE='plano1'" > /usr/local/hestia/data/users/alice/user.conf
echo "CPU_QUOTA='unlimited'" > /usr/local/hestia/data/packages/plano1.pkg
$B plan-limits plano1 --tasks 77 --iops 900 >/dev/null; expect "D. plan-limits" 0 $?
expect "D. tasks na fatia na hora, sem sync" 77 "$(cat $(slice alice)/pids.max)"
expect "D. limits --user diz que veio do plano" package \
    "$($B limits --user alice | field 'd["from"]["tasks"]')"
$B plan-limits plano1 --tasks 0 >/dev/null 2>&1; expect "D. tasks inválido" 2 $?
$B plan-limits nao-existe --tasks 10 >/dev/null 2>&1; expect "D. plano que não existe" 3 $?
expect "D. default volta ao padrão" '{"iops": "900"}' "$($B plan-limits plano1 --tasks default)"
expect "D. tasks do padrão de novo" no "$([ "$(cat $(slice alice)/pids.max)" = 77 ] && echo yes || echo no)"
$B plan-limits plano1 --iops default >/dev/null
expect "D. plano sem nada: arquivo apagado" no \
    "$([ -e /usr/local/lsws/conf/limit-plans/plano1.json ] && echo yes || echo no)"
rm -rf /usr/local/hestia/data/users/alice /usr/local/hestia/data/packages/plano1.pkg \
    $A/blog $A/c1.php $A/blog2.php
$B sync >/dev/null
exit $status
