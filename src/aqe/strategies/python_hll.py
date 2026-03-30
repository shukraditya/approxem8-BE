"""HyperLogLog strategy for approximate distinct counts using datasketch."""
import time
from typing import Any, Dict

import duckdb
from datasketch import HyperLogLog

from aqe.strategies import ExecutionStrategy


class PythonHLLStrategy(ExecutionStrategy):
    """HyperLogLog for approximate COUNT DISTINCT with tunable precision.

    Higher precision = more accuracy but more memory:
    - p=12: ~1.3% error, 4KB
    - p=14: ~0.6% error, 16KB
    - p=16: ~0.3% error, 64KB
    """

    name = "python_hll"
    description = "HyperLogLog with tunable precision (datasketch)"

    def execute(self, sql: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute COUNT DISTINCT using HyperLogLog.

        Extracts column from SQL and streams data through HLL.
        """
        import re

        # Extract column name from COUNT(DISTINCT col)
        match = re.search(r"COUNT\s*\(\s*DISTINCT\s+(\w+)\s*\)", sql, re.IGNORECASE)
        if not match:
            raise ValueError("SQL must contain COUNT(DISTINCT column)")

        column = match.group(1)
        table = self._extract_table(sql)
        precision = config.get("hll_precision", 14)

        # Build query to get distinct column values
        # Note: HLL on sampled data cannot be linearly scaled
        # Skip sampling for HLL to maintain accuracy
        query = f"SELECT {column} FROM {table}"

        db = duckdb.connect()
        db.execute(f"CREATE VIEW sales AS SELECT * FROM 'data/sales.parquet'")

        start = time.perf_counter()

        # Create HLL with specified precision
        hll = HyperLogLog(p=precision)

        result = db.execute(query)

        # Process in chunks to avoid memory issues
        chunk_size = 10000
        while True:
            rows = result.fetchmany(chunk_size)
            if not rows:
                break
            for row in rows:
                if row[0] is not None:
                    hll.update(str(row[0]).encode("utf-8"))

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Calculate theoretical error: 1.04 / sqrt(2^p)
        error_pct = (1.04 / (2 ** (precision / 2))) * 100

        count = int(hll.count())

        db.close()

        return {
            "results": [{"approx_count_distinct": count}],
            "metadata": {
                "strategy": self.name,
                "query_time_ms": round(elapsed_ms, 2),
                "estimated_error_pct": round(error_pct, 3),
                "hll_precision": precision,
            },
        }

    def supports(self, sql: str) -> bool:
        """Check if SQL contains COUNT(DISTINCT ...)."""
        import re

        return bool(
            re.search(r"COUNT\s*\(\s*DISTINCT", sql, re.IGNORECASE)
        )

    def _extract_table(self, sql: str) -> str:
        """Extract table name from SQL."""
        import re

        match = re.search(r"FROM\s+(\w+)", sql, re.IGNORECASE)
        if match:
            return match.group(1)
        return "sales"  # default
