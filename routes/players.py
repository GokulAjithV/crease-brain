"""
Player profile + stats routes.

Handles player CRUD, profile retrieval, per-player career stats,
and match history lookups.
"""

from fastapi import APIRouter, HTTPException
from typing import cast

from models.schemas import (
    PlayerCreate,
    PlayerUpdate,
    PlayerResponse,
    APIResponse,
    PlayerMatchStats,
)
from services.db import supabase

router = APIRouter(prefix="/players", tags=["players"])


# ─── Player CRUD ──────────────────────────────────────────────────────────────

@router.post("/", response_model=APIResponse)
async def create_player(player: PlayerCreate):
    """Register a new player profile."""
    payload = {
        "name": player.name,
        "role": player.role.value,
        "batting_style": player.batting_style.value,
    }
    if player.email:
        payload["email"] = player.email
    if player.phone:
        payload["phone"] = player.phone
    if player.avatar_url:
        payload["avatar_url"] = player.avatar_url
    if player.bowling_style:
        payload["bowling_style"] = player.bowling_style.value
    if player.team_id:
        payload["team_id"] = player.team_id

    response = supabase.table("users").insert(payload).execute()

    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create player")

    return APIResponse(message="Player created", data=response.data)


@router.get("/", response_model=APIResponse)
async def list_players(team_id: str | None = None, limit: int = 50):
    """List players, optionally filtered by team."""
    query = supabase.table("users").select("*").order("name").limit(limit)
    if team_id:
        query = query.eq("team_id", team_id)
    response = query.execute()
    return APIResponse(message="Players fetched", data=response.data)


@router.get("/{player_id}", response_model=APIResponse)
async def get_player(player_id: str):
    """Get a player profile by ID."""
    response = (
        supabase.table("users")
        .select("*")
        .eq("id", player_id)
        .single()
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Player not found")
    return APIResponse(message="Player fetched", data=response.data)


@router.patch("/{player_id}", response_model=APIResponse)
async def update_player(player_id: str, updates: PlayerUpdate):
    """Partially update a player profile."""
    payload = updates.model_dump(exclude_none=True)

    # Convert enum values to strings
    for key in ("role", "batting_style", "bowling_style"):
        if key in payload and hasattr(payload[key], "value"):
            payload[key] = payload[key].value

    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")

    response = (
        supabase.table("users")
        .update(payload)
        .eq("id", player_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=404, detail="Player not found or update failed")
    return APIResponse(message="Player updated", data=response.data)


# ─── Player Stats ─────────────────────────────────────────────────────────────

@router.get("/{player_id}/stats", response_model=APIResponse)
async def get_player_stats(player_id: str):
    """Get aggregated career stats for a player from the player_match_stats cache."""
    response = (
        supabase.table("player_match_stats")
        .select("*")
        .eq("player_id", player_id)
        .execute()
    )
    rows = cast(list[dict], response.data or [])

    if not rows:
        return APIResponse(message="No stats found", data={
            "matches": 0,
            "runs": 0,
            "balls_faced": 0,
            "fours": 0,
            "sixes": 0,
            "wickets": 0,
            "catches": 0,
        })

    career = {
        "matches": len(rows),
        "runs": sum(r.get("runs_scored", 0) for r in rows),
        "balls_faced": sum(r.get("balls_faced", 0) for r in rows),
        "fours": sum(r.get("fours", 0) for r in rows),
        "sixes": sum(r.get("sixes", 0) for r in rows),
        "highest_score": max((r.get("runs_scored", 0) for r in rows), default=0),
        "wickets": sum(r.get("wickets_taken", 0) for r in rows),
        "catches": sum(r.get("catches", 0) for r in rows),
        "man_of_match_count": sum(1 for r in rows if r.get("is_man_of_match")),
    }

    # Batting average (runs / dismissals)
    dismissals = sum(1 for r in rows if r.get("is_out", True))
    career["batting_average"] = round(career["runs"] / max(dismissals, 1), 2)

    # Overall strike rate
    career["strike_rate"] = round(
        (career["runs"] / max(career["balls_faced"], 1)) * 100, 2
    )

    return APIResponse(message="Player stats fetched", data=career)


@router.get("/{player_id}/matches", response_model=APIResponse)
async def get_player_match_history(player_id: str, limit: int = 20):
    """Get per-match stats history for a player."""
    response = (
        supabase.table("player_match_stats")
        .select("*, matches(team_a, team_b, format, status, winner, created_at)")
        .eq("player_id", player_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return APIResponse(message="Match history fetched", data=response.data)
