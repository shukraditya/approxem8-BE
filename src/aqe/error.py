"""Error estimation for approximate query results."""
import math
from typing import Optional


def estimate_count_error(sample_count: int, sample_rate: float, confidence: float = 0.95) -> dict:
    """Estimate error bounds for COUNT based on Bernoulli sampling.

    Uses normal approximation for binomial proportion confidence interval.

    Args:
        sample_count: Count from the sample
        sample_rate: Fraction of data sampled (e.g., 0.1 for 10%)
        confidence: Confidence level (0.95 for 95%)

    Returns:
        Dict with estimated_total, error_pct, ci_low, ci_high
    """
    z = 1.96 if confidence == 0.95 else 2.576  # 95% or 99% CI

    # Scale up to population estimate
    estimated_total = int(sample_count / sample_rate)

    # Standard error for proportion (using finite population correction)
    n = sample_count
    N = estimated_total  # Population size
    p = sample_rate

    # Finite population correction factor
    fpc = math.sqrt((N - n) / (N - 1)) if N > 1 else 1.0

    # Standard error of proportion
    se_prop = math.sqrt(p * (1 - p) / n) * fpc

    # Margin of error
    margin = z * se_prop

    # Convert to percentage of estimate
    error_pct = margin * 100

    return {
        "estimated_total": estimated_total,
        "error_pct": round(error_pct, 2),
        "ci_low": int(estimated_total * (1 - margin)),
        "ci_high": int(estimated_total * (1 + margin)),
        "confidence": confidence,
    }


def estimate_sum_error(
    sample_sum: float,
    sample_std: float,
    sample_size: int,
    population_size: int,
    confidence: float = 0.95,
) -> dict:
    """Estimate error bounds for SUM based on sampling.

    Args:
        sample_sum: Sum from the sample
        sample_std: Standard deviation of sampled values
        sample_size: Number of rows in sample
        population_size: Total rows in population
        confidence: Confidence level

    Returns:
        Dict with estimated_total, error_pct, ci_low, ci_high
    """
    z = 1.96 if confidence == 0.95 else 2.576
    sample_rate = sample_size / population_size

    # Scale factor
    scale = 1 / sample_rate
    estimated_total = sample_sum * scale

    # Standard error of sum = N * (std / sqrt(n)) * fpc
    fpc = math.sqrt((population_size - sample_size) / (population_size - 1))
    se_sum = population_size * (sample_std / math.sqrt(sample_size)) * fpc

    margin = z * se_sum
    error_pct = (margin / estimated_total) * 100 if estimated_total != 0 else 0

    return {
        "estimated_total": round(estimated_total, 2),
        "error_pct": round(error_pct, 2),
        "ci_low": round(estimated_total - margin, 2),
        "ci_high": round(estimated_total + margin, 2),
        "confidence": confidence,
    }


def estimate_avg_error(
    sample_std: float,
    sample_size: int,
    population_size: int,
    confidence: float = 0.95,
) -> dict:
    """Estimate error bounds for AVG based on sampling.

    Args:
        sample_std: Standard deviation of sampled values
        sample_size: Number of rows in sample
        population_size: Total rows in population
        confidence: Confidence level

    Returns:
        Dict with standard_error, margin, error_pct (relative)
    """
    z = 1.96 if confidence == 0.95 else 2.576

    # Standard error of mean with finite population correction
    fpc = math.sqrt((population_size - sample_size) / (population_size - 1))
    sem = (sample_std / math.sqrt(sample_size)) * fpc

    margin = z * sem

    return {
        "standard_error": round(sem, 4),
        "margin": round(margin, 4),
        "confidence": confidence,
    }


def get_sample_stats(db, table: str, sample_rate: float) -> dict:
    """Get population size and sample size for error estimation."""
    # Get total population size
    pop_result = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    population_size = pop_result[0]

    # Get sample size (expected)
    sample_size = int(population_size * sample_rate)

    return {
        "population_size": population_size,
        "sample_size": sample_size,
        "sample_rate": sample_rate,
    }
