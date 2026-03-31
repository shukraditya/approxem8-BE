# AQE System Architecture

## Overview

Approximate Query Engine (AQE) - FastAPI service for fast analytical queries using materialized samples and DuckDB native approximate functions.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (React + Vite)                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────────────────────┐  │
│  │ SQL Editor  │    │ Accuracy    │    │ Results + Compare View              │  │
│  │             │    │ 3-Step      │    │ (exact vs approx, speedup)          │  │
│  │ SELECT ...  │    │ Fast/Bal/   │    │                                     │  │
│  │             │    │ Precise     │    │ Refresh button → /refresh           │  │
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
│  │         ├── CREATE TABLE sales_sample_1pct  (1% uniform)                   ││
│  │         ├── CREATE TABLE sales_sample_10pct (10% uniform)                  ││
│  │         ├── CREATE TABLE sales_sample_20pct (20% uniform)                  ││
│  │         └── CREATE TABLE sales_sample_stratified (10% per region)          ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │ /query ENDPOINT                                                             ││
│  │                                                                             ││
│  │  Input: {sql: "SELECT region, COUNT(*) FROM sales GROUP BY region",        ││
│  │          accuracy: 0.925}  ← 0.875=1%, 0.925=10%, 0.975=20%               ││
│  │                                                                             ││
│  │  Step 1: AUTO-ROUTER (src/aqe/router.py)                                   ││
│  │          ├── Parse SQL (sqlglot) → table, columns, aggregations            ││
│  │          ├── Check DataProfile → row_count, skew                           ││
│  │          ├── IF COUNT(DISTINCT) → duckdb_approx                            ││
│  │          ├── IF PERCENTILE/MEDIAN → duckdb_quantile                        ││
│  │          ├── IF GROUP BY + materialized exists → materialized              ││
│  │          └── ELSE → duckdb_sample                                          ││
│  │                                                                             ││
│  │  Step 2: STRATEGY EXECUTION                                                ││
│  │          └── Route to strategy.execute(sql, config, db)                    ││
│  │                                                                             ││
│  │  Step 3: BUILD RESPONSE                                                    ││
│  │          └── QueryResponse {results, metadata: {strategy, time, accuracy}} ││
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
│  │  │ sales.parquet       │    │ Materialized Samples (31% overhead)     │ │    │
│  │  │ (500M rows, ~5GB)   │    │                                         │ │    │
│  │  │                     │    │ sales_sample_1pct   (5M rows, ~50MB)   │ │    │
│  │  │ user_id: BIGINT     │    │ sales_sample_10pct  (50M rows, ~500MB) │ │    │
│  │  │ region: VARCHAR     │    │ sales_sample_20pct  (100M rows, ~1GB)  │ │    │
│  │  │ amount: DECIMAL     │    │ sales_sample_stratified                │ │    │
│  │  │ date: TIMESTAMP     │    │   (10% per region, balanced)           │ │    │
│  │  │                     │    │                                         │ │    │
│  │  └─────────────────────┘    └─────────────────────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │ CACHE LAYER (DataProfiler)                                               │    │
│  │                                                                          │    │
│  │  IN-MEMORY: cache = {"sales": {row_count, columns, ...}}                 │    │
│  │  ON-DISK:   .cache/sales_profile.json (TTL: 20 min)                      │    │
│  │                                                                          │    │
│  │  ┌─────────────────────────────────────────────────────────────────┐     │    │
│  │  │ Cache Strategy                                                  │     │    │
│  │  │ 1. Check in-memory → return if hit                              │     │    │
│  │  │ 2. Check disk cache → load if not expired (20 min TTL)          │     │    │
│  │  │ 3. Profile table → save to disk + memory                        │     │    │
│  │  │                                                                 │     │    │
│  │  │ INVALIDATION: POST /refresh OR auto-expire after 20 min         │     │    │
│  │  └─────────────────────────────────────────────────────────────────┘     │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## API Endpoints

| Endpoint | Method | Description | Request | Response |
|----------|--------|-------------|---------|----------|
| `/health` | GET | Health check | - | `{status, data_loaded}` |
| `/query` | POST | Execute query with auto-routing | `{sql, accuracy?, mode?}` | `{results, metadata}` |
| `/compare` | POST | Exact vs approx with speedup | `{sql, sample_rate?}` | `{exact, approx, speedup}` |
| `/compare-strategies` | POST | Benchmark all strategies | `{sql, sample_rate?}` | `{exact, strategies[]}` |
| `/refresh` | POST | Invalidate cache + rebuild samples | - | `{status, dropped[], created[]}` |

## Strategy Routing

```
User Query
    ↓
IF COUNT(DISTINCT col) → duckdb_approx (DuckDB APPROX_COUNT_DISTINCT)
IF PERCENTILE/MEDIAN → duckdb_quantile (DuckDB APPROX_QUANTILE)
IF accuracy ≥ 0.95 + has 20pct → materialized (sales_sample_20pct)
IF accuracy ≥ 0.90 + has 10pct → materialized (sales_sample_10pct)
IF accuracy ≥ 0.85 + has 1pct → materialized (sales_sample_1pct)
IF GROUP BY + stratified exists → materialized (sales_sample_stratified)
ELSE → duckdb_sample (runtime sampling)
```

## File Structure

```
src/aqe/
├── main.py              # FastAPI app, endpoints, CORS
├── models.py            # Pydantic: QueryRequest, QueryResponse
├── profiler.py          # DataProfiler: stats + materialized samples
├── router.py            # AutoRouter: strategy selection
├── accuracy.py          # Accuracy → sample rate mapping
├── error.py             # Error estimation
└── strategies/
     ├── __init__.py
     ├── materialized.py    # Pre-computed sample tables
     ├── duckdb_approx.py   # APPROX_COUNT_DISTINCT
     ├── duckdb_quantile.py # APPROX_QUANTILE
     └── stratified.py      # Per-group sampling
```

## Performance Characteristics

| Strategy | Speedup | Error | Use Case |
|----------|---------|-------|----------|
| materialized | 10-150x | 0% | GROUP BY, simple aggregates |
| duckdb_approx | 680x | ~4% | COUNT DISTINCT |
| duckdb_quantile | 3.5x | ~0.2% | MEDIAN, percentiles |
| duckdb_sample | 10x | ~5% | Fallback |

## Accuracy Tiers

| Accuracy Target | Sample Used | Storage | Query Time |
|-----------------|-------------|---------|------------|
| 85-90% (Fast) | 1% (5M rows) | 50MB | ~1ms |
| 90-95% (Balanced) | 10% (50M rows) | 500MB | ~3ms |
| 96-99% (Precise) | 20% (100M rows) | 1GB | ~5ms |
