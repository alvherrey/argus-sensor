# Argus File Rotation System

## Overview

The Argus sensor uses **`rasplit`** (or **`rastream`** in newer argus-clients) for automatic file rotation based on time intervals, integrated with **radium** as central hub.

## How it Works

1. **Argus** (port 561) captures packets and generates bidirectional flows with payload
2. **radium** (port 562) acts as central hub:
   - Receives stream from Argus
   - Enriches with GeoIP (country codes + ASN) via `RADIUM_CLASSIFIER_FILE`
   - Distributes to multiple consumers
3. **rasplit/rastream** connects to radium stream (localhost:562)
4. **rasplit/rastream** writes enriched flows to files with automatic rotation
5. Files are organized in date-based directories: `archive/YYYY/MM/DD/`

## Architecture

```
Argus (:561, primitivo+payload)
    ↓
radium (:562, hub+enrich)
    ├→ rasplit/rastream → archive/YYYY/MM/DD/*.out (for ML)
    └→ rastrip → racluster → rabins → InfluxDB (for dashboards)
```

## Configuration

### Environment Variables

Set in `docker-compose.yaml` or export before running:

| Variable | Default | Description |
|----------|---------|-------------|
| `INTERFACE` | ens160 | Network interface to capture |
| `PORT` | 561 | Argus TCP stream port |
| `ROTATION_INTERVAL` | 1d | File rotation interval |
| `ENABLE_ENRICHMENT` | yes | Enable GeoIP enrichment via radium |
| `ENABLE_INFLUXDB` | no | Enable InfluxDB pipeline |

### Rotation Intervals

| Value | Meaning | New file every | Files per day |
|-------|---------|----------------|---------------|
| `1h` | 1 hour | 60 minutes | 24 |
| `6h` | 6 hours | 6 hours | 4 |
| `12h` | 12 hours | 12 hours | 2 |
| `1d` | 1 day | Midnight | 1 |
| `1w` | 1 week | Sunday midnight | 1/7 |

## File Naming Convention

```
/var/log/argus/archive/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S.out
```

**Example:**
```
argus-data/archive/2026/02/15/argus.2026.02.15.14.30.00.out
                   │    │  │        │    │  │  │  │  │
                   Year ┘  │ Day ───┘    │  │  │  │  └─ Seconds
                      Month┘              │  │  │  └──── Minutes  
                                    Year ─┘  │  └─────── Hour
                                       Month ─┘
```

## Directory Structure

```
argus-data/
└── archive/
    ├── 2026/
    │   ├── 01/
    │   │   ├── 15/
    │   │   │   ├── argus.2026.01.15.00.00.00.out
    │   │   │   └── argus.2026.01.15.00.00.00.out.gz
    │   │   └── 16/
    │   │       └── argus.2026.01.16.00.00.00.out
    │   └── 02/
    │       ├── 14/
    │       └── 15/
    │           ├── argus.2026.02.15.00.00.00.out
    │           ├── argus.2026.02.15.01.00.00.out (if hourly)
    │           └── argus.2026.02.15.02.00.00.out (if hourly)
    └── 2027/
        └── ...
```

## Changing Rotation Interval

### Method 1: Environment Variable (recommended)

```bash
export ROTATION_INTERVAL=6h
./run-argus.sh ens160
```

### Method 2: Edit docker-compose.yaml

```yaml
environment:
  - ROTATION_INTERVAL=6h
```

### Method 3: Pass to run script

```bash
ROTATION_INTERVAL=1h ./run-argus.sh ens160
```

## Storage Considerations

### Estimated file sizes

Depends heavily on traffic volume. Examples:

| Traffic Volume | Rotation | Files/day | Approx size/file | Daily total |
|---------------|----------|-----------|------------------|-------------|
| Low (home) | 1d | 1 | 10-50 MB | 50 MB |
| Medium (office) | 1d | 1 | 100-500 MB | 500 MB |
| High (enterprise) | 6h | 4 | 500 MB - 2 GB | 2-8 GB |
| Very High | 1h | 24 | 500 MB - 1 GB | 12-24 GB |

### Compression

Files compress well (3-4:1 ratio):
- Uncompressed: 1 GB → Compressed: 250-330 MB

## Archive Management

### Automatic cleanup script

```bash
# Compress old files and delete after 90 days
./cleanup-archives.sh

# Custom retention
RETENTION_DAYS=7 ./cleanup-archives.sh

# Only delete, no compression
COMPRESS=no RETENTION_DAYS=90 ./cleanup-archives.sh
```

### Cron setup

```bash
# Edit crontab
crontab -e

# Add (run daily at 3 AM)
0 3 * * * cd /path/to/argus-sensor && ./cleanup-archives.sh >> /var/log/argus-cleanup.log 2>&1
```

### Manual management

```bash
# Compress specific day
gzip argus-data/archive/2026/02/14/*.out

# Delete month
rm -rf argus-data/archive/2026/01/

# Move to cold storage
tar -czf 2026-01.tar.gz argus-data/archive/2026/01/
aws s3 cp 2026-01.tar.gz s3://my-bucket/argus-archive/
rm -rf argus-data/archive/2026/01/
```

## Reading Rotated Files

### Single file

```bash
ra -r argus-data/archive/2026/02/15/argus.2026.02.15.00.00.00.out
```

### All files from a day

```bash
ra -r argus-data/archive/2026/02/15/*.out
```

### Compressed files (automatic)

```bash
ra -r argus-data/archive/2026/02/15/*.out.gz
```

### Multiple days

```bash
ra -r argus-data/archive/2026/02/{14,15,16}/*.out
```

### Time range query

```bash
# Specific time window
ra -r argus-data/archive/2026/02/15/*.out -t 2026/02/15.14:00:00-2026/02/15.16:00:00
```

## Troubleshooting

### Files not rotating

```bash
# Check archive rotation process is running
sudo docker compose exec argus ps aux | grep -E "rasplit|rastream"

# Check logs
sudo docker compose logs | grep -E "rasplit|rastream"

# Verify radium stream (enriched data on port 562)
ra -S localhost:562 -c 5
```

### Wrong rotation time

```bash
# Check container timezone
sudo docker compose exec argus date

# Sync with host (add to docker-compose.yaml)
volumes:
  - /etc/timezone:/etc/timezone:ro
  - /etc/localtime:/etc/localtime:ro
```

### Disk full

```bash
# Check usage
du -sh argus-data/archive/

# Quick cleanup (keep last 3 days)
find argus-data/archive -name "*.out" -mtime +3 -delete

# Aggressive compression
find argus-data/archive -name "*.out" -exec gzip {} \;
```

## Advanced: Custom Rotation Logic

Edit `entrypoint.sh` to customize rotation parameters (`rasplit`/`rastream`):

```bash
# Example: 5-minute rotation
rasplit -S localhost:${PORT} \
  -M time 5m \
  -w "/var/log/argus/archive/%Y/%m/%d/argus.%Y.%m.%d.%H.%M.%S.out" \
  -Z b &
```

## Integration with Analysis Pipeline

### Dual Pipeline Architecture

**Files (for ML batch analysis):**
```
Argus:561 → radium:562 (enrich) → rasplit/rastream → archive/*.out (payload+GeoIP)
```

**InfluxDB (for real-time dashboards):**
```
Argus:561 → radium:562 (enrich) → rastrip → racluster → rabins → Telegraf → InfluxDB → Grafana
```

### Files (batch processing)
```
Archive files → Python/Spark → Feature extraction → ML models
```

### Hybrid approach
```
Stream: Real-time alerting
Files: Deep analysis, training, forensics
```
