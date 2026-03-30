"""t-Digest strategy for approximate quantiles (median, p99, etc.)."""
import time
from typing import Any, Dict

import duckdb
from tdigest import TDigest

from aqe.strategies import ExecutionStrategy


class TDigestStrategy(ExecutionStrategy):
    """t-Digest for accurate quantile estimation.

    Much more accurate than sampling for median/p99:
    - Sampling: median ±5-10% error
    - t-Digest: median ±0.1% error
    """

    name = "tdigest"
    description = "t-Digest for accurate quantiles (median, p95, p99)"

    def execute(self, sql: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute quantile query using t-Digest.

        Extracts column and computes requested percentiles.
        """
        import re

        # Extract percentiles and column from SQL
        # Supports: percentile_cont(0.5), median, percentile_disc, etc.
        percentiles = self._extract_percentiles(sql)
        column = self._extract_column(sql)
        table = self._extract_table(sql)

        compression = config.get("tdigest_compression", 100)
        sample_rate = config.get("sample_rate", 1.0)

        db = duckdb.connect()
        db.execute(f"CREATE VIEW sales AS SELECT * FROM 'data/sales.parquet'")

        start = time.perf_counter()

        # Create t-Digest
        digest = TDigest()
        digest.compression = compression  # Set compression attribute

        # Build query
        if sample_rate < 1.0:
            query = f"SELECT {column} FROM {table} USING SAMPLE {int(sample_rate * 100)}%"
        else:
            query = f"SELECT {column} FROM {table}"

        # Stream values into digest
        result = db.execute(query)

        chunk_size = 10000
        while True:
            rows = result.fetchmany(chunk_size)
            if not rows:
                break
            for row in rows:
                if row[0] is not None:
                    digest.update(float(row[0]))

        # Compute percentiles
        results = {}
        for name, p in percentiles.items():
            results[name] = round(digest.percentile(p * 100), 4)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Estimate error based on compression
        # Higher compression = more centroids = lower error
        # Typical: compression 100 → ~0.1-0.5% error
        estimated_error = 1.0 / compression

        db.close()

        return {
            "results": [results],
            "metadata": {
                "strategy": self.name,
                "query_time_ms": round(elapsed_ms, 2),
                "estimated_error_pct": round(estimated_error * 100, 3),
                "tdigest_compression": compression,
                "sample_rate": sample_rate if sample_rate < 1.0 else None,
            },
        }

    def supports(self, sql: str) -> bool:
        """Check if SQL contains percentile or median."""
        import re

        patterns = [
            r"percentile_",
            r"median",
            r"quantile",
        ]
        sql_lower = sql.lower()
        return any(p in sql_lower for p in patterns)

    def _extract_percentiles(self, sql: str) -> Dict[str, float]:
        """Extract requested percentiles from SQL.

        Returns dict mapping name to percentile (0-1).
        """
        import re

        percentiles = {}
        sql_lower = sql.lower()

        # Look for percentile_cont(0.5) or percentile_disc(0.95)
        matches = re.findall(r"percentile_\w+\((0\.\d+)\)", sql_lower)
        for i, m in enumerate(matches):
            p = float(m)
            name = f"p{int(p * 100)}"
            percentiles[name] = p

        # Check for median
        if "median" in sql_lower:
            percentiles["median"] = 0.5

        # Default if nothing found
        if not percentiles:
            percentiles["median"] = 0.5

        return percentiles

    def _extract_column(self, sql: str) -> str:
        """Extract column name from percentile function."""
        import re

        # Try to find percentile function with column in ORDER BY
        # Matches: percentile_cont(0.5) WITHIN GROUP (ORDER BY amount)
        match = re.search(r"ORDER\s+BY\s+(\w+)", sql, re.IGNORECASE)
        if match:
            return match.group(1)

        # Fallback: look for column after SELECT
        match = re.search(r"SELECT\s+\w+\((\w+)\)", sql, re.IGNORECASE)
        if match:
            return match.group(1)

        return "amount"  # default

    def _extract_table(self, sql: str) -> str:
        """Extract table name from SQL."""
        import re

        match = re.search(r"FROM\s+(\w+)", sql, re.IGNORECASE)
        if match:
            return match.group(1)
        return "sales"
