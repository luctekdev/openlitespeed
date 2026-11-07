# Build reproduzível do OpenLiteSpeed para desenvolvimento do suporte a .htaccess.
#
# Execute a partir da raiz do repositório:
#   docker build -f htaccess-compat/docker/ols.Dockerfile -t ols-htaccess:dev .
#
# build.sh altera arquivos do código-fonte (CMakeLists etc.). Por isso ele roda
# sempre sobre a cópia dentro da imagem, nunca sobre o repositório.

FROM ubuntu:24.04 AS deps

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    autoconf automake build-essential ca-certificates clang cmake curl git \
    golang-go libaio-dev libcap-dev libexpat1-dev libtool make patch \
    pkg-config python3 unzip wget zlib1g-dev

# As bibliotecas ficam numa camada própria: compilar BoringSSL e cia. é lento e
# raramente muda. Módulos fora do escopo atual (ModSecurity, Lua, PageSpeed) são
# omitidos; unittest-cpp é mantido, pois os testes do upstream dependem dele.
WORKDIR /work
RUN git clone https://github.com/litespeedtech/third-party.git \
    && mkdir third-party/lib64 \
    && cd third-party/script \
    && sed -i -e 's/unittest-cpp/unittest-cpp bcrypt/g' \
              -e 's/libmodsec/ /g' -e 's/psol/ /g' -e 's/luajit/ /g' build_ols.sh \
    && ./build_ols.sh

FROM deps AS build

COPY . /work/openlitespeed
WORKDIR /work/openlitespeed

# Em checkouts no Windows, links simbólicos viram arquivos de texto (src/liblsquic,
# por exemplo). O índice do Git guarda a versão correta. Em Linux isto não muda nada.
RUN git config core.symlinks true \
    && git ls-files -s | awk '$1 == "120000" {print $4}' \
       | while read -r link; do rm -f "$link"; git checkout -- "$link"; done

# build.sh usa CUR_PATH sem defini-lo ao compilar o lsrecaptcha.
ENV CUR_PATH=/work/openlitespeed
RUN ./build.sh -o OFF -l OFF -p OFF && test -x build/src/openlitespeed

# WAF: o módulo do ModSecurity ligado à libmodsecurity do sistema, que o apt atualiza.
# No servidor, o pacote libmodsecurity3t64 (Ubuntu 24.04 e 26.04) basta.
RUN apt-get update && apt-get install -y --no-install-recommends libmodsecurity-dev \
    && cmake -S . -B build-modsec -DCMAKE_BUILD_TYPE=Release -DMOD_SECURITY=ON \
             -DMODSEC_SYSTEM=ON -DMOD_PAGESPEED=OFF -DMOD_LUA=OFF >/dev/null \
    && cmake --build build-modsec --target mod_security -j"$(nproc)" \
    && cp build-modsec/src/modules/modsecurity-ls/mod_security.so dist/modules/ \
    && ldd dist/modules/mod_security.so | grep -q libmodsecurity

# Testes do upstream: build Debug (com ASan) a partir do código original, sem as
# alterações que build.sh faz nos CMakeLists.
#   docker build -f htaccess-compat/docker/ols.Dockerfile --target test -t ols-htaccess:test .
#   docker run --rm ols-htaccess:test
FROM deps AS test

RUN apt-get install -y --no-install-recommends valgrind \
    && /work/third-party/script/build_unittest-cpp.sh

COPY . /work/openlitespeed
WORKDIR /work/openlitespeed
RUN git config core.symlinks true \
    && git ls-files -s | awk '$1 == "120000" {print $4}' \
       | while read -r link; do rm -f "$link"; git checkout -- "$link"; done \
    && rm -rf lsquic \
    && git clone https://github.com/litespeedtech/lsquic.git \
    && git -C lsquic checkout "$(cat LSQUICCOMMIT)" \
    && git -C lsquic submodule update --init --recursive \
    && cmake -S . -B build-test -DCMAKE_BUILD_TYPE=Debug \
             -DMOD_PAGESPEED=OFF -DMOD_SECURITY=OFF -DMOD_LUA=OFF \
    && cmake --build build-test -j"$(nproc)" -- -k
CMD ["ctest", "--test-dir", "build-test", "--output-on-failure", "--timeout", "120"]

FROM build AS runtime

RUN cd /work/openlitespeed && ./install.sh
EXPOSE 8088 7080
CMD ["/usr/local/lsws/bin/openlitespeed", "-n"]

# OLS com PHP (LSAPI), para testar php_value e o caminho até a aplicação.
FROM runtime AS php

RUN curl -fsSL https://www.php.net/distributions/php-8.3.14.tar.gz | tar xz -C /work     && cd /work/php-8.3.14     && ./configure --disable-all --enable-litespeed --prefix=/usr/local/lsws/lsphp83        > /dev/null     && make -j"$(nproc)" > /dev/null     && mkdir -p /usr/local/lsws/lsphp83/bin     && cp sapi/litespeed/php /usr/local/lsws/lsphp83/bin/lsphp     && rm -rf /work/php-8.3.14

# Servidor com ASan, para rodar a comparação e os testes de carga.
#   OLS_IMAGE=ols-htaccess:asan docker compose ...
FROM runtime AS asan

COPY --from=test /work/openlitespeed/build-test/src/openlitespeed /usr/local/lsws/bin/openlitespeed
ENV ASAN_OPTIONS=detect_leaks=0:abort_on_error=0:log_path=/tmp/asan
