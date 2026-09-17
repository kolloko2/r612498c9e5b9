#!/bin/sh
set -eu
test -n "${POSTGRES_REPLICATION_PASSWORD:-}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=repl_password="$POSTGRES_REPLICATION_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD %L', :'repl_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'replicator') \gexec
SELECT pg_create_physical_replication_slot('trainer_standby')
WHERE NOT EXISTS (SELECT 1 FROM pg_replication_slots WHERE slot_name = 'trainer_standby');
ALTER SYSTEM SET synchronous_standby_names = 'FIRST 1 (trainer_standby)';
EOSQL
