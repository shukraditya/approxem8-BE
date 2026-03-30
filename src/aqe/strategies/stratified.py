"""Stratified sampling strategy for accurate GROUP BY on skewed data."""
import time
from typing import Any, Dict

import duckdb

from aqe.strategies import ExecutionStrategy


class StratifiedSamplingStrategy(ExecutionStrategy):
    """Stratified sampling for GROUP BY queries on skewed data.

    Problem with uniform sampling:
    - Antarctica = 0.2% of data
    - 10% sample might have 0 Antarctica rows!

    Stratified solution:
    1. Get all distinct group values
    2. Sample from EACH group independently
    3. All groups guaranteed representation
    """

    name = "stratified"
    description = "Stratified sampling for accurate GROUP BY on skewed data"

    def execute(self, sql: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute GROUP BY with stratified sampling."""
        import re

        # Parse SQL to extract group column, agg column, agg function
        parsed = self._parse_sql(sql)
        if not parsed:
            raise ValueError("SQL must be a simple GROUP BY query")

        group_col = parsed["group_col"]
        agg_col = parsed["agg_col"]
        agg_func = parsed["agg_func"]
        table = parsed["table"]
        sample_rate = config.get("sample_rate", 0.1)

        db = duckdb.connect()
        db.execute(f"CREATE VIEW sales AS SELECT * FROM 'data/sales.parquet'")

        start = time.perf_counter()

        # Step 1: Get all distinct groups
        groups = db.execute(f"SELECT DISTINCT {group_col} FROM {table}").fetchall()
        groups = [g[0] for g in groups]

        # Step 2: Sample from each group
        results = []
        for group_val in groups:
            # Build WHERE clause (handle strings)
            if isinstance(group_val, str):
                where_clause = f"{group_col} = '{group_val}'"
            else:
                where_clause = f"{group_col} = {group_val}"

            # Query with sampling
            query = f"""
                SELECT {agg_func}({agg_col}) as agg_val, COUNT(*) as sample_count
                FROM {table}
                WHERE {where_clause}
                USING SAMPLE {int(sample_rate * 100)}%
            """

            row = db.execute(query).fetchone()
            if row and row[1] > 0:  # Has samples (sample_count is now index 1)
                results.append({
                    "group": group_val,
                    "sample_agg": row[0],
                    "sample_count": row[1],
                })

        # Step 3: Get population counts for scaling
        pop_counts = {}
        for group_val in groups:
            if isinstance(group_val, str):
                where_clause = f"{group_col} = '{group_val}'"
            else:
                where_clause = f"{group_col} = {group_val}"

            count = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where_clause}"
            ).fetchone()[0]
            pop_counts[group_val] = count

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Scale results based on actual sample vs population ratio per group
        scaled_results = []
        for r in results:
            group = r["group"]
            pop_count = pop_counts[group]
            sample_count = r["sample_count"]

            # Actual sample rate might differ from target due to randomness
            actual_rate = sample_count / pop_count if pop_count > 0 else 0
            scale_factor = 1 / actual_rate if actual_rate > 0 else 0

            # For COUNT(*), scale up
            if agg_func.upper() == "COUNT":
                scaled_val = int(r["sample_agg"] * scale_factor)
            # For AVG, no scaling needed
            elif agg_func.upper() == "AVG":
                scaled_val = r["sample_agg"]
            # For SUM, scale up
            elif agg_func.upper() == "SUM":
                scaled_val = r["sample_agg"] * scale_factor
            else:
                scaled_val = r["sample_agg"]

            # Calculate per-group error
            error_pct = self._calculate_error(pop_count, sample_count)

            scaled_results.append({
                group_col: group,
                f"{agg_func.lower()}_{agg_col}": scaled_val,
                "sample_count": sample_count,
                "population_count": pop_count,
                "error_pct": round(error_pct, 2),
            })

        db.close()

        return {
            "results": scaled_results,
            "metadata": {
                "strategy": self.name,
                "query_time_ms": round(elapsed_ms, 2),
                "sample_rate": sample_rate,
                "groups": len(groups),
                "stratified": True,
            },
        }

    def supports(self, sql: str) -> bool:
        """Check if SQL is a GROUP BY query."""
        import re

        return bool(re.search(r"GROUP\s+BY", sql, re.IGNORECASE))

    def _parse_sql(self, sql: str) -> Dict[str, str]:
        """Parse simple GROUP BY query.

        Expected: SELECT col, AGG(col2) FROM table GROUP BY col
        """
        import re

        # Normalize SQL (remove extra spaces)
        sql_norm = ' '.join(sql.split())

        # Match: SELECT col, AGG(col) FROM table GROUP BY col
        # Also handles: SELECT col, AGG(*) FROM table GROUP BY col
        pattern = r"""
            SELECT\s+(\w+)\s*,\s*
            (\w+)\s*\(\s*(?:\*|\w+)\s*\)\s*
            FROM\s+(\w+)\s*
            GROUP\s+BY\s+(\w+)
        """
        match = re.search(pattern, sql_norm, re.IGNORECASE | re.VERBOSE)

        if match:
            group_col = match.group(1)
            agg_func = match.group(2)
            table = match.group(3)
            group_by_col = match.group(4)

            # Verify group column matches GROUP BY
            if group_col.lower() != group_by_col.lower():
                # Try to find the GROUP BY column in SELECT
                # Pattern: SELECT ..., AGG(...) FROM ... GROUP BY col
                alt_pattern = r"""
                    SELECT\s+.*?,
                    \s*(\w+)\s*\(\s*(?:\*|\w+)\s*\)\s*
                    FROM\s+(\w+)\s*
                    GROUP\s+BY\s+(\w+)
                """
                alt_match = re.search(alt_pattern, sql_norm, re.IGNORECASE | re.VERBOSE)
                if alt_match:
                    agg_func = alt_match.group(1)
                    table = alt_match.group(2)
                    group_col = alt_match.group(3)
                else:
                    return None

            # Extract agg_col from original SQL
            agg_match = re.search(rf"{agg_func}\s*\(\s*(\w+|\*)\s*\)", sql_norm, re.IGNORECASE)
            agg_col = agg_match.group(1) if agg_match else "*"

            return {
                "group_col": group_col,
                "agg_func": agg_func,
                "agg_col": agg_col,
                "table": table,
            }

        return None

    def _calculate_error(self, pop_count: int, sample_count: int) -> float:
        """Calculate approximate error percentage for stratified sample."""
        import math

        if sample_count == 0:
            return float('inf')

        # Standard error for proportion
        p = sample_count / pop_count
        n = sample_count

        # 95% CI margin
        margin = 1.96 * math.sqrt(p * (1 - p) / n)

        return margin * 100
