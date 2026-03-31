#!/usr/bin/env python3
"""Benchmark script to compare exact vs approximate query performance."""

import requests
import json
import time
import statistics
from datetime import datetime

SERVER = "http://localhost:8000"
QUERIES = [
    {
        "name": "Simple COUNT",
        "sql": "SELECT COUNT(*) as cnt FROM sales",
        "description": "Basic aggregation - best case for sampling"
    },
    {
        "name": "GROUP BY region",
        "sql": "SELECT region, COUNT(*) as cnt FROM sales GROUP BY region",
        "description": "Low cardinality GROUP BY"
    },
    {
        "name": "GROUP BY region with AVG",
        "sql": "SELECT region, COUNT(*), AVG(amount), SUM(amount) FROM sales GROUP BY region",
        "description": "Multiple aggregations per group"
    },
    {
        "name": "COUNT DISTINCT",
        "sql": "SELECT COUNT(DISTINCT user_id) as distinct_users FROM sales",
        "description": "HyperLogLog optimized"
    },
    {
        "name": "MEDIAN",
        "sql": "SELECT MEDIAN(amount) as median_amount FROM sales",
        "description": "Quantile approximation"
    },
    {
        "name": "PERCENTILE",
        "sql": "SELECT quantile_cont(0.95) WITHIN GROUP (ORDER BY amount) as p95 FROM sales",
        "description": "95th percentile"
    },
    {
        "name": "Filtered query",
        "sql": "SELECT region, COUNT(*) FROM sales WHERE amount > 500 GROUP BY region",
        "description": "WHERE clause + GROUP BY"
    },
]

ACCURACY_LEVELS = [0.85, 0.90, 0.95, 0.99]

def check_server():
    """Check if server is running."""
    try:
        resp = requests.get(f"{SERVER}/health", timeout=5)
        return resp.status_code == 200
    except:
        return False

def run_query(sql, mode="exact", accuracy=None, strategy=None, runs=3):
    """Run query multiple times and return stats."""
    times = []
    result = None

    for i in range(runs):
        payload = {"sql": sql, "mode": mode}
        if accuracy:
            payload["accuracy"] = accuracy
        if strategy:
            payload["strategy"] = strategy

        start = time.time()
        resp = requests.post(f"{SERVER}/query", json=payload, timeout=120)
        elapsed = (time.time() - start) * 1000

        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}: {resp.text}"

        result = resp.json()
        # Use server-reported time for consistency
        times.append(result["metadata"]["query_time_ms"])

    return {
        "times": times,
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "min": min(times),
        "max": max(times),
        "result": result
    }, None

def run_compare(sql, runs=3):
    """Run exact vs approx comparison."""
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
        print(f"  Error: {e}")
        return None

def run_compare_strategies(sql):
    """Run all strategies comparison."""
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
        print(f"  Error: {e}")
        return None

def format_time(ms):
    """Format milliseconds nicely."""
    if ms < 1:
        return f"{ms:.3f}ms"
    elif ms < 1000:
        return f"{ms:.1f}ms"
    else:
        return f"{ms/1000:.2f}s"

def print_header(title):
    """Print section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

def print_results_table(results):
    """Print results in a formatted table."""
    print(f"\n{'Query':<25} {'Mode':<15} {'Time':<12} {'Speedup':<10} {'Strategy'}")
    print("-" * 80)
    for r in results:
        print(f"{r['query']:<25} {r['mode']:<15} {r['time']:<12} {r.get('speedup', '-'):<10} {r.get('strategy', '-')}")

def main():
    print("="*70)
    print("  AQE Benchmark Suite")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    # Check server
    print("\nChecking server...")
    if not check_server():
        print("  ERROR: Server not running at", SERVER)
        print("  Start with: uv run uvicorn aqe.main:app --reload")
        return
    print("  Server is up!")

    all_results = []

    # Test 1: Exact vs Approx for each query
    print_header("TEST 1: Exact vs Approximate Comparison")

    for query in QUERIES[:5]:  # First 5 queries only for speed
        print(f"\n{query['name']}")
        print(f"  SQL: {query['sql']}")
        print(f"  {query['description']}")

        result = run_compare(query['sql'])
        if not result:
            print("  FAILED")
            continue

        exact_time = result['exact']['time_ms']
        approx_time = result['approx']['time_ms']
        speedup = result['speedup']
        strategy = result['approx'].get('strategy', 'unknown')

        print(f"  Exact:  {format_time(exact_time)}")
        print(f"  Approx: {format_time(approx_time)} (strategy: {strategy})")
        print(f"  Speedup: {speedup}x")

        all_results.append({
            'query': query['name'],
            'mode': 'exact',
            'time': format_time(exact_time),
            'speedup': '-',
            'strategy': '-'
        })
        all_results.append({
            'query': query['name'],
            'mode': 'approx',
            'time': format_time(approx_time),
            'speedup': f"{speedup}x",
            'strategy': strategy
        })

    # Test 2: Accuracy levels
    print_header("TEST 2: Accuracy-Based Sample Selection")

    test_sql = "SELECT COUNT(*) FROM sales"
    print(f"\nQuery: {test_sql}")
    print(f"\n{'Accuracy':<10} {'Target':<15} {'Time':<12} {'Sample'}")
    print("-" * 50)

    for acc in ACCURACY_LEVELS:
        stats, err = run_query(test_sql, mode="approx", accuracy=acc, runs=3)
        if err:
            print(f"{acc:<10} {'ERROR':<15} {err}")
            continue

        strategy = stats['result']['metadata'].get('strategy', 'unknown')
        sample = stats['result']['metadata'].get('sample_rate', 'N/A')
        if sample and sample != 'N/A':
            sample = f"{sample*100:.0f}%"

        print(f"{acc:<10.2f} {strategy:<15} {format_time(stats['mean']):<12} {sample}")

    # Test 3: All strategies comparison
    print_header("TEST 3: Strategy Comparison")

    test_query = QUERIES[1]  # GROUP BY region
    print(f"\nQuery: {test_query['name']}")
    print(f"SQL: {test_query['sql']}\n")

    result = run_compare_strategies(test_query['sql'])
    if result:
        print(f"{'Strategy':<20} {'Supported':<12} {'Time':<12} {'Error'}")
        print("-" * 70)

        # Exact baseline
        exact_time = result['exact']['time_ms']
        print(f"{'EXACT (baseline)':<20} {'yes':<12} {format_time(exact_time):<12} {'-'}")

        # Each strategy
        for strat in result['strategies']:
            name = strat['name']
            supported = 'yes' if strat['supported'] else 'NO'

            if strat['supported']:
                time_ms = strat['time_ms']
                speedup = exact_time / time_ms if time_ms > 0 else 0
                error = strat.get('error_estimate', {})
                error_str = json.dumps(error) if error else '-'
                print(f"{name:<20} {supported:<12} {format_time(time_ms):<12} {error_str[:30]}")
            else:
                err = strat.get('error', 'not supported')
                print(f"{name:<20} {supported:<12} {'-':<12} {err}")

    # Summary table
    print_header("SUMMARY")
    print_results_table(all_results)

    print("\n" + "="*70)
    print("  Benchmark complete!")
    print("="*70)

if __name__ == "__main__":
    main()
