#!/usr/bin/env bash
set -euo pipefail

# Run the batch ML pipeline:
# 1) Build feature Parquet from Argus archives.
# 2) Score shadow IT risk from feature Parquet.

# Auto-load local .env if present.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

INPUT_ROOT="${INPUT_ROOT:-argus-data/archive}"
FEATURE_OUTPUT_ROOT="${FEATURE_OUTPUT_ROOT:-argus-data/l2_features}"
SCORE_OUTPUT_ROOT="${SCORE_OUTPUT_ROOT:-argus-data/shadowit_scores}"
SITE="${SITE:-default-site}"
WINDOW="${WINDOW:-5m}"
FEATURE_VERSION="${FEATURE_VERSION:-shadowit-v1}"
MODEL_VERSION="${MODEL_VERSION:-shadowit-v1}"
MODEL_CONFIG="${MODEL_CONFIG:-config/shadowit-model-v1.json}"
CLOUD_ASNS="${CLOUD_ASNS:-}"
CLOUD_ASN_FILE="${CLOUD_ASN_FILE:-}"
MAX_FILES="${MAX_FILES:-0}"
REPROCESS="${REPROCESS:-no}"
DRY_RUN="${DRY_RUN:-no}"
PUBLISH_SCORE_TO_INFLUX="${PUBLISH_SCORE_TO_INFLUX:-no}"
PUBLISH_FEATURES_TOP="${PUBLISH_FEATURES_TOP:-yes}"
ONLY_ANOMALIES="${ONLY_ANOMALIES:-no}"
FROM_TS="${FROM_TS:-}"
TO_TS="${TO_TS:-}"

echo "========================================="
echo "Argus ML Pipeline"
echo "========================================="
echo "Input root:           ${INPUT_ROOT}"
echo "Feature output root:  ${FEATURE_OUTPUT_ROOT}"
echo "Score output root:    ${SCORE_OUTPUT_ROOT}"
echo "Site:                 ${SITE}"
echo "Window:               ${WINDOW}"
echo "Feature version:      ${FEATURE_VERSION}"
echo "Model version:        ${MODEL_VERSION}"
echo "Model config:         ${MODEL_CONFIG}"
echo "Publish to Influx:    ${PUBLISH_SCORE_TO_INFLUX}"
echo "========================================="

feature_cmd=(
  python3 scripts/build_l2_features.py
  --input-root "${INPUT_ROOT}"
  --output-root "${FEATURE_OUTPUT_ROOT}"
  --site "${SITE}"
  --window "${WINDOW}"
  --feature-version "${FEATURE_VERSION}"
  --max-files "${MAX_FILES}"
)

if [[ -n "${CLOUD_ASNS}" ]]; then
  feature_cmd+=(--cloud-asns "${CLOUD_ASNS}")
fi
if [[ -n "${CLOUD_ASN_FILE}" ]]; then
  feature_cmd+=(--cloud-asn-file "${CLOUD_ASN_FILE}")
fi
if [[ -n "${FROM_TS}" ]]; then
  feature_cmd+=(--from-ts "${FROM_TS}")
fi
if [[ -n "${TO_TS}" ]]; then
  feature_cmd+=(--to-ts "${TO_TS}")
fi
if [[ "${REPROCESS}" == "yes" ]]; then
  feature_cmd+=(--reprocess)
fi
if [[ "${DRY_RUN}" == "yes" ]]; then
  feature_cmd+=(--dry-run)
fi

echo "[ML] Building feature parquet..."
"${feature_cmd[@]}"

score_cmd=(
  python3 scripts/score_shadowit.py
  --input-root "${FEATURE_OUTPUT_ROOT}"
  --output-root "${SCORE_OUTPUT_ROOT}"
  --model-config "${MODEL_CONFIG}"
  --model-version "${MODEL_VERSION}"
  --max-files "${MAX_FILES}"
)

if [[ "${REPROCESS}" == "yes" ]]; then
  score_cmd+=(--reprocess)
fi
if [[ "${DRY_RUN}" == "yes" ]]; then
  score_cmd+=(--dry-run)
fi
if [[ "${PUBLISH_SCORE_TO_INFLUX}" == "yes" ]]; then
  score_cmd+=(--publish-influx)
fi
if [[ "${PUBLISH_FEATURES_TOP}" == "yes" ]]; then
  score_cmd+=(--publish-features-top)
fi
if [[ "${ONLY_ANOMALIES}" == "yes" ]]; then
  score_cmd+=(--only-anomalies)
fi

echo "[ML] Scoring shadow IT..."
"${score_cmd[@]}"

echo "[ML] Pipeline completed."
