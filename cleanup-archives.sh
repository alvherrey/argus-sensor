#!/bin/bash
# Script para gestión de archivos Argus rotativos
# Limpia archivos antiguos y opcionalmente comprime

set -euo pipefail

ARCHIVE_DIR="${ARCHIVE_DIR:-./argus-data/archive}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
COMPRESS="${COMPRESS:-yes}"

echo "============================================"
echo "Argus Archive Management"
echo "============================================"
echo "Archive dir: ${ARCHIVE_DIR}"
echo "Retention: ${RETENTION_DAYS} days"
echo "Compress: ${COMPRESS}"
echo "============================================"

# Comprimir archivos de más de 2 días
if [ "${COMPRESS}" = "yes" ]; then
  echo "Comprimiendo archivos antiguos (>2 días)..."
  find "${ARCHIVE_DIR}" -name "*.out" -type f -mtime +2 ! -name "*.gz" -exec gzip -v {} \;
  echo "Compresión completada"
fi

# Eliminar archivos más antiguos que RETENTION_DAYS
echo "Eliminando archivos con más de ${RETENTION_DAYS} días..."
find "${ARCHIVE_DIR}" -type f -mtime +${RETENTION_DAYS} -delete
echo "Limpieza completada"

# Eliminar directorios vacíos
echo "Limpiando directorios vacíos..."
find "${ARCHIVE_DIR}" -type d -empty -delete
echo "Directorios vacíos eliminados"

# Mostrar uso de disco
echo ""
echo "Uso de disco actual:"
du -sh "${ARCHIVE_DIR}"
echo ""
echo "Archivos por día:"
find "${ARCHIVE_DIR}" -name "*.out*" -type f -printf '%TY-%Tm-%Td\n' | sort | uniq -c | tail -10

echo "============================================"
echo "Gestión completada"
echo "============================================"
