# Emite um certificado para o domínio $1, assinado pela intermediária de /test/ca.
leaf() {
    cd /test/ca
    openssl req -newkey rsa:2048 -nodes -subj "/CN=$1" -keyout "$2.key" -out "$2.csr" 2>/dev/null
    printf 'subjectAltName=DNS:%s,DNS:www.%s\n' "$1" "$1" > "$2.ext"
    openssl x509 -req -in "$2.csr" -CA int.crt -CAkey int.key -CAcreateserial -days 30 \
        -extfile "$2.ext" -out "$2.crt" 2>/dev/null
    rm -f "$2.csr" "$2.ext"
}
