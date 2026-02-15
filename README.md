# Argus Docker PoC (minimal)

This repository contains a minimal PoC to run **Argus** in Docker.  
It includes the essential files: `Dockerfile`, `docker-compose.yml`, `argus.conf` and `run-argus.sh`.

> Note: commands assume Linux. For realistic network captures you should use a physical NIC connected to a switch mirror/TAP (not the host's managed Wi-Fi), unless you capture on the AP's wired side or use monitor mode.

---

## Requirements
- Docker and docker-compose installed.
- A network interface for sniffing (e.g. `eth1`, or a USB-Ethernet adapter connected to a mirror/TAP port).  
  If you use the built-in Wi-Fi, see Wi-Fi limitations below.

---

## Minimal structure
- `Dockerfile` — builds Argus from upstream.
- `docker-compose.yml` — runs the image with host networking.
- `argus.conf` — minimal example configuration.
- `run-argus.sh` — helper script that readies the interface and launches docker-compose.
- Folders: `argus-data/` (output) and `pcap/` (pcaps for tests).

---

## Quickstart

1. Create the project folder and prepare directories:
```bash
cd ~/Desktop/work/argus-docker
mkdir -p argus-data pcap
````

2. Make the helper executable:

```bash
chmod +x run-argus.sh
```

3. Build the image (recommended without cache to see errors):

```bash
DOCKER_BUILDKIT=0 docker-compose build --no-cache --progress=plain argus
```

4. Prepare the interface and start the PoC (run from the repo directory):

```bash
./run-argus.sh <INTERFACE>
# Example:
./run-argus.sh eth1
# Or use your Wi-Fi interface:
./run-argus.sh wlxec086b1ee1f6
./run-argus.sh wlo1
```

The script:

* checks the interface exists,
* brings it UP and sets promiscuous mode,
* builds the image and starts `docker-compose` detached.

5. Check status and logs:

```bash
docker-compose ps
docker-compose logs -f
# or follow only the service:
docker-compose logs -f argus
```

6. Confirm Argus output files are being created:

```bash
ls -lh ./argus-data
# quick inspection with ra (run inside the container)
sudo docker compose exec argus ra -r /var/log/argus/argus.out -s saddr daddr sport dport pkts bytes | head
```

7. Stop the PoC and clean up:

```bash
docker-compose down
sudo ip link set dev <INTERFACE> promisc off
```

## Comandos ra para probar el stream (sin fichero)
1. 10 flujos en vivo
ra -S localhost:561 -n -L \
  -s "stime proto saddr sport daddr dport spkts dpkts sbytes dbytes" \
  -c 10

2. sólo TCP/22 (SSH)
ra -S localhost:561 -n -L 'tcp and port 22' \
  -s "stime saddr sport daddr dport spkts dpkts sbytes dbytes" \
  -c 10

3. sólo tráfico hacia fuera de 10.0.0.0/8  (ajusta tu CIDR)
ra -S localhost:561 -n -L 'not (src net 10.0.0.0/8 and dst net 10.0.0.0/8)' \
  -s "stime proto saddr sport daddr dport sbytes dbytes" \
  -c 20



conectar desde mi host:

10.20.1.51:561

ra -S 127.0.0.1:561 -n -T 10 -N o20 \
     -s stime proto saddr sport daddr dport spkts dpkts sbytes dbytes \
     -L 0


Install argus-client:

sudo apt-get update
sudo apt-get install -y build-essential autoconf automake libtool bison flex pkg-config libpcap-dev git

cd /tmp
git clone --depth 1 https://github.com/openargus/clients.git
cd clients
autoreconf -fi || true
./configure
make -j"$(nproc)"
sudo make install

213.60.255.45:561

# 1) Streaming continuo en texto (sin cabecera cada X líneas)
ra -S 127.0.0.1:561 -n -L 0
ra -S 10.20.1.51:561 -n -L 0
ra -S 213.60.255.45:561 -n -L 0


# 2) Mismo pero con campos útiles
ra -S 127.0.0.1:561 -n -L 0 \
  -s stime proto saddr sport daddr dport spkts dpkts sbytes dbytes

ra -S 213.60.255.45:561 -n -L 0 \
  -s stime proto saddr sport daddr dport spkts dpkts sbytes dbytes

# 3) CSV (delimitador coma), cabecera una sola vez
ra -S 127.0.0.1:561 -n -L -1 -c , \
  -s stime ltime proto saddr sport daddr dport spkts dpkts sbytes dbytes

# 4) JSON (ideal para Python)
ra -S 127.0.0.1:561 -n -L -1 -M json




diagnosticar en el firewall
diagnose sniffer packet any 'port 561' 4