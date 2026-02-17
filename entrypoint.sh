#!/bin/bash
set -euo pipefail

# Variables de entorno con valores por defecto
INTERFACE="${INTERFACE:-eth0}"
PORT="${PORT:-561}"
ROTATION_INTERVAL="${ROTATION_INTERVAL:-1d}"
ENABLE_ENRICHMENT="${ENABLE_ENRICHMENT:-yes}"
ENABLE_INFLUXDB="${ENABLE_INFLUXDB:-no}"
AGGREGATION_INTERVAL="${AGGREGATION_INTERVAL:-1m}"

echo "========================================="
echo "Argus Sensor - Arquitectura Oficial"
echo "========================================="
echo "Interface: ${INTERFACE}"
echo "Argus Port: ${PORT}"
echo "Rotation: ${ROTATION_INTERVAL}"
echo "Enrichment: ${ENABLE_ENRICHMENT}"
echo "InfluxDB: ${ENABLE_INFLUXDB}"
if [ "${ENABLE_INFLUXDB}" = "yes" ]; then
  echo "Aggregation: ${AGGREGATION_INTERVAL}"
fi
echo "========================================="

# Crear estructura de directorios
mkdir -p /var/log/argus/archive

# Iniciar Argus con stream TCP en background
# Use -w flag to override argus.conf ARGUS_OUTPUT_STREAM (guaranteed by Argus CLI)
# Note: argus -w expects just host:port format, not argus:// URI scheme
echo "Iniciando Argus con stream TCP en puerto ${PORT}..."
argus -i "${INTERFACE}" -P "${PORT}" -U 256 -m -R -S 5 -w localhost:"${PORT}" -- "ip or arp" &
ARGUS_PID=$!

# Dar tiempo a Argus para iniciar el stream
sleep 3

# =========================================================================
# ARQUITECTURA OFICIAL ARGUS (basada en documentación openargus.org)
# =========================================================================
# radium: Hub central de distribución con enriquecimiento integrado
# - Multiplexor oficial para streams argus
# - Enriquece con RADIUM_CLASSIFIER_FILE (ralabel.conf)
# - Distribuye a múltiples consumidores (archivos + time-series)
# =========================================================================

if [ "${ENABLE_ENRICHMENT}" = "yes" ]; then
  echo "[1/3] Iniciando radium como hub central (puerto 562)..."
  
  # radium recibe de argus, enriquece con classifier (via radium.conf), redistribuye
  # -S: source argus, -P: puerto de salida, -f: config file
  radium -S localhost:${PORT} -P 562 -f /etc/radium.conf &
  RADIUM_PID=$!
  sleep 3
  
  echo "✓ Radium: Argus:${PORT} → radium:562 (enriquecido via RADIUM_CLASSIFIER_FILE)"
else
  # Sin radium: acceso directo a argus
  RADIUM_PID=""
  STREAM_PORT=${PORT}
fi

# Determinar puerto de stream según arquitectura
if [ -n "${RADIUM_PID}" ]; then
  STREAM_PORT=562
else
  STREAM_PORT=${PORT}
fi

# Select archive splitter command based on installed argus-clients version.
# Newer releases moved rasplit functionality into rastream.
if command -v rasplit >/dev/null 2>&1; then
  ARCHIVER_BIN="rasplit"
elif command -v rastream >/dev/null 2>&1; then
  ARCHIVER_BIN="rastream"
else
  echo "ERROR: Neither 'rasplit' nor 'rastream' is available in PATH"
  echo "       Verify argus-clients installation in the image."
  exit 1
fi

# =========================================================================
# PIPELINE 1: Archivos completos (Ground Truth + ML)
# =========================================================================
# rasplit/rastream lee de radium (enriquecido) o argus (primitivo)
# Archivos contienen: payload + headers + country codes + ASN
# Uso: Batch ML, análisis forense, retraining
# =========================================================================

echo "[2/3] Iniciando ${ARCHIVER_BIN} para archivos ML..."
"${ARCHIVER_BIN}" -S localhost:${STREAM_PORT} \
  -M time ${ROTATION_INTERVAL} \
  -w "/var/log/argus/archive/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S.out" \
  -Z b &
ARCHIVER_PID=$!

if [ "${ENABLE_ENRICHMENT}" = "yes" ]; then
  echo "✓ Archivos ML: radium:562 → ${ARCHIVER_BIN} (payload + enrich)"
else
  echo "✓ Archivos ML: argus:${PORT} → ${ARCHIVER_BIN} (primitivo)"
fi

# =========================================================================
# PIPELINE 2: InfluxDB (Time-Series Dashboards)
# =========================================================================
# rastrip: Elimina solo payload (suser/duser DSRs)
# racluster: Agrega por flow key (reduce cardinalidad)
# rabins: Binning temporal (reduce ingestión)
# ra: Formatea a CSV para telegraf
# =========================================================================

if [ "${ENABLE_INFLUXDB}" = "yes" ]; then
  echo "[3/3] Iniciando pipeline InfluxDB (sin payload, agregado)..."
  
  # Crear named pipe en volumen compartido con host
  PIPE_PATH="/var/log/argus/argus-telegraf.pipe"
  # Remove if exists and is not a pipe
  if [ -e "${PIPE_PATH}" ] && [ ! -p "${PIPE_PATH}" ]; then
    echo "WARNING: ${PIPE_PATH} exists but is not a pipe, removing..."
    rm -f "${PIPE_PATH}"
  fi
  if ! mkfifo "${PIPE_PATH}" 2>/dev/null; then
    if [ ! -p "${PIPE_PATH}" ]; then
      echo "ERROR: Failed to create named pipe at ${PIPE_PATH}"
      exit 1
    fi
  fi
  
  # rastrip elimina SOLO payload (DSRs suser/duser)
  # racluster agrega por flow key COMPLETO (incluye todos los campos de salida)
  # rabins hace binning temporal
  # ra formatea con -u (unix timestamp) y -n (numeric ports/ASN)
  rastrip -S localhost:${STREAM_PORT} -M dsrs="-suser,-duser" \
    | racluster -m saddr daddr proto sport dport sco dco sas das \
    | rabins -M time ${AGGREGATION_INTERVAL} \
    | ra -u -n -s stime saddr daddr proto sport dport sco dco sas das spkts dpkts sbytes dbytes dur -c , \
    > "${PIPE_PATH}" &
  INFLUXDB_PID=$!
  
  echo "✓ InfluxDB: radium:${STREAM_PORT} → rastrip → racluster → rabins:${AGGREGATION_INTERVAL} → ${PIPE_PATH}"
  echo "  (Telegraf debe leer desde el host: <repo>/argus-data/argus-telegraf.pipe)"
else
  INFLUXDB_PID=""
fi

echo "========================================="
echo "Estado de servicios:"
echo "  Argus PID: ${ARGUS_PID} (puerto ${PORT})"
if [ -n "${RADIUM_PID}" ]; then
  echo "  Radium PID: ${RADIUM_PID} (puerto 562)"
fi
  echo "  ${ARCHIVER_BIN} PID: ${ARCHIVER_PID}"
if [ -n "${INFLUXDB_PID}" ]; then
  echo "  Pipeline InfluxDB PID: ${INFLUXDB_PID} (named pipe → Telegraf)"
fi
echo ""
echo "Configuración:"
echo "  Rotación: ${ROTATION_INTERVAL}"
echo "  Enriquecimiento: ${ENABLE_ENRICHMENT}"
echo "  Archivos: /var/log/argus/archive/YYYY/MM/DD/"
echo "========================================="

# Función para cleanup graceful
cleanup() {
  echo "Recibida señal de parada, deteniendo servicios..."
  
  # Detener en orden inverso al inicio
  if [ -n "${INFLUXDB_PID}" ]; then
    echo "  Deteniendo pipeline InfluxDB..."
    kill -SIGTERM ${INFLUXDB_PID} 2>/dev/null || true
    wait ${INFLUXDB_PID} 2>/dev/null || true
  fi
  
  echo "  Deteniendo ${ARCHIVER_BIN}..."
  kill -SIGTERM ${ARCHIVER_PID} 2>/dev/null || true
  wait ${ARCHIVER_PID} 2>/dev/null || true
  
  if [ -n "${RADIUM_PID}" ]; then
    echo "  Deteniendo radium..."
    kill -SIGTERM ${RADIUM_PID} 2>/dev/null || true
    wait ${RADIUM_PID} 2>/dev/null || true
  fi
  
  echo "  Deteniendo argus..."
  kill -SIGINT ${ARGUS_PID} 2>/dev/null || true
  wait ${ARGUS_PID} 2>/dev/null || true
  
  echo "✓ Servicios detenidos correctamente"
  exit 0
}

trap cleanup SIGTERM SIGINT

# Mantener el contenedor vivo y monitorear procesos
while true; do
  if ! kill -0 ${ARGUS_PID} 2>/dev/null; then
    echo "ERROR: Argus ha terminado inesperadamente"
    exit 1
  fi
  
  if [ -n "${RADIUM_PID}" ] && ! kill -0 ${RADIUM_PID} 2>/dev/null; then
    echo "ERROR: Radium ha terminado inesperadamente"
    exit 1
  fi
  
  if ! kill -0 ${ARCHIVER_PID} 2>/dev/null; then
    echo "ERROR: ${ARCHIVER_BIN} ha terminado inesperadamente"
    exit 1
  fi
  
  if [ -n "${INFLUXDB_PID}" ] && ! kill -0 ${INFLUXDB_PID} 2>/dev/null; then
    echo "WARNING: Pipeline InfluxDB ha terminado inesperadamente"
    INFLUXDB_PID=""
  fi
  
  sleep 10
done
