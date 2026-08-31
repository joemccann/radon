#!/usr/bin/env bash
set -euo pipefail

# Mint the broker mTLS pair for radon-ib-gateway-remote.
# Run on the broker. Copy ca.pem + client.pem + client-key.pem to the app host
# at the same paths. Never commit the keys.

DEST="${RADON_IB_REMOTE_CERT_DIR:-/etc/radon/ib-remote}"
umask 077
mkdir -p "$DEST"

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -subj "/CN=radon-ib-remote-ca" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -keyout "$DEST/ca-key.pem" -out "$DEST/ca.pem"

# One ext file per role (R-495): the server pair is serverAuth only, so it
# cannot be presented back to the daemon as a client; the client pair carries
# the DNS name the daemon allowlists via RADON_IB_REMOTE_CLIENT_NAMES.
server_ext="$(mktemp)"
client_ext="$(mktemp)"
trap 'rm -f "$server_ext" "$client_ext"' EXIT
printf 'subjectAltName=IP:10.0.0.4,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > "$server_ext"
printf 'subjectAltName=DNS:radon-app\nextendedKeyUsage=clientAuth\n' > "$client_ext"

openssl req -newkey rsa:2048 -nodes -subj "/CN=ib-gateway-remote" \
  -keyout "$DEST/server-key.pem" -out "$DEST/server.csr"
openssl x509 -req -days 825 -in "$DEST/server.csr" \
  -CA "$DEST/ca.pem" -CAkey "$DEST/ca-key.pem" -CAcreateserial \
  -out "$DEST/server.pem" -extfile "$server_ext"

openssl req -newkey rsa:2048 -nodes -subj "/CN=radon-app" \
  -keyout "$DEST/client-key.pem" -out "$DEST/client.csr"
openssl x509 -req -days 825 -in "$DEST/client.csr" \
  -CA "$DEST/ca.pem" -CAkey "$DEST/ca-key.pem" -CAcreateserial \
  -out "$DEST/client.pem" -extfile "$client_ext"

rm -f "$DEST/server.csr" "$DEST/client.csr"
chmod 0600 "$DEST"/*.pem "$DEST"/*-key.pem
chmod 0644 "$DEST/ca.pem" "$DEST/server.pem" "$DEST/client.pem"
chown -R radon:radon "$DEST"

cat <<EOF
Wrote $DEST
Broker /etc/radon/env:
  RADON_IB_REMOTE_CERT=$DEST/server.pem
  RADON_IB_REMOTE_KEY=$DEST/server-key.pem
  RADON_IB_REMOTE_CA=$DEST/ca.pem
  RADON_IB_REMOTE_BIND=10.0.0.4
  RADON_IB_REMOTE_PORT=8340
  RADON_IB_REMOTE_ALLOW=10.0.0.2
  RADON_IB_REMOTE_CLIENT_NAMES=radon-app
App /etc/radon/env:
  RADON_IB_REMOTE_URL=https://10.0.0.4:8340
  RADON_IB_REMOTE_CA=$DEST/ca.pem
  RADON_IB_REMOTE_CLIENT_CERT=$DEST/client.pem
  RADON_IB_REMOTE_CLIENT_KEY=$DEST/client-key.pem
Copy ca.pem client.pem client-key.pem to the app host. Keep ca-key.pem on the broker.
EOF
