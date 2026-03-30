# Approximate Query Engine

FastAPI service for approximate analytical queries using DuckDB.

## Quick Start

```bash
# Generate dataset (10M rows)
uv run scripts/generate_data.py

# Run API
uv run uvicorn aqe.main:app --reload

# Test
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT COUNT(*) as cnt FROM sales", "mode": "exact"}'
```

## Endpoints

### POST /query
Execute a query in exact or approximate mode.

```json
{
  "sql": "SELECT region, COUNT(*), AVG(amount) FROM sales GROUP BY region",
  "mode": "approx",
  "sample_rate": 0.1
}
```

### POST /compare
Run both exact and approximate, return comparison with speedup.

## Performance

| Mode | Time | Speedup |
|------|------|---------|
| Exact | ~180ms | 1x |
| Approx (10%) | ~5ms | **36x** |

## Phase 1 Complete
- [x] Synthetic dataset (10M rows, skewed regions)
- [x] Exact query endpoint
- [x] Approximate sampling endpoint
- [x] Compare endpoint

## Phase 2 TODO
- [ ] Error estimation for approx queries
- [ ] Scale factor for COUNT(*) in approx mode
- [ ] Python sketch strategies (HLL, t-digest)
