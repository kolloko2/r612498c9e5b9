#!/bin/sh
set -eu

source_dir=/run/tls-source
runtime_dir=/run/postgresql-tls

test -f "$source_dir/postgres.cert.pem"
test -f "$source_dir/postgres.key.pem"
test -f "$source_dir/ca.cert.pem"

install -d -o postgres -g postgres -m 0700 "$runtime_dir"
install -o postgres -g postgres -m 0600 "$source_dir/postgres.key.pem" "$runtime_dir/server.key"
install -o postgres -g postgres -m 0644 "$source_dir/postgres.cert.pem" "$runtime_dir/server.crt"
install -o postgres -g postgres -m 0644 "$source_dir/ca.cert.pem" "$runtime_dir/ca.crt"

exec docker-entrypoint.sh "$@" \
  -c ssl=on \
  -c ssl_cert_file="$runtime_dir/server.crt" \
  -c ssl_key_file="$runtime_dir/server.key" \
  -c ssl_ca_file="$runtime_dir/ca.crt" \
  -c ssl_min_protocol_version=TLSv1.2 \
  -c hba_file=/etc/postgresql/pg_hba.tls.conf
