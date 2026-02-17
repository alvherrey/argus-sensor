#!/usr/bin/env python3
"""Build an L2 feature store (Parquet) from Argus archive files.

This script reads Argus binary archives (L0/L1), extracts flow records with `ra`,
aggregates features by time window and identity, and writes partitioned Parquet
files suitable for ML training/backtesting.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import DefaultDict


INVALID_VALUES = {"", "-", "--", "0", "unknown", "UNKNOWN"}


@dataclass
class WindowAggregate:
    flow_count: int = 0
    bytes_out: int = 0
    bytes_in: int = 0
    unique_daddr: set[str] = field(default_factory=set)
    unique_asn: set[str] = field(default_factory=set)
    cloud_asn_unique: set[str] = field(default_factory=set)
    https_flows: int = 0
    quic_flows: int = 0
    dport_counter: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build L2 Parquet features from Argus archives"
    )
    parser.add_argument(
        "--input-root",
        default="argus-data/archive",
        help="Root directory containing Argus .out/.out.gz files",
    )
    parser.add_argument(
        "--output-root",
        default="argus-data/l2_features",
        help="Output root for partitioned Parquet dataset",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="JSON file with processed files state (default: <output-root>/_state/processed_files.json)",
    )
    parser.add_argument(
        "--manifest-dir",
        default=None,
        help="Directory for run manifests (default: <output-root>/_manifests)",
    )
    parser.add_argument(
        "--site",
        default="default-site",
        help="Site label included in every feature row",
    )
    parser.add_argument(
        "--window",
        default="5m",
        help="Aggregation window (examples: 30s, 1m, 5m, 15m, 1h)",
    )
    parser.add_argument(
        "--from-ts",
        default=None,
        help="Start timestamp UTC (ISO 8601, example: 2026-02-17T00:00:00Z)",
    )
    parser.add_argument(
        "--to-ts",
        default=None,
        help="End timestamp UTC (ISO 8601, example: 2026-02-18T00:00:00Z)",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Limit number of input files for a run (0 = no limit)",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Ignore state file and process all matching files",
    )
    parser.add_argument(
        "--feature-version",
        default="shadowit-v1",
        help="Feature version label stored in output rows",
    )
    parser.add_argument(
        "--cloud-asns",
        default="",
        help="Comma separated cloud ASN list (e.g. 15169,16509,8075,13335)",
    )
    parser.add_argument(
        "--cloud-asn-file",
        default=None,
        help="Optional file with one ASN per line for cloud ASN detection",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute features but do not write Parquet/state",
    )
    return parser.parse_args()


def parse_window_to_seconds(window: str) -> int:
    if len(window) < 2:
        raise ValueError(f"Invalid window value: {window}")
    num = window[:-1]
    unit = window[-1].lower()
    if not num.isdigit():
        raise ValueError(f"Invalid window value: {window}")
    value = int(num)
    if value <= 0:
        raise ValueError("Window value must be greater than 0")
    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    if unit == "d":
        return value * 86400
    raise ValueError(f"Unsupported window unit in {window}; use s/m/h/d")


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_file_timestamp(path: Path) -> datetime | None:
    # Expected format: argus.YYYY.MM.DD.HH.MM.SS.out[.gz|.bz2]
    name = path.name
    parts = name.split(".")
    if len(parts) < 9 or parts[0] != "argus":
        return None
    try:
        year = int(parts[1])
        month = int(parts[2])
        day = int(parts[3])
        hour = int(parts[4])
        minute = int(parts[5])
        second = int(parts[6])
        return datetime(
            year, month, day, hour, minute, second, tzinfo=timezone.utc
        )
    except ValueError:
        return None


def normalize_asn(raw: str) -> str | None:
    value = raw.strip()
    if not value or value in INVALID_VALUES:
        return None
    value = value.upper().replace("AS", "")
    if not value:
        return None
    return value


def parse_int_like(raw: str) -> int:
    value = raw.strip()
    if not value or value in INVALID_VALUES:
        return 0
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return 0


def normalize_dport(raw: str) -> str:
    value = raw.strip().lower()
    if not value or value in INVALID_VALUES:
        return "0"
    if value == "https":
        return "443"
    return value


def is_https_flow(dport: str) -> bool:
    return dport == "443"


def is_quic_flow(proto: str, dport: str) -> bool:
    return proto.lower() == "udp" and dport == "443"


def compute_port_entropy(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for count in counter.values():
        p = count / total
        entropy -= p * math.log(p, 2)
    return entropy


def load_state(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()
    with state_file.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    processed = payload.get("processed_files", [])
    if not isinstance(processed, list):
        raise ValueError(f"Invalid state format in {state_file}")
    return {str(item) for item in processed}


def write_state(state_file: Path, processed_files: set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        "processed_files": sorted(processed_files),
    }
    with state_file.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def load_cloud_asn_set(args: argparse.Namespace) -> set[str]:
    asns: set[str] = set()
    if args.cloud_asns:
        for raw in args.cloud_asns.split(","):
            normalized = normalize_asn(raw)
            if normalized:
                asns.add(normalized)
    if args.cloud_asn_file:
        path = Path(args.cloud_asn_file)
        if not path.exists():
            raise FileNotFoundError(f"Cloud ASN file not found: {path}")
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                normalized = normalize_asn(stripped)
                if normalized:
                    asns.add(normalized)
    return asns


def list_input_files(
    input_root: Path,
    from_ts: datetime | None,
    to_ts: datetime | None,
    max_files: int,
    already_processed: set[str],
    reprocess: bool,
) -> list[Path]:
    candidates = sorted(
        p for p in input_root.rglob("*") if p.is_file() and ".out" in p.name
    )
    selected: list[Path] = []
    for path in candidates:
        file_ts = parse_file_timestamp(path)
        if file_ts is not None:
            if from_ts and file_ts < from_ts:
                continue
            if to_ts and file_ts >= to_ts:
                continue
        rel = str(path.relative_to(input_root))
        if not reprocess and rel in already_processed:
            continue
        selected.append(path)
        if max_files > 0 and len(selected) >= max_files:
            break
    return selected


def stream_ra_rows(path: Path):
    # Keep a compact yet useful feature contract for Shadow IT signals.
    fields = "stime dur saddr daddr proto dport das sbytes dbytes"
    cmd = ["ra", "-r", str(path), "-u", "-n", "-s", fields, "-c", ","]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    reader = csv.reader(proc.stdout)

    for row in reader:
        # Ignore empty lines and possible non-data rows.
        if not row or len(row) < 9:
            continue
        try:
            stime = float(row[0])
            saddr = row[2].strip()
            daddr = row[3].strip()
            proto = row[4].strip().lower()
            dport = normalize_dport(row[5])
            das = normalize_asn(row[6])
            sbytes = parse_int_like(row[7])
            dbytes = parse_int_like(row[8])
        except ValueError:
            # Header or malformed row.
            continue

        if not saddr:
            continue

        yield {
            "stime": stime,
            "saddr": saddr,
            "daddr": daddr,
            "proto": proto,
            "dport": dport,
            "das": das,
            "sbytes": sbytes,
            "dbytes": dbytes,
        }

    stderr = ""
    if proc.stderr is not None:
        stderr = proc.stderr.read().strip()
    code = proc.wait()
    if code != 0:
        raise RuntimeError(
            f"'ra' failed for {path} (exit={code}). stderr: {stderr or '<empty>'}"
        )


def build_feature_rows(
    files: list[Path],
    input_root: Path,
    site: str,
    window_seconds: int,
    feature_version: str,
    cloud_asn_set: set[str],
    run_id: str,
) -> tuple[list[dict[str, object]], list[str]]:
    aggregates: DefaultDict[tuple[int, str], WindowAggregate] = defaultdict(
        WindowAggregate
    )
    processed_rel_paths: list[str] = []

    for path in files:
        rel_path = str(path.relative_to(input_root))
        print(f"[L2] Processing {rel_path}", file=sys.stderr)

        for record in stream_ra_rows(path):
            window_start_epoch = (
                int(record["stime"]) // window_seconds
            ) * window_seconds
            identity = str(record["saddr"])
            key = (window_start_epoch, identity)
            agg = aggregates[key]

            agg.flow_count += 1
            agg.bytes_out += int(record["sbytes"])
            agg.bytes_in += int(record["dbytes"])
            agg.unique_daddr.add(str(record["daddr"]))

            asn = record["das"]
            if asn:
                asn_str = str(asn)
                agg.unique_asn.add(asn_str)
                if asn_str in cloud_asn_set:
                    agg.cloud_asn_unique.add(asn_str)

            dport = str(record["dport"])
            agg.dport_counter[dport] += 1
            if is_https_flow(dport):
                agg.https_flows += 1
            if is_quic_flow(str(record["proto"]), dport):
                agg.quic_flows += 1

        processed_rel_paths.append(rel_path)

    staged_rows: list[dict[str, object]] = []
    for (window_start_epoch, identity), agg in aggregates.items():
        flow_count = max(agg.flow_count, 1)
        bytes_total = agg.bytes_out + agg.bytes_in
        staged_rows.append(
            {
                "window_start_epoch": window_start_epoch,
                "site": site,
                "identity": identity,
                "flow_count": agg.flow_count,
                "bytes_out": agg.bytes_out,
                "bytes_in": agg.bytes_in,
                "bytes_total": bytes_total,
                "unique_daddr": len(agg.unique_daddr),
                "unique_asn": len(agg.unique_asn),
                "cloud_asn_unique": len(agg.cloud_asn_unique),
                "https_ratio": agg.https_flows / flow_count,
                "quic_ratio": agg.quic_flows / flow_count,
                "port_entropy": compute_port_entropy(agg.dport_counter),
                "asn_set": set(agg.unique_asn),
                "feature_version": feature_version,
                "source_file_count": len(files),
                "run_id": run_id,
            }
        )

    # Compute novelty feature in temporal order per identity.
    staged_rows.sort(key=lambda row: (str(row["identity"]), int(row["window_start_epoch"])))
    seen_asn_by_identity: DefaultDict[str, set[str]] = defaultdict(set)
    final_rows: list[dict[str, object]] = []
    for row in staged_rows:
        identity = str(row["identity"])
        asn_set = set(row["asn_set"])
        seen = seen_asn_by_identity[identity]

        if asn_set:
            new_asn = asn_set - seen
            new_asn_ratio = len(new_asn) / len(asn_set)
        else:
            new_asn_ratio = 0.0

        seen.update(asn_set)

        window_start_dt = datetime.fromtimestamp(
            int(row["window_start_epoch"]), tz=timezone.utc
        )
        window_end_dt = datetime.fromtimestamp(
            int(row["window_start_epoch"]) + window_seconds, tz=timezone.utc
        )
        output = dict(row)
        output.pop("asn_set", None)
        output["new_asn_ratio"] = new_asn_ratio
        output["window_start"] = window_start_dt
        output["window_end"] = window_end_dt
        output["dt"] = window_start_dt.strftime("%Y-%m-%d")
        output["hour"] = int(window_start_dt.strftime("%H"))
        final_rows.append(output)

    return final_rows, processed_rel_paths


def write_parquet_dataset(rows: list[dict[str, object]], output_root: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pyarrow is required to write Parquet. Install dependencies from "
            "requirements-l2.txt"
        ) from exc

    if not rows:
        print("[L2] No rows generated; nothing to write.", file=sys.stderr)
        return

    columns = {
        "window_start": [row["window_start"] for row in rows],
        "window_end": [row["window_end"] for row in rows],
        "site": [row["site"] for row in rows],
        "identity": [row["identity"] for row in rows],
        "flow_count": [int(row["flow_count"]) for row in rows],
        "bytes_out": [int(row["bytes_out"]) for row in rows],
        "bytes_in": [int(row["bytes_in"]) for row in rows],
        "bytes_total": [int(row["bytes_total"]) for row in rows],
        "unique_daddr": [int(row["unique_daddr"]) for row in rows],
        "unique_asn": [int(row["unique_asn"]) for row in rows],
        "cloud_asn_unique": [int(row["cloud_asn_unique"]) for row in rows],
        "https_ratio": [float(row["https_ratio"]) for row in rows],
        "quic_ratio": [float(row["quic_ratio"]) for row in rows],
        "port_entropy": [float(row["port_entropy"]) for row in rows],
        "new_asn_ratio": [float(row["new_asn_ratio"]) for row in rows],
        "feature_version": [row["feature_version"] for row in rows],
        "source_file_count": [int(row["source_file_count"]) for row in rows],
        "run_id": [row["run_id"] for row in rows],
        "dt": [row["dt"] for row in rows],
        "hour": [int(row["hour"]) for row in rows],
    }

    schema = pa.schema(
        [
            pa.field("window_start", pa.timestamp("s", tz="UTC")),
            pa.field("window_end", pa.timestamp("s", tz="UTC")),
            pa.field("site", pa.string()),
            pa.field("identity", pa.string()),
            pa.field("flow_count", pa.int64()),
            pa.field("bytes_out", pa.int64()),
            pa.field("bytes_in", pa.int64()),
            pa.field("bytes_total", pa.int64()),
            pa.field("unique_daddr", pa.int32()),
            pa.field("unique_asn", pa.int32()),
            pa.field("cloud_asn_unique", pa.int32()),
            pa.field("https_ratio", pa.float32()),
            pa.field("quic_ratio", pa.float32()),
            pa.field("port_entropy", pa.float32()),
            pa.field("new_asn_ratio", pa.float32()),
            pa.field("feature_version", pa.string()),
            pa.field("source_file_count", pa.int32()),
            pa.field("run_id", pa.string()),
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
    args: argparse.Namespace,
    input_root: Path,
    output_root: Path,
    processed_files: list[str],
    rows: list[dict[str, object]],
    row_count: int,
    window_seconds: int,
) -> None:
    manifest_dir.mkdir(parents=True, exist_ok=True)
    partitions = sorted(
        {f"dt={row['dt']}/hour={int(row['hour']):02d}" for row in rows}
    )
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_root": str(input_root),
        "output_root": str(output_root),
        "window": args.window,
        "window_seconds": window_seconds,
        "site": args.site,
        "feature_version": args.feature_version,
        "row_count": row_count,
        "processed_file_count": len(processed_files),
        "processed_files": processed_files,
        "partitions": partitions,
        "reprocess": bool(args.reprocess),
        "dry_run": bool(args.dry_run),
    }
    filename = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{run_id}.json"
    path = manifest_dir / filename
    with path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)


def check_dependencies() -> None:
    if shutil.which("ra") is None:
        raise RuntimeError(
            "Argus client binary 'ra' was not found in PATH. "
            "Install argus-clients or run inside the sensor container."
        )


def main() -> int:
    args = parse_args()

    input_root = Path(args.input_root).resolve()
    output_root = Path(args.output_root).resolve()
    state_file = (
        Path(args.state_file).resolve()
        if args.state_file
        else (output_root / "_state" / "processed_files.json")
    )
    manifest_dir = (
        Path(args.manifest_dir).resolve()
        if args.manifest_dir
        else (output_root / "_manifests")
    )
    window_seconds = parse_window_to_seconds(args.window)
    from_ts = parse_utc_timestamp(args.from_ts)
    to_ts = parse_utc_timestamp(args.to_ts)
    run_id = uuid.uuid4().hex[:12]

    if not input_root.exists():
        print(f"[L2] Input root does not exist: {input_root}", file=sys.stderr)
        return 2

    check_dependencies()
    cloud_asn_set = load_cloud_asn_set(args)

    processed_state = set()
    if not args.reprocess:
        processed_state = load_state(state_file)

    files = list_input_files(
        input_root=input_root,
        from_ts=from_ts,
        to_ts=to_ts,
        max_files=args.max_files,
        already_processed=processed_state,
        reprocess=args.reprocess,
    )

    print(
        f"[L2] Selected {len(files)} file(s) from {input_root} "
        f"(window={args.window}, site={args.site}, run_id={run_id})",
        file=sys.stderr,
    )

    if not files:
        print("[L2] Nothing to process.", file=sys.stderr)
        return 0

    rows, processed_files = build_feature_rows(
        files=files,
        input_root=input_root,
        site=args.site,
        window_seconds=window_seconds,
        feature_version=args.feature_version,
        cloud_asn_set=cloud_asn_set,
        run_id=run_id,
    )

    print(f"[L2] Generated {len(rows)} feature row(s).", file=sys.stderr)

    if not args.dry_run:
        write_parquet_dataset(rows, output_root)
        updated_state = processed_state.union(processed_files)
        write_state(state_file, updated_state)
    else:
        print("[L2] Dry run enabled; skipping Parquet/state writes.", file=sys.stderr)

    write_manifest(
        manifest_dir=manifest_dir,
        run_id=run_id,
        args=args,
        input_root=input_root,
        output_root=output_root,
        processed_files=processed_files,
        rows=rows,
        row_count=len(rows),
        window_seconds=window_seconds,
    )

    print("[L2] Completed successfully.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(141)
    except Exception as exc:  # pragma: no cover - CLI top-level
        print(f"[L2] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
