"""Accuracy-to-parameters mapping for approximate query strategies."""
import math


def accuracy_to_sample_rate(
    accuracy: float,
    mean: float,
    stddev: float,
    total_rows: int,
    confidence: float = 0.95
) -> float:
    """Calculate sample rate needed for given accuracy.

    For mean estimation with 95% CI:
        margin = (1 - accuracy) * mean * 2
        sample_rate = (z * stddev / (margin * sqrt(total_rows)))^2

    Args:
        accuracy: Target accuracy (0.90-0.99)
        mean: Mean of the column being aggregated
        stddev: Standard deviation of the column
        total_rows: Total rows in the table
        confidence: Confidence level (0.95 or 0.99)

    Returns:
        Sample rate between 0.05 and 0.5
    """
    z = 1.96 if confidence == 0.95 else 2.576
    margin = (1 - accuracy) * mean * 2

    # Edge cases: zero margin or zero mean
    if margin == 0 or mean == 0:
        return 0.1  # Default

    # Required sample size for this margin
    required_n = (z * stddev / margin) ** 2

    # Sample rate as fraction of total rows
    sample_rate = min(required_n / total_rows, 0.5)

    # Clamp to valid range (5% to 50%)
    return max(0.05, min(0.5, sample_rate))


def accuracy_to_hll_precision(accuracy: float) -> int:
    """Map accuracy target to HLL precision.

    HLL error = 1.04 / sqrt(2^p)

    accuracy 0.90 → error 10%  → p=8
    accuracy 0.95 → error 5%   → p=12
    accuracy 0.99 → error 1%   → p=16

    Args:
        accuracy: Target accuracy (0.90-0.99)

    Returns:
        HLL precision p (4-16)
    """
    if accuracy >= 0.99:
        return 16
    elif accuracy >= 0.95:
        return 14
    elif accuracy >= 0.90:
        return 12
    else:
        return 10