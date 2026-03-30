"""Auto-router for intelligent strategy selection."""
import sqlglot
from typing import Optional, Dict, Any

from aqe.profiler import DataProfiler
from aqe.accuracy import accuracy_to_sample_rate, accuracy_to_hll_precision


class AutoRouter:
    """Routes queries to optimal strategies based on patterns and data profile.

    Uses sqlglot to parse SQL and apply rule-based routing logic.
    """

    def __init__(self, profiler: DataProfiler):
        """Initialize router with profiler.

        Args:
            profiler: DataProfiler instance for table statistics
        """
        self.profiler = profiler

    def route(
        self,
        sql: str,
        db,
        accuracy: float
    ) -> Dict[str, Any]:
        """Parse SQL and determine optimal strategy + parameters.

        Args:
            sql: SQL query to analyze
            db: DuckDB connection
            accuracy: Target accuracy (0.90-0.99)

        Returns:
            Dict with "strategy" and optional "config"
        """
        # Parse SQL with sqlglot
        try:
            parsed = sqlglot.parse_one(sql)
        except Exception:
            # If parsing fails, default to duckdb_sample
            return {"strategy": "duckdb_sample", "config": {"sample_rate": 0.1}}

        # Extract table name
        table = self._extract_table(parsed)

        # Get table profile
        profile = self.profiler.profile_table(db, table)

        # Detect query patterns
        has_distinct_count = self._has_count_distinct(parsed)
        has_group_by = self._has_group_by(parsed)
        has_quantile = self._has_quantile(parsed)

        # Routing rules

        # 1. Quantile queries → t-Digest
        if has_quantile:
            return {
                "strategy": "tdigest",
                "config": {"sample_rate": 1.0},  # No sampling needed for t-Digest
            }

        # 2. COUNT DISTINCT → HyperLogLog
        elif has_distinct_count:
            precision = accuracy_to_hll_precision(accuracy)
            return {
                "strategy": "python_hll",
                "config": {"hll_precision": precision},
            }

        # 3. GROUP BY → check if skewed
        elif has_group_by:
            group_col = self._extract_group_by_column(parsed)
            col_profile = profile["columns"].get(group_col, {})

            # If column is skewed (Gini > 0.6), use stratified sampling
            if col_profile.get("gini", 0) > 0.6:
                sample_rate = accuracy_to_sample_rate(
                    accuracy,
                    col_profile.get("mean", 0),
                    col_profile.get("stddev", 0),
                    profile["row_count"]
                )
                return {
                    "strategy": "stratified",
                    "config": {"sample_rate": sample_rate},
                }

            # Otherwise use DuckDB sample
            sample_rate = accuracy_to_sample_rate(
                accuracy,
                col_profile.get("mean", 0),
                col_profile.get("stddev", 0),
                profile["row_count"]
            )
            return {
                "strategy": "duckdb_sample",
                "config": {"sample_rate": sample_rate},
            }

        # 4. Simple aggregate → DuckDB sample (or exact for small tables)
        else:
            # Find the column being aggregated
            agg_col = self._find_aggregated_column(parsed) or "amount"
            col_profile = profile["columns"].get(agg_col, {})

            # Small table → exact (no approximation needed)
            if profile["row_count"] < 100000:
                return {"strategy": "exact", "config": {}}

            sample_rate = accuracy_to_sample_rate(
                accuracy,
                col_profile.get("mean", 0),
                col_profile.get("stddev", 0),
                profile["row_count"]
            )
            return {
                "strategy": "duckdb_sample",
                "config": {"sample_rate": sample_rate},
            }

    def _extract_table(self, parsed) -> str:
        """Extract table name from parsed SQL.

        Args:
            parsed: sqlglot parsed expression

        Returns:
            Table name (defaults to "sales")
        """
        for table in parsed.find_all(sqlglot.exp.Table):
            return table.name
        return "sales"

    def _has_count_distinct(self, parsed) -> bool:
        """Check if SQL contains COUNT(DISTINCT ...).

        Args:
            parsed: sqlglot parsed expression

        Returns:
            True if COUNT DISTINCT is present
        """
        # Check for DISTINCT inside COUNT
        for count in parsed.find_all(sqlglot.exp.Count):
            # Check if any child is Distinct
            for child in count.walk():
                if isinstance(child, sqlglot.exp.Distinct):
                    return True
        return False

    def _has_group_by(self, parsed) -> bool:
        """Check if SQL has GROUP BY clause.

        Args:
            parsed: sqlglot parsed expression

        Returns:
            True if GROUP BY is present
        """
        return parsed.find(sqlglot.exp.Group) is not None

    def _has_quantile(self, parsed) -> bool:
        """Check if SQL contains quantile/median functions.

        Args:
            parsed: sqlglot parsed expression

        Returns:
            True if PERCENTILE or MEDIAN is present
        """
        sql_lower = parsed.sql().lower()
        return "percentile" in sql_lower or "median" in sql_lower

    def _extract_group_by_column(self, parsed) -> str:
        """Extract the first GROUP BY column name.

        Args:
            parsed: sqlglot parsed expression

        Returns:
            Group by column name
        """
        group = parsed.find(sqlglot.exp.Group)
        if group:
            # Get the SQL representation and extract first column
            group_sql = group.sql()
            # Remove "GROUP BY" prefix and get first column
            parts = group_sql.replace("group by", "").strip().split(",")
            if parts:
                return parts[0].strip()
        return ""

    def _find_aggregated_column(self, parsed) -> Optional[str]:
        """Find the first aggregated column (inside COUNT/SUM/AVG/MIN/MAX).

        Args:
            parsed: sqlglot parsed expression

        Returns:
            Column name or None
        """
        # Look for aggregation functions
        for agg in parsed.find_all(sqlglot.exp.AggFunc):
            # Get the argument (column)
            args = list(agg.expressions)
            if args:
                # Handle COUNT(*) vs COUNT(col)
                arg = args[0]
                if isinstance(arg, sqlglot.exp.Column):
                    return arg.name
                elif isinstance(arg, sqlglot.star.Star):
                    return "*"

        # Also look for plain columns in SELECT (for AVG without explicit agg func in some dialects)
        for col in parsed.find_all(sqlglot.exp.Column):
            if col.name:
                return col.name

        return None