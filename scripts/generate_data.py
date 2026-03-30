"""Generate synthetic sales dataset for AQE testing."""
import duckdb
import time
from pathlib import Path


def generate_dataset(output_path: str = "data/sales.parquet", n_rows: int = 10_000_000):
    """Generate dataset with skewed regions."""
    start = time.time()

    conn = duckdb.connect()

    # For large datasets, use COPY with SELECT directly to avoid memory issues
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    print(f"Generating {n_rows:,} rows...")

    # Create and export in one go
    conn.execute(f"""
        COPY (
            SELECT
                (random() * 1000000)::BIGINT as user_id,
                CASE
                    WHEN random() < 0.90 THEN 'US'
                    WHEN random() < 0.98 THEN 'UK'
                    ELSE 'Antarctica'
                END as region,
                (random() * 1000)::DECIMAL(10,2) as amount,
                now() - INTERVAL (random() * 365) DAY as date
            FROM range({n_rows})
        ) TO '{output_path}' (FORMAT PARQUET)
    """)

    # Show stats
    stats = conn.execute(f"""
        SELECT
            region,
            COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as pct
        FROM '{output_path}'
        GROUP BY region
        ORDER BY count DESC
    """).fetchall()

    # Get file size
    file_size_mb = Path(output_path).stat().st_size / (1024 * 1024)

    elapsed = time.time() - start
    print(f"Generated {n_rows:,} rows in {elapsed:.1f}s")
    print(f"Saved to: {output_path} ({file_size_mb:.1f} MB)")
    print(f"\nRegion distribution:")
    for region, count, pct in stats:
        print(f"  {region}: {count:,} rows ({pct}%)")

    conn.close()


if __name__ == "__main__":
    import sys
    n_rows = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
    output = sys.argv[2] if len(sys.argv) > 2 else "data/sales.parquet"
    generate_dataset(output, n_rows)
