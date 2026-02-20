#!/bin/bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════════════════
# Argus Sensor — entrypoint enterprise
#
# Arquitectura:              ┌─────────────────┐
#   NIC ──► argus :561 ──► radium :562 ──►─┤ ra / rasqlinsert │
#            (sensor)        (multiplexor)   │ dashboards / ML  │
#                              │             └─────────────────┘
#                              └──► rastream (archiva a disco)
#
# Puertos por defecto:
#   561 — argus sensor  (127.0.0.1, solo radium)
#   562 — radium público (0.0.0.0,  N clientes)
#
# IMPORTANTE: -S y -P se pasan SOLO por CLI.
#   Si se definen también en radium.conf, radium abre conexiones
#   duplicadas al sensor y cada flow se emite dos veces.
# ═══════════════════════════════════════════════════════════════════════

ARCHIVE_DIR="/var/log/argus/archive"
mkdir -p "$ARCHIVE_DIR"

SENSOR_PORT=${SENSOR_PORT:-561}   # puerto argus  (default 561)
PUBLIC_PORT=${PORT:-562}          # puerto radium (default 562)
INTERFACE=${INTERFACE:-any}

# ── 1. Argus sensor (escucha en loopback, SENSOR_PORT) ──────────────
argus -F /etc/argus.conf -i "$INTERFACE" -P "$SENSOR_PORT" &
ARGUS_PID=$!
echo "[entrypoint] argus  PID=$ARGUS_PID  iface=$INTERFACE  127.0.0.1:$SENSOR_PORT"

# Esperar a que argus abra el puerto antes de arrancar radium
for i in $(seq 1 20); do
  if bash -c "exec 3<>/dev/tcp/127.0.0.1/$SENSOR_PORT" 2>/dev/null; then
    echo "[entrypoint] argus ready after ${i}s"
    break
  fi
  sleep 1
done

# ── 2. Radium multiplexor (único cliente de argus → N clientes) ─────
#   -S y -P SOLO aquí, NUNCA en radium.conf (ver nota arriba)
radium -f /etc/radium.conf -S 127.0.0.1:"$SENSOR_PORT" -P "$PUBLIC_PORT" &
RADIUM_PID=$!
echo "[entrypoint] radium PID=$RADIUM_PID  127.0.0.1:$SENSOR_PORT → 0.0.0.0:$PUBLIC_PORT"

# Esperar a que radium abra el puerto público
for i in $(seq 1 10); do
  if bash -c "exec 3<>/dev/tcp/127.0.0.1/$PUBLIC_PORT" 2>/dev/null; then
    echo "[entrypoint] radium ready after ${i}s"
    break
  fi
  sleep 1
done

# ── 3. rastream archiva los flujos (comprimidos, rotación diaria) ───
#   -z  comprime los archivos de salida (gzip)
#   -M time 1d  rota cada 24h
rastream -S localhost:"$PUBLIC_PORT" -M time 1d -z \
  -w "$ARCHIVE_DIR/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S" &
RASTREAM_PID=$!
echo "[entrypoint] rastream PID=$RASTREAM_PID  archiving (compressed) via radium:$PUBLIC_PORT"

echo "[entrypoint] ════ All processes started ════"
echo "[entrypoint]   sensor:  127.0.0.1:$SENSOR_PORT (argus, internal)"
echo "[entrypoint]   radium:  0.0.0.0:$PUBLIC_PORT   (public, multi-client)"
echo "[entrypoint]   archive: $ARCHIVE_DIR (daily rotation, gzip compressed)"

# ── Cleanup: señal a todos los procesos hijos y esperar ─────────────
cleanup() {
  echo "[entrypoint] shutting down…"
  kill $RASTREAM_PID 2>/dev/null || true
  kill $RADIUM_PID   2>/dev/null || true
  kill $ARGUS_PID    2>/dev/null || true
  wait 2>/dev/null
  echo "[entrypoint] all processes stopped"
}
trap cleanup SIGTERM SIGINT EXIT

# ── Monitor: si algún proceso crítico muere, reiniciar todo ─────────
while true; do
  if ! kill -0 $ARGUS_PID 2>/dev/null; then
    echo "[entrypoint] FATAL: argus (PID=$ARGUS_PID) died unexpectedly"
    exit 1
  fi
  if ! kill -0 $RADIUM_PID 2>/dev/null; then
    echo "[entrypoint] FATAL: radium (PID=$RADIUM_PID) died unexpectedly"
    exit 1
  fi
  if ! kill -0 $RASTREAM_PID 2>/dev/null; then
    echo "[entrypoint] WARNING: rastream died, restarting…"
    rastream -S localhost:"$PUBLIC_PORT" -M time 1d -z \
      -w "$ARCHIVE_DIR/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S" &
    RASTREAM_PID=$!
    echo "[entrypoint] rastream restarted PID=$RASTREAM_PID"
  fi
  sleep 5
done
