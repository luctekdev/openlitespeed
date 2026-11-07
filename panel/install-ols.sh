#!/bin/sh
# Instala o OpenLiteSpeed do fork e a ponte, sem iniciar nada.
#   sh install.sh            instala em /usr/local/lsws
#   sh install.sh --upgrade  troca só o binário e a ponte de uma instalação existente
#
# Depois: /usr/local/lsws/bin/olsbridge check, e então install --mode offset.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
LSWS=/usr/local/lsws
upgrade=no
[ "${1:-}" = "--upgrade" ] && upgrade=yes

fail() { echo "erro: $*" >&2; exit 1; }

# Página e comandos do HestiaCP. Se a integração já está instalada, ela é atualizada.
install_addons() {
    [ -d "$here/hestia" ] || return 0
    rm -rf "$LSWS/add-ons/olsbridge-hestia"
    mkdir -p "$LSWS/add-ons"
    cp -r "$here/hestia" "$LSWS/add-ons/olsbridge-hestia"
    if [ -e /usr/local/hestia/bin/v-list-sys-litespeed ]; then
        "$LSWS/bin/olsbridge" hestia-integrate
    fi
}
[ "$(id -u)" = 0 ] || fail "rode como root"
[ "$(uname -m)" = x86_64 ] || fail "o pacote é para x86_64"
command -v python3 >/dev/null || fail "a ponte precisa de python3"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
tar -xzf "$here/lsws.tgz" -C "$tmp"
# O binário foi compilado em outro sistema: precisa rodar aqui antes de instalar.
if ! "$tmp/lsws/bin/openlitespeed" -v >/dev/null 2>&1; then
    ldd "$tmp/lsws/bin/openlitespeed" | grep "not found" >&2 || true
    fail "o OpenLiteSpeed deste pacote não roda neste sistema ($(cat "$here/VERSION" | grep built_on))"
fi

if [ -e "$LSWS/bin/openlitespeed" ]; then
    [ "$upgrade" = yes ] || fail "$LSWS já existe; use --upgrade para trocar o binário"
    stamp=$(date +%Y%m%d%H%M%S)
    cp -p "$LSWS/bin/openlitespeed" "$LSWS/bin/openlitespeed.$stamp"
    install -m 755 "$tmp/lsws/bin/openlitespeed" "$LSWS/bin/openlitespeed.new"
    mv -f "$LSWS/bin/openlitespeed.new" "$LSWS/bin/openlitespeed"
    cp -f "$tmp/lsws/modules/"*.so "$LSWS/modules/" 2>/dev/null || true
    install -m 755 "$here/olsbridge" "$LSWS/bin/olsbridge"
    install_addons
    echo "Binário trocado (anterior: $LSWS/bin/openlitespeed.$stamp)."
    echo "Vale no próximo restart: systemctl restart olsbridge (offset) ou o serviço do Apache (replace)."
    exit 0
fi

# Mesmo usuário que o instalador do OLS cria para a configuração.
if ! getent group lsadm >/dev/null; then groupadd -r lsadm; fi
if ! getent passwd lsadm >/dev/null; then
    useradd -r -g lsadm -d / -s "$(command -v nologin || echo /bin/false)" lsadm
fi
tar -xzf "$here/lsws.tgz" -C /usr/local
mkdir -p "$LSWS/logs" "$LSWS/tmp"
# A chave do WebAdmin é deste servidor, nunca a da imagem. O WebAdmin fica desligado.
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 -subj "/CN=$(hostname -f 2>/dev/null || hostname)" \
    -keyout "$LSWS/admin/conf/webadmin.key" -out "$LSWS/admin/conf/webadmin.crt" >/dev/null 2>&1
chown lsadm:lsadm "$LSWS/admin/conf/webadmin.key" "$LSWS/admin/conf/webadmin.crt"
chmod 600 "$LSWS/admin/conf/webadmin.key" "$LSWS/admin/conf/webadmin.crt"
install -m 755 "$here/olsbridge" "$LSWS/bin/olsbridge"
install_addons
cp "$here/VERSION" "$LSWS/VERSION.olsbridge"

echo "Instalado em $LSWS. Nada foi iniciado e o Apache não foi tocado."
echo "Próximos passos:"
[ -d /usr/local/hestia ] && echo "  $LSWS/bin/olsbridge hestia-integrate   (página OpenLiteSpeed no HestiaCP)"
echo "  $LSWS/bin/olsbridge check"
echo "  $LSWS/bin/olsbridge install --mode offset"
echo "  $LSWS/bin/olsbridge compare"
