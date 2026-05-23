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
    SquadSubmit,
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
    """Compute a live scorecard from innings + overs tables."""
    innings_res = (
        supabase.table("innings")
        .select("*")
        .eq("match_id", match_id)
        .order("innings_number")
        .execute()
    )

    innings_data = innings_res.data or []
    if not innings_data:
        return None

    scorecard = []
    for inn in innings_data:
        innings_id = inn["id"]
        # Fetch overs to see bowlers performance
        overs_res = (
            supabase.table("overs")
            .select("*")
            .eq("innings_id", innings_id)
            .execute()
        )
        overs_data = overs_res.data or []

        # Bowler aggregations from overs
        bowlers = {}
        for o in overs_data:
            brid = o["bowler_id"]
            if brid not in bowlers:
                bowlers[brid] = {"runs": 0, "wickets": 0, "overs": 0}
            bowlers[brid]["runs"] += o.get("runs") or 0
            bowlers[brid]["wickets"] += o.get("wickets") or 0
            bowlers[brid]["overs"] += 1

        scorecard.append({
            "innings_id": innings_id,
            "batting_team_id": inn["batting_team_id"],
            "bowling_team_id": inn["bowling_team_id"],
            "innings_number": inn.get("innings_number", 1),
            "total_runs": inn.get("total_runs") or 0,
            "total_wickets": inn.get("total_wickets") or 0,
            "overs": str(inn.get("overs_played") or "0.0"),
            "bowlers": bowlers,
            "batsmen": {} # No deliveries table, returning empty dict
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


@router.get("/live", response_model=APIResponse)
async def get_live_matches():
    """Get all live matches for the homepage."""
    res = (
        supabase.table("matches")
        .select("*")
        .in_("status", ["toss", "playing", "innings_break"])
        .order("created_at", desc=True)
        .execute()
    )
    
    matches_list = res.data or []
    if not matches_list:
        return APIResponse(message="No live matches found", data=[])
        
    team_ids = set()
    for m in matches_list:
        team_ids.add(m["team_a_id"])
        team_ids.add(m["team_b_id"])
        
    teams_res = supabase.table("teams").select("id, name, avatar_color").in_("id", list(team_ids)).execute()
    teams_map = {t["id"]: t for t in (teams_res.data or [])}
    
    def get_initials(name):
        if not name:
            return "??"
        parts = name.strip().split()
        if len(parts) > 1:
            return (parts[0][0] + parts[-1][0]).upper()
        return name[:2].upper()

    live_matches = []
    for m in matches_list:
        match_id = m["id"]
        
        innings_res = (
            supabase.table("innings")
            .select("*")
            .eq("match_id", match_id)
            .order("innings_number", desc=True)
            .limit(1)
            .execute()
        )
        current_innings = innings_res.data[0] if innings_res.data else None
        
        team_a_meta = teams_map.get(m["team_a_id"], {})
        team_b_meta = teams_map.get(m["team_b_id"], {})
        
        team_a = {
            "id": m["team_a_id"],
            "name": team_a_meta.get("name", "Team A"),
            "initials": get_initials(team_a_meta.get("name")),
            "color": team_a_meta.get("avatar_color", "#7c3aed")
        }
        team_b = {
            "id": m["team_b_id"],
            "name": team_b_meta.get("name", "Team B"),
            "initials": get_initials(team_b_meta.get("name")),
            "color": team_b_meta.get("avatar_color", "#3b82f6")
        }
        
        live_matches.append({
            "id": match_id,
            "match_type": m["match_type"],
            "status": m["status"],
            "team_a": team_a,
            "team_b": team_b,
            "current_innings": current_innings
        })
        
    return APIResponse(message="Live matches retrieved", data=live_matches)


@router.get("/{match_id}", response_model=APIResponse)
async def get_match(match_id: str):
    """Get full state for a single match."""
    response = supabase.table("matches").select("*").eq("id", match_id).single().execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Match not found")
        
    match_data = response.data
    team_a_id = match_data["team_a_id"]
    team_b_id = match_data["team_b_id"]

    teams_res = supabase.table("teams").select("id, name, avatar_color").in_("id", [team_a_id, team_b_id]).execute()
    teams_map = {t["id"]: t for t in (teams_res.data or [])}
    
    def get_initials(name):
        if not name:
            return "??"
        parts = name.strip().split()
        if len(parts) > 1:
            return (parts[0][0] + parts[-1][0]).upper()
        return name[:2].upper()

    team_a_meta = teams_map.get(team_a_id, {})
    team_b_meta = teams_map.get(team_b_id, {})
    
    team_a = {
        "id": team_a_id,
        "name": team_a_meta.get("name", "Team A"),
        "initials": get_initials(team_a_meta.get("name")),
        "color": team_a_meta.get("avatar_color", "#7c3aed")
    }
    team_b = {
        "id": team_b_id,
        "name": team_b_meta.get("name", "Team B"),
        "initials": get_initials(team_b_meta.get("name")),
        "color": team_b_meta.get("avatar_color", "#3b82f6")
    }

    squads_res = supabase.table("match_squads").select(
        "id, team_id, user_id, batting_order, is_captain, is_vc, users(id, first_name, last_name, phone, avatar_color)"
    ).eq("match_id", match_id).execute()
    
    squad_a = []
    squad_b = []
    
    for sq in (squads_res.data or []):
        u = sq.get("users") or {}
        first = u.get("first_name", "")
        last = u.get("last_name", "")
        full_name = f"{first} {last}".strip()
        player_obj = {
            "id": u.get("id"),
            "name": full_name or "Unknown Player",
            "initials": (first[0] if first else "") + (last[0] if last else ""),
            "role": "Batsman",
            "jersey": 0,
            "isCaptain": sq.get("is_captain", False),
            "isVC": sq.get("is_vc", False),
            "batting_order": sq.get("batting_order")
        }
        if not player_obj["initials"]:
            player_obj["initials"] = get_initials(player_obj["name"])
            
        if sq["team_id"] == team_a_id:
            squad_a.append(player_obj)
        else:
            squad_b.append(player_obj)

    squad_a.sort(key=lambda x: x.get("batting_order") or 99)
    squad_b.sort(key=lambda x: x.get("batting_order") or 99)

    innings_res = supabase.table("innings").select("*").eq("match_id", match_id).order("innings_number").execute()
    innings_list = innings_res.data or []

    scorecard = await _build_scorecard(match_id)

    full_state = {
        "match": match_data,
        "team_a": team_a,
        "team_b": team_b,
        "squad_a": squad_a,
        "squad_b": squad_b,
        "innings": innings_list,
        "scorecard": scorecard
    }
    
    return APIResponse(message="Match state fetched", data=full_state)


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


@router.post("/{match_id}/squads", response_model=APIResponse)
async def submit_match_squad(match_id: str, squad: SquadSubmit, user: dict = Depends(get_current_user)):
    """Submit playing XI squad for a team in a match."""
    # Delete existing squad entries for this match + team
    supabase.table("match_squads").delete().eq("match_id", match_id).eq("team_id", squad.team_id).execute()
    
    payloads = []
    for idx, player_id in enumerate(squad.player_ids):
        payloads.append({
            "match_id": match_id,
            "team_id": squad.team_id,
            "user_id": player_id,
            "batting_order": idx + 1,
            "is_captain": player_id == squad.captain_id,
            "is_vc": player_id == squad.vice_captain_id
        })
        
    res = supabase.table("match_squads").insert(payloads).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Failed to submit match squad")
        
    return APIResponse(message="Playing XI submitted successfully", data=res.data)


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
