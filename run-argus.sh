#!/usr/bin/env bash
set -euo pipefail

# Argus Sensor Deployment Script
# This script validates prerequisites and starts the sensor

IFACE=${1:-}
SERVICE=${SERVICE:-argus}
IMAGE=${IMAGE:-argus:latest}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================="
echo "Argus Sensor - Deployment Script"
echo "========================================="

# Validate interface argument
if [ -z "$IFACE" ]; then
  echo -e "${RED}Error: Interfaz de red requerida${NC}" >&2
  echo "Uso: $0 <INTERFACE>" >&2
  echo "Ejemplo: $0 ens160" >&2
  echo "Interfaces disponibles: $(ls /sys/class/net | tr '\n' ' ')" >&2
  exit 1
fi

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  echo -e "${RED}Error: interfaz $IFACE no encontrada${NC}" >&2
  echo "Interfaces: $(ls /sys/class/net | tr '\n' ' ')" >&2
  exit 2
fi

# Check prerequisites
echo ""
echo "Verificando prerequisitos..."

# 1. Check .env file
if [ ! -f .env ]; then
  echo -e "${YELLOW}⚠️  Archivo .env no encontrado${NC}"
  echo "Creando desde .env.example..."
  cp .env.example .env
  sed -i "s/^INTERFACE=.*/INTERFACE=$IFACE/" .env
  echo -e "${GREEN}✓ .env creado - REVISA Y AJUSTA las variables${NC}"
  echo ""
  echo "Variables importantes:"
  echo "  - ENABLE_ENRICHMENT=yes (requiere GeoIP databases)"
  echo "  - ENABLE_INFLUXDB=no (habilitar solo si tienes InfluxDB+Telegraf)"
  echo "  - ROTATION_INTERVAL=1d (1h, 6h, 12h, 1d, 1w)"
  echo ""
else
  echo -e "${GREEN}✓ .env encontrado${NC}"
fi

# 2. Check GeoIP databases (if enrichment enabled)
source .env 2>/dev/null || true
if [ "${ENABLE_ENRICHMENT:-yes}" = "yes" ]; then
  if [ ! -f geoip-data/delegated-ipv4-latest ] || [ ! -f geoip-data/GeoLite2-ASN.mmdb ]; then
    echo -e "${RED}✗ Bases GeoIP no encontradas${NC}"
    echo ""
    echo "El enrichment está habilitado pero faltan las bases de datos."
    echo "Debes ejecutar:"
    echo ""
    echo "  export MAXMIND_LICENSE_KEY='tu_license_key'"
    echo "  ./download-geoip.sh"
    echo ""
    echo "Obtén una license key gratuita en:"
    echo "  https://www.maxmind.com/en/geolite2/signup"
    echo ""
    echo -e "${YELLOW}Opciones:${NC}"
    echo "  1. Descargar GeoIP ahora (requiere MAXMIND_LICENSE_KEY)"
    echo "  2. Continuar sin enrichment (editar .env: ENABLE_ENRICHMENT=no)"
    echo "  3. Cancelar"
    echo ""
    read -p "Selecciona opción [1/2/3]: " choice
    
    case $choice in
      1)
        if [ -z "${MAXMIND_LICENSE_KEY:-}" ]; then
          echo -e "${RED}Error: MAXMIND_LICENSE_KEY no configurada${NC}"
          echo "Ejecuta: export MAXMIND_LICENSE_KEY='tu_key'"
          exit 3
        fi
        ./download-geoip.sh || exit 3
        ;;
      2)
        echo "Deshabilitando enrichment..."
        sed -i 's/^ENABLE_ENRICHMENT=.*/ENABLE_ENRICHMENT=no/' .env
        echo -e "${YELLOW}⚠️  Enrichment deshabilitado - campos sco, dco, sas, das estarán vacíos${NC}"
        ;;
      3)
        echo "Cancelado por usuario"
        exit 0
        ;;
      *)
        echo -e "${RED}Opción inválida${NC}"
        exit 1
        ;;
    esac
  else
    echo -e "${GREEN}✓ Bases GeoIP encontradas${NC}"
  fi
fi

# 3. Check Telegraf configuration (if InfluxDB enabled)
if [ "${ENABLE_INFLUXDB:-no}" = "yes" ]; then
  echo ""
  echo -e "${YELLOW}⚠️  InfluxDB pipeline habilitado${NC}"
  echo ""
  echo "Asegúrate de haber configurado Telegraf en el HOST:"
  echo "  1. cp telegraf.conf /etc/telegraf/telegraf.d/argus.conf"
  echo "  2. Editar ruta absoluta del pipe (línea 10)"
  echo "  3. Configurar credenciales InfluxDB"
  echo "  4. sudo systemctl restart telegraf"
  echo ""
  echo "Ver: DEPLOYMENT.md sección 4 para detalles completos"
  echo ""
  read -p "¿Telegraf ya está configurado? [s/N]: " telegraf_ready
  if [[ ! "$telegraf_ready" =~ ^[sS]$ ]]; then
    echo -e "${YELLOW}⚠️  Continúa deployment, pero pipeline InfluxDB no funcionará sin Telegraf${NC}"
    echo "    Puedes configurar Telegraf después y reiniciar: docker compose restart"
  fi
fi

# 3. Setup interface
echo ""
echo "Configurando interfaz $IFACE..."
if ! ip link show "$IFACE" | grep -q PROMISC; then
  sudo ip link set dev "$IFACE" up
  sudo ip link set dev "$IFACE" promisc on
  echo -e "${GREEN}✓ $IFACE UP y en modo promiscuo${NC}"
else
  echo -e "${GREEN}✓ $IFACE ya está en modo promiscuo${NC}"
fi

# 4. Build/check image
echo ""
if ! sudo docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Construyendo imagen $IMAGE..."
  sudo docker compose build "$SERVICE"
  echo -e "${GREEN}✓ Imagen construida${NC}"
else
  echo -e "${GREEN}✓ Imagen $IMAGE existe${NC}"
  read -p "¿Reconstruir imagen? [s/N]: " rebuild
  if [[ "$rebuild" =~ ^[sS]$ ]]; then
    sudo docker compose build "$SERVICE"
    echo -e "${GREEN}✓ Imagen reconstruida${NC}"
  fi
fi

# 5. Start services
echo ""
echo "Iniciando servicios..."
export INTERFACE="$IFACE"
sudo -E docker compose up -d --no-build

# Wait for health check
echo ""
echo "Esperando health check..."
sleep 5

# 6. Validate deployment
echo ""
echo "Validando deployment..."

if sudo docker compose ps | grep -q "healthy"; then
  echo -e "${GREEN}✓ Contenedor healthy${NC}"
else
  echo -e "${YELLOW}⚠️  Health check aún no pasó (espera 20-30s)${NC}"
fi

# Try to connect to stream
if timeout 3 sudo docker compose exec -T argus ra -S localhost:${PORT:-561} -c 1 >/dev/null 2>&1; then
  echo -e "${GREEN}✓ Stream Argus respondiendo (puerto ${PORT:-561})${NC}"
else
  echo -e "${YELLOW}⚠️  Stream no responde aún (puede tardar unos segundos)${NC}"
fi

# Check radium if enrichment enabled
if [ "${ENABLE_ENRICHMENT:-yes}" = "yes" ]; then
  if timeout 3 sudo docker compose exec -T argus ra -S localhost:562 -c 1 >/dev/null 2>&1; then
    echo -e "${GREEN}✓ Stream radium respondiendo (puerto 562, con enrichment)${NC}"
  else
    echo -e "${YELLOW}⚠️  Stream radium no responde aún${NC}"
  fi
fi

echo ""
echo "========================================="
echo -e "${GREEN}Argus Sensor desplegado correctamente${NC}"
echo "========================================="
echo ""
echo "Información:"
echo "  Interface: $IFACE"
echo "  Argus Stream: localhost:${PORT:-561}"
echo "  Radium Stream: localhost:562 (enriched)"
echo "  Archivos: ./argus-data/archive/YYYY/MM/DD/"
echo "  Enrichment: ${ENABLE_ENRICHMENT:-yes}"
echo "  InfluxDB: ${ENABLE_INFLUXDB:-no}"
echo ""
echo "Comandos útiles:"
echo "  Ver logs:      sudo docker compose logs -f"
echo "  Ver estado:    sudo docker compose ps"
echo "  Test stream:   sudo docker compose exec argus ra -S localhost:${PORT:-561} -c 10"
echo "  Test enrich:   sudo docker compose exec argus ra -S localhost:562 -s saddr daddr sco dco sas das -c 5"
echo "  Parar:         sudo docker compose down"
echo ""
echo "Documentación:"
echo "  README.md - Arquitectura y uso"
echo "  DEPLOYMENT.md - Guía de producción completa"
echo "========================================="
