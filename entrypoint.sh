#!/bin/bash
set -e

ARCHIVE_DIR="/var/log/argus/archive"
mkdir -p "$ARCHIVE_DIR"

SENSOR_PORT=${SENSOR_PORT:-562}   # puerto interno de argus (solo radium)
PUBLIC_PORT=${PORT:-561}          # puerto público de radium (clientes)

# ── 1. Argus sensor (escucha internamente en SENSOR_PORT) ──
argus -F /etc/argus.conf -i ${INTERFACE:-any} -P $SENSOR_PORT &
ARGUS_PID=$!
echo "[entrypoint] argus PID=$ARGUS_PID  listening on 127.0.0.1:$SENSOR_PORT"
sleep 2

# ── 2. Radium multiplexor (consume de argus, sirve en PUBLIC_PORT) ──
#   Radium es el ÚNICO cliente de argus.
#   Todos los clientes externos (ra, rasqlinsert, dashboards…) van a radium.
radium -f /etc/radium.conf -S 127.0.0.1:$SENSOR_PORT -P $PUBLIC_PORT &
RADIUM_PID=$!
echo "[entrypoint] radium PID=$RADIUM_PID  127.0.0.1:$SENSOR_PORT → 0.0.0.0:$PUBLIC_PORT"
sleep 1

# ── 3. rastream archiva los flujos (se conecta a radium en PUBLIC_PORT) ──
rastream -S localhost:$PUBLIC_PORT -M time 1d \
  -w "$ARCHIVE_DIR/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S" &
RASTREAM_PID=$!
echo "[entrypoint] rastream PID=$RASTREAM_PID  archiving via radium:$PUBLIC_PORT"

# ── Cleanup: parar todo si cualquier proceso muere ──
cleanup() {
  echo "[entrypoint] shutting down…"
  kill $RASTREAM_PID $RADIUM_PID $ARGUS_PID 2>/dev/null || true
  wait
}
trap cleanup SIGTERM SIGINT

# Espera a que termine argus (proceso principal)
wait $ARGUS_PID
