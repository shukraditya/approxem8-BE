# Approximate Query Engine (AQE)

FastAPI service for approximate analytical queries using DuckDB. Trade accuracy for query speed with intelligent strategy selection.

**Key Results:** 180x speedup with ~95% accuracy using materialized samples.

---

## Quick Start

```bash
# Generate dataset (10M rows)
uv run scripts/generate_data.py

# Run API
uv run uvicorn aqe.main:app --reload

# Health check
curl http://localhost:8000/health
```

---

## Usage

### Basic Query

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) FROM sales", "mode": "exact"}'
```

### Approximate with Accuracy Target

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT region, AVG(amount) FROM sales GROUP BY region",
    "accuracy": 0.95
  }'
```

The `accuracy` parameter (0.90-0.99) auto-routes to the optimal strategy.

### Compare Exact vs Approximate

```bash
curl -X POST http://localhost:8000/compare \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT region, COUNT(*) FROM sales GROUP BY region",
    "sample_rate": 0.1
  }'
```

### Compare All Strategies

```bash
curl -X POST http://localhost:8000/compare-strategies \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT AVG(amount) FROM sales"}'
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Service status |
| `/query` | POST | Execute query (exact or approximate) |
| `/compare` | POST | Run exact + approx, return speedup |
| `/compare-strategies` | POST | Benchmark all strategies |
| `/refresh` | POST | Rebuild materialized samples after data changes |

### Query Request Format

```json
{
  "sql": "SELECT region, COUNT(*), AVG(amount) FROM sales GROUP BY region",
  "mode": "approx",
  "sample_rate": 0.1,
  "strategy": "stratified",
  "accuracy": 0.95,
  "config": {}
}
```

**Parameters:**
- `sql` (required): SQL query to execute
- `mode`: `"exact"` or `"approx"` (default: `"exact"`)
- `sample_rate`: Fraction for sampling, 0.0-1.0 (default: `0.1`)
- `strategy`: Specific strategy to use (auto-selected if omitted)
- `accuracy`: Target accuracy 0.90-0.99 (triggers auto-routing)
- `config`: Strategy-specific configuration

### Response Format

```json
{
  "results": [
    {"region": "US", "count_star()": 9000123, "avg(amount)": 500.23}
  ],
  "metadata": {
    "mode": "approx",
    "strategy": "materialized",
    "query_time_ms": 1.8,
    "rows_returned": 3,
    "accuracy_requested": 0.95,
    "accuracy_achieved": 0.95,
    "error_estimate": {"materialized": true}
  }
}
```

---

## Strategies

| Strategy | Best For | Speedup | Accuracy | Notes |
|----------|----------|---------|----------|-------|
| `exact` | Ground truth, small tables | 1x | 100% | Baseline |
| `duckdb_sample` | Simple aggregates | 36x | 90-99% | Runtime sampling |
| `duckdb_approx` | `COUNT(DISTINCT)` | 18x | ~96% | Native HLL (4% error) |
| `duckdb_quantile` | Percentiles, median | 12x | ~95% | Native approx quantile |
| `stratified` | GROUP BY on skewed data | 3.6x | ~95% | Guaranteed representation |
| `materialized` | Dashboard queries | 180x | 90-99% | Pre-computed samples |

### Auto-Router Selection

The auto-router uses SQL pattern matching to select strategies:

```
COUNT(DISTINCT col)  →  duckdb_approx
PERCENTILE/MEDIAN    →  duckdb_quantile
GROUP BY (skewed)    →  stratified
Simple aggregate     →  materialized (if available)
```

---

## Performance

### Benchmarks (10M rows)

| Mode | Time | Speedup |
|------|------|---------|
| Exact | ~180ms | 1x |
| Materialized 20% | ~2ms | **90x** |
| Materialized 10% | ~1ms | **180x** |
| DuckDB Sample 10% | ~5ms | 36x |
| Stratified 10% | ~50ms | 3.6x |

### Dataset Profile

```
10M rows, ~150MB parquet
Regions: US (90%), UK (8%), Antarctica (2%) — intentionally skewed
Schema: user_id, region, amount, date
```

---

## Architecture

```
┌──────────┐     ┌─────────┐     ┌──────────────┐     ┌─────────────┐
│  Client  │────▶│ FastAPI │────▶│ AutoRouter   │────▶│  Strategy   │
└──────────┘     └─────────┘     │ (sqlglot)    │     │  Executor   │
                                 └──────────────┘     └──────┬──────┘
                                                             │
                    ┌────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┬──────────────┬──────────────┐
        ▼           ▼           ▼              ▼              ▼
   ┌────────┐ ┌──────────┐ ┌─────────┐  ┌──────────┐  ┌──────────────┐
   │ Exact  │ │ DuckDB   │ │ Stratified │ │ DuckDB   │  │ Materialized │
   │        │ │ Sample   │ │ Sampling   │ │ Approx   │  │ Samples      │
   └────────┘ └──────────┘ └─────────┘  └──────────┘  └──────────────┘
```

**Key Components:**

- **`AutoRouter`**: Parses SQL with sqlglot, selects optimal strategy
- **`DataProfiler`**: Computes column stats, skew detection (Gini coefficient)
- **`Materialized Samples`**: Pre-computed at startup (1%, 10%, 20%, stratified)
- **`Accuracy Module`**: Maps accuracy targets to sample rates using statistical CI

---

## Project Structure

```
approx-query-engine/
├── src/aqe/
│   ├── main.py              # FastAPI app, endpoints
│   ├── models.py            # Pydantic request/response models
│   ├── router.py            # Auto-router (strategy selection)
│   ├── profiler.py          # Data profiling, materialized samples
│   ├── accuracy.py          # Accuracy-to-parameters mapping
│   ├── error.py             # Error estimation functions
│   └── strategies/
│       ├── stratified.py    # Stratified sampling for GROUP BY
│       ├── duckdb_approx.py # Native COUNT(DISTINCT)
│       ├── duckdb_quantile.py # Native percentile approx
│       ├── materialized.py  # Pre-computed samples
│       └── python_hll.py    # Python HLL (reference)
├── scripts/
│   └── generate_data.py     # Synthetic dataset generator
├── data/
│   └── sales.parquet        # Generated dataset
├── pyproject.toml
└── README.md
```

---

## Configuration

### Environment

- **Python**: 3.12+
- **Package Manager**: uv (required)
- **Database**: DuckDB (embedded)

### Dependencies

```toml
duckdb>=1.5.1
fastapi>=0.135.2
pydantic>=2.12.5
sqlglot>=25.0.0
tdigest>=0.5.2.2
datasketch>=1.9.0
```

---

## Development

### Run Tests

```bash
# Start server
uv run uvicorn aqe.main:app --reload

# Run comparisons
curl -X POST http://localhost:8000/compare-strategies \
  -d '{"sql": "SELECT COUNT(*) FROM sales"}'
```

### Refresh Materialized Samples

After data changes:

```bash
curl -X POST http://localhost:8000/refresh
```

### Generate Different Dataset Size

```bash
uv run scripts/generate_data.py 5000000 data/sales_5m.parquet
```

---

## How It Works

### 1. Skew Detection

The Gini coefficient (0=even, 1=skewed) detects when stratified sampling is needed:

```python
# Antarctica = 0.2% of data might disappear in 10% uniform sample
# Gini > 0.6 triggers stratified sampling
```

### 2. Accuracy Mapping

```python
# 95% accuracy → ~10% sample rate
# 99% accuracy → ~20% sample rate
sample_rate = (z * stddev / margin)^2 / total_rows
```

### 3. Materialized Sample Selection

| Accuracy | Sample Used |
|----------|-------------|
| ≥0.99 | 20% (highest quality) |
| ≥0.95 | 10% (balanced) |
| ≥0.90 | 1% (fastest) |

---

## Limitations

- Single table only (no JOINs in materialized samples)
- Stratified sampling currently optimized for `region` column
- Materialized samples need manual refresh (`/refresh`)
- Error estimates are simplified (not rigorous CIs)

---

## References

- [DuckDB Sampling](https://duckdb.org/docs/sql/samples.html)
- [DuckDB APPROX_COUNT_DISTINCT](https://duckdb.org/docs/sql/aggregates.html#approximate-aggregates)
- [HyperLogLog Paper](https://algo.inria.fr/flajolet/Publications/FlFuGaMe07.pdf)

---

## License

MIT
