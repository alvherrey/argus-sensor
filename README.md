# Argus Sensor - Arquitectura Oficial para ML y Monitorización

Este repositorio contiene un **Argus Network Flow Sensor** con arquitectura oficial basada en la documentación de OpenArgus, diseñado para:
- 🎯 **Batch ML** (detección de Shadow IT, anomalías, túneles)
- 📊 **Dashboards en tiempo real** (InfluxDB + Grafana)
- 🔒 **Análisis forense** (archivos completos con payload)

**Características principales:**
- ✅ **radium** como hub central de distribución
- ✅ Enriquecimiento GeoIP (country codes, ASN) integrado
- ✅ Rotación automática de archivos (1h/6h/12h/1d/1w)
- ✅ Pipeline dual: archivos completos + time-series ligera
- ✅ Agregación temporal para reducir cardinalidad en InfluxDB
- ✅ Captura completa: payload (256B), MACs, RTT, status updates

---

## 🏗️ Arquitectura

```
Argus (puerto 561, primitivo + payload)
    ↓
radium (puerto 562, hub central)
    └→ RADIUM_CLASSIFIER_FILE (enriquecimiento GeoIP)
    ↓
    ├─→ rasplit/rastream → archivos .out (COMPLETOS: payload + enrich)
    │                ↓
    │      build_l2_features.py (opcional) → Parquet de features
    │                ↓
    │      score_shadowit.py (opcional) → score Parquet
    │                ↓
    │      shadowit_score / shadowit_features_top → InfluxDB
    │                ↓
    │           Grafana alerting + backtesting
    │
    └─→ rastrip (quita payload) → racluster (agrega) → rabins (binning 1m)
                                        ↓
                                   argus-data/argus-telegraf.pipe
                                        ↓
                                   Telegraf → InfluxDB → Grafana
```

### Flujos de datos

| Componente | Contenido | Tamaño | Retención | Uso |
|------------|-----------|--------|-----------|-----|
| **Archivos .out** | Headers + Payload + GeoIP | 5GB/día | 90 días | SoT, forense, reproceso |
| **Parquet de features (opcional)** | Features por ventana/identidad | Depende de ventana | 90-365 días | Entrenamiento y backtesting |
| **Parquet de score (opcional)** | `score`, `severity`, `reason_1..3` | Bajo | 90-365 días | Trazabilidad del modelo |
| **InfluxDB** | Series para dashboards (agregadas) | 2GB/día | 30 días | Visualización/alertas |

### Modelo simple en esta rama

Estado real actual:

1. Guardas `.out` enriquecido en `argus-data/archive/` (base operativa).
2. Sirves realtime con Influx/Grafana (`argus_flows`).
3. Si haces ML, generas Parquet de features en `argus-data/l2_features/`.
4. Si haces scoring, generas score y lo publicas a Influx (`shadowit_score`).

No necesitas separar stores extra para operar hoy.

---

## 🚀 Quick Start

### 1. Descargar bases GeoIP

```bash
# Opción A: Script automático (requiere cuenta MaxMind gratuita)
export MAXMIND_LICENSE_KEY="tu_license_key"
./download-geoip.sh

# Opción B: Manual
mkdir -p geoip-data
curl -o geoip-data/delegated-ipv4-latest \
  https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest
# Descargar GeoLite2-ASN.mmdb desde MaxMind
```

### 2. Configurar variables de entorno

```bash
cp .env.example .env
nano .env
```

```bash
# .env
INTERFACE=ens160
ROTATION_INTERVAL=1d
ENABLE_ENRICHMENT=yes
ENABLE_INFLUXDB=no         # yes para dashboards
AGGREGATION_INTERVAL=1m    # 30s, 1m, 5m, 15m, 1h
```

### 3. Ejecutar el sensor

```bash
./run-argus.sh <INTERFACE>
# Ejemplo:
./run-argus.sh ens160
```

### 4. Verificar operación

```bash
# Logs del contenedor
sudo docker compose logs -f

# Estado de los procesos
sudo docker compose exec argus ps aux | grep -E "argus|radium|rasplit|rastream"

# Test de stream enriquecido (puerto 562)
ra -S localhost:562 -c 10 -s saddr daddr sco dco sas das

# Archivos generados
ls -lh argus-data/archive/$(date +%Y/%m/%d)/
```

---

## 📁 Estructura de Archivos

```
argus-data/
└── archive/
    └── 2026/              # Año
        └── 02/            # Mes
            └── 15/        # Día
                ├── argus.2026.02.15.00.00.00.out  # Archivos completos
                ├── argus.2026.02.15.01.00.00.out  # (payload + enrich)
                └── ...
```

### Formato de archivos

Los archivos `.out` contienen flows enriquecidos en formato binario Argus:
- **Headers completos**: IPs, puertos, protocolo, TTL, flags, etc.
- **Payload**: Primeros 256 bytes de aplicación (para ML)
- **GeoIP**: Country codes (sco, dco) y ASN (sas, das)
- **Métricas**: Packets, bytes, duración, jitter, RTT

Leer con cualquier herramienta argus-clients:

```bash
# Ver flows completos
ra -r argus-data/archive/2026/02/15/argus.2026.02.15.12.00.00.out

# Filtrar por país
ra -r archivo.out - 'sco="CN" or dco="CN"'

# Exportar a CSV para ML
ra -r archivo.out -s saddr daddr proto dport sco dco sas das bytes pkts dur -c , > flows.csv
```

---

## ⚙️ Configuración Avanzada

### Intervalos de rotación

| Valor | Descripción | Archivos/día | Tamaño aprox |
|-------|-------------|--------------|--------------|
| `1h` | Horaria | 24 | ~200MB/archivo |
| `6h` | Cada 6 horas | 4 | ~1.2GB/archivo |
| `12h` | Cada 12 horas | 2 | ~2.5GB/archivo |
| `1d` | Diaria (default) | 1 | ~5GB/archivo |
| `1w` | Semanal | 1/7 | ~35GB/archivo |

### Pipeline InfluxDB (opcional)

Para habilitar dashboards en tiempo real:

```bash
# .env
ENABLE_INFLUXDB=yes
AGGREGATION_INTERVAL=1m
```

Esto activa:
1. **rastrip**: Elimina payload (DSRs suser/duser)
2. **racluster**: Agrega por flow key (src, dst, proto, port, geo)
3. **rabins**: Binning temporal (reduce ingesta)
4. **Named pipe**: `argus-data/argus-telegraf.pipe` → Telegraf

Configurar Telegraf en el **host**:

```bash
cp telegraf.conf /etc/telegraf/telegraf.d/argus.conf
# IMPORTANTE: Editar la ruta absoluta del pipe en telegraf.conf
# Ejemplo: /home/user/argus-sensor/argus-data/argus-telegraf.pipe
vim /etc/telegraf/telegraf.d/argus.conf
systemctl restart telegraf
```

---

## 🧠 Dataset de Features (Parquet, opcional)

El repositorio incluye un pipeline opcional para generar features de Shadow IT desde
los archivos Argus rotados (`argus-data/archive`).

### Script

- `scripts/build_l2_features.py`

### Dependencia

```bash
python3 -m pip install -r requirements-l2.txt
```

### Ejecución base

```bash
python3 scripts/build_l2_features.py \
  --input-root argus-data/archive \
  --output-root argus-data/l2_features \
  --site madrid-dc1 \
  --window 5m \
  --feature-version shadowit-v1
```

Salida:

- Parquet particionado: `argus-data/l2_features/dt=YYYY-MM-DD/hour=HH/`
- Estado incremental: `argus-data/l2_features/_state/processed_files.json`
- Manifest de run: `argus-data/l2_features/_manifests/*.json`

Ver guía completa en `docs/L2_FEATURE_STORE.md`.
Handover de arquitectura para desarrollo: `docs/HANDOVER-SHADOWIT-ARCHITECTURE.md`.
Guía de scoring y esquema Influx: `docs/ML_SCORING.md`.

---

## 🤖 Pipeline ML (features + scoring)

Puedes ejecutar toda la parte ML en un solo comando:

```bash
./scripts/run-ml-pipeline.sh
```

`run-ml-pipeline.sh` carga `.env` automaticamente si el archivo existe.

Este script hace:

1. `scripts/build_l2_features.py` -> `argus-data/l2_features/`
2. `scripts/score_shadowit.py` -> `argus-data/shadowit_scores/`
3. (Opcional) publica `shadowit_score` y `shadowit_features_top` en Influx.

Variables relevantes en `.env`:

- `SITE`, `WINDOW`, `FEATURE_VERSION`
- `MODEL_VERSION`, `MODEL_CONFIG`
- `PUBLISH_SCORE_TO_INFLUX`, `PUBLISH_FEATURES_TOP`, `ONLY_ANOMALIES`
- `INFLUXDB_URL`, `INFLUXDB_ORG`, `INFLUXDB_BUCKET_SHADOWIT`, `INFLUXDB_TOKEN`

Mediciones recomendadas en Influx:

1. `argus_flows`: telemetría operativa en tiempo real (pipeline telegraf actual).
2. `shadowit_score`: score por identidad/ventana para alertas.
3. `shadowit_features_top`: pocas features explicativas del score.

---

## 📊 Casos de Uso

### 1. Batch ML para detección de Shadow IT

```python
#!/usr/bin/env python3
# batch_hourly.py - Procesa archivos horarios

import subprocess
import pandas as pd
from datetime import datetime, timedelta

def process_hourly_batch(argus_file):
    """Lee archivo con flows enriquecidos"""
    cmd = f"ra -r {argus_file} -s saddr daddr proto dport sco dco sas das bytes pkts dur -c ,"
    output = subprocess.check_output(cmd, shell=True, text=True)
    df = pd.read_csv(io.StringIO(output))
    
    # Detección de Shadow IT
    # 1. Servicios cloud no autorizados
    shadow_countries = df[df['dco'].isin(['US', 'IE', 'SG'])]  # AWS, Azure, GCP
    shadow_https = shadow_countries[shadow_countries['dport'] == 443]
    
    # 2. Túneles (volumen alto en puertos no estándar)
    tunnels = df[(df['dport'] > 1024) & (df['bytes'] > 1000000)]
    
    # 3. Exfiltración (volumen saliente anómalo)
    exfil = df.groupby('saddr')['bytes'].sum()
    anomalies = exfil[exfil > exfil.quantile(0.99)]
    
    return shadow_https, tunnels, anomalies

# Procesar último archivo horario
hour = datetime.now().replace(minute=0, second=0) - timedelta(hours=1)
file_pattern = f"argus-data/archive/{hour:%Y/%m/%d}/argus.{hour:%Y.%m.%d.%H}.*.out"
```

### 2. Análisis forense con payload

```bash
# Buscar patrones específicos en payload
ra -r archivo.out -s saddr daddr user - 'dst port 80' | grep -i "password"

# Detectar tunneling DNS
ra -r archivo.out -s saddr daddr bytes pkts - 'proto udp and dst port 53 and bytes > 512'

# Identificar BitTorrent / P2P
ra -r archivo.out - 'dst port range 6881-6889 or dst port 51413'
```

### 3. Dashboard queries (InfluxDB)

```sql
-- Top 10 países de origen (última hora)
SELECT sum(spkts) FROM argus_flows 
WHERE time > now() - 1h 
GROUP BY sco 
ORDER BY sum DESC 
LIMIT 10

-- Tráfico por ASN
SELECT sum(sbytes), sum(dbytes) FROM argus_flows
WHERE time > now() - 24h
GROUP BY das, time(5m)

-- Alertas de países sospechosos
SELECT * FROM argus_flows
WHERE (sco IN ('CN', 'RU', 'KP') OR dco IN ('CN', 'RU', 'KP'))
AND time > now() - 1h
```

---

## 🧹 Gestión de Archivos

### Limpieza automática

El script `cleanup-archives.sh` gestiona archivos antiguos:

```bash
# Default: comprimir archivos >2 días, eliminar archivos >90 días
./cleanup-archives.sh

# Retención personalizada (7 días)
RETENTION_DAYS=7 ./cleanup-archives.sh

# Sin compresión
COMPRESS=no RETENTION_DAYS=90 ./cleanup-archives.sh

# Cambiar umbral de compresión
COMPRESS_AFTER_DAYS=1 ./cleanup-archives.sh
```

### Cron para limpieza diaria

```bash
# Ejecutar a las 3 AM diariamente
0 3 * * * /path/to/argus-sensor/cleanup-archives.sh >> /var/log/argus-cleanup.log 2>&1
```

### Políticas de retención recomendadas

| Tipo de archivo | Compresión | Retención | Uso |
|-----------------|------------|-----------|-----|
| Raw (últimos 7 días) | No | 7 días | Análisis rápido, reprocessing |
| Comprimidos | gzip -9 | 90 días | ML batch, investigaciones |
| Agregados diarios | No | 1 año | Tendencias, baseline |

---

## 🔧 Troubleshooting

### Sensor no captura tráfico

```bash
# Verificar interfaz existe
ip link show <INTERFACE>

# Verificar permisos
sudo docker compose exec argus argus -i <INTERFACE> -P 0 -d

# Verificar modo promiscuo
ip link show <INTERFACE> | grep PROMISC
```

### Pipeline InfluxDB no envía datos

```bash
# Verificar named pipe existe en volumen compartido
ls -l argus-data/argus-telegraf.pipe

# Verificar procesos
docker compose exec argus ps aux | grep -E "rastrip|racluster|rabins"

# Test manual del pipeline
docker compose exec argus bash -c \
  "rastrip -S localhost:562 -M dsrs='-suser,-duser' | ra -c 10"
```

### GeoIP no funciona

```bash
# Verificar bases de datos
ls -lh geoip-data/

# Debe contener:
# - delegated-ipv4-latest
# - GeoLite2-ASN.mmdb

# Test de enriquecimiento
ra -S localhost:562 -c 5 -s saddr daddr sco dco sas das
```

### Archivos no rotan

```bash
# Verificar proceso de rotación (rasplit o rastream)
docker compose exec argus ps aux | grep -E "rasplit|rastream"

# Ver logs de rotación
docker compose logs -f | grep -E "rasplit|rastream"

# Verificar permisos directorio
ls -ld argus-data/archive/
```

---

## 📚 Referencias

- [Argus Official Documentation](https://openargus.org/using-argus)
- [Argus Clients Man Pages](https://openargus.org/documentation)
- [radium Configuration](https://openargus.org/oldsite/man/man8/radium.8.html)
- [ralabel GeoIP](https://openargus.org/using-argus#ralabel---inserting-geolocation-data-into-argus-records)
- [MaxMind GeoLite2](https://dev.maxmind.com/geoip/geolite2-free-geolocation-data)

---

## 📄 License

MIT License - Ver archivo LICENSE para detalles.

## 🤝 Contribuciones

Pull requests son bienvenidos. Para cambios mayores, por favor abre un issue primero.
