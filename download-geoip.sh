#!/bin/bash
# Script para descargar bases de datos GeoIP necesarias para ralabel
# Ejecutar antes del primer despliegue

set -euo pipefail

GEOIP_DIR="${GEOIP_DIR:-./geoip-data}"
MAXMIND_LICENSE_KEY="${MAXMIND_LICENSE_KEY:-}"

echo "========================================="
echo "Descargando bases de datos GeoIP"
echo "========================================="

# Crear directorio si no existe
mkdir -p "${GEOIP_DIR}"

# 1. Descargar delegated-ipv4-latest (RIR database para country codes)
echo "[1/2] Descargando delegated-ipv4-latest (Country Codes)..."
curl -sL https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest \
  -o "${GEOIP_DIR}/delegated-arin-latest"

curl -sL https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest \
  -o "${GEOIP_DIR}/delegated-ripe-latest"

curl -sL https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest \
  -o "${GEOIP_DIR}/delegated-apnic-latest"

# Merge all RIR databases
cat "${GEOIP_DIR}"/delegated-*-latest > "${GEOIP_DIR}/delegated-ipv4-latest"
echo "✓ Country codes database descargada"

# 2. Descargar MaxMind GeoLite2-ASN (requiere licencia gratuita)
echo "[2/2] Descargando GeoLite2-ASN..."

if [ -z "${MAXMIND_LICENSE_KEY}" ]; then
  echo "⚠️  MAXMIND_LICENSE_KEY no configurada"
  echo ""
  echo "Para descargar GeoLite2-ASN necesitas una cuenta gratuita en MaxMind:"
  echo "1. Registrarse: https://www.maxmind.com/en/geolite2/signup"
  echo "2. Generar license key: https://www.maxmind.com/en/accounts/current/license-key"
  echo "3. Exportar: export MAXMIND_LICENSE_KEY='tu_license_key'"
  echo "4. Volver a ejecutar este script"
  echo ""
  echo "Alternativa: Descargar manualmente y colocar en ${GEOIP_DIR}/GeoLite2-ASN.mmdb"
  exit 1
else
  # Descargar usando la API de MaxMind
  curl -sL "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-ASN&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz" \
    -o /tmp/GeoLite2-ASN.tar.gz
  
  tar -xzf /tmp/GeoLite2-ASN.tar.gz -C /tmp/
  mv /tmp/GeoLite2-ASN_*/GeoLite2-ASN.mmdb "${GEOIP_DIR}/"
  rm -rf /tmp/GeoLite2-ASN*
  
  echo "✓ GeoLite2-ASN database descargada"
fi

echo "========================================="
echo "Bases de datos instaladas en:"
ls -lh "${GEOIP_DIR}"
echo "========================================="
echo ""
echo "Siguiente paso: Iniciar Argus con enriquecimiento"
echo "  ENABLE_ENRICHMENT=yes ./run-argus.sh"
