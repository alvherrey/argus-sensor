# Argus Network Flow Sensor - Client Access Guide

This document explains how to connect to the remote Argus flow sensor and read
real-time network telemetry streams for further processing and analytics.

**Sensor Public IP:** 213.60.255.45  
**Sensor Local IP:** 10.20.1.51
**Argus Server Port:** 561  
**SSH Access:** `ssh argus@10.20.1.51` (user: `argus`, password: `argus`)

The container image provides both:
- **Argus Server binary** (not needed for the client use case)
- **Argus Clients (`ra`, `rasort`, etc.)** — these are what developers use to
  consume live flow data.

---

## 0. Connect to Sensor Host (SSH)

```bash
# Connect to the host running the Argus sensor
# Local IP (from same network):
ssh argus@10.20.1.51

# Password: argus

# Once inside, you can:
# - Check sensor status: docker compose ps
# - View logs: docker compose logs -f
# - Access local stream: ra -S localhost:561 -c 10
```

**Note:** You may see warnings like `ArgusCheckClientMessage: client noname never started: timed out` in the logs. These are normal and indicate TCP connection attempts that didn't complete the Argus handshake (e.g., port scanners, TCP health checks). They don't affect sensor operation.

---

## 1. Pull the Docker Image

```bash
sudo docker pull roalt67184/argus:20251027
````

---

## 2. Enter the Container (Client Shell)

The image defaults to starting the Argus server.
To use the client tools, override the entrypoint:

```bash
sudo docker run --rm -it \
  --entrypoint /bin/bash \
  roalt67184/argus:20251027
```

You will now be inside the container shell.

---

## 3. Stream Live Flow Data

### Human-readable output

```bash
ra -S 213.60.255.45:561 -n -L 0 \
  -s stime proto saddr sport daddr dport spkts dpkts sbytes dbytes
```

### With MAC addresses

```bash
ra -S 213.60.255.45:561 -n -L 0 \
  -s stime smac dmac proto saddr sport daddr dport spkts dpkts sbytes dbytes
```

### JSON Output (for ML / data ingestion)

```bash
ra -S 213.60.255.45:561 -n -M json
```

### Poll test (should exit with code 0 if OK)

```bash
ra -S 213.60.255.45:561 -M poll -D 2 ; echo $?
```

Expected output:

```
0
```

---

## 4. Example: Stream Only DNS Traffic

```bash
ra -S 213.60.255.45:561 -n -L - udp port 53
```

---

## 5. Exit the Container

```bash
exit
```

---

## 6. One-liner (Run Client Without Entering Shell)

You can run `ra` directly:

```bash
sudo docker run --rm -it \
  --entrypoint ra \
  roalt67184/argus:20251027 \
  -S 213.60.255.45:561 -n -L
```
