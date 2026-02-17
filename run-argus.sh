#!/usr/bin/env bash
set -euo pipefail

IFACE=${1:-ens160}
SERVICE=${SERVICE:-argus}
IMAGE=${IMAGE:-argus:latest}

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo "Error: interfaz $IFACE no encontrada. Interfaces: $(ls /sys/class/net)" >&2
  exit 2
fi

echo "Poniendo $IFACE UP y modo promiscuo..."
sudo ip link set dev "$IFACE" up
sudo ip link set dev "$IFACE" promisc on

export INTERFACE="$IFACE"
export PORT="${PORT:-561}"
export ROTATION_INTERVAL="${ROTATION_INTERVAL:-1d}"

echo "========================================="
echo "Configuración de rotación:"
echo "  ROTATION_INTERVAL=${ROTATION_INTERVAL}"
echo "  Opciones válidas: 1h, 6h, 12h, 1d, 1w"
echo "========================================="

if ! sudo docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Imagen $IMAGE no existe; construyendo (con cache)..."
  sudo -E docker compose build "$SERVICE"
else
  echo "Imagen $IMAGE ya existe; saltando build."
fi

echo "Arrancando docker compose (detached) sin build..."
sudo -E docker compose up -d --no-build

echo "========================================="
echo "Argus iniciado correctamente"
echo "Stream: localhost:${PORT}"
echo "Archivos: ./argus-data/archive/YYYY/MM/DD/"
echo "Logs: sudo docker compose logs -f"
echo "========================================="
