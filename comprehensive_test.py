#!/usr/bin/env python3
"""Comprehensive AQE test suite with detailed logging and visualization."""

import subprocess
import time
import json
import requests
import statistics
from datetime import datetime
from pathlib import Path
import sys

# Configuration
SERVER = "http://localhost:8000"
LOG_DIR = Path("logs")
RESULTS_FILE = LOG_DIR / f"comprehensive_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
REPORT_FILE = LOG_DIR / f"comprehensive_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

# Ensure log directory exists
LOG_DIR.mkdir(exist_ok=True)

# Test queries organized by category
TEST_QUERIES = {
    "simple_aggregates": [
        {
            "name": "Simple COUNT(*)",
            "sql": "SELECT COUNT(*) as cnt FROM sales",
            "description": "Basic count - best case for materialized samples"
        },
        {
            "name": "AVG amount",
            "sql": "SELECT AVG(amount) as avg_amount FROM sales",
            "description": "Average calculation"
        },
        {
            "name": "SUM amount",
            "sql": "SELECT SUM(amount) as total_revenue FROM sales",
            "description": "Sum aggregation"
        },
        {
            "name": "Multiple aggregates",
            "sql": "SELECT COUNT(*), AVG(amount), SUM(amount), MIN(amount), MAX(amount) FROM sales",
            "description": "Multiple aggregations in one query"
        }
    ],
    "group_by": [
        {
            "name": "GROUP BY region - COUNT",
            "sql": "SELECT region, COUNT(*) as cnt FROM sales GROUP BY region",
            "description": "Low cardinality GROUP BY - tests stratified sampling"
        },
        {
            "name": "GROUP BY region - full stats",
            "sql": "SELECT region, COUNT(*), AVG(amount), SUM(amount) FROM sales GROUP BY region",
            "description": "GROUP BY with multiple aggregations"
        }
    ],
    "distinct": [
        {
            "name": "COUNT DISTINCT user_id",
            "sql": "SELECT COUNT(DISTINCT user_id) as distinct_users FROM sales",
            "description": "HyperLogLog test - approx count distinct"
        }
    ],
    "quantiles": [
        {
            "name": "MEDIAN",
            "sql": "SELECT MEDIAN(amount) as median_amount FROM sales",
            "description": "Median calculation using approx quantile"
        },
        {
            "name": "95th percentile",
            "sql": "SELECT quantile_cont(0.95) WITHIN GROUP (ORDER BY amount) as p95 FROM sales",
            "description": "95th percentile - approx quantile"
        }
    ],
    "filtered": [
        {
            "name": "WHERE amount > 500",
            "sql": "SELECT COUNT(*) FROM sales WHERE amount > 500",
            "description": "Filtered count - may not use materialized"
        },
        {
            "name": "WHERE region = 'US'",
            "sql": "SELECT COUNT(*), AVG(amount) FROM sales WHERE region = 'US'",
            "description": "Single region filter"
        }
    ]
}

ACCURACY_LEVELS = [0.85, 0.90, 0.95, 0.99]
STRATEGIES = ["duckdb_sample", "stratified", "duckdb_approx", "duckdb_quantile", "materialized"]

class Logger:
    """Simple logger that writes to both console and file."""
    def __init__(self, log_file):
        self.log_file = log_file
        self.log_file.write(f"# AQE Comprehensive Test Suite\n")
        self.log_file.write(f"**Started:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime('%H:%M:%S')
        line = f"[{timestamp}] [{level}] {message}\n"
        print(line, end='')
        self.log_file.write(line)
        self.log_file.flush()

    def section(self, title):
        self.log("")
        self.log("=" * 70)
        self.log(f"  {title}")
        self.log("=" * 70)
        self.log("")

def check_server():
    """Check if server is running."""
    try:
        resp = requests.get(f"{SERVER}/health", timeout=5)
        return resp.status_code == 200
    except:
        return False

def wait_for_server(timeout=60):
    """Wait for server to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        if check_server():
            return True
        time.sleep(1)
    return False

def run_query(sql, mode="exact", accuracy=None, strategy=None, runs=1, warmup=0):
    """Run query and collect detailed metrics."""
    # Warmup runs
    for _ in range(warmup):
        try:
            payload = {"sql": sql, "mode": mode}
            if accuracy:
                payload["accuracy"] = accuracy
            if strategy:
                payload["strategy"] = strategy
            requests.post(f"{SERVER}/query", json=payload, timeout=60)
        except:
            pass

    # Actual runs
    times = []
    results = []
    errors = []

    for i in range(runs):
        payload = {"sql": sql, "mode": mode}
        if accuracy:
            payload["accuracy"] = accuracy
        if strategy:
            payload["strategy"] = strategy

        try:
            start = time.perf_counter()
            resp = requests.post(f"{SERVER}/query", json=payload, timeout=120)
            elapsed = (time.perf_counter() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                times.append(data["metadata"]["query_time_ms"])
                results.append(data)
            else:
                errors.append(f"HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            errors.append(str(e))

    if not times:
        return None, errors[0] if errors else "Unknown error"

    return {
        "times": times,
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "stddev": statistics.stdev(times) if len(times) > 1 else 0,
        "result": results[0] if results else None,
        "metadata": results[0]["metadata"] if results else None
    }, None

def run_compare_endpoint(sql):
    """Use the /compare endpoint for exact vs approx."""
    try:
        resp = requests.post(
            f"{SERVER}/compare",
            json={"sql": sql, "mode": "approx", "sample_rate": 0.1},
            timeout=120
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        return {"error": str(e)}

def run_compare_strategies_endpoint(sql):
    """Use the /compare-strategies endpoint."""
    try:
        resp = requests.post(
            f"{SERVER}/compare-strategies",
            json={"sql": sql, "sample_rate": 0.1},
            timeout=120
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        return {"error": str(e)}

def format_time(ms):
    """Format milliseconds nicely."""
    if ms < 1:
        return f"{ms:.2f}ms"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"

def main():
    # Initialize logging
    log_path = LOG_DIR / f"test_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logger = Logger(open(log_path, 'w'))

    logger.section("AQE COMPREHENSIVE TEST SUITE")
    logger.log(f"Results will be saved to: {RESULTS_FILE}")
    logger.log(f"Report will be saved to: {REPORT_FILE}")

    # Check server
    logger.section("SERVER CHECK")
    if not check_server():
        logger.log("Server not running. Please start with:")
        logger.log("  uv run uvicorn aqe.main:app --reload")
        logger.log("")
        logger.log("Attempting to start automatically...")

        # Try to start server
        proc = subprocess.Popen(
            ["uv", "run", "uvicorn", "aqe.main:app", "--reload"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="/Users/user/dev/saska/approx-query-engine"
        )

        logger.log("Waiting for server to start...")
        if not wait_for_server(timeout=60):
            logger.log("ERROR: Server failed to start", "ERROR")
            return 1
        logger.log("Server started successfully!")
    else:
        logger.log("Server is already running")

    all_results = {
        "timestamp": datetime.now().isoformat(),
        "server": SERVER,
        "tests": {}
    }

    # TEST 1: Cold vs Warm Cache
    logger.section("TEST 1: Cold vs Warm Cache Comparison")

    test_query = "SELECT COUNT(*) FROM sales"
    logger.log(f"Query: {test_query}")
    logger.log("Running cold cache test (first query after startup)...")

    cold_result, err = run_query(test_query, accuracy=0.95, runs=1)
    if err:
        logger.log(f"Error: {err}", "ERROR")
    else:
        logger.log(f"  Cold query time: {format_time(cold_result['mean'])}")
        logger.log(f"  Strategy used: {cold_result['metadata'].get('strategy', 'unknown')}")

        # Warm cache - run 5 times
        logger.log("Running warm cache test (5 runs)...")
        warm_result, err = run_query(test_query, accuracy=0.95, runs=5)
        if err:
            logger.log(f"Error: {err}", "ERROR")
        else:
            logger.log(f"  Mean: {format_time(warm_result['mean'])}")
            logger.log(f"  Median: {format_time(warm_result['median'])}")
            logger.log(f"  Min: {format_time(warm_result['min'])}")
            logger.log(f"  Max: {format_time(warm_result['max'])}")
            logger.log(f"  StdDev: {format_time(warm_result['stddev'])}")

            all_results["tests"]["cache_comparison"] = {
                "query": test_query,
                "cold_time_ms": cold_result['mean'],
                "warm_times_ms": warm_result['times'],
                "warm_mean_ms": warm_result['mean'],
                "warm_median_ms": warm_result['median'],
                "strategy": warm_result['metadata'].get('strategy')
            }

    # TEST 2: Exact vs Approximate for each query type
    logger.section("TEST 2: Exact vs Approximate Comparison")

    comparison_results = []
    for category, queries in TEST_QUERIES.items():
        logger.log(f"\n{category.upper().replace('_', ' ')}:")
        for query in queries[:2]:  # First 2 from each category
            logger.log(f"  {query['name']}")
            logger.log(f"    SQL: {query['sql']}")

            result = run_compare_endpoint(query['sql'])
            if result and 'error' not in result:
                exact_time = result['exact']['time_ms']
                approx_time = result['approx']['time_ms']
                speedup = result['speedup']
                strategy = result['approx'].get('strategy', 'unknown')

                logger.log(f"    Exact:  {format_time(exact_time)}")
                logger.log(f"    Approx: {format_time(approx_time)} (strategy: {strategy})")
                logger.log(f"    Speedup: {speedup}x")

                comparison_results.append({
                    "category": category,
                    "name": query['name'],
                    "sql": query['sql'],
                    "exact_time_ms": exact_time,
                    "approx_time_ms": approx_time,
                    "speedup": speedup,
                    "strategy": strategy
                })
            else:
                logger.log(f"    ERROR: {result.get('error', 'Unknown error')}", "ERROR")

    all_results["tests"]["exact_vs_approx"] = comparison_results

    # TEST 3: Accuracy Levels
    logger.section("TEST 3: Accuracy-Based Sample Selection")

    accuracy_results = []
    test_sql = "SELECT COUNT(*) FROM sales"
    logger.log(f"Query: {test_sql}")
    logger.log(f"\n{'Accuracy':<12} {'Time':<12} {'Strategy':<20} {'Sample Rate'}")
    logger.log("-" * 60)

    for acc in ACCURACY_LEVELS:
        result, err = run_query(test_sql, accuracy=acc, runs=3, warmup=1)
        if err:
            logger.log(f"{acc:<12.2f} ERROR: {err}")
        else:
            strategy = result['metadata'].get('strategy', 'unknown')
            sample_rate = result['metadata'].get('sample_rate')
            sample_str = f"{sample_rate*100:.0f}%" if sample_rate else "N/A"

            logger.log(f"{acc:<12.2f} {format_time(result['mean']):<12} {strategy:<20} {sample_str}")

            accuracy_results.append({
                "accuracy_requested": acc,
                "mean_time_ms": result['mean'],
                "strategy": strategy,
                "sample_rate": sample_rate,
                "times": result['times']
            })

    all_results["tests"]["accuracy_levels"] = accuracy_results

    # TEST 4: Strategy Comparison
    logger.section("TEST 4: All Strategies Comparison")

    strategy_test_sql = "SELECT COUNT(*) FROM sales"
    logger.log(f"Query: {strategy_test_sql}")
    logger.log("Comparing all available strategies...")

    strategies_result = run_compare_strategies_endpoint(strategy_test_sql)
    if strategies_result and 'error' not in strategies_result:
        exact_time = strategies_result['exact']['time_ms']
        logger.log(f"\n{'Strategy':<20} {'Supported':<12} {'Time':<12} {'Speedup':<10}")
        logger.log("-" * 60)
        logger.log(f"{'EXACT (baseline)':<20} {'yes':<12} {format_time(exact_time):<12} {'1.0x':<10}")

        strategy_comparison = []
        for strat in strategies_result['strategies']:
            name = strat['name']
            supported = strat['supported']

            if supported:
                time_ms = strat['time_ms']
                speedup = exact_time / time_ms if time_ms > 0 else 0
                logger.log(f"{name:<20} {'yes':<12} {format_time(time_ms):<12} {speedup:.1f}x")

                strategy_comparison.append({
                    "name": name,
                    "supported": True,
                    "time_ms": time_ms,
                    "speedup": speedup
                })
            else:
                logger.log(f"{name:<20} {'no':<12} {'-':<12} {'-':<10}")
                strategy_comparison.append({
                    "name": name,
                    "supported": False,
                    "error": strat.get('error', 'not supported')
                })

        all_results["tests"]["strategy_comparison"] = strategy_comparison

    # TEST 5: GROUP BY with Stratified (detailed)
    logger.section("TEST 5: GROUP BY - Stratified Sampling Detail")

    group_sql = "SELECT region, COUNT(*) as cnt FROM sales GROUP BY region"
    logger.log(f"Query: {group_sql}")

    # Exact
    logger.log("Running EXACT...")
    exact_result, err = run_query(group_sql, mode="exact", runs=3)
    if err:
        logger.log(f"  Error: {err}", "ERROR")
    else:
        logger.log(f"  Time: {format_time(exact_result['mean'])}")
        logger.log(f"  Results: {exact_result['result']['results']}")

    # Approx with accuracy (should trigger stratified or materialized)
    logger.log("Running APPROX with accuracy=0.95...")
    approx_result, err = run_query(group_sql, accuracy=0.95, runs=3)
    if err:
        logger.log(f"  Error: {err}", "ERROR")
    else:
        logger.log(f"  Time: {format_time(approx_result['mean'])}")
        logger.log(f"  Strategy: {approx_result['metadata'].get('strategy')}")
        logger.log(f"  Results: {approx_result['result']['results']}")

        if exact_result and approx_result:
            speedup = exact_result['mean'] / approx_result['mean']
            logger.log(f"  Speedup: {speedup:.1f}x")

            all_results["tests"]["group_by_detail"] = {
                "sql": group_sql,
                "exact_time_ms": exact_result['mean'],
                "approx_time_ms": approx_result['mean'],
                "speedup": speedup,
                "exact_results": exact_result['result']['results'],
                "approx_results": approx_result['result']['results'],
                "strategy": approx_result['metadata'].get('strategy')
            }

    # Save results to JSON
    logger.section("SAVING RESULTS")
    with open(RESULTS_FILE, 'w') as f:
        json.dump(all_results, f, indent=2)
    logger.log(f"Results saved to: {RESULTS_FILE}")

    # Generate Markdown Report
    generate_report(all_results, REPORT_FILE, logger)

    logger.section("TEST SUITE COMPLETE")
    logger.log(f"JSON results: {RESULTS_FILE}")
    logger.log(f"Markdown report: {REPORT_FILE}")

    return 0

def generate_report(results, report_path, logger):
    """Generate a visual markdown report."""
    logger.log("Generating markdown report...")

    with open(report_path, 'w') as f:
        f.write("# AQE Comprehensive Test Results\n\n")
        f.write(f"**Date:** {results['timestamp']}\n")
        f.write(f"**Server:** {results['server']}\n\n")

        # Summary table
        f.write("## Summary\n\n")

        # Cache comparison
        if 'cache_comparison' in results['tests']:
            cache = results['tests']['cache_comparison']
            f.write("### Cold vs Warm Cache\n\n")
            f.write(f"- **Cold query:** {cache['cold_time_ms']:.2f}ms\n")
            f.write(f"- **Warm query (median):** {cache['warm_median_ms']:.2f}ms\n")
            f.write(f"- **Speedup from caching:** {cache['cold_time_ms'] / cache['warm_median_ms']:.1f}x\n\n")

        # Exact vs Approx
        if 'exact_vs_approx' in results['tests']:
            f.write("### Exact vs Approximate Performance\n\n")
            f.write("| Query | Exact | Approx | Speedup | Strategy |\n")
            f.write("|-------|-------|--------|---------|----------|\n")
            for r in results['tests']['exact_vs_approx']:
                f.write(f"| {r['name'][:30]} | {r['exact_time_ms']:.1f}ms | {r['approx_time_ms']:.1f}ms | {r['speedup']:.1f}x | {r['strategy']} |\n")
            f.write("\n")

        # Accuracy levels
        if 'accuracy_levels' in results['tests']:
            f.write("### Accuracy Level Comparison\n\n")
            f.write("| Target Accuracy | Mean Time | Strategy | Sample Rate |\n")
            f.write("|-----------------|-----------|----------|-------------|\n")
            for r in results['tests']['accuracy_levels']:
                sample = f"{r['sample_rate']*100:.0f}%" if r['sample_rate'] else "N/A"
                f.write(f"| {r['accuracy_requested']:.0%} | {r['mean_time_ms']:.2f}ms | {r['strategy']} | {sample} |\n")
            f.write("\n")

        # Strategy comparison
        if 'strategy_comparison' in results['tests']:
            f.write("### Strategy Comparison (COUNT(*))\n\n")
            f.write("| Strategy | Supported | Time | Speedup |\n")
            f.write("|----------|-----------|------|---------|\n")
            for r in results['tests']['strategy_comparison']:
                if r['supported']:
                    f.write(f"| {r['name']} | Yes | {r['time_ms']:.2f}ms | {r['speedup']:.1f}x |\n")
                else:
                    f.write(f"| {r['name']} | No | - | - |\n")
            f.write("\n")

        # GROUP BY detail
        if 'group_by_detail' in results['tests']:
            gb = results['tests']['group_by_detail']
            f.write("### GROUP BY Detail\n\n")
            f.write(f"**Query:** `{gb['sql']}`\n\n")
            f.write(f"- **Exact time:** {gb['exact_time_ms']:.2f}ms\n")
            f.write(f"- **Approx time:** {gb['approx_time_ms']:.2f}ms\n")
            f.write(f"- **Speedup:** {gb['speedup']:.1f}x\n")
            f.write(f"- **Strategy:** {gb['strategy']}\n\n")

            f.write("**Exact Results:**\n")
            f.write("```json\n")
            f.write(json.dumps(gb['exact_results'], indent=2))
            f.write("\n```\n\n")

            f.write("**Approximate Results:**\n")
            f.write("```json\n")
            f.write(json.dumps(gb['approx_results'], indent=2))
            f.write("\n```\n\n")

        # Raw JSON
        f.write("## Raw Results (JSON)\n\n")
        f.write("```json\n")
        f.write(json.dumps(results, indent=2))
        f.write("\n```\n")

    logger.log(f"Report saved to: {report_path}")

if __name__ == "__main__":
    sys.exit(main())
