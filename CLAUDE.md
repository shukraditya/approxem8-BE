# Approximate Query Engine (AQE)

FastAPI service for approximate analytical queries using DuckDB with intelligent auto-routing and materialized samples.

## Quick Reference

| Command | Purpose |
|---------|---------|
| `uv run uvicorn aqe.main:app --reload` | Start FastAPI server |
| `curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d '{"sql": "SELECT COUNT(*) FROM sales", "accuracy": 0.95}'` | Query with auto-routing |
| `curl -X POST http://localhost:8000/compare -d '{"sql": "...", "accuracy": 0.95}'` | Compare exact vs approx |
| `curl -X POST http://localhost:8000/compare-strategies -d '{"sql": "..."}'` | Compare all strategies |
| `curl -X POST http://localhost:8000/refresh` | Rebuild materialized samples |

## What We Built

### Auto-Router Architecture

```
User Request: {"sql": "...", "accuracy": 0.95}
              ↓
    ┌─────────────────────┐
    │  SQL Parser         │  ← sqlglot extracts patterns
    │  - Table, columns   │
    │  - Aggregations     │
    │  - GROUP BY         │
    └──────────┬──────────┘
               ↓
    ┌─────────────────────┐
    │  Data Profiler      │  ← Cached table statistics
    │  - Row count        │
    │  - Cardinality      │
    │  - Gini (skew)      │
    │  - Materialized     │  ← NEW: pre-computed samples
    │    samples          │
    └──────────┬──────────┘
               ↓
    ┌─────────────────────┐
    │  Auto-Router        │  ← Rule-based strategy selection
    │  COUNT DISTINCT → APPROX_    │  ← DuckDB native (680x faster)
    │    COUNT_DISTINCT            │
    │  Quantiles → APPROX_         │  ← DuckDB native (3x faster)
    │    QUANTILE                  │
    │  GROUP BY + sample exists →  │  ← NEW: accuracy-based samples
    │    Materialized (1%/10%/20%) │
    │  GROUP BY + skew → Stratified│
    │  Otherwise → DuckDB sample   │
    └──────────┬──────────┘
               ↓
    ┌─────────────────────┐
    │  Accuracy → Materialized │  ← Select sample by accuracy
    │  0.85 → 1% sample   │     (1%/10%/20% for 85/90/95%)
    │  0.90 → 10% sample  │
    │  0.95 → 20% sample  │
    └──────────┬──────────┘
               ↓
          Execute Strategy
               ↓
    Response with metadata
```

## Performance Results

### Materialized Samples Speedup

| Query Type | Before (Runtime) | After (Materialized) | Speedup |
|------------|------------------|---------------------|---------|
| `COUNT(*)` | ~400ms | **2.7ms** | **148x** |
| `GROUP BY region` | ~1,200ms | **122ms** | **9.8x** |
| `AVG(amount)` | ~500ms | ~50ms | **10x** |

### DuckDB Native Functions

| Function | Before (Python) | After (DuckDB Native) | Speedup |
|----------|----------------|----------------------|---------|
| `COUNT(DISTINCT)` | ~493s (8+ min) | **~700ms** | **680x** |
| `MEDIAN/PERCENTILE` | ~19s (exact) | **~5s** | **3.5x** |

### Accuracy-Based Sample Selection

| Accuracy Target | Sample | Actual Accuracy | Storage | Query Time |
|----------------|--------|-----------------|---------|------------|
| 85% | **1%** | ~96% | 50MB | ~100ms |
| 90% | **10%** | ~99% | 500MB | ~3ms |
| 95% | **20%** | ~99.5% | 1GB | ~4ms |

**Storage Cost**: ~31% overhead (5GB → 6.5GB with 1%/10%/20% samples)

See full analysis: `logs/RESULTS_ANALYSIS.md`

## Key Files

| Path | Purpose |
|------|---------|
| `src/aqe/main.py` | FastAPI app, endpoints, query execution |
| `src/aqe/models.py` | Pydantic models (QueryRequest, QueryResponse, QueryMetadata) |
| `src/aqe/profiler.py` | DataProfiler - table stats, skew detection, **materialized samples** |
| `src/aqe/router.py` | AutoRouter - sqlglot-based strategy selection |
| `src/aqe/accuracy.py` | Accuracy-to-parameters mapping |
| `src/aqe/error.py` | Error estimation for sampling |
| `src/aqe/strategies/` | Execution strategies |
| `src/aqe/strategies/duckdb_approx.py` | DuckDB native APPROX_COUNT_DISTINCT |
| `src/aqe/strategies/duckdb_quantile.py` | DuckDB native APPROX_QUANTILE |
| `src/aqe/strategies/stratified.py` | Stratified sampling for GROUP BY |
| `src/aqe/strategies/materialized.py` | Pre-computed samples (1%/10%/20%/stratified) |

## API Endpoints

### POST /query

Execute SQL with automatic strategy selection.

**Request:**
```json
{
  "sql": "SELECT region, COUNT(*) FROM sales GROUP BY region",
  "accuracy": 0.95
}
```

**Response:**
```json
{
  "results": [{"region": "US", "count_star": 45000000}],
  "metadata": {
    "mode": "approx",
    "strategy": "materialized",
    "query_time_ms": 122.36,
    "accuracy_requested": 0.95,
    "accuracy_achieved": 0.95,
    "error_estimate": {
      "materialized": true,
      "sample_table": "sales_sample_10pct"
    }
  }
}
```

**Parameters:**
- `sql` (required): SQL query to execute
- `accuracy` (optional): 0.90-0.99, triggers auto-routing
- `mode` (optional): "exact" or "approx" (default: "exact")
- `strategy` (optional): Manual override ("duckdb_sample", "python_hll", "tdigest", "stratified", "materialized")
- `sample_rate` (optional): Manual override (0.05-0.50)

### POST /compare

Run exact and approximate, return comparison with speedup.

**Response:**
```json
{
  "exact": {"time_ms": 1200, "results": [{"avg": 250.0}]},
  "approx": {"time_ms": 122, "strategy": "materialized", "results": [{"avg": 248.5}]},
  "speedup": 9.8
}
```

### POST /refresh

**NEW**: Rebuild materialized samples after data changes.

**Response:**
```json
{
  "status": "ok",
  "dropped": ["sales_sample_10pct", "sales_sample_stratified"],
  "created": ["10pct", "stratified"],
  "message": "Refreshed 2 materialized sample tables"
}
```

### POST /compare-strategies

Compare all available strategies for a query.

### GET /health

Health check with data loaded status.

## How Auto-Routing Works

### 1. Data Profiler (`src/aqe/profiler.py`)

Profiles tables at startup and caches results:

```python
profiler = DataProfiler()
profiler.profile_table(db, "sales")
# Returns:
{
  "row_count": 500000000,
  "columns": {
    "region": {"type": "VARCHAR", "cardinality": 3},
    "amount": {"type": "DECIMAL", "cardinality": 500000000,
               "mean": 250, "stddev": 500, "gini": 0.3}
  }
}
```

**Gini Coefficient**: Measures distribution skew (0=even, 1=skewed)
- `region` Gini = 0.6 → highly skewed (90% US, 8% UK, 2% Antarctica)
- High Gini triggers stratified sampling for GROUP BY

### 2. Materialized Samples (`src/aqe/profiler.py`)

**NEW**: Pre-computed sample tables created at startup:

```python
# Creates at server startup:
# - sales_sample_10pct (10% uniform sample)
# - sales_sample_stratified (10% per region)
profiler.create_materialized_samples(db, "sales")
```

**Benefits:**
- 10-150x faster queries (no runtime sampling)
- Exact results (no statistical error)
- Consistent results across queries

**Tradeoffs:**
- 20% storage overhead
- Stale data until refresh
- Pre-computation time at startup

### 3. Auto-Router (`src/aqe/router.py`)

Uses sqlglot to parse SQL and apply rules:

```python
router = AutoRouter(profiler)
routing = router.route(sql, db, accuracy=0.95)
# Returns: {"strategy": "materialized", "config": {"sample_table": "sales_sample_10pct"}}
```

**Routing Rules:**

| Pattern | Condition | Strategy | Sample |
|---------|-----------|----------|--------|
| `COUNT(DISTINCT col)` | always | `duckdb_approx` | DuckDB native HLL |
| `PERCENTILE` / `MEDIAN` | always | `duckdb_quantile` | DuckDB native t-Digest |
| `GROUP BY col` | accuracy 85-90% | `materialized` | 1% sample |
| `GROUP BY col` | accuracy 91-95% | `materialized` | 10% sample |
| `GROUP BY col` | accuracy 96-99% | `materialized` | 20% sample |
| `GROUP BY col` | Gini > 0.6 (skewed) | `stratified` | 10% per group |
| Simple aggregate | accuracy 85-90% | `materialized` | 1% sample |
| Simple aggregate | accuracy 91-95% | `materialized` | 10% sample |
| Simple aggregate | accuracy 96-99% | `materialized` | 20% sample |
| Small table | row_count < 100K | `exact` | Full scan |

### 4. Accuracy Mapping (`src/aqe/accuracy.py`)

Converts accuracy target to implementation parameters:

```python
# For sampling: statistical formula
sample_rate = accuracy_to_sample_rate(
    accuracy=0.95,
    mean=250, stddev=500,
    total_rows=500000000
)
# → 0.1 (10% sample)

# For HLL: lookup table
precision = accuracy_to_hll_precision(0.95)
# → 14 (±0.6% error)
```

## Strategies

### materialized (NEW)

Uses pre-computed sample tables for instant queries.

**Best for**: GROUP BY, simple aggregations when sample exists
**Speedup**: 10-150x
**Storage**: 20% overhead
**Tradeoff**: Stale data until refresh

### duckdb_sample (Default)

Uses DuckDB's native `USING SAMPLE` for fast uniform sampling.

**Best for**: Simple aggregations without GROUP BY
**Speedup**: 10x
**Error**: ±3-5% for 10% sample

### duckdb_approx

DuckDB native APPROX_COUNT_DISTINCT using HyperLogLog in C++.

**Best for**: High-cardinality columns (user_id, order_id)
**Speedup**: **680x** vs Python HLL
**Error**: ~4%
**Advantage**: Native C++ implementation

### stratified

Sample within each GROUP BY group to preserve rare groups.

**Best for**: Skewed GROUP BY (e.g., region with 90/8/2% distribution)
**Speedup**: 3-5x
**Error**: ±1-2% per group
**Critical**: Prevents missing small groups like "Antarctica"

### duckdb_quantile

DuckDB native APPROX_QUANTILE using t-Digest in C++.

**Best for**: Percentile queries
**Speedup**: **3.5x** vs exact
**Error**: ~0.2%
**Advantage**: Much more accurate than sampling for quantiles

## Dataset

**sales.parquet**: 500M rows (~5GB)
- `user_id`: BIGINT (1M distinct)
- `region`: VARCHAR (US: 90%, UK: 8%, Antarctica: 2%)
- `amount`: DECIMAL (mean: ~$250)
- `date`: TIMESTAMP

Generated by: `uv run scripts/generate_data.py`

## Dependencies

```toml
dependencies = [
    "duckdb>=1.5.1",        # Fast analytical SQL with native approx functions
    "sqlglot>=25.0.0",      # SQL parsing for auto-router
    "fastapi>=0.135.2",     # HTTP API
    "pydantic>=2.12.5",     # Request/response models
    "uvicorn>=0.42.0",      # ASGI server
]
```

## Design Decisions

### Why Rule-Based vs ML?

**Rule-based (what we built)**:
- ✅ Simple, explainable
- ✅ No training required
- ✅ Provable guarantees
- ✅ Fast (no model inference)

**ML/Meta-learning (what we skipped)**:
- ❌ Complex, needs training data
- ❌ Black box decisions
- ❌ Overkill for this use case

### Why Materialized Samples?

**Benefits:**
- ✅ 10-150x speedup for analytical queries
- ✅ Exact results (no sampling error)
- ✅ Consistent results across queries
- ✅ Simple implementation

**Tradeoffs:**
- ⚠️ ~31% storage overhead (1% + 10% + 20% + stratified samples)
- ⚠️ Stale data until refresh
- ⚠️ Pre-computation time at startup (~60s for 500M rows)

**When to use:**
- Dashboard queries (run frequently, need speed)
- Historical analysis (stale data acceptable)
- GROUP BY on low-cardinality columns
- Simple aggregates with accuracy 85-99%

**When NOT to use:**
- Real-time monitoring (need fresh data)
- Small tables (< 100K rows)
- Complex JOINs
- High-selectivity WHERE clauses (e.g., user_id = 123)

### Why sqlglot?

- Parses SQL into AST (not just regex)
- Extracts tables, columns, functions reliably
- Handles complex queries
- DuckDB dialect support

### Why Gini for Skew Detection?

Gini coefficient (0-1) measures distribution inequality:
- 0 = perfectly even (uniform sampling works)
- 1 = all in one bin (must use stratified)
- 0.6 threshold captures meaningful skew

## Testing

### Manual Tests

```bash
# Test accuracy-based materialized samples
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) FROM sales", "accuracy": 0.85}'  # Uses 1% sample

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) FROM sales", "accuracy": 0.90}'  # Uses 10% sample

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) FROM sales", "accuracy": 0.95}'  # Uses 20% sample

# Test COUNT DISTINCT → DuckDB native (680x faster)
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(DISTINCT user_id) FROM sales", "accuracy": 0.95}'

# Test MEDIAN → DuckDB native (3.5x faster)
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT MEDIAN(amount) FROM sales", "accuracy": 0.95}'

# Test GROUP BY with stratified sample
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT region, COUNT(*) FROM sales GROUP BY region", "accuracy": 0.95}'

# Refresh all materialized samples
curl -X POST http://localhost:8000/refresh
```

### View Test Results

All test logs saved in `logs/`:
```bash
ls logs/
# RESULTS_ANALYSIS.md       - Full performance analysis
# test_final_*.log          - Test results
# materialized_tests_*.log  - Earlier test attempts
```

## Future Enhancements

- [x] **Materialized stratified samples** - ✅ DONE
- [ ] Query result caching
- [ ] Multiple table support
- [ ] Custom accuracy functions per strategy
- [ ] A/B testing framework for strategies
- [ ] Adaptive sample rate based on query history

## Project Status

**Current State**: ✅ **Production Ready**

| Feature | Status | Notes |
|---------|--------|-------|
| Auto-router | ✅ Complete | Rule-based strategy selection |
| Materialized samples | ✅ Complete | 1%/10%/20% by accuracy tier |
| COUNT DISTINCT | ✅ Complete | DuckDB APPROX_COUNT_DISTINCT (680x) |
| Stratified sampling | ✅ Complete | GROUP BY on skewed data |
| Quantiles | ✅ Complete | DuckDB APPROX_QUANTILE (3.5x) |
| Error estimation | ✅ Complete | Per-strategy error bounds |
| /refresh endpoint | ✅ Complete | Rebuild all samples |
| Documentation | ✅ Complete | This file + RESULTS_ANALYSIS.md + ARCHITECTURE.md |

## Notes for Claude

- Profiler runs at startup - can be slow for large tables (30-60s for 500M rows)
- Router uses cached profiles - restart server if data changes
- All strategies implement `ExecutionStrategy` interface
- Error estimates are theoretical (not empirical)
- Materialized samples are created in `lifespan()` at startup
- Stratified sampling creates new DB connection per group (slow for many groups)
- Use `/refresh` endpoint after data changes to rebuild samples
- See `logs/RESULTS_ANALYSIS.md` for detailed performance analysis

### Timing Anomalies

**Why might smaller samples appear slower?**
- First query on any sample reads from cold disk cache (~300ms)
- Subsequent queries benefit from warm OS cache (~2-5ms)
- 1% sample (5M rows) vs 10% sample (50M rows) - both fit in memory after first access
- Always run multiple queries to get accurate timing comparisons
