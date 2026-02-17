# ML Scoring + Grafana (dual pipeline)

Este documento aterriza la arquitectura actual en dos partes claras:

1. Realtime para visualizacion (`argus_flows`).
2. Batch ML para features + score (`shadowit_score`).

## 1) Directorios y retencion recomendada

1. `argus-data/archive/`:
   - Argus binario enriquecido.
   - SoT operativo.
   - Retencion sugerida: 90 dias (comprimido).
2. `argus-data/l2_features/`:
   - Parquet de features por ventana.
   - Retencion sugerida: 365 dias.
3. `argus-data/shadowit_scores/`:
   - Parquet de scoring para trazabilidad.
   - Retencion sugerida: 180-365 dias.

## 2) Realtime (Grafana)

Se mantiene pipeline actual:

`radium -> rastrip -> racluster -> rabins -> telegraf -> influx (argus_flows)`

Uso:

- dashboards operativos
- troubleshooting de red
- contexto cercano al tiempo real

## 3) Batch ML

### 3.1 Features

Script:

`scripts/build_l2_features.py`

Salida:

`argus-data/l2_features/dt=YYYY-MM-DD/hour=HH/*.parquet`

### 3.2 Scoring

Script:

`scripts/score_shadowit.py`

Salida:

`argus-data/shadowit_scores/dt=YYYY-MM-DD/hour=HH/*.parquet`

Publicacion opcional a Influx:

- measurement `shadowit_score`
- measurement `shadowit_features_top`

## 4) Esquema recomendado de Influx para score

### `shadowit_score`

Tags:

- `host` (identidad)
- `site`
- `model_version`

Fields:

- `score`
- `severity`
- `is_anom`
- `reason_1`
- `reason_2`
- `reason_3`

### `shadowit_features_top`

Tags:

- `host`
- `site`
- `model_version`

Fields:

- `unique_asn`
- `unique_daddr`
- `cloud_asn_unique`
- `bytes_out`
- `https_ratio`
- `quic_ratio`

## 5) Comandos operativos

### Ejecutar ML completo

```bash
./scripts/run-ml-pipeline.sh
```

### Ejecutar solo scoring y publicar en Influx

```bash
export INFLUXDB_URL='http://localhost:8086'
export INFLUXDB_ORG='tu-org'
export INFLUXDB_BUCKET_SHADOWIT='argus-shadowit'
export INFLUXDB_TOKEN='tu-token'

python3 scripts/score_shadowit.py \
  --publish-influx \
  --publish-features-top
```

## 6) Nota importante de identidad (NAT)

Si `saddr` no representa host real por NAT, definir identidad alternativa
(`user`, `asset_id`, pre-NAT sensor) antes de confiar en score por host.
