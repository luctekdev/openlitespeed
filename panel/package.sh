#!/bin/sh
# Monta o pacote para servidores: o /usr/local/lsws da imagem de teste, a ponte e
# o instalador. Rode na raiz do repositório, depois de gerar a imagem:
#   docker build -f htaccess-compat/docker/ols.Dockerfile --target runtime -t ols-htaccess:runtime .
#   sh panel/package.sh [pasta de saída]
#
# Ficam de fora o PHP que o instalador do OLS baixa (a ponte usa o PHP-FPM do
# painel), as senhas e chaves geradas na imagem e os logs.
set -eu
image=${OLS_IMAGE:-ols-htaccess:runtime}
out=${1:-panel/dist}
commit=$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- panel || echo -dirty)
base=$(docker run --rm "$image" sh -c '. /etc/os-release && echo "$ID$VERSION_ID"')
name=olsbridge-$commit-$base
work=$out/$name

rm -rf "$work" && mkdir -p "$work"
MSYS_NO_PATHCONV=1 docker run --rm "$image" tar -czf - -C /usr/local \
    --exclude=lsws/logs/* --exclude=lsws/tmp/* --exclude=lsws/cgid/* \
    --exclude=lsws/adminpasswd --exclude=lsws/admin/conf/htpasswd \
    --exclude='lsws/admin/conf/webadmin.*' --exclude='lsws/admin/fcgi-bin/*' \
    --exclude='lsws/fcgi-bin/lsphp*' --exclude=lsws/conf/bridge \
    --exclude=lsws/conf/olsbridge.conf lsws > "$work/lsws.tgz"
cp panel/bridge/olsbridge.py "$work/olsbridge"
cp panel/install-ols.sh "$work/install.sh"
{
    echo "commit=$(git rev-parse HEAD)"
    echo "built_on=$base"
    MSYS_NO_PATHCONV=1 docker run --rm "$image" /usr/local/lsws/bin/openlitespeed -v 2>&1 | head -1 \
        | sed 's/^/ols=/'
} > "$work/VERSION"
tar -czf "$out/$name.tgz" -C "$out" "$name"
rm -rf "$work"
echo "$out/$name.tgz"
