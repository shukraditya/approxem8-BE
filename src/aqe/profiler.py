"""Data profiling module for intelligent strategy selection."""
from typing import Dict, Any
import duckdb


class DataProfiler:
    """Profiles tables to enable intelligent strategy selection.

    Caches profiling results to avoid re-computation.
    """

    def __init__(self):
        self.cache: Dict[str, Dict[str, Any]] = {}

    def profile_table(self, db, table_name: str) -> Dict[str, Any]:
        """Profile a table: row count, column stats, skew.

        Args:
            db: DuckDB connection
            table_name: Name of table to profile

        Returns:
            Dictionary with row_count and column statistics
        """
        # Return cached result if available
        if table_name in self.cache:
            return self.cache[table_name]

        # Count total rows
        row_count = db.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

        # Get column information
        columns = db.execute(f"DESCRIBE {table_name}").fetchall()
        col_stats = {}

        for col_info in columns:
            col_name = col_info[0]
            col_type = col_info[1]

            # Cardinality (distinct count)
            try:
                distinct = db.execute(
                    f"SELECT COUNT(DISTINCT {col_name}) FROM {table_name}"
                ).fetchone()[0]
            except Exception:
                distinct = 0

            # For numeric columns: mean, stddev, histogram, gini
            if 'INT' in col_type or 'DECIMAL' in col_type or 'FLOAT' in col_type:
                try:
                    stats = db.execute(f"""
                        SELECT
                            AVG({col_name}) as mean,
                            STDDEV({col_name}) as stddev
                        FROM {table_name}
                    """).fetchone()

                    mean = float(stats[0]) if stats[0] else 0
                    stddev = float(stats[1]) if stats[1] else 0

                    # Build 20-bin histogram for skew detection
                    hist = db.execute(f"""
                        SELECT histogram({col_name}, 20)
                        FROM {table_name}
                    """).fetchone()[0]

                    gini = self._calculate_gini(hist)

                    col_stats[col_name] = {
                        "type": col_type,
                        "cardinality": distinct,
                        "mean": mean,
                        "stddev": stddev,
                        "gini": gini,
                    }
                except Exception:
                    col_stats[col_name] = {
                        "type": col_type,
                        "cardinality": distinct,
                    }
            else:
                # For categorical columns: just cardinality
                col_stats[col_name] = {
                    "type": col_type,
                    "cardinality": distinct,
                }

        self.cache[table_name] = {
            "row_count": row_count,
            "columns": col_stats,
        }
        return self.cache[table_name]

    def _calculate_gini(self, histogram) -> float:
        """Calculate Gini coefficient from histogram (0=even, 1=skewed).

        Args:
            histogram: DuckDB histogram object

        Returns:
            Gini coefficient between 0 and 1
        """
        # Extract bins from histogram
        try:
            bins = histogram.bins
        except (AttributeError, TypeError):
            # If histogram doesn't have bins, return 0
            return 0

        if not bins:
            return 0

        # Extract counts from bins
        counts = [bin_val.count for bin_val in bins]
        total = sum(counts)

        if total == 0:
            return 0

        # Gini formula
        sorted_counts = sorted(counts)
        n = len(sorted_counts)

        # G = (2 * sum(i * ci)) / (n * sum(ci)) - (n + 1) / n
        numerator = 2 * sum((i + 1) * c for i, c in enumerate(sorted_counts))
        gini = numerator / (n * total) - (n + 1) / n

        return abs(gini)  # 0 to 1