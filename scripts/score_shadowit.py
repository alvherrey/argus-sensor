#!/usr/bin/env python3
"""Compute Shadow IT scores from feature Parquet and optionally publish to InfluxDB."""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_MODEL = {
    "model_version": "shadowit-v1",
    "anomaly_threshold": 70.0,
    "severity_thresholds": {"medium": 40.0, "high": 70.0},
    "weights": {
        "unique_daddr": 18.0,
        "unique_asn": 16.0,
        "cloud_asn_unique": 16.0,
        "bytes_out": 14.0,
        "https_ratio": 12.0,
        "quic_ratio": 8.0,
        "port_entropy": 8.0,
        "new_asn_ratio": 8.0,
    },
    "scales": {
        "unique_daddr": 50.0,
        "unique_asn": 20.0,
        "cloud_asn_unique": 8.0,
        "bytes_out": 100_000_000.0,
        "https_ratio": 1.0,
        "quic_ratio": 0.6,
        "port_entropy": 4.0,
        "new_asn_ratio": 1.0,
    },
    "reason_codes": {
        "unique_daddr": "MANY_DESTINATIONS",
        "unique_asn": "MANY_ASNS",
        "cloud_asn_unique": "MANY_CLOUD_ASNS",
        "bytes_out": "HIGH_EGRESS",
        "https_ratio": "HIGH_HTTPS_RATIO",
        "quic_ratio": "HIGH_QUIC_RATIO",
        "port_entropy": "HIGH_PORT_ENTROPY",
        "new_asn_ratio": "HIGH_NEW_ASN_RATIO",
    },
}


@dataclass
class PublishConfig:
    url: str
    org: str
    bucket: str
    token: str
    timeout_s: int
    publish_features_top: bool
    only_anomalies: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score Shadow IT risk from Parquet features"
    )
    parser.add_argument(
        "--input-root",
        default="argus-data/l2_features",
        help="Root of feature Parquet dataset",
    )
    parser.add_argument(
        "--output-root",
        default="argus-data/shadowit_scores",
        help="Output root for score Parquet dataset",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Processed feature-file state JSON path (default: <output-root>/_state/processed_feature_files.json)",
    )
    parser.add_argument(
        "--manifest-dir",
        default=None,
        help="Run manifests directory (default: <output-root>/_manifests)",
    )
    parser.add_argument(
        "--model-config",
        default="config/shadowit-model-v1.json",
        help="JSON config with model weights/scales/thresholds",
    )
    parser.add_argument(
        "--model-version",
        default=None,
        help="Override model version label",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit number of feature parquet files to process (0 = no limit)",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Ignore state file and process all parquet files",
    )
    parser.add_argument(
        "--publish-influx",
        action="store_true",
        help="Publish scores to InfluxDB",
    )
    parser.add_argument(
        "--influx-url",
        default=None,
        help="InfluxDB base URL (or env INFLUXDB_URL)",
    )
    parser.add_argument(
        "--influx-org",
        default=None,
        help="InfluxDB organization (or env INFLUXDB_ORG)",
    )
    parser.add_argument(
        "--influx-bucket",
        default=None,
        help="InfluxDB bucket for scores (or env INFLUXDB_BUCKET_SHADOWIT)",
    )
    parser.add_argument(
        "--influx-token",
        default=None,
        help="InfluxDB token (or env INFLUXDB_TOKEN)",
    )
    parser.add_argument(
        "--influx-timeout",
        type=int,
        default=10,
        help="HTTP timeout in seconds for Influx writes",
    )
    parser.add_argument(
        "--publish-features-top",
        action="store_true",
        help="Also publish shadowit_features_top measurement",
    )
    parser.add_argument(
        "--only-anomalies",
        action="store_true",
        help="Publish to Influx only rows with is_anom=true",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute scores without writing Parquet/state/Influx",
    )
    return parser.parse_args()


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise TypeError(f"Unsupported datetime value: {value!r}")


def to_float(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return 0.0
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if not v or v in {"-", "--"}:
            return 0.0
        return float(v)
    return float(value)


def to_int(value: Any) -> int:
    return int(round(to_float(value)))


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def merge_model_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            nested = dict(result[key])
            nested.update(value)
            result[key] = nested
        else:
            result[key] = value
    return result


def load_model(model_config_path: Path, model_version_override: str | None) -> dict[str, Any]:
    model = dict(DEFAULT_MODEL)
    if model_config_path.exists():
        model = merge_model_config(model, load_json(model_config_path))
    if model_version_override:
        model["model_version"] = model_version_override
    return model


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = load_json(path)
    processed = payload.get("processed_feature_files", [])
    if not isinstance(processed, list):
        raise ValueError(f"Invalid state format in {path}")
    return {str(item) for item in processed}


def write_state(path: Path, processed: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "processed_feature_files": sorted(processed),
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def list_feature_parquet_files(
    input_root: Path, processed: set[str], reprocess: bool, max_files: int
) -> list[Path]:
    files = sorted(input_root.rglob("*.parquet"))
    selected: list[Path] = []
    for path in files:
        rel = str(path.relative_to(input_root))
        if not reprocess and rel in processed:
            continue
        selected.append(path)
        if max_files > 0 and len(selected) >= max_files:
            break
    return selected


def normalize_feature(value: float, scale: float) -> float:
    if scale <= 0:
        return 0.0
    return max(0.0, min(value / scale, 1.0))


def severity_from_score(score: float, thresholds: dict[str, Any]) -> str:
    medium = float(thresholds.get("medium", 40.0))
    high = float(thresholds.get("high", 70.0))
    if score >= high:
        return "high"
    if score >= medium:
        return "medium"
    return "low"


def escape_tag(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def escape_field_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def build_reasons(contrib: dict[str, float], reason_codes: dict[str, str]) -> tuple[str, str, str]:
    ranked = sorted(contrib.items(), key=lambda item: item[1], reverse=True)
    reasons: list[str] = []
    for feature, contribution in ranked:
        if contribution <= 0:
            continue
        reasons.append(str(reason_codes.get(feature, feature.upper())))
        if len(reasons) == 3:
            break
    while len(reasons) < 3:
        reasons.append("NONE")
    return reasons[0], reasons[1], reasons[2]


def build_score_row(feature_row: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    weights: dict[str, Any] = model["weights"]
    scales: dict[str, Any] = model["scales"]
    reason_codes: dict[str, str] = model["reason_codes"]
    anomaly_threshold = float(model.get("anomaly_threshold", 70.0))

    contributions: dict[str, float] = {}
    score = 0.0

    for feature_name, weight_raw in weights.items():
        weight = float(weight_raw)
        value = to_float(feature_row.get(feature_name, 0.0))
        scale = float(scales.get(feature_name, 1.0))
        norm = normalize_feature(value, scale)
        contrib = norm * weight
        contributions[feature_name] = contrib
        score += contrib

    score = max(0.0, min(score, 100.0))
    severity = severity_from_score(score, model.get("severity_thresholds", {}))
    is_anom = score >= anomaly_threshold
    reason_1, reason_2, reason_3 = build_reasons(contributions, reason_codes)

    window_start = parse_dt(feature_row["window_start"])
    window_end = parse_dt(feature_row["window_end"])
    dt = str(feature_row.get("dt", window_start.strftime("%Y-%m-%d")))
    hour = int(feature_row.get("hour", int(window_start.strftime("%H"))))

    return {
        "window_start": window_start,
        "window_end": window_end,
        "site": str(feature_row.get("site", "unknown")),
        "identity": str(feature_row.get("identity", "unknown")),
        "score": float(score),
        "severity": severity,
        "is_anom": bool(is_anom),
        "reason_1": reason_1,
        "reason_2": reason_2,
        "reason_3": reason_3,
        "model_version": str(model.get("model_version", "shadowit-v1")),
        "feature_version": str(feature_row.get("feature_version", "unknown")),
        "source_run_id": str(feature_row.get("run_id", "unknown")),
        "unique_daddr": to_int(feature_row.get("unique_daddr", 0)),
        "unique_asn": to_int(feature_row.get("unique_asn", 0)),
        "cloud_asn_unique": to_int(feature_row.get("cloud_asn_unique", 0)),
        "bytes_out": to_int(feature_row.get("bytes_out", 0)),
        "https_ratio": float(to_float(feature_row.get("https_ratio", 0.0))),
        "quic_ratio": float(to_float(feature_row.get("quic_ratio", 0.0))),
        "dt": dt,
        "hour": hour,
    }


def write_parquet_scores(rows: list[dict[str, Any]], output_root: Path) -> None:
    if not rows:
        print("[SCORE] No rows generated; nothing to write.", file=sys.stderr)
        return
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = {
        "window_start": [row["window_start"] for row in rows],
        "window_end": [row["window_end"] for row in rows],
        "site": [row["site"] for row in rows],
        "identity": [row["identity"] for row in rows],
        "score": [float(row["score"]) for row in rows],
        "severity": [row["severity"] for row in rows],
        "is_anom": [bool(row["is_anom"]) for row in rows],
        "reason_1": [row["reason_1"] for row in rows],
        "reason_2": [row["reason_2"] for row in rows],
        "reason_3": [row["reason_3"] for row in rows],
        "model_version": [row["model_version"] for row in rows],
        "feature_version": [row["feature_version"] for row in rows],
        "source_run_id": [row["source_run_id"] for row in rows],
        "unique_daddr": [int(row["unique_daddr"]) for row in rows],
        "unique_asn": [int(row["unique_asn"]) for row in rows],
        "cloud_asn_unique": [int(row["cloud_asn_unique"]) for row in rows],
        "bytes_out": [int(row["bytes_out"]) for row in rows],
        "https_ratio": [float(row["https_ratio"]) for row in rows],
        "quic_ratio": [float(row["quic_ratio"]) for row in rows],
        "dt": [row["dt"] for row in rows],
        "hour": [int(row["hour"]) for row in rows],
    }

    schema = pa.schema(
        [
            pa.field("window_start", pa.timestamp("s", tz="UTC")),
            pa.field("window_end", pa.timestamp("s", tz="UTC")),
            pa.field("site", pa.string()),
            pa.field("identity", pa.string()),
            pa.field("score", pa.float32()),
            pa.field("severity", pa.string()),
            pa.field("is_anom", pa.bool_()),
            pa.field("reason_1", pa.string()),
            pa.field("reason_2", pa.string()),
            pa.field("reason_3", pa.string()),
            pa.field("model_version", pa.string()),
            pa.field("feature_version", pa.string()),
            pa.field("source_run_id", pa.string()),
            pa.field("unique_daddr", pa.int32()),
            pa.field("unique_asn", pa.int32()),
            pa.field("cloud_asn_unique", pa.int32()),
            pa.field("bytes_out", pa.int64()),
            pa.field("https_ratio", pa.float32()),
            pa.field("quic_ratio", pa.float32()),
            pa.field("dt", pa.string()),
            pa.field("hour", pa.int8()),
        ]
    )

    table = pa.Table.from_pydict(columns, schema=schema)
    output_root.mkdir(parents=True, exist_ok=True)
    pq.write_to_dataset(
        table,
        root_path=str(output_root),
        partition_cols=["dt", "hour"],
        compression="zstd",
    )


def write_manifest(
    manifest_dir: Path,
    run_id: str,
    input_root: Path,
    output_root: Path,
    processed_feature_files: list[str],
    rows: list[dict[str, Any]],
    publish_influx: bool,
    influx_lines: int,
) -> None:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    partitions = sorted(
        {f"dt={row['dt']}/hour={int(row['hour']):02d}" for row in rows}
    )
    payload = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_root": str(input_root),
        "output_root": str(output_root),
        "processed_feature_files": processed_feature_files,
        "processed_feature_file_count": len(processed_feature_files),
        "score_row_count": len(rows),
        "partitions": partitions,
        "publish_influx": publish_influx,
        "influx_lines_written": influx_lines,
    }
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{run_id}.json"
    out = manifest_dir / name
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def build_influx_lines(rows: list[dict[str, Any]], cfg: PublishConfig) -> list[str]:
    lines: list[str] = []
    for row in rows:
        if cfg.only_anomalies and not row["is_anom"]:
            continue
        ts = int(parse_dt(row["window_end"]).timestamp())
        host = escape_tag(str(row["identity"]))
        site = escape_tag(str(row["site"]))
        model_version = escape_tag(str(row["model_version"]))

        score_line = (
            f"shadowit_score,host={host},site={site},model_version={model_version} "
            f"score={float(row['score'])},"
            f"severity=\"{escape_field_string(str(row['severity']))}\","
            f"is_anom={'true' if row['is_anom'] else 'false'},"
            f"reason_1=\"{escape_field_string(str(row['reason_1']))}\","
            f"reason_2=\"{escape_field_string(str(row['reason_2']))}\","
            f"reason_3=\"{escape_field_string(str(row['reason_3']))}\" "
            f"{ts}"
        )
        lines.append(score_line)

        if cfg.publish_features_top:
            feat_line = (
                f"shadowit_features_top,host={host},site={site},model_version={model_version} "
                f"unique_asn={int(row['unique_asn'])}i,"
                f"unique_daddr={int(row['unique_daddr'])}i,"
                f"cloud_asn_unique={int(row['cloud_asn_unique'])}i,"
                f"bytes_out={int(row['bytes_out'])}i,"
                f"https_ratio={float(row['https_ratio'])},"
                f"quic_ratio={float(row['quic_ratio'])} "
                f"{ts}"
            )
            lines.append(feat_line)
    return lines


def publish_to_influx(lines: list[str], cfg: PublishConfig) -> int:
    if not lines:
        return 0
    endpoint = (
        f"{cfg.url.rstrip('/')}/api/v2/write"
        f"?org={quote(cfg.org)}&bucket={quote(cfg.bucket)}&precision=s"
    )
    total_written = 0
    batch_size = 5000
    for i in range(0, len(lines), batch_size):
        chunk = lines[i : i + batch_size]
        body = ("\n".join(chunk)).encode("utf-8")
        req = Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Token {cfg.token}",
                "Content-Type": "text/plain; charset=utf-8",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=cfg.timeout_s):
            pass
        total_written += len(chunk)
    return total_written


def build_publish_config(args: argparse.Namespace) -> PublishConfig:
    import os

    url = args.influx_url or os.getenv("INFLUXDB_URL", "")
    org = args.influx_org or os.getenv("INFLUXDB_ORG", "")
    bucket = args.influx_bucket or os.getenv("INFLUXDB_BUCKET_SHADOWIT", "")
    token = args.influx_token or os.getenv("INFLUXDB_TOKEN", "")
    missing = [k for k, v in {"INFLUXDB_URL": url, "INFLUXDB_ORG": org, "INFLUXDB_BUCKET_SHADOWIT": bucket, "INFLUXDB_TOKEN": token}.items() if not v]
    if missing:
        raise ValueError(
            "Missing Influx config for publish: " + ", ".join(missing)
        )
    return PublishConfig(
        url=url,
        org=org,
        bucket=bucket,
        token=token,
        timeout_s=args.influx_timeout,
        publish_features_top=args.publish_features_top,
        only_anomalies=args.only_anomalies,
    )


def main() -> int:
    args = parse_args()
    run_id = uuid.uuid4().hex[:12]

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    state_file = (
        Path(args.state_file).resolve()
        if args.state_file
        else (output_root / "_state" / "processed_feature_files.json")
    )
    manifest_dir = (
        Path(args.manifest_dir).resolve()
        if args.manifest_dir
        else (output_root / "_manifests")
    )

    if not input_root.exists():
        print(f"[SCORE] Input root does not exist: {input_root}", file=sys.stderr)
        return 2

    model = load_model(Path(args.model_config).resolve(), args.model_version)
    processed_state = set() if args.reprocess else load_state(state_file)
    parquet_files = list_feature_parquet_files(
        input_root=input_root,
        processed=processed_state,
        reprocess=args.reprocess,
        max_files=args.max_files,
    )

    print(
        f"[SCORE] Selected {len(parquet_files)} feature file(s) "
        f"from {input_root} (run_id={run_id})",
        file=sys.stderr,
    )
    if not parquet_files:
        print("[SCORE] Nothing to process.", file=sys.stderr)
        return 0

    import pyarrow.parquet as pq

    score_rows: list[dict[str, Any]] = []
    processed_rel_files: list[str] = []
    for path in parquet_files:
        rel = str(path.relative_to(input_root))
        print(f"[SCORE] Processing {rel}", file=sys.stderr)
        table = pq.read_table(path)
        for feature_row in table.to_pylist():
            score_rows.append(build_score_row(feature_row, model))
        processed_rel_files.append(rel)

    influx_lines_written = 0
    if not args.dry_run:
        write_parquet_scores(score_rows, output_root)
        updated = processed_state.union(processed_rel_files)
        write_state(state_file, updated)

        if args.publish_influx:
            publish_cfg = build_publish_config(args)
            lines = build_influx_lines(score_rows, publish_cfg)
            influx_lines_written = publish_to_influx(lines, publish_cfg)

    write_manifest(
        manifest_dir=manifest_dir,
        run_id=run_id,
        input_root=input_root,
        output_root=output_root,
        processed_feature_files=processed_rel_files,
        rows=score_rows,
        publish_influx=bool(args.publish_influx and not args.dry_run),
        influx_lines=influx_lines_written,
    )

    print(
        f"[SCORE] Completed: score_rows={len(score_rows)}, "
        f"influx_lines_written={influx_lines_written}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(141)
    except Exception as exc:  # pragma: no cover - CLI top-level
        print(f"[SCORE] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
