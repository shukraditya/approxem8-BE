"""Base strategy interface for approximate query execution."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class ExecutionStrategy(ABC):
    """Base class for query execution strategies."""

    name: str = ""
    description: str = ""

    @abstractmethod
    def execute(self, sql: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Execute query with this strategy.

        Returns dict with:
            - results: List[dict] - query results
            - metadata: dict - execution metadata (time, error estimates, etc.)
        """
        pass

    @abstractmethod
    def supports(self, sql: str) -> bool:
        """Check if this strategy can handle the given SQL."""
        pass
