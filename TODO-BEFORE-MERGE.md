# Pre-Merge Checklist

Este archivo documenta los pasos pendientes antes de hacer merge de `feature/radium-dual-pipeline` a `master`.

## ⚠️ Prerequisitos Operativos Pendientes

### 1. Descargar Bases GeoIP (CRÍTICO)

**Estado:** ❌ Pendiente  
**Impacto:** Sin estas bases, el enrichment NO funcionará (campos `sco`, `dco`, `sas`, `das` vacíos)

```bash
# Obtener license key gratuita de MaxMind
# https://www.maxmind.com/en/geolite2/signup

export MAXMIND_LICENSE_KEY='tu_license_key_aqui'
./download-geoip.sh

# Verificar descarga exitosa
ls -lh geoip-data/
# Debe mostrar:
# - delegated-ipv4-latest (~50MB)
# - GeoLite2-ASN.mmdb (~5MB)
```

**Archivos esperados en `geoip-data/`:**
- `delegated-ipv4-latest` - Country codes (ARIN, RIPE, APNIC merged)
- `GeoLite2-ASN.mmdb` - ASN database (MaxMind GeoLite2)

### 2. Crear Archivo `.env`

**Estado:** ❌ Pendiente  
**Impacto:** Docker compose usará valores por defecto

```bash
cp .env.example .env
vim .env

# Configurar mínimo:
INTERFACE=eth0              # Tu interfaz de captura
PORT=561                    # Puerto Argus (default OK)
ROTATION_INTERVAL=1d        # 1h, 6h, 12h, 1d, 1w
ENABLE_ENRICHMENT=yes       # Requiere paso 1 completado
ENABLE_INFLUXDB=no          # Solo si tienes InfluxDB
```

## ✅ Validaciones Pre-Merge

### Sintaxis y Configuración

```bash
# Validar sintaxis shell
bash -n entrypoint.sh
bash -n run-argus.sh
bash -n cleanup-archives.sh
bash -n download-geoip.sh

# Validar docker compose
docker compose config

# Validar permisos de ejecución
ls -l *.sh
# Todos deben tener 'x' (ejecutable)
```

### Testing End-to-End

```bash
# 1. Build
docker compose build

# 2. Start
docker compose up -d

# 3. Verificar Argus captura
docker compose exec argus bash -c 'ra -S localhost:${PORT:-561} -c 5'

# 4. Verificar radium enriquece
docker compose exec argus ra -S localhost:562 -s saddr daddr sco dco sas das -c 5
# Debe mostrar country codes y ASN (no '--' en todos)

# 5. Verificar archivos se generan
ls -lh argus-data/archive/$(date +%Y/%m/%d)/

# 6. Verificar contenido tiene enrichment
docker compose exec argus ra -r /var/log/argus/archive/$(date +%Y/%m/%d)/argus.*.out \
  -s saddr daddr sco dco sas das -c 5

# 7. Si ENABLE_INFLUXDB=yes, verificar pipe
ls -l argus-data/argus-telegraf.pipe
# Debe ser: prw-r--r-- (named pipe)

# 8. Cleanup
docker compose down
```

## 📋 Cambios Arquitectónicos a Documentar en PR

### Breaking Changes

1. **Nuevo componente:** radium como hub central (puerto 562)
   - Antes: Argus → rasplit directo
   - Ahora: Argus:561 → radium:562 → [rasplit | rastrip]

2. **Prerequisito GeoIP:** Enrichment requiere bases descargadas
   - Script: `./download-geoip.sh`
   - License key: MaxMind (gratuita)

3. **Dual pipeline:** Archivos + InfluxDB opcional
   - Archivos: Payload completo para ML batch
   - InfluxDB: Agregado sin payload para dashboards

### New Features

- ✅ Rotación automática configurable (1h/6h/12h/1d/1w)
- ✅ Enrichment GeoIP integrado (country codes + ASN)
- ✅ Pipeline InfluxDB opcional con agregación
- ✅ Puerto dinámico vía CLI flags
- ✅ Cleanup automático de archivos antiguos
- ✅ Health checks mejorados
- ✅ Documentación completa (DEPLOYMENT.md, ROTATION.md)

### Configuration Changes

**Nuevos archivos:**
- `radium.conf` - Configuración hub central
- `ralabel.conf` - Reglas enrichment GeoIP
- `telegraf.conf` - Pipeline InfluxDB
- `.env.example` - Template variables de entorno
- `DEPLOYMENT.md` - Guía deployment producción
- `ROTATION.md` - Documentación rotación
- `download-geoip.sh` - Provisión bases GeoIP
- `cleanup-archives.sh` - Gestión retención

**Archivos modificados:**
- `argus.conf` - Removido `hostuuid`, PORT dinámico
- `docker-compose.yaml` - Nuevos mounts y variables
- `README.md` - Reescrito con nueva arquitectura

## 🚀 Pasos para Merge

```bash
# 1. Completar prerequisitos operativos (pasos 1 y 2 arriba)

# 2. Ejecutar validaciones (testing end-to-end)

# 3. Push de la rama
git push origin feature/radium-dual-pipeline

# 4. Crear Pull Request en GitHub
# Incluir:
# - Resumen de cambios arquitectónicos
# - Breaking changes
# - Prerequisitos operativos
# - Validaciones realizadas

# 5. Después de merge a master, actualizar docs
# - Actualizar README.md badge de branch a master
# - Crear release tag (ej: v2.0.0-radium)
# - Documentar migration guide para usuarios existentes
```

## 📝 Migration Guide (para usuarios de v1.x)

Para usuarios con deployment existente de argus-sensor v1.x:

```bash
# 1. Backup configuración actual
cp .env .env.v1.backup
cp argus.conf argus.conf.v1.backup

# 2. Pull cambios
git fetch origin
git checkout master
git pull origin master

# 3. Descargar bases GeoIP (nuevo prerequisito)
export MAXMIND_LICENSE_KEY='tu_key'
./download-geoip.sh

# 4. Actualizar .env con nuevas variables
# Copiar desde .env.example: ENABLE_ENRICHMENT, ENABLE_INFLUXDB, AGGREGATION_INTERVAL

# 5. Rebuild y restart
docker compose down
docker compose build
docker compose up -d

# 6. Validar enrichment funciona
docker compose exec argus ra -S localhost:562 -s sco dco sas das -c 5
```

## ⚠️ Notas Importantes

1. **Sin bases GeoIP, el sensor arranca pero enrichment no funciona**
   - Los campos `sco`, `dco`, `sas`, `das` estarán vacíos (`--`)
   - No es error fatal, pero pierde funcionalidad clave

2. **Puerto 562 ahora es crítico**
   - radium escucha en 562 (fijo, no configurable fácilmente)
   - Asegurar que no hay conflictos de puerto

3. **Volumen `geoip-data/` debe persistir**
   - Contiene bases GeoIP descargadas
   - Si se borra, re-ejecutar `./download-geoip.sh`

4. **InfluxDB es opcional**
   - Por defecto `ENABLE_INFLUXDB=no`
   - Requiere Telegraf configurado en host si se habilita

---

**Última actualización:** 2026-02-17  
**Branch:** feature/radium-dual-pipeline  
**Commit:** 79522ea
