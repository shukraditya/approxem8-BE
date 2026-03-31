"""DuckDB native approximate count distinct strategy."""
import time
from typing import Any, Dict
import duckdb

from aqe.strategies import ExecutionStrategy


class DuckDBApproxStrategy(ExecutionStrategy):
    """Uses DuckDB's native APPROX_COUNT_DISTINCT for fast approximate distinct counts.

    DuckDB implements HyperLogLog in C++ for much better performance than Python.
    - ~4% error rate
    - ~700ms for 500M rows (vs 8+ minutes for Python HLL)
    - Native C++ implementation
    """

    name = "duckdb_approx"
    description = "DuckDB native APPROX_COUNT_DISTINCT (C++ HLL)"

    def execute(
        self, sql: str, config: Dict[str, Any], db: duckdb.DuckDBPyConnection = None
    ) -> Dict[str, Any]:
        """Execute COUNT DISTINCT using DuckDB's APPROX_COUNT_DISTINCT.

        Rewrites: SELECT COUNT(DISTINCT col) FROM table
        To:       SELECT APPROX_COUNT_DISTINCT(col) FROM table
        """
        import re

        # Extract column name from COUNT(DISTINCT col)
        match = re.search(r"COUNT\s*\(\s*DISTINCT\s+(\w+)\s*\)", sql, re.IGNORECASE)
        if not match:
            raise ValueError("SQL must contain COUNT(DISTINCT column)")

        column = match.group(1)

        # Rewrite SQL to use APPROX_COUNT_DISTINCT
        approx_sql = re.sub(
            r"COUNT\s*\(\s*DISTINCT\s+(\w+)\s*\)",
            r"APPROX_COUNT_DISTINCT(\1)",
            sql,
            flags=re.IGNORECASE
        )

        start = time.perf_counter()

        # Execute on provided connection or create new one
        close_db = False
        if db is None:
            db = duckdb.connect()
            db.execute("CREATE VIEW sales AS SELECT * FROM 'data/sales.parquet'")
            close_db = True

        try:
            result = db.execute(approx_sql).fetchone()
            elapsed_ms = (time.perf_counter() - start) * 1000

            count = int(result[0]) if result else 0
        finally:
            if close_db:
                db.close()

        # DuckDB's APPROX_COUNT_DISTINCT uses HLL with ~4% error
        error_pct = 4.0

        return {
            "results": [{"approx_count_distinct": count}],
            "metadata": {
                "strategy": self.name,
                "query_time_ms": round(elapsed_ms, 2),
                "estimated_error_pct": error_pct,
            },
        }

    def supports(self, sql: str) -> bool:
        """Check if SQL contains COUNT(DISTINCT ...)."""
        import re
        return bool(re.search(r"COUNT\s*\(\s*DISTINCT", sql, re.IGNORECASE))
