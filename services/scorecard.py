"""
Scorecard computation logic.

Core service for computing batting/bowling stats, partnerships,
and run rates from raw delivery data.

TODO: Implement full scorecard aggregation used by routes/matches.py
"""

from typing import Any


def compute_strike_rate(runs: int, balls: int) -> float:
    """Compute batting strike rate."""
    if balls == 0:
        return 0.0
    return round((runs / balls) * 100, 2)


def compute_economy(runs: int, balls: int) -> float:
    """Compute bowling economy rate."""
    if balls == 0:
        return 0.0
    overs = balls / 6
    return round(runs / overs, 2)


def compute_overs_string(balls: int) -> str:
    """Convert total legal balls to overs string (e.g. 12.3)."""
    return f"{balls // 6}.{balls % 6}"


def compute_run_rate(runs: int, balls: int) -> float:
    """Compute current run rate."""
    if balls == 0:
        return 0.0
    overs = balls / 6
    return round(runs / overs, 2)


def compute_required_run_rate(target: int, current_runs: int, balls_remaining: int) -> float:
    """Compute required run rate for a chase."""
    if balls_remaining <= 0:
        return 0.0
    runs_needed = target - current_runs
    overs_remaining = balls_remaining / 6
    return round(runs_needed / overs_remaining, 2)
