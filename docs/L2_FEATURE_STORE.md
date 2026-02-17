# Dataset de Features (Parquet)

Este documento define el dataset de features para Shadow IT en este repositorio.

## Objetivo

Convertir los archivos Argus rotados (`argus-data/archive`) en un dataset de
features por ventana para entrenamiento, backtesting y scoring.

Nota:

- `argus-data/archive` es la base operativa (enriched cuando
  `ENABLE_ENRICHMENT=yes`).

## Implementacion

Script principal:

`/Users/alvaro/Development/lab/argus-sensor/scripts/build_l2_features.py`

Entrada:

- Archivos Argus en `argus-data/archive/YYYY/MM/DD/*.out*`
- Lectura con `ra` (argus-clients)

Salida:

- Dataset Parquet particionado en `argus-data/l2_features/dt=YYYY-MM-DD/hour=HH/`
- Estado incremental en `argus-data/l2_features/_state/processed_files.json`
- Manifest por ejecucion en `argus-data/l2_features/_manifests/*.json`

## Features incluidas (v1)

- `flow_count`
- `bytes_out`
- `bytes_in`
- `bytes_total`
- `unique_daddr`
- `unique_asn`
- `cloud_asn_unique`
- `https_ratio`
- `quic_ratio`
- `port_entropy`
- `new_asn_ratio`

Claves:

- Ventana temporal: configurable (`--window`, default `5m`)
- Identidad: `saddr` (host origen)
- Contexto: `site` (tag operacional)

## Dependencias

1. `ra` en `PATH` (argus-clients).
2. Python 3.10+.
3. `pyarrow`:

```bash
python3 -m pip install -r requirements-l2.txt
```

## Ejecucion

Ejemplo base:

```bash
python3 scripts/build_l2_features.py \
  --input-root argus-data/archive \
  --output-root argus-data/l2_features \
  --site madrid-dc1 \
  --window 5m \
  --feature-version shadowit-v1
```

Rango temporal:

```bash
python3 scripts/build_l2_features.py \
  --from-ts 2026-02-17T00:00:00Z \
  --to-ts 2026-02-18T00:00:00Z
```

Backfill controlado (100 archivos max):

```bash
python3 scripts/build_l2_features.py --max-files 100
```

Reproceso completo (ignora estado incremental):

```bash
python3 scripts/build_l2_features.py --reprocess
```

Dry run:

```bash
python3 scripts/build_l2_features.py --dry-run
```

## Cloud ASN list (opcional)

Inline:

```bash
python3 scripts/build_l2_features.py \
  --cloud-asns "15169,16509,8075,13335"
```

Desde archivo:

```bash
python3 scripts/build_l2_features.py \
  --cloud-asn-file config/cloud_asns.txt
```

Formato del archivo:

```text
# one ASN per line
15169
16509
8075
13335
```

## Esquema de salida

Columnas principales:

- `window_start` (timestamp UTC)
- `window_end` (timestamp UTC)
- `site` (string)
- `identity` (string)
- `flow_count` (int)
- `bytes_out` (int)
- `bytes_in` (int)
- `bytes_total` (int)
- `unique_daddr` (int)
- `unique_asn` (int)
- `cloud_asn_unique` (int)
- `https_ratio` (float)
- `quic_ratio` (float)
- `port_entropy` (float)
- `new_asn_ratio` (float)
- `feature_version` (string)
- `source_file_count` (int)
- `run_id` (string)
- `dt` (partition key)
- `hour` (partition key)

## Notas operativas

1. El modo incremental evita reprocesar archivos ya registrados en el state
   file.
2. Para cambiar logica de features sin mezclar datasets, subir
   `--feature-version` (por ejemplo `shadowit-v2`).
3. Mantener sincronia entre la ventana de features y la frecuencia de scoring.
