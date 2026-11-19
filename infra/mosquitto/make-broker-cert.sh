#!/bin/sh
# The broker's TLS certificate, signed by the project's own CA (T1-36, infra/readme.md).
#
# Run from the repo root (Git Bash on Windows has openssl):
#   sh infra/mosquitto/make-broker-cert.sh ca                        once: the CA, 10 years
#   sh infra/mosquitto/make-broker-cert.sh server NAME [NAME ...]    the broker's certificate, 2 years
#
# NAME: every DNS name or IP address the gateway uses to reach the broker. On a laptop
# `localhost 127.0.0.1`; on a server, its domain.
#
# ca     writes infra/mosquitto/ca/ca.key and ca.crt. ca.key signs every broker certificate: keep
#        it in the password manager or offline, never on the server, never in git. ca.crt is
#        public: the gateway's firmware trusts it.
# server writes infra/mosquitto/staging/secrets/server.crt and server.key. Copy those two to the
#        server's secrets folder and restart the broker.
# Both folders are git-ignored.
#
# Works with OpenSSL 1.1.1 and 3.x. Each certificate is made from this script's own config, never
# the system's openssl.cnf, whose extensions differ between versions (OpenSSL 1.1.1 would add a
# second Basic Constraints, which mbedTLS on the gateway refuses). Each one is checked before it
# is saved, so a failed run leaves nothing behind.
set -eu

CA_DAYS=3650
SERVER_DAYS=730
CA_NAME="Smart Nutrient Advisor broker CA"

here=$(cd "$(dirname "$0")" && pwd)
ca_dir="$here/ca"
secrets="$here/staging/secrets"

usage() {
    echo "usage: sh infra/mosquitto/make-broker-cert.sh ca" >&2
    echo "       sh infra/mosquitto/make-broker-cert.sh server NAME [NAME ...]   (for example localhost 127.0.0.1)" >&2
    exit 2
}

fail() {
    echo "make-broker-cert: $*" >&2
    echo "make-broker-cert: nothing was saved. $(openssl version)" >&2
    exit 1
}

# A scratch folder for keys and configs, removed on exit. Keys in it are readable by this user only.
start_work() {
    work=$(mktemp -d)
    trap 'cd / && rm -rf "$work"' EXIT
    umask 077
    cd "$work"
}

# Runs openssl, showing its output only when it fails (1.1.1's verify writes errors to stdout).
run() {
    if ! openssl "$@" > openssl.out 2>&1; then
        cat openssl.out >&2
        fail "openssl $1 failed (above)."
    fi
}

# Each X.509 extension at most once: mbedTLS refuses a certificate that repeats one.
check_extensions() {
    duplicates=$(openssl x509 -in "$1" -noout -text | grep -oE 'X509v3 [A-Za-z ]+:' | sort | uniq -d)
    [ -z "$duplicates" ] || fail "$1 has an extension twice:" $duplicates
}

make_ca() {
    if [ -e "$ca_dir/ca.key" ] || [ -e "$ca_dir/ca.crt" ]; then
        echo "make-broker-cert: infra/mosquitto/ca/ already has a CA. A new CA means every gateway" \
            "must be given the new ca.crt; delete the old one by hand if that is really meant." >&2
        exit 1
    fi
    start_work
    cat > ca.cnf <<EOF
[req]
prompt = no
distinguished_name = dn
x509_extensions = ca_ext
[dn]
CN = $CA_NAME
[ca_ext]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
EOF
    run req -config ca.cnf -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt \
        -days "$CA_DAYS" -sha256

    check_extensions ca.crt
    openssl x509 -in ca.crt -noout -text | grep -q 'CA:TRUE, pathlen:0' \
        || fail "ca.crt is not a CA with pathlen 0."
    run verify -CAfile ca.crt ca.crt

    mkdir -p "$ca_dir"
    cp ca.key ca.crt "$ca_dir/"
    chmod 600 "$ca_dir/ca.key"
    chmod 644 "$ca_dir/ca.crt"
    echo "CA made and checked: infra/mosquitto/ca/ca.crt (public, for the gateway) and ca.key (secret)."
    openssl x509 -in "$ca_dir/ca.crt" -noout -subject -enddate
    echo "Put ca.key in the password manager or keep it offline. It must never reach the server or git."
}

make_server() {
    [ $# -gt 0 ] || usage
    if [ ! -f "$ca_dir/ca.key" ] || [ ! -f "$ca_dir/ca.crt" ]; then
        echo "make-broker-cert: no CA in infra/mosquitto/ca/. Run 'make-broker-cert.sh ca' first," \
            "or put the team's ca.crt and ca.key there from the password manager." >&2
        exit 1
    fi
    names=""
    for name in "$@"; do
        case "$name" in
            *[!A-Za-z0-9.:-]*) echo "make-broker-cert: '$name' is not a DNS name or IP address" >&2; exit 2 ;;
        esac
        if echo "$name" | grep -Eq '^[0-9.]+$|:'; then
            entry="IP:$name"
        else
            entry="DNS:$name"
        fi
        names="${names:+$names,}$entry"
    done

    start_work
    cp "$ca_dir/ca.crt" "$ca_dir/ca.key" ./
    # A CA made by an older version of this script on OpenSSL 1.1.1 repeats Basic Constraints.
    check_extensions ca.crt
    cat > server.cnf <<EOF
[req]
prompt = no
distinguished_name = dn
[dn]
CN = $1
EOF
    cat > server.ext <<EOF
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = $names
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF
    serial="0x1$(head -c 15 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    run req -config server.cnf -new -newkey rsa:2048 -nodes -keyout server.key -out server.csr
    run x509 -req -in server.csr -CA ca.crt -CAkey ca.key -set_serial "$serial" \
        -days "$SERVER_DAYS" -sha256 -extfile server.ext -out server.crt

    check_extensions server.crt
    run verify -CAfile ca.crt -purpose sslserver server.crt

    mkdir -p "$secrets"
    cp server.key server.crt "$secrets/"
    chmod 600 "$secrets/server.key"
    chmod 644 "$secrets/server.crt"
    echo "Broker certificate made and checked: infra/mosquitto/staging/secrets/server.crt and server.key"
    openssl x509 -in "$secrets/server.crt" -noout -subject -enddate
    openssl x509 -in "$secrets/server.crt" -noout -text | grep -A1 'Subject Alternative Name'
    echo "Renew it before that date: set a reminder. Then restart the broker."
}

case "${1:-}" in
    ca) [ $# -eq 1 ] || usage; make_ca ;;
    server) shift; make_server "$@" ;;
    *) usage ;;
esac
