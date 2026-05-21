"""
Match CRUD + scoring routes.

Handles match creation, toss, innings start, ball-by-ball recording,
scorecard retrieval, and live WebSocket updates.
"""

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from typing import List, cast

from models.schemas import (
    MatchCreate,
    MatchResponse,
    TossRecord,
    InningsCreate,
    InningsResponse,
    BallEventCreate,
    BallEventResponse,
    APIResponse,
)
from services.db import supabase
from services.websocket import manager
from services.auth import get_current_user

router = APIRouter(prefix="/matches", tags=["matches"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _build_scorecard(match_id: str) -> dict | None:
    """Compute a live scorecard from innings + deliveries tables."""
    innings_res = (
        supabase.table("innings")
        .select("*")
        .eq("match_id", match_id)
        .order("innings_number")
        .execute()
    )

    innings_data = cast(list[dict], innings_res.data or [])
    if not innings_data:
        return None

    scorecard: list[dict] = []

    for inn in innings_data:
        innings_id = inn["id"]
        deliveries_res = (
            supabase.table("deliveries")
            .select("*")
            .eq("innings_id", innings_id)
            .execute()
        )
        deliveries = cast(list[dict], deliveries_res.data or [])

        total_runs = sum(d["runs"] + d.get("extras", 0) for d in deliveries)
        total_wickets = sum(1 for d in deliveries if d.get("is_wicket"))

        legal_balls = sum(
            1 for d in deliveries
            if d.get("extra_type") not in ("wide", "no_ball")
        )
        overs = f"{legal_balls // 6}.{legal_balls % 6}"

        # Batsman aggregations
        batsmen: dict[str, dict] = {}
        for d in deliveries:
            bid = d["batsman_id"]
            if bid not in batsmen:
                batsmen[bid] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
            batsmen[bid]["runs"] += d["runs"]
            if d.get("runs") == 4 and d.get("is_boundary", False):
                batsmen[bid]["fours"] += 1
            if d.get("runs") == 6 or d.get("is_six", False):
                batsmen[bid]["sixes"] += 1
            if d.get("extra_type") != "wide":
                batsmen[bid]["balls"] += 1

        # Bowler aggregations
        bowlers: dict[str, dict] = {}
        for d in deliveries:
            brid = d["bowler_id"]
            if brid not in bowlers:
                bowlers[brid] = {"runs": 0, "wickets": 0, "balls": 0}
            bowlers[brid]["runs"] += d["runs"] + d.get("extras", 0)
            if d.get("is_wicket"):
                bowlers[brid]["wickets"] += 1
            if d.get("extra_type") not in ("wide", "no_ball"):
                bowlers[brid]["balls"] += 1

        scorecard.append({
            "innings_id": innings_id,
            "batting_team": inn["batting_team"],
            "innings_number": inn.get("innings_number", 1),
            "total_runs": total_runs,
            "total_wickets": total_wickets,
            "overs": overs,
            "batsmen": batsmen,
            "bowlers": bowlers,
        })

    return {"match_id": match_id, "scorecard": scorecard}


# ─── Match CRUD ───────────────────────────────────────────────────────────────

@router.post("/", response_model=APIResponse)
async def create_match(match: MatchCreate, user: dict = Depends(get_current_user)):
    """Create a new match."""
    user_id = user.get("sub")
    payload = {
        "team_a_id": match.team_a_id,
        "team_b_id": match.team_b_id,
        "match_type": match.match_type.value,
        "total_overs": match.total_overs,
        "overs_per_bowler": match.overs_per_bowler,
        "status": "setup",
        "created_by": user_id,
    }
    if match.city:
        payload["city"] = match.city
    if match.venue:
        payload["venue"] = match.venue
    if match.ball_type:
        payload["ball_type"] = match.ball_type
    if match.pitch_type:
        payload["pitch_type"] = match.pitch_type
    if match.scheduled_at:
        payload["scheduled_at"] = match.scheduled_at.isoformat()

    response = supabase.table("matches").insert(payload).execute()

    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to create match")

    return APIResponse(message="Match created", data=response.data[0])


@router.get("/", response_model=APIResponse)
async def list_matches(status: str | None = None, limit: int = 20):
    """List matches, optionally filtered by status."""
    query = supabase.table("matches").select("*").order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    response = query.execute()
    return APIResponse(message="Matches fetched", data=response.data)


@router.get("/{match_id}", response_model=APIResponse)
async def get_match(match_id: str):
    """Get a single match by ID."""
    response = supabase.table("matches").select("*").eq("id", match_id).single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Match not found")
    return APIResponse(message="Match fetched", data=response.data)


# ─── Toss ─────────────────────────────────────────────────────────────────────

@router.post("/{match_id}/toss", response_model=APIResponse)
async def record_toss(match_id: str, toss: TossRecord):
    """Record toss result and update match status."""
    response = (
        supabase.table("matches")
        .update({
            "toss_winner_id": toss.toss_winner_id,
            "toss_election": toss.toss_election,
            "status": "toss",
        })
        .eq("id", match_id)
        .execute()
    )
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to record toss")
    return APIResponse(message="Toss recorded", data=response.data[0])


# ─── Innings ──────────────────────────────────────────────────────────────────

@router.post("/{match_id}/innings", response_model=APIResponse)
async def start_innings(match_id: str, innings: InningsCreate):
    """Start a new innings within a match."""
    payload = {
        "match_id": match_id,
        "batting_team": innings.batting_team,
        "bowling_team": innings.bowling_team,
        "innings_number": innings.innings_number,
        "status": "ongoing",
    }
    response = supabase.table("innings").insert(payload).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to start innings")

    # Update match status to playing
    supabase.table("matches").update({"status": "playing"}).eq("id", match_id).execute()

    return APIResponse(message="Innings started", data=response.data)


# ─── Ball-by-Ball ─────────────────────────────────────────────────────────────

@router.post("/{match_id}/innings/{innings_id}/ball", response_model=APIResponse)
async def record_ball(match_id: str, innings_id: str, ball: BallEventCreate):
    """Record a single delivery and broadcast live update via WebSocket."""
    payload = {
        "innings_id": innings_id,
        "over_number": ball.over_number,
        "ball_number": ball.ball_number,
        "batsman_id": ball.batsman_id,
        "non_striker_id": ball.non_striker_id,
        "bowler_id": ball.bowler_id,
        "runs": ball.runs,
        "is_wicket": ball.is_wicket,
        "extras": ball.extras,
    }
    if ball.wicket_type:
        payload["wicket_type"] = ball.wicket_type.value
    if ball.dismissed_player_id:
        payload["dismissed_player_id"] = ball.dismissed_player_id
    if ball.fielder_id:
        payload["fielder_id"] = ball.fielder_id
    if ball.extra_type:
        payload["extra_type"] = ball.extra_type.value
    if ball.commentary:
        payload["commentary"] = ball.commentary

    response = supabase.table("deliveries").insert(payload).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to record delivery")

    # Broadcast updated scorecard to all WebSocket viewers
    scorecard = await _build_scorecard(match_id)
    if scorecard:
        await manager.broadcast(match_id, scorecard)

    return APIResponse(message="Ball recorded", data=response.data)


# ─── Scorecard ────────────────────────────────────────────────────────────────

@router.get("/{match_id}/scorecard")
async def get_scorecard(match_id: str):
    """Get the full computed scorecard for a match."""
    scorecard = await _build_scorecard(match_id)
    if scorecard is None:
        raise HTTPException(status_code=404, detail="No scorecard data found")
    return scorecard


# ─── WebSocket (Live Score) ───────────────────────────────────────────────────

@router.websocket("/ws/{match_id}")
async def websocket_endpoint(websocket: WebSocket, match_id: str):
    """Live score WebSocket — pushes scorecard updates in real time."""
    await manager.connect(websocket, match_id)
    try:
        # Send initial scorecard on connection
        scorecard = await _build_scorecard(match_id)
        if scorecard:
            await websocket.send_json(scorecard)
        # Keep connection alive, listening for pings
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, match_id)
