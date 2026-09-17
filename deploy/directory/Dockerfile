FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install --yes --no-install-recommends slapd ldap-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/* /var/lib/ldap/* /etc/ldap/slapd.d/*

COPY --chmod=0755 entrypoint.sh /usr/local/bin/directory-entrypoint

EXPOSE 636
ENTRYPOINT ["/usr/local/bin/directory-entrypoint"]
