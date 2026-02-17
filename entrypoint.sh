#!/bin/bash
set -e

ARCHIVE_DIR="/var/log/argus/archive"
mkdir -p "$ARCHIVE_DIR"

# Arranca Argus con archivo de configuración (escucha en puerto 561)
argus -F /etc/argus.conf -i ${INTERFACE:-any} -P ${PORT:-561} &
ARGUS_PID=$!

# Espera que Argus esté listo
sleep 2

# rastream consume del stream de Argus y rota por día
rastream -S localhost:${PORT:-561} -M time 1d \
  -w "$ARCHIVE_DIR/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S" &

# Espera a que termine Argus
wait $ARGUS_PID
