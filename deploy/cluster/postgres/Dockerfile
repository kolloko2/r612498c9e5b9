FROM postgres:16-bookworm

COPY --chmod=0555 ha-entrypoint.sh /usr/local/bin/trainer112-ha-entrypoint
COPY --chmod=0444 pg_hba.conf /etc/postgresql/pg_hba.trainer112.conf

ENTRYPOINT ["trainer112-ha-entrypoint"]
CMD ["postgres"]
