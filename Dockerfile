FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git autoconf automake libtool bison flex pkg-config \
    libpcap-dev dh-autoreconf wget curl tzdata ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# Argus server
RUN git clone --depth 1 https://github.com/openargus/argus.git /opt/argus
WORKDIR /opt/argus
RUN autoreconf -fi || true
RUN ./configure || (echo "configure failed - see config.log" && tail -n 200 config.log && false)
RUN make -j$(nproc) && make install

# Argus clients
RUN git clone --depth 1 https://github.com/openargus/clients.git /opt/clients
WORKDIR /opt/clients
RUN autoreconf -fi || true
RUN ./configure || (echo "clients configure failed - see config.log" && tail -n 200 config.log && false)
RUN make -j$(nproc) && make install

RUN mkdir -p /var/log/argus /pcap /etc/argus && chmod 755 /var/log/argus
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
VOLUME ["/var/log/argus","/pcap","/etc/argus"]
EXPOSE 561/tcp

STOPSIGNAL SIGINT
ENTRYPOINT ["/entrypoint.sh"]
CMD []
