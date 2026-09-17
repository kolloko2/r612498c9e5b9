#!/bin/sh
set -eu

role=${PG_HA_ROLE:?Set PG_HA_ROLE to primary or standby}
service=${PG_TLS_SERVICE:?Set PG_TLS_SERVICE}
source_dir=/run/tls-source
runtime_dir=/run/postgresql-tls

test -f "$source_dir/$service.cert.pem"
test -f "$source_dir/$service.key.pem"
test -f "$source_dir/ca.cert.pem"
install -d -o postgres -g postgres -m 0700 "$runtime_dir"
install -o postgres -g postgres -m 0600 "$source_dir/$service.key.pem" "$runtime_dir/server.key"
install -o postgres -g postgres -m 0644 "$source_dir/$service.cert.pem" "$runtime_dir/server.crt"
install -o postgres -g postgres -m 0644 "$source_dir/ca.cert.pem" "$runtime_dir/ca.crt"

common_args="-c ssl=on -c ssl_cert_file=$runtime_dir/server.crt -c ssl_key_file=$runtime_dir/server.key -c ssl_ca_file=$runtime_dir/ca.crt -c ssl_min_protocol_version=TLSv1.2 -c hba_file=/etc/postgresql/pg_hba.trainer112.conf"

if [ "$role" = primary ]; then
  # synchronous_standby_names is installed by the primary init script only after
  # bootstrap, otherwise the official one-node temporary init server can block.
  exec docker-entrypoint.sh "$@" $common_args \
    -c wal_level=replica -c max_wal_senders=5 -c max_replication_slots=5 \
    -c wal_keep_size=256MB -c synchronous_commit=remote_apply
fi

if [ "$role" != standby ]; then
  echo "Unsupported PG_HA_ROLE: $role" >&2
  exit 64
fi

test -n "${POSTGRES_REPLICATION_PASSWORD:-}"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  if [ -n "$(find "$PGDATA" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    echo "Standby PGDATA is non-empty but has no PG_VERSION; reset the rehearsal volumes explicitly." >&2
    exit 65
  fi
  until pg_isready -h pg-primary -p 5432 -U replicator >/dev/null 2>&1; do
    sleep 1
  done
  install -d -o postgres -g postgres -m 0700 "$PGDATA"
  printf 'pg-primary:5432:replication:replicator:%s\n' "$POSTGRES_REPLICATION_PASSWORD" \
    > /var/lib/postgresql/.pgpass
  chown postgres:postgres /var/lib/postgresql/.pgpass
  chmod 0600 /var/lib/postgresql/.pgpass
  export PGPASSFILE=/var/lib/postgresql/.pgpass
  gosu postgres pg_basebackup \
    --dbname="host=pg-primary port=5432 user=replicator application_name=trainer_standby sslmode=verify-full sslrootcert=$runtime_dir/ca.crt" \
    --pgdata="$PGDATA" --format=plain --wal-method=stream --write-recovery-conf \
    --slot=trainer_standby --progress
fi

export PGPASSFILE=/var/lib/postgresql/.pgpass
exec docker-entrypoint.sh "$@" $common_args -c hot_standby=on
