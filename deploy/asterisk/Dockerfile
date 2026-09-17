# syntax=docker/dockerfile:1
ARG ASTERISK_VERSION=22.11.0
ARG ASTERISK_SHA256=3bd5ee040509a3d3cd9b1ba9520c18e6ec0a7e7981ca68c457dcd36ba3c54d94

FROM debian:bookworm-slim AS build
ARG ASTERISK_VERSION
ARG ASTERISK_SHA256

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        autoconf-archive build-essential bzip2 ca-certificates curl file \
        libcurl4-openssl-dev libedit-dev libjansson-dev libnewt-dev libsrtp2-dev \
        libsqlite3-dev libssl-dev libxml2-dev patch pkg-config python3-dev \
        uuid-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src
RUN curl --fail --location --proto '=https' --tlsv1.2 \
        --output asterisk.tar.gz \
        "https://downloads.asterisk.org/pub/telephony/asterisk/releases/asterisk-${ASTERISK_VERSION}.tar.gz" \
    && echo "${ASTERISK_SHA256}  asterisk.tar.gz" | sha256sum --check --strict \
    && mkdir asterisk \
    && tar --extract --gzip --file asterisk.tar.gz --directory asterisk --strip-components=1 \
    && rm asterisk.tar.gz

WORKDIR /usr/src/asterisk
# Bundled pjproject has shown nondeterministic archive failures under parallel
# BuildKit compilation on Docker Desktop; serialize for a reproducible image.
RUN ./configure --with-jansson-bundled --with-pjproject-bundled \
    && make menuselect.makeopts \
    && menuselect/menuselect --disable BUILD_NATIVE \
        --enable chan_pjsip \
        --enable chan_websocket \
        --enable res_ari \
        --enable res_ari_channels \
        --enable res_http_websocket \
        --enable res_pjsip \
        --enable res_pjsip_outbound_registration \
        --enable res_stasis \
        --enable res_srtp \
        --enable res_websocket_client \
        --enable bridge_softmix \
        menuselect.makeopts \
    && NOISY_BUILD=yes make -j1 \
    && make install DESTDIR=/opt/asterisk-root \
    && strip /opt/asterisk-root/usr/sbin/asterisk

FROM debian:bookworm-slim AS runtime
ARG ASTERISK_VERSION

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ca-certificates libcurl4 libedit2 libjansson4 libsrtp2-1 libsqlite3-0 \
        libssl3 libtinfo6 libuuid1 libxml2 python3 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system asterisk \
    && useradd --system --gid asterisk --home-dir /var/lib/asterisk --shell /usr/sbin/nologin asterisk \
    && install -d -o asterisk -g asterisk -m 0750 \
        /etc/asterisk /run/asterisk /var/lib/asterisk /var/log/asterisk /var/spool/asterisk

COPY --from=build /opt/asterisk-root/usr /usr
COPY --from=build --chown=asterisk:asterisk /opt/asterisk-root/var/lib/asterisk /var/lib/asterisk
COPY --chown=asterisk:asterisk config /opt/asterisk-config
COPY --chmod=0555 entrypoint.py healthcheck.py /usr/local/bin/

LABEL org.opencontainers.image.title="112 AI Trainer Asterisk" \
      org.opencontainers.image.version="${ASTERISK_VERSION}" \
      org.opencontainers.image.source="https://downloads.asterisk.org/pub/telephony/asterisk/releases/"

USER asterisk
EXPOSE 5060/udp 5061/tcp 8088/tcp 8089/tcp 20000-20199/udp
VOLUME ["/var/log/asterisk", "/var/spool/asterisk"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=6 \
    CMD ["python3", "/usr/local/bin/healthcheck.py"]
ENTRYPOINT ["python3", "/usr/local/bin/entrypoint.py"]
CMD ["/usr/sbin/asterisk", "-f", "-vvv", "-C", "/etc/asterisk/asterisk.conf"]
