FROM postgres:16-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-cryptography \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/backup
COPY deploy/backup_worker.py deploy/full_backup.py ./
COPY deploy/backup/entrypoint.sh /usr/local/bin/trainer-backup-entrypoint
RUN useradd --uid 10001 --create-home trainerbackup \
    && chmod 0755 /usr/local/bin/trainer-backup-entrypoint
ENTRYPOINT ["trainer-backup-entrypoint"]
CMD ["python3", "backup_worker.py"]
