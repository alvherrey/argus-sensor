# Production Deployment Checklist

Este documento lista los pasos necesarios para un despliegue production-ready del sensor Argus.

## Pre-requisitos

- [ ] Docker y Docker Compose instalados
- [ ] Interfaz de red disponible para captura (TAP, SPAN, interface dedicada)
- [ ] Permisos NET_RAW y NET_ADMIN (docker usa host networking)
- [ ] Espacio en disco suficiente (~5GB/día para archivos .out con payload)
- [ ] **MaxMind License Key** (gratuita) para descargar GeoLite2-ASN

**IMPORTANTE:** Las bases GeoIP son prerequisito obligatorio para enrichment. Sin ellas, los campos `sco`, `dco`, `sas`, `das` estarán vacíos.

## Setup Inicial

### 1. Clonar repositorio

```bash
git clone https://github.com/alvherrey/argus-sensor.git
cd argus-sensor
```

### 2. Descargar bases GeoIP (CRÍTICO para enrichment)

**⚠️ Este paso es OBLIGATORIO antes del primer despliegue con `ENABLE_ENRICHMENT=yes`**

```bash
# Registrarse en MaxMind (gratuito): https://www.maxmind.com/en/geolite2/signup
# Generar license key: https://www.maxmind.com/en/accounts/current/license-key

export MAXMIND_LICENSE_KEY='tu_license_key_aqui'
chmod +x download-geoip.sh
./download-geoip.sh
```

**Verificar descarga exitosa:**

```bash
ls -lh geoip-data/
# Debe mostrar:
# - delegated-ipv4-latest (~50MB)
# - GeoLite2-ASN.mmdb (~5MB)
```

❌ **SIN ESTAS BASES, EL ENRICHMENT NO FUNCIONARÁ** (campos sco, dco, sas, das estarán vacíos)

### 3. Configurar variables de entorno

```bash
cp .env.example .env
vim .env
```

**Configuración mínima:**

```bash
INTERFACE=eth0              # Cambiar por tu interfaz (ip link show)
PORT=561                    # Puerto TCP de Argus
ROTATION_INTERVAL=1d        # 1h, 6h, 12h, 1d, 1w
ENABLE_ENRICHMENT=yes       # Requiere paso 2 completado
ENABLE_INFLUXDB=no          # Activar solo si tienes InfluxDB configurado
```

### 4. Configurar Telegraf (solo si ENABLE_INFLUXDB=yes)

```bash
cp telegraf.conf /etc/telegraf/telegraf.d/argus.conf
vim /etc/telegraf/telegraf.d/argus.conf
```

**CRÍTICO: Actualizar ruta absoluta del pipe:**

```toml
# Línea 8 de telegraf.conf
files = ["/ruta/completa/a/argus-sensor/argus-data/argus-telegraf.pipe"]
#         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#         Reemplazar con tu path absoluto (pwd en el directorio del repo)
```

**Ejemplo para usuario 'alvaro':**

```bash
# Obtener ruta absoluta
cd /home/alvaro/Desktop/work/argus/git/argus-sensor
pwd
# Output: /home/alvaro/Desktop/work/argus/git/argus-sensor

# Editar telegraf.conf línea 8:
files = ["/home/alvaro/Desktop/work/argus/git/argus-sensor/argus-data/argus-telegraf.pipe"]
```

Configurar credenciales InfluxDB:

```toml
# Línea 64-67
[[outputs.influxdb_v2]]
  urls = ["http://tu-influxdb:8086"]
  token = "${INFLUXDB_TOKEN}"
  organization = "${INFLUXDB_ORG}"
  bucket = "${INFLUXDB_BUCKET}"
```

Reiniciar Telegraf:

```bash
sudo systemctl restart telegraf
sudo systemctl status telegraf
```

### 5. Crear estructura de directorios

```bash
mkdir -p argus-data/archive
chmod -R 755 argus-data
```

### 6. Build y deploy

```bash
# Build de la imagen
docker compose build

# Iniciar sensor
docker compose up -d

# Verificar logs
docker compose logs -f
```

## Validación Post-Deploy

### Verificar Argus captura tráfico

```bash
# Debe mostrar flows en tiempo real (usa el PORT configurado en .env, default 561)
docker compose exec argus bash -c 'ra -S localhost:${PORT:-561} -c 5'
```

### Verificar radium enriquece datos

```bash
# Debe mostrar country codes (sco, dco) y ASN (sas, das)
# radium escucha en puerto 562 (fijo)
docker compose exec argus ra -S localhost:562 -s saddr daddr sco dco sas das -c 5

# Ejemplo de salida esperada:
#       SrcAddr        DstAddr sco dco    sas    das
#   192.168.1.10    8.8.8.8     --  US     --  15169
#   10.0.0.5        1.1.1.1     --  AU     --  13335
```

❌ Si ves `--` en todos los campos geo, las bases GeoIP no están cargadas correctamente.

### Verificar archivos se están generando

```bash
# Debe mostrar archivos .out con timestamp reciente
ls -lh argus-data/archive/$(date +%Y/%m/%d)/

# Inspeccionar contenido (debe incluir sco, dco, sas, das)
docker compose exec argus ra -r /var/log/argus/archive/$(date +%Y/%m/%d)/argus.*.out \
  -s saddr daddr sco dco sas das bytes -c 10
```

### Verificar rotación funciona

```bash
# Esperar al siguiente intervalo de rotación y verificar nuevo archivo
watch -n 10 'ls -lht argus-data/archive/$(date +%Y/%m/%d)/ | head -5'
```

### Verificar pipeline InfluxDB (si habilitado)

```bash
# 1. Verificar pipe existe y es FIFO
ls -l argus-data/argus-telegraf.pipe
# Debe mostrar: prw-r--r-- (la 'p' indica named pipe)

# 2. Verificar procesos del pipeline
docker compose exec argus ps aux | grep -E 'rastrip|racluster|rabins'

# 3. Ver datos en el pipe (CTRL+C para salir)
cat argus-data/argus-telegraf.pipe | head -5

# 4. Verificar Telegraf lee el pipe
sudo journalctl -u telegraf -f | grep argus

# 5. Verificar datos en InfluxDB
influx query 'from(bucket:"argus") |> range(start: -5m) |> limit(n:10)'
```

## Problemas Comunes

### ❌ Enrichment no funciona (campos geo vacíos)

**Causa:** Bases GeoIP no descargadas o mal montadas.

**Solución:**

```bash
# 1. Verificar bases existen
ls -lh geoip-data/
# Debe mostrar delegated-ipv4-latest y GeoLite2-ASN.mmdb

# 2. Si faltan, descargarlas
export MAXMIND_LICENSE_KEY='tu_key'
./download-geoip.sh

# 3. Reiniciar contenedor
docker compose restart
```

### ❌ Telegraf no lee el pipe

**Causa:** Ruta del pipe incorrecta o relativa.

**Solución:**

```bash
# 1. Verificar ruta absoluta del pipe
readlink -f argus-data/argus-telegraf.pipe

# 2. Actualizar telegraf.conf con ruta absoluta
vim /etc/telegraf/telegraf.d/argus.conf
# Línea 8: files = ["/ruta/absoluta/argus-data/argus-telegraf.pipe"]

# 3. Reiniciar Telegraf
sudo systemctl restart telegraf
```

### ❌ No se generan archivos .out

**Causa:** rasplit no está conectándose a radium.

**Solución:**

```bash
# 1. Verificar radium está corriendo (puerto 562 fijo)
docker compose exec argus ra -S localhost:562 -c 1

# 2. Verificar logs de rasplit
docker compose logs | grep rasplit

# 3. Verificar permisos
ls -ld argus-data/archive/
# Debe ser escribible
```

### ❌ Puerto dinámico no funciona

**Causa:** argus.conf hardcodeado en 561, no respeta PORT.

**Solución:**

El entrypoint.sh usa `-w localhost:${PORT}` como flag CLI de argus (override garantizado).
Verificar con:

```bash
docker compose exec argus ps aux | grep argus
# Debe mostrar: argus -i ... -P <PORT> ... -w localhost:<PORT>
```

## Monitoreo Continuo

### Health check

```bash
docker compose ps
# Estado debe ser "healthy" después de 20s
```

### Logs en tiempo real

```bash
docker compose logs -f
```

### Uso de disco

```bash
# Archivos .out
du -sh argus-data/archive/

# Cleanup automático (retención 90 días por defecto)
./cleanup-archives.sh

# Cleanup custom (ej: 7 días)
RETENTION_DAYS=7 ./cleanup-archives.sh
```

## Backup y Recuperación

### Backup de archivos .out

```bash
# Comprimir archivos antiguos
find argus-data/archive -name "*.out" -mtime +7 -exec gzip {} \;

# Rsync a backup server
rsync -avz --progress argus-data/archive/ backup-server:/backups/argus/
```

### Restaurar configuración

```bash
# Variables de entorno
cp .env .env.backup

# Configuraciones críticas
tar czf config-backup.tar.gz .env argus.conf ralabel.conf radium.conf telegraf.conf
```

## Updates

```bash
# Pull latest changes
git pull origin master

# Rebuild
docker compose down
docker compose build --no-cache
docker compose up -d

# Verificar versión
docker compose exec argus argus -v
```

## Referencias

- **Documentación principal:** [README.md](README.md)
- **Rotación de archivos:** [ROTATION.md](ROTATION.md)
- **Argus oficial:** https://openargus.org/
- **MaxMind GeoIP:** https://dev.maxmind.com/geoip/geolite2-free-geolocation-data
