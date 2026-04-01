"""FastAPI application for Approximate Query Engine."""
import time
from contextlib import asynccontextmanager

import duckdb
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aqe.models import QueryRequest, QueryResponse, QueryMetadata
from aqe.error import estimate_count_error, estimate_sum_error, estimate_avg_error
from aqe.strategies.duckdb_approx import DuckDBApproxStrategy
from aqe.strategies.duckdb_quantile import DuckDBQuantileStrategy
from aqe.strategies.stratified import StratifiedSamplingStrategy
from aqe.profiler import DataProfiler
from aqe.router import AutoRouter

# Global DuckDB connection
_db = None

# Global profiler and router
_profiler = None
_router = None

# Profiling status for progress tracking
_profiling_status = {
    "is_profiling": False,
    "table": None,
    "started_at": None,
    "progress": 0,  # 0-100
    "message": "",
}


def get_db():
    """Get or create DuckDB connection with sales data loaded."""
    global _db
    if _db is None:
        _db = duckdb.connect()
        _db.execute("CREATE VIEW sales AS SELECT * FROM 'data/sales.parquet'")
    return _db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database connection lifecycle."""
    global _profiler, _router, _profiling_status
    db = get_db()  # Initialize on startup

    # Initialize profiler and profile tables with progress tracking
    _profiler = DataProfiler()

    def update_progress(progress, message):
        _profiling_status["is_profiling"] = True
        _profiling_status["table"] = "sales"
        _profiling_status["progress"] = progress
        _profiling_status["message"] = message
        print(f"Profiling: {progress}% - {message}")

    _profiling_status["is_profiling"] = True
    _profiling_status["started_at"] = time.time()
    _profiler.profile_table(db, "sales", progress_callback=update_progress)
    _profiling_status["is_profiling"] = False
    _profiling_status["progress"] = 100
    _profiling_status["message"] = "Complete"

    # Create materialized samples for fast querying
    _profiler.create_materialized_samples(db, "sales")

    # Initialize router
    _router = AutoRouter(_profiler)

    yield
    global _db
    if _db:
        _db.close()
        _db = None


app = FastAPI(title="Approximate Query Engine", lifespan=lifespan)

# CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "data_loaded": _db is not None}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Execute SQL query (exact or approximate)."""
    db = get_db()

    start = time.perf_counter()

    # Auto-router: if accuracy specified, determine optimal strategy
    if req.accuracy is not None and _router is not None:
        routing = _router.route(req.sql, db, req.accuracy)
        strategy = routing["strategy"]
        config = routing.get("config", {})

        # Apply routing decisions
        req.strategy = strategy
        req.mode = "approx"  # Accuracy implies approximate mode
        if "sample_rate" in config:
            req.sample_rate = config["sample_rate"]
        if "hll_precision" in config:
            req.config = req.config or {}
            req.config["hll_precision"] = config["hll_precision"]

    if req.mode == "exact":
        result = db.execute(req.sql).fetchdf()
        records = result.to_dict("records")
        elapsed_ms = (time.perf_counter() - start) * 1000

        return QueryResponse(
            results=records,
            metadata=QueryMetadata(
                mode=req.mode,
                query_time_ms=round(elapsed_ms, 2),
                rows_returned=len(records),
                accuracy_requested=req.accuracy,
                accuracy_achieved=1.0,  # Exact queries have 100% accuracy
            ),
        )

    # Approximate mode - choose strategy
    strategy = req.strategy or "duckdb_sample"

    if strategy == "duckdb_approx":
        approx = DuckDBApproxStrategy()
        if not approx.supports(req.sql):
            return {"error": "duckdb_approx strategy requires COUNT(DISTINCT column)"}

        config = req.config or {}
        result = approx.execute(req.sql, config, db=db)

        # Calculate achieved accuracy from error percentage
        error_pct = result["metadata"]["estimated_error_pct"]
        achieved = 1.0 - (error_pct / 100)

        response = QueryResponse(
            results=result["results"],
            metadata=QueryMetadata(
                mode=req.mode,
                strategy=strategy,
                query_time_ms=result["metadata"]["query_time_ms"],
                rows_returned=len(result["results"]),
                sample_rate=None,
                error_estimate={"approx_count_distinct": result["metadata"]["estimated_error_pct"]},
                accuracy_requested=req.accuracy,
                accuracy_achieved=round(achieved, 3),
            ),
        )
        if cache_key:
            _cache_result(cache_key, response)
        return response

    elif strategy == "duckdb_quantile":
        dq = DuckDBQuantileStrategy()
        if not dq.supports(req.sql):
            return {"error": "duckdb_quantile strategy requires percentile/median query"}

        config = req.config or {}
        result = dq.execute(req.sql, config, db=db)

        # Calculate achieved accuracy from error percentage
        error_pct = result["metadata"]["estimated_error_pct"]
        achieved = 1.0 - (error_pct / 100)

        return QueryResponse(
            results=result["results"],
            metadata=QueryMetadata(
                mode=req.mode,
                strategy=strategy,
                query_time_ms=result["metadata"]["query_time_ms"],
                rows_returned=len(result["results"]),
                sample_rate=None,
                error_estimate={"quantiles": result["metadata"]["estimated_error_pct"]},
                accuracy_requested=req.accuracy,
                accuracy_achieved=round(achieved, 3),
            ),
        )

    elif strategy == "stratified":
        ss = StratifiedSamplingStrategy()
        if not ss.supports(req.sql):
            return {"error": "stratified strategy requires GROUP BY query"}

        config = req.config or {}
        config.setdefault("sample_rate", req.sample_rate)
        result = ss.execute(req.sql, config)

        # Estimate achieved accuracy from number of groups and samples
        groups = result["metadata"].get("groups", 10)
        # More groups with samples = better representation
        # Simplified: assume 95% confidence
        achieved = 0.95 if groups > 0 else 0.5

        return QueryResponse(
            results=result["results"],
            metadata=QueryMetadata(
                mode=req.mode,
                strategy=strategy,
                query_time_ms=result["metadata"]["query_time_ms"],
                rows_returned=len(result["results"]),
                sample_rate=result["metadata"].get("sample_rate"),
                error_estimate={"per_group": result["metadata"].get("groups")},
                accuracy_requested=req.accuracy,
                accuracy_achieved=round(achieved, 3),
            ),
        )

    elif strategy == "materialized":
        from aqe.strategies.materialized import MaterializedSampleStrategy

        # Use config from router (has sample_table), fallback to req.config
        effective_config = {**(req.config or {}), **config}
        sample_table = effective_config.get("sample_table", "sales_sample_10pct")
        ms = MaterializedSampleStrategy(sample_table)

        if not ms.supports(req.sql):
            # Fall back to runtime sampling
            # Re-run with duckdb_sample strategy
            req.strategy = "duckdb_sample"
            return await query(req)

        # Pass the database connection to use existing tables
        result = ms.execute(req.sql, config, db=db)

        return QueryResponse(
            results=result["results"],
            metadata=QueryMetadata(
                mode=req.mode,
                strategy=strategy,
                query_time_ms=result["metadata"]["query_time_ms"],
                rows_returned=len(result["results"]),
                sample_rate=None,  # Materialized samples have fixed size
                error_estimate={"materialized": True, "sample_table": sample_table},
                accuracy_requested=req.accuracy,
                accuracy_achieved=0.95,  # Materialized samples are consistent
            ),
        )

    else:
        # Default: duckdb_sample
        sql = add_sample_clause(req.sql, req.sample_rate)
        result = db.execute(sql).fetchdf()
        elapsed_ms = (time.perf_counter() - start) * 1000

        records = result.to_dict("records")

        # Calculate error estimates for COUNT columns
        error_estimate = calculate_sampling_errors(db, req.sql, req.sample_rate, records)

        return QueryResponse(
            results=records,
            metadata=QueryMetadata(
                mode=req.mode,
                strategy="duckdb_sample",
                query_time_ms=round(elapsed_ms, 2),
                rows_returned=len(records),
                sample_rate=req.sample_rate,
                error_estimate=error_estimate,
                accuracy_requested=req.accuracy,
                accuracy_achieved=round(1.0 - (0.05 if req.sample_rate else 0.0), 3),
            ),
        )


def calculate_sampling_errors(db, sql: str, sample_rate: float, records: list) -> dict:
    """Calculate error estimates for sampled query results."""
    import re

    errors = {}

    # Get population size
    table_match = re.search(r"FROM\s+(\w+)", sql, re.IGNORECASE)
    if not table_match:
        return errors

    table = table_match.group(1)
    pop_result = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    population_size = pop_result[0]

    # Find COUNT(*) columns and estimate errors
    count_matches = re.findall(r"COUNT\s*\(\s*\*\s*\)(?:\s+AS\s+(\w+))?", sql, re.IGNORECASE)
    for i, alias in enumerate(count_matches):
        alias = alias or f"count_{i}"
        for record in records:
            # Find the count column in record
            for key, value in record.items():
                if isinstance(value, int) and value > 0:
                    # This is likely a count column
                    error = estimate_count_error(value, sample_rate)
                    errors[key] = error
                    break

    return errors


def add_sample_clause(sql: str, sample_rate: float) -> str:
    """Add USING SAMPLE clause to SELECT query using subquery approach.

    DuckDB requires USING SAMPLE in a subquery when using GROUP BY, ORDER BY, etc.
    """
    import re

    sample_percent = int(sample_rate * 100)

    # Pattern to match FROM table_name followed by space or keyword
    reserved = ['WHERE', 'GROUP', 'ORDER', 'LIMIT', 'UNION', 'JOIN', 'HAVING', 'ON']
    reserved_pattern = '|'.join(reserved)

    pattern = rf"FROM\s+(\w+)\b(?:\s+(?!{reserved_pattern}\b)(\w+))?"
    match = re.search(pattern, sql, flags=re.IGNORECASE)

    if not match:
        return sql

    table_name = match.group(1)
    alias = match.group(2)
    alias = alias or table_name

    replacement = rf"FROM (SELECT * FROM {table_name} USING SAMPLE {sample_percent}%) AS {alias}"

    return sql[:match.start()] + replacement + sql[match.end():]


@app.post("/compare")
async def compare(req: QueryRequest):
    """Run exact and approximate queries, compare results.

    Uses accuracy-based routing to select optimal strategy (materialized samples).
    """
    # Run exact
    exact_req = QueryRequest(sql=req.sql, mode="exact")
    exact_result = await query(exact_req)

    # Run approx with accuracy-based routing (triggers materialized samples)
    # Default to 0.95 accuracy which selects 10% materialized sample
    accuracy = req.accuracy or 0.95
    approx_req = QueryRequest(
        sql=req.sql, mode="approx", accuracy=accuracy
    )
    approx_result = await query(approx_req)

    # Calculate speedup
    speedup = exact_result.metadata.query_time_ms / approx_result.metadata.query_time_ms

    return {
        "exact": {
            "time_ms": exact_result.metadata.query_time_ms,
            "results": exact_result.results,
        },
        "approx": {
            "time_ms": approx_result.metadata.query_time_ms,
            "strategy": approx_result.metadata.strategy,
            "sample_rate": approx_result.metadata.sample_rate,
            "accuracy_requested": approx_result.metadata.accuracy_requested,
            "accuracy_achieved": approx_result.metadata.accuracy_achieved,
            "results": approx_result.results,
            "error_estimate": approx_result.metadata.error_estimate,
        },
        "speedup": round(speedup, 2),
    }


@app.get("/profiling-status")
async def profiling_status():
    """Get current profiling status for progress tracking."""
    global _profiling_status
    return {
        "is_profiling": _profiling_status["is_profiling"],
        "table": _profiling_status["table"],
        "progress": _profiling_status["progress"],
        "message": _profiling_status["message"],
    }


@app.post("/refresh")
async def refresh():
    """Refresh profiler cache and re-profile tables with progress tracking."""
    import threading
    global _profiler, _router, _profiling_status

    def do_refresh():
        db = get_db()

        def update_progress(progress, message):
            _profiling_status["is_profiling"] = True
            _profiling_status["table"] = "sales"
            _profiling_status["progress"] = progress
            _profiling_status["message"] = message
            print(f"Profiling: {progress}% - {message}")

        # Clear cache
        if _profiler:
            _profiler.invalidate_cache("sales")

        # Re-profile with progress
        _profiling_status["started_at"] = time.time()
        profile = _profiler.profile_table(db, "sales", progress_callback=update_progress)

        # Recreate materialized samples
        _profiler.create_materialized_samples(db, "sales")

        # Update router
        global _router
        _router = AutoRouter(_profiler)

        _profiling_status["is_profiling"] = False
        _profiling_status["progress"] = 100
        _profiling_status["message"] = "Complete"

    # Start profiling in background thread
    if not _profiling_status["is_profiling"]:
        thread = threading.Thread(target=do_refresh)
        thread.start()

    return {
        "status": "started",
        "message": "Profiling started",
        "table": "sales",
    }


@app.post("/compare-strategies")
async def compare_strategies(req: QueryRequest):
    """Compare all available strategies for a query."""
    strategies = []

    # Test each strategy
    for strategy_name in ["duckdb_sample", "stratified", "duckdb_approx", "duckdb_quantile", "materialized"]:
        try:
            test_req = QueryRequest(
                sql=req.sql,
                mode="approx",
                sample_rate=req.sample_rate,
                strategy=strategy_name,
            )
            result = await query(test_req)

            if hasattr(result, 'metadata'):
                strategies.append({
                    "name": strategy_name,
                    "supported": True,
                    "time_ms": result.metadata.query_time_ms,
                    "error_estimate": result.metadata.error_estimate,
                    "results": result.results,
                })
            else:
                # Strategy returned an error dict
                strategies.append({
                    "name": strategy_name,
                    "supported": False,
                    "error": result.get("error", "Unknown error"),
                })
        except Exception as e:
            strategies.append({
                "name": strategy_name,
                "supported": False,
                "error": str(e),
            })

    # Also run exact for comparison
    exact_req = QueryRequest(sql=req.sql, mode="exact")
    exact_result = await query(exact_req)

    return {
        "exact": {
            "time_ms": exact_result.metadata.query_time_ms,
            "results": exact_result.results,
        },
        "strategies": strategies,
    }


@app.post("/refresh")
async def refresh_samples():
    """Rebuild materialized samples after data changes.

    Drops existing sample tables and recreates them from the
    current source data.

    Returns:
        Status message with list of recreated samples
    """
    db = get_db()

    dropped = []
    created = []

    # Drop existing samples (all types)
    for sample_name in ["sales_sample_1pct", "sales_sample_10pct", "sales_sample_20pct", "sales_sample_stratified"]:
        try:
            db.execute(f"DROP TABLE IF EXISTS {sample_name}")
            dropped.append(sample_name)
        except Exception as e:
            print(f"Warning: Could not drop {sample_name}: {e}")

    # Clear profiler's materialized sample tracking AND file cache
    global _profiler
    if _profiler:
        _profiler.materialized_samples = {}
        _profiler.invalidate_cache("sales")

    # Recreate samples
    if _profiler:
        _profiler.create_materialized_samples(db, "sales")
        created = _profiler.materialized_samples.get("sales", [])

    return {
        "status": "ok",
        "dropped": dropped,
        "created": created,
        "message": f"Refreshed {len(created)} materialized sample tables"
    }
