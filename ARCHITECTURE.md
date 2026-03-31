# AQE System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (Your UI)                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────────────┐  │
│  │ SQL Editor  │    │ Accuracy    │    │ Results Table + Metadata Cards      │  │
│  │             │    │ Slider      │    │ (strategy, latency, accuracy)       │  │
│  │ SELECT ...  │    │ 80% ─── 99% │    │                                     │  │
│  └──────┬──────┘    └──────┬──────┘    └─────────────────────────────────────┘  │
│         │                  │                                                    │
│         └──────────────────┘                                                    │
│                    │                                                            │
│                    ▼                                                            │
│         POST /query {sql, accuracy}                                             │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           FASTAPI SERVER (src/aqe/main.py)                       │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ LIFESPAN (startup)                                                          ││
│  │  ├── DataProfiler.profile_table(db, "sales")                                ││
│  │  │       └── Collect: row_count, cardinality, mean, stddev, gini           ││
│  │  └── DataProfiler.create_materialized_samples(db, "sales")                ││
│  │         ├── CREATE TABLE sales_sample_10pct (10% uniform)                  ││
│  │         └── CREATE TABLE sales_sample_stratified (10% per region)          ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ /query ENDPOINT                                                             ││
│  │                                                                             ││
│  │  Input: {sql: "SELECT region, COUNT(*) FROM sales GROUP BY region",        ││
│  │          accuracy: 0.95}                                                   ││
│  │                                                                             ││
│  │  Step 1: AUTO-ROUTER (src/aqe/router.py)                                   ││
│  │          ├── Parse SQL (sqlglot) → table, columns, aggregations            ││
│  │          ├── Check DataProfile → row_count, skew                           ││
│  │          ├── Check has_materialized_sample? → YES                          ││
│  │          └── Return: {strategy: "materialized",                            ││
│  │                      config: {sample_table: "sales_sample_stratified"}}    ││
│  │                                                                             ││
│  │  Step 2: STRATEGY EXECUTION                                                ││
│  │          └── MaterializedSampleStrategy.execute(sql, config, db)           ││
│  │              ├── Rewrite: FROM sales → FROM sales_sample_stratified        ││
│  │              ├── db.execute(modified_sql)                                  ││
│  │              └── Return DataFrame                                          ││
│  │                                                                             ││
│  │  Step 3: BUILD RESPONSE                                                    ││
│  │          └── QueryResponse {                                               ││
│  │              results: [{region: "US", cnt: 44695974}, ...],                 ││
│  │              metadata: {                                                    ││
│  │                mode: "approx",                                              ││
│  │                strategy: "materialized",                                    ││
│  │                query_time_ms: 122.36,                                       ││
│  │                accuracy_requested: 0.95,                                    ││
│  │                accuracy_achieved: 0.95,                                     ││
│  │                error_estimate: {materialized: true,                         ││
│  │                                sample_table: "sales_sample_stratified"}     ││
│  │              }                                                              ││
│  │          }                                                                  ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                      │                                          │
│                                      ▼                                          │
│                           JSON RESPONSE TO FRONTEND                              │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                              DATA LAYER                                          │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │ DUCKDB                                                                   │    │
│  │                                                                          │    │
│  │  ┌─────────────────────┐    ┌─────────────────────────────────────────┐ │    │
│  │  │ sales.parquet       │    │ sales_sample_10pct                      │ │    │
│  │  │ (500M rows, ~5GB)   │    │ (50M rows, ~500MB)                      │ │    │
│  │  │                     │    │ Uniform random sample                   │ │    │
│  │  │ user_id: BIGINT     │    │                                         │ │    │
│  │  │ region: VARCHAR     │    │ ┌─────────────────────────────────┐     │ │    │
│  │  │ amount: DECIMAL     │    │ │ sales_sample_stratified         │     │ │    │
│  │  │ date: TIMESTAMP     │    │ │ (50M rows, ~500MB)              │     │ │    │
│  │  │                     │    │ │                                 │     │ │    │
│  │  │                     │    │ │ 10% from US (45M rows)         │     │ │    │
│  │  │                     │    │ │ 10% from UK (4M rows)          │     │ │    │
│  │  │                     │    │ │ 10% from Antarctica (1M rows)  │     │ │    │
│  │  │                     │    │ │                                 │     │ │    │
│  │  │                     │    │ │ Balanced for GROUP BY region    │     │ │    │
│  │  │                     │    │ └─────────────────────────────────┘     │ │    │
│  │  └─────────────────────┘    └─────────────────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │ IN-MEMORY CACHE (DataProfiler)                                           │    │
│  │                                                                          │    │
│  │  cache = {                                                               │    │
│  │    "sales": {                                                            │    │
│  │      row_count: 500000000,                                               │    │
│  │      columns: {                                                          │    │
│  │        region: {type: "VARCHAR", cardinality: 3, gini: 0.6},             │    │
│  │        amount: {type: "DECIMAL", cardinality: 500M,                      │    │
│  │                  mean: 250, stddev: 500, gini: 0.3}                      │    │
│  │      }                                                                   │    │
│  │    }                                                                     │    │
│  │  }                                                                       │    │
│  │                                                                          │    │
│  │  materialized_samples = {                                                │    │
│  │    "sales": ["10pct", "stratified"]                                      │    │
│  │  }                                                                       │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                           STRATEGY HIERARCHY                                     │
│                                                                                  │
│  ROUTER DECISION TREE (priority order):                                          │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ 0. MATERIALIZED ───────────────────────────────────────────────────────────││
│  │    IF: GROUP BY + materialized sample exists                                 ││
│  │    THEN: Use pre-computed table (10-150x faster)                            ││
│  │    PROS: Instant, exact results                                             ││
│  │    CONS: 20% storage overhead, stale data                                   ││
│  │                                                                             ││
│  │ 1. TDIGEST ─────────────────────────────────────────────────────────────────││
│  │    IF: PERCENTILE / MEDIAN queries                                          ││
│  │    THEN: t-Digest algorithm                                                 ││
│  │    PROS: Accurate quantiles                                                 ││
│  │    CONS: High memory usage                                                  ││
│  │                                                                             ││
│  │ 2. PYTHON_HLL ──────────────────────────────────────────────────────────────││
│  │    IF: COUNT(DISTINCT column)                                               ││
│  │    THEN: HyperLogLog (datasketch)                                           ││
│  │    PROS: 4-64KB memory regardless of data size                              ││
│  │    CONS: ~1% error, slow in Python (8+ min for 500M rows)                   ││
│  │                                                                             ││
│  │ 3. STRATIFIED ──────────────────────────────────────────────────────────────││
│  │    IF: GROUP BY + Gini > 0.6 (skewed data)                                  ││
│  │    THEN: Sample within each group                                           ││
│  │    PROS: Preserves rare groups (Antarctica)                                 ││
│  │    CONS: Slower than uniform sampling                                       ││
│  │                                                                             ││
│  │ 4. DUCKDB_SAMPLE ───────────────────────────────────────────────────────────││
│  │    IF: Simple aggregates or fallback                                        ││
│  │    THEN: DuckDB USING SAMPLE clause                                         ││
│  │    PROS: Fast native implementation                                         ││
│  │    CONS: Runtime sampling overhead                                          ││
│  │                                                                             ││
│  │ 5. EXACT ───────────────────────────────────────────────────────────────────││
│  │    IF: accuracy not specified OR table < 100K rows                          ││
│  │    THEN: Full table scan                                                    ││
│  │    PROS: 100% accurate                                                      ││
│  │    CONS: Slow for large tables                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                           FILE STRUCTURE                                         │
│                                                                                  │
│  src/aqe/                                                                        │
│  ├── main.py              # FastAPI app, endpoints, query orchestration         │
│  ├── models.py            # Pydantic: QueryRequest, QueryResponse, Metadata     │
│  ├── profiler.py          # DataProfiler: table stats + materialized samples    │
│  ├── router.py            # AutoRouter: strategy selection logic                │
│  ├── accuracy.py          # accuracy_to_sample_rate, accuracy_to_hll_precision  │
│  ├── error.py             # Error estimation functions                          │
│  └── strategies/                                                                 │
│       ├── __init__.py     # ExecutionStrategy base class                        │
│       ├── materialized.py # MaterializedSampleStrategy                          │
│       ├── python_hll.py   # HyperLogLog for COUNT DISTINCT                      │
│       ├── stratified.py   # Stratified sampling for GROUP BY                    │
│       └── tdigest.py      # t-Digest for quantiles                              │
│                                                                                  │
│  logs/                                                                           │
│  └── RESULTS_ANALYSIS.md  # Performance benchmarks                              │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                           API ENDPOINTS                                          │
│                                                                                  │
│  GET    /health           → {status: "ok", data_loaded: true}                    │
│  POST   /query            → Execute SQL with auto-routing                        │
│  POST   /compare          → Compare exact vs approximate with speedup            │
│  POST   /compare-strategies → Benchmark all strategies                           │
│  POST   /refresh          → Rebuild materialized samples                         │
│                                                                                  │
│  Request:  {sql: string, accuracy?: 0.80-0.99, mode?: "exact"|"approx"}          │
│  Response: {results: [{}], metadata: {mode, strategy, query_time_ms, ...}}       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow Summary

```
User Query
    ↓
[Router] Parse SQL → Check Profile → Pick Strategy
    ↓
[Strategy] Execute on appropriate data source
    ↓
[Response] Results + Metadata (latency, accuracy, strategy used)
```

## Key Design Decisions

1. **Materialized samples preferred**: Pre-computed tables are 10-150x faster
2. **Accuracy-based API**: Users specify target (0.80-0.99), system picks implementation
3. **Automatic fallback**: If materialized doesn't support query, falls back to runtime sampling
4. **Profile caching**: Table stats computed once at startup, reused for all queries
5. **Pluggable strategies**: New algorithms can be added without changing router logic
