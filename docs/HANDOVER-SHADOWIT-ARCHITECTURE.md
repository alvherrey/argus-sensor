# Handover de Arquitectura (modelo simple)

Fecha: 2026-02-17  
Rama: `feature/radium-dual-pipeline`  
Repo: `/Users/alvaro/Development/lab/argus-sensor`

## 1) Como funciona hoy (sin complejidad extra)

Flujo real:

`argus -> radium (enrichment) -> rasplit -> archive/*.out`

En paralelo, opcional para dashboards:

`radium -> rastrip -> racluster -> rabins -> telegraf -> influx`

Opcional para ML:

`archive/*.out -> build_l2_features.py -> parquet de features`

## 2) Donde se guarda cada cosa

1. Base operativa (fuente de verdad del sistema actual):
   - `argus-data/archive/YYYY/MM/DD/*.out*`
2. Realtime para visualizacion:
   - InfluxDB (`argus_flows`)
3. Features para modelos (solo si lo necesitas):
   - `argus-data/l2_features/dt=YYYY-MM-DD/hour=HH/*.parquet`

## 3) Decision recomendada para este repo

1. Mantener el `.out` enriquecido como base principal.
2. No separar ahora en mas stores (raw/enriched separados) salvo necesidad real.
3. Usar Parquet de features solo para entrenamiento/backtesting.

## 4) Cuándo si conviene separar mas stores

Separar raw y enriched tiene sentido solo si:

1. Vas a re-enriquecer historico frecuentemente (cambia GeoIP/ruleset).
2. Tienes auditoria estricta que exige conservar raw sin transformacion.
3. Necesitas comparar varias estrategias de enrichment sobre el mismo historico.

Si no se cumple eso, mantenerlo simple es mejor.

## 5) Siguiente paso practico (sin sobrearquitectura)

1. Programar job diario de `run-ml-pipeline.sh`.
2. Publicar `shadowit_score` hacia Influx con `PUBLISH_SCORE_TO_INFLUX=yes`.
3. Mantener `argus_flows` como telemetria operativa, no como dataset de entrenamiento.
