#!/bin/sh
set -eu

# Limit ownership changes to the two explicit state mounts. Source media and
# configuration mounts stay read-only and are never traversed here.
for directory in /data/operations /data/backups; do
  if [ ! -d "$directory" ]; then
    echo "Required state directory is not mounted: $directory" >&2
    exit 1
  fi
  chown -R 10001:10001 "$directory"
  chmod 0770 "$directory"
done

exec gosu trainerbackup "$@"
