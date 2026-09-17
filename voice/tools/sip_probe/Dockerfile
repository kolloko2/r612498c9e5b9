FROM debian:bookworm-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates && \
    sed -i s#http://deb.debian.org#https://deb.debian.org#g /etc/apt/sources.list.d/debian.sources && \
    apt-get update -qq && apt-get install -y -qq --no-install-recommends baresip-core sox && \
    rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
