"""Pydantic models for API requests and responses."""
from typing import Any, Literal, Optional
from pydantic import BaseModel


class QueryRequest(BaseModel):
    """Request body for /query endpoint."""
    sql: str
    mode: Literal["exact", "approx"] = "exact"
    sample_rate: Optional[float] = 0.1
    strategy: Optional[str] = None  # "duckdb_sample", "python_hll", "tdigest"
    config: Optional[dict] = None   # Strategy-specific config


class QueryMetadata(BaseModel):
    """Metadata about query execution."""
    mode: str
    query_time_ms: float
    rows_returned: int
    sample_rate: Optional[float] = None
    strategy: Optional[str] = None
    error_estimate: Optional[dict] = None  # Error bounds per column


class QueryResponse(BaseModel):
    """Response from /query endpoint."""
    results: list[dict]
    metadata: QueryMetadata
