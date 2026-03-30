This file provides guidance to Claude Code (claude.ai/code) when working with the Approximate Query Engine (AQE) integration for the Saska Reasoner NL→SQL system.
Project Overview
Saska Reasoner + AQE - Natural Language to SQL (NL-SQL) Agent with Approximate Query Processing for high-speed analytical insights.
A CLI tool and HTTP API that lets users ask questions in plain English about order data, generates PostgreSQL queries using the existing Saska Reasoner pipeline, and transparently routes analytical queries through an Approximate Query Engine for 3x+ speedup with tunable accuracy.
Status: Integration phase - AQE layer beneath existing NL→SQL pipeline
Quick Reference
Table
Command	Purpose
uv run reasoner "average sales by region"	Run NL query with default 95% accuracy
uv run reasoner "average sales by region" --accuracy 0.90	Faster approximation (90% confidence)
uv run reasoner "average sales by region" --exact	Force exact execution
uv run api_server	Start FastAPI with AQE endpoints
curl "http://localhost:8000/query?q=average+sales+by+region&accuracy=0.90"	API with accuracy parameter
docker compose up -d	Start local PostgreSQL
./scripts/clone_db.sh	Clone AWS data to local DB
Architecture
System Design
plain
Copy

┌─────────────────────────────────────────────────────────────┐
│                         Clients                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │     CLI      │  │   FastAPI    │  │   External   │       │
│  │   (rich)     │  │   (uvicorn)  │  │   Tools      │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
└─────────┼─────────────────┼─────────────────┼─────────────────┘
          │                 │                 │
          └─────────────────┴─────────────────┘
                            │
                    ┌───────▼────────┐
                    │  SASKA REASONER │
                    │  (unchanged)    │
                    │  NL → SQL       │
                    └───────┬────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
  ┌─────▼─────┐     ┌───────▼───────┐   ┌──────▼──────┐
  │Decomposer │     │ SQL Generator │   │   Logger    │
  │(rule/LLM) │     │ (deterministic│   │(JSONL +    │
  └───────────┘     │   SQL gen)    │   │ in-memory)  │
                    └───────────────┘   └─────────────┘
                            │
                            ▼
              ┌─────────────────────────┐
              │    SQL OUTPUT (str)     │
              │  "SELECT ... FROM ..."  │
              └───────────┬─────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  🔥 AQE INTEGRATION LAYER (NEW)                              │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Query Analyzer (sqlglot)                            │   │
│  │  ├── Parse: aggregations, GROUP BY, table sizes      │   │
│  │  ├── Detect: COUNT DISTINCT → HyperLogLog            │   │
│  │  ├── Detect: GROUP BY → Stratified sampling          │   │
│  │  └── Decision: Route exact vs approximate            │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                  │
│           ┌───────────────┴───────────────┐                  │
│           ▼                               ▼                  │
│  ┌─────────────────┐            ┌─────────────────────┐        │
│  │  EXACT PATH     │            │  APPROXIMATE PATH   │        │
│  │  (passthrough)  │            │                     │        │
│  │                 │            │ ┌───────────────┐     │        │
│  │  → asyncpg      │            │ │ Sampling      │     │        │
│  │  → 100% accurate│            │ │ • Uniform     │     │        │
│  │  → Slower       │            │ │ • Stratified  │     │        │
│  │                 │            │ │ • Reservoir   │     │        │
│  └─────────────────┘            │ └───────────────┘     │        │
│                                 │ ┌───────────────┐       │        │
│                                 │ │ Sketches      │       │        │
│                                 │ │ • HyperLogLog │       │        │
│                                 │ │ • Count-Min   │       │        │
│                                 │ │ • T-Digest    │       │        │
│                                 │ └───────────────┘       │        │
│                                 └─────────────────────────┘        │
│                                              │                 │
│  ┌───────────────────────────────────────────┴─────────┐      │
│  │  Result Unifier (matches existing format)           │      │
│  │  ├── Same: columns, rows, row_count                 │      │
│  │  ├── Add: is_approximate flag                       │      │
│  │  ├── Add: confidence_intervals                      │      │
│  │  └── Add: accuracy_target metadata                  │      │
│  └─────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  RESPONSE ENHANCEMENT                                        │
│  ├── CLI: "⚡ Approximate (95% confidence)" badge           │
│  ├── API: approximation metadata in JSON                    │
│  └── UI: Error bars, accuracy slider, exact toggle        │
└─────────────────────────────────────────────────────────────┘

Key Files
Table
Path	Purpose
AQE Core
src/reasoner/aqe/integration.py	Main AQE wrapper, routing logic
src/reasoner/aqe/analyzer.py	SQL parsing, approximability detection
src/reasoner/aqe/sampling.py	Stratified, uniform, reservoir sampling
src/reasoner/aqe/sketches.py	HyperLogLog, T-Digest, Count-Min management
src/reasoner/aqe/confidence.py	Error bounds, confidence interval calculation
Integration Points
src/reasoner/aqe/wrapper.py	Drop-in replacement for execute_query
src/reasoner/api/routes.py	Modified endpoints with accuracy params
src/reasoner/__main__.py	CLI with --accuracy and --exact flags
Configuration
src/reasoner/aqe/config.py	AQEConfig dataclass, tuning parameters
AQE Configuration
Tunable Parameters
Table
Parameter	Default	Description
enabled	True	Master AQE on/off switch
default_accuracy	0.95	Default confidence level (95%)
min_table_size	100_000	Only approximate tables larger than this
sampling_rates	{0.80: 0.05, 0.90: 0.10, 0.95: 0.20, 0.99: 0.50}	Sample % by accuracy target
max_relative_error	0.05	Maximum acceptable 5% error
sketch_tables	["orders.functional_order_id"]	Pre-computed sketch columns
Accuracy vs Speed Trade-offs
Table
Accuracy Target	Sample Rate	Expected Speedup	Use Case
80%	5%	15-20x	Quick trends, dashboards
90%	10%	8-10x	Exploratory analysis
95%	20%	4-5x	Standard reporting
99%	50%	2x	Critical decisions
Approximation Techniques
1. Uniform Random Sampling
For: Simple aggregations (AVG, SUM, COUNT) without GROUP BY
Implementation: TABLESAMPLE SYSTEM (n) in PostgreSQL
Scaling: Multiply COUNT/SUM by 1/sample_rate
2. Stratified Sampling
For: GROUP BY queries (region, status, category)
Implementation: Sample within each group to preserve rare groups
Critical for: Your GROUP BY cr.region queries
3. HyperLogLog Sketches
For: COUNT DISTINCT on high-cardinality columns
Best for: functional_order_id (your business key)
Speedup: 100-1000x with ~2% error
Pre-compute: Build sketches during off-peak hours
4. T-Digest
For: Percentiles, medians, quantiles
Use case: "What's the 95th percentile order value?"
Query Routing Logic
Approximable Queries (Route to AQE)

    ✅ SELECT AVG(amount) FROM orders
    ✅ SELECT region, SUM(sales) FROM orders GROUP BY region
    ✅ SELECT COUNT(DISTINCT functional_order_id) FROM manufacturing
    ✅ SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY amount) FROM orders

Exact-Only Queries (Bypass AQE)

    ❌ SELECT * FROM orders LIMIT 10 (small result)
    ❌ SELECT ... ORDER BY date LIMIT 5 (needs exact sorting)
    ❌ SELECT ... WHERE id = 'specific-id' (point lookup)
    ❌ Any query with window functions ROW_NUMBER(), RANK()

Database Schema Considerations
Your 12 Tables - AQE Suitability
Table
Table	Rows (est.)	AQE Candidate	Best Technique
orders	~500K	✅ High	Stratified sampling
manufacturing	~400K	✅ High	Join-optimized sampling
cargo	~300K	✅ High	Uniform sampling
user_activities	~1M	✅ High	Time-based reservoir
customers	~10K	❌ Low	Exact (too small)
transporters	~50	❌ Low	Exact (too small)
product_items	~5K	❌ Low	Exact (too small)
Critical Business Rule (Preserved)
⚠️ AQE never changes join logic - functional_order_id joins remain exact.
API Endpoints (Modified)
GET /query
Enhanced with accuracy parameter.
Query Parameters:

    q (required): Natural language question
    accuracy (optional): 0.80-0.99, default 0.95
    exact (optional): Force exact execution
    request_id (optional): Custom tracing ID

Response (Enhanced):
JSON
Copy

{
  "success": true,
  "request_id": "abc123de",
  "question": "average sales by region last month",
  "sql": "SELECT cr.region, AVG(o.total_amount) ...",
  "results": {
    "columns": ["region", "avg_amount"],
    "rows": [{"region": "North", "avg_amount": 12500.50}],
    "row_count": 5
  },
  "approximation": {
    "enabled": true,
    "accuracy_target": 0.95,
    "confidence_intervals": {
      "avg_amount": [11875.47, 13125.53]
    },
    "execution_time_ms": 450.2,
    "speedup_vs_exact": 4.2,
    "sample_size": 45000,
    "technique": "stratified_sampling"
  },
  "timing": {
    "decompose_ms": 320.5,
    "generate_ms": 5.2,
    "aqe_route_ms": 2.1,
    "execute_ms": 450.2,
    "total_ms": 778.0
  }
}

GET /health
Response:
JSON
Copy

{
  "status": "ok",
  "version": "0.2.0",
  "aqe_enabled": true,
  "sketches_ready": ["orders.functional_order_id"]
}

CLI Usage
Basic Query (with AQE default 95%)
bash
Copy

$ uv run reasoner "average order value by region"
⚡ Approximate result (95% confidence) in 420ms
┌────────┬─────────────┐
│ region │ avg_amount  │
├────────┼─────────────┤
│ North  │ $12,500.50  │
│ South  │ $8,200.30   │
│ East   │ $15,100.80  │
│ West   │ $9,500.60   │
└────────┴─────────────┘
±5% confidence intervals shown. Use --exact for precise values.

Speed-Optimized (90% accuracy)
bash
Copy

$ uv run reasoner "total sales last quarter" --accuracy 0.90
⚡ Approximate result (90% confidence) in 180ms (8.5x faster)

Exact Mode
bash
Copy

$ uv run reasoner "total sales last quarter" --exact
🎯 Exact result in 1,530ms

Implementation Checklist
Phase 1: Core AQE (MVP)

    [ ] Create src/reasoner/aqe/ module structure
    [ ] Implement QueryAnalyzer with sqlglot parsing
    [ ] Implement UniformSampler using PostgreSQL TABLESAMPLE
    [ ] Implement StratifiedSampler for GROUP BY queries
    [ ] Create AQEWrapper as drop-in execute_query replacement
    [ ] Modify api/routes.py to accept accuracy parameter
    [ ] Add --accuracy and --exact flags to CLI
    [ ] Benchmark: Prove 3x speedup on orders table

Phase 2: Sketches (Brownie Points)

    [ ] Implement HyperLogLog for COUNT DISTINCT functional_order_id
    [ ] Implement T-Digest for percentile queries
    [ ] Create sketch pre-computation job (run nightly)
    [ ] Add sketch warmup to docker-compose.yml health checks

Phase 3: Advanced Features

    [ ] Query result caching with accuracy tiers
    [ ] Automatic accuracy selection based on query complexity
    [ ] Comparison UI in API response (exact vs approx side-by-side)
    [ ] Streaming AQE for real-time dashboards

Dependencies
Add to pyproject.toml:
toml
Copy

[project]
dependencies = [
    # ... existing Saska Reasoner deps ...
    
    # AQE Core
    "datasketch>=1.6.0",      # HyperLogLog, MinHash
    "tdigest>=0.5.2",         # T-Digest for percentiles
    "sqlglot>=20.0.0",        # SQL parsing (may already exist)
    
    # Sampling (via existing Polars/DuckDB)
    # "polars>=0.20.0",       # Already in Saska Reasoner
    
    # Confidence intervals
    "statsmodels>=0.14.0",    # Statistical validation
]

Safety & Constraints
Table
Constraint	Implementation
Read-only	AQE layer inherits existing validator blocks
Row limits	Existing LIMIT 100 preserved
Accuracy bounds	Hard floor at 80%, ceiling at 99%
Fallback	Any AQE error → automatic exact execution
Transparency	Every response includes is_approximate flag
Testing Strategy
Unit Tests

    tests/aqe/test_analyzer.py - Query classification
    tests/aqe/test_sampling.py - Sample correctness
    tests/aqe/test_confidence.py - Error bound validation

Integration Tests

    tests/aqe/test_integration.py - End-to-end with local DB
    Compare exact vs approximate results within error bounds

Benchmarks

    scripts/benchmark_aqe.py - Speedup measurements
    Target: 3x speedup on 95% accuracy, 10x on 90% accuracy

Common Query Patterns (AQE-Optimized)
sql
Copy

-- Stratified sampling for regional analysis
SELECT cr.region, AVG(o.total_amount), COUNT(*)
FROM orders o TABLESAMPLE SYSTEM (20)
JOIN customers c ON o.customer_id = c.id
JOIN customers_region cr ON c.id = cr.customer_id
WHERE o.order_date > CURRENT_DATE - INTERVAL '30 days'
GROUP BY cr.region;

-- HyperLogLog for distinct order count
-- (Uses pre-computed sketch, no table scan)
SELECT hll_cardinality(functional_order_id) 
FROM orders_hll_sketch;

-- T-Digest for percentile
SELECT tdigest_percentile(total_amount, 0.95)
FROM orders_tdigest_sketch;

Logging
AQE adds new log steps to your existing JSONL:
Table
Step	Description
AQE_ANALYZE_START	SQL parsing begins
AQE_ROUTING_DECISION	Exact vs approximate choice
AQE_TECHNIQUE_SELECTED	Uniform/Stratified/HyperLogLog/T-Digest
AQE_SAMPLE_SIZE	Rows sampled vs total
AQE_CONFIDENCE_CALC	Error bounds computed
AQE_FALLBACK	AQE failed, routing to exact
Notes for Future Claude Instances

    Preserve existing pipeline - AQE is a transparent layer, not a replacement
    Never modify NL→SQL logic - Your decomposer/generator stay untouched
    Use existing schema_registry - AQE reads table sizes from your metadata
    Maintain async/await - All AQE operations must be non-blocking
    PostgreSQL-specific - TABLESAMPLE syntax varies by DB (SYSTEM vs BERNOULLI)
    functional_order_id is key - High cardinality makes it perfect for HyperLogLog
    Stratified sampling critical - Your GROUP BY region/status queries need this
    Test with real data - Use scripts/clone_db.sh to populate local DB
    Benchmark against exact - Prove speedup before shipping
    Default to safe - When in doubt, route to exact execution

References

    docs/schema_context.py - Your 12-table schema for AQE optimization hints
    docs/er-diagram.md - Join paths for sampling strategy
    src/reasoner/core/executor.py - Existing executor to wrap
    src/reasoner/utils/schema_registry.py - Table metadata for size estimation