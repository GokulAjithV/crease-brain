"""
Leaderboard / rankings routes.

TODO: Implement ranking endpoints using player_match_stats aggregation.
"""

from fastapi import APIRouter

from models.schemas import APIResponse
from services.db import supabase

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("/", response_model=APIResponse)
async def get_leaderboard(
    category: str = "overall",
    period: str = "all_time",
    limit: int = 20,
):
    """
    Get player rankings.

    Args:
        category: overall | batting | bowling | all_rounder
        period: weekly | monthly | all_time
        limit: Number of entries to return
    """
    # TODO: Implement aggregation query from player_match_stats
    return APIResponse(
        message="Leaderboard endpoint — coming soon",
        data=[],
    )
