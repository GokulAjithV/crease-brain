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
    InningsStart,
    InningsResponse,
    OverStart,
    DeliveryCreate,
    BallEventCreate,
    BallEventResponse,
    APIResponse,
)
from services.db import supabase
from services.websocket import manager
from services.auth import get_current_user
from postgrest.types import CountMethod

router = APIRouter(prefix="/matches", tags=["matches"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _build_scorecard(match_id: str) -> dict | None:
    """Compute a live scorecard from innings, overs, and deliveries tables."""
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

    # Fetch squad players to resolve names
    squad_res = (
        supabase.table("match_squads")
        .select("user_id, users(first_name, last_name)")
        .eq("match_id", match_id)
        .execute()
    )
    squad_map = {}
    for sq in (squad_res.data or []):
        u = sq.get("users") or {}
        first = u.get("first_name", "")
        last = u.get("last_name", "")
        squad_map[sq["user_id"]] = f"{first} {last}".strip() or "Player"

    scorecard = []
    for inn in innings_data:
        innings_id = inn["id"]
        
        # Query all deliveries for this innings
        deliveries_res = (
            supabase.table("deliveries")
            .select("runs_batsman, runs_extras, extra_type, is_wicket, wicket_type, batsman_id, non_striker_id, bowler_id")
            .eq("innings_id", innings_id)
            .execute()
        )
        deliveries = deliveries_res.data or []

        batsmen = {}
        bowlers = {}
        
        for d in deliveries:
            bid = d["batsman_id"]
            brid = d["bowler_id"]
            
            # Batsman stats
            if bid not in batsmen:
                batsmen[bid] = {"runs": 0, "balls": 0, "fours": 0, "sixes": 0}
            batsmen[bid]["runs"] += d.get("runs_batsman") or 0
            if d.get("extra_type") != "wide":
                batsmen[bid]["balls"] += 1
            if d.get("runs_batsman") == 4:
                batsmen[bid]["fours"] += 1
            if d.get("runs_batsman") == 6:
                batsmen[bid]["sixes"] += 1
                
            # Bowler stats
            if brid not in bowlers:
                bowlers[brid] = {"runs": 0, "wickets": 0, "balls": 0}
            
            extra_type = d.get("extra_type")
            extra_runs = d.get("runs_extras") or 0
            batsman_runs = d.get("runs_batsman") or 0
            if extra_type in ["wide", "noball"]:
                bowlers[brid]["runs"] += batsman_runs + extra_runs
            else:
                bowlers[brid]["runs"] += batsman_runs
                
            if d.get("is_wicket") and d.get("wicket_type") in ["bowled", "caught", "lbw", "stumped", "hit_wicket"]:
                bowlers[brid]["wickets"] += 1
                
            if extra_type not in ["wide", "noball"]:
                bowlers[brid]["balls"] += 1

        formatted_batsmen = {}
        for bid, stats in batsmen.items():
            formatted_batsmen[bid] = {
                "name": squad_map.get(bid, "Unknown Batsman"),
                **stats
            }
            
        formatted_bowlers = {}
        for brid, stats in bowlers.items():
            b_balls = stats["balls"]
            formatted_bowlers[brid] = {
                "name": squad_map.get(brid, "Unknown Bowler"),
                "runs": stats["runs"],
                "wickets": stats["wickets"],
                "overs": f"{b_balls // 6}.{b_balls % 6}"
            }

        scorecard.append({
            "innings_id": innings_id,
            "batting_team_id": inn["batting_team_id"],
            "bowling_team_id": inn["bowling_team_id"],
            "innings_number": inn.get("innings_number", 1),
            "total_runs": inn.get("total_runs") or 0,
            "total_wickets": inn.get("total_wickets") or 0,
            "overs": str(inn.get("overs_played") or "0.0"),
            "bowlers": formatted_bowlers,
            "batsmen": formatted_batsmen
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
            "created_by": m.get("created_by"),
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
    
    team_players_res = (
        supabase.table("team_players")
        .select("team_id, user_id, role")
        .in_("team_id", [team_a_id, team_b_id])
        .execute()
    )
    tp_map = {}
    for tp in (team_players_res.data or []):
        tp_map[(tp["team_id"], tp["user_id"])] = tp.get("role")
        
    squad_a = []
    squad_b = []
    
    role_map = {
        "BAT": "Batsman",
        "BOWL": "Bowler",
        "ALL": "All-Rounder",
        "WK": "WK-Batsman"
    }
    
    for sq in (squads_res.data or []):
        u = sq.get("users") or {}
        first = u.get("first_name", "")
        last = u.get("last_name", "")
        full_name = f"{first} {last}".strip()
        
        role_code = tp_map.get((sq["team_id"], sq["user_id"])) or "BAT"
        role_str = role_map.get(role_code.upper(), "Batsman")
        
        player_obj = {
            "id": u.get("id"),
            "name": full_name or "Unknown Player",
            "initials": (first[0] if first else "") + (last[0] if last else ""),
            "role": role_str,
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

    overs_res = supabase.table("overs").select("*").eq("match_id", match_id).order("over_number").execute()
    overs_list = overs_res.data or []

    deliveries_res = supabase.table("deliveries").select("*").eq("match_id", match_id).execute()
    deliveries_list = deliveries_res.data or []

    scorecard = await _build_scorecard(match_id)

    full_state = {
        "match": match_data,
        "team_a": team_a,
        "team_b": team_b,
        "squad_a": squad_a,
        "squad_b": squad_b,
        "innings": innings_list,
        "overs": overs_list,
        "deliveries": deliveries_list,
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

@router.post("/{match_id}/innings/start", response_model=APIResponse)
async def start_innings(match_id: str, innings: InningsStart, user: dict = Depends(get_current_user)):
    """Start a new innings within a match."""
    # Check if this innings already exists
    existing = (
        supabase.table("innings")
        .select("*")
        .eq("match_id", match_id)
        .eq("innings_number", innings.innings_number)
        .execute()
    )
    if existing.data:
        supabase.table("matches").update({"status": "playing"}).eq("id", match_id).execute()
        return APIResponse(message="Innings already started", data=existing.data[0])

    payload = {
        "match_id": match_id,
        "innings_number": innings.innings_number,
        "batting_team_id": innings.batting_team_id,
        "bowling_team_id": innings.bowling_team_id,
        "total_runs": 0,
        "total_wickets": 0,
        "overs_played": 0.0,
        "status": "playing",
        "extras_wide": 0,
        "extras_noball": 0,
        "extras_bye": 0,
        "extras_legbye": 0
    }
    response = supabase.table("innings").insert(payload).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Failed to start innings")

    # Update match status to playing
    supabase.table("matches").update({"status": "playing"}).eq("id", match_id).execute()

    return APIResponse(message="Innings started", data=response.data[0])


@router.post("/innings/{innings_id}/over/start", response_model=APIResponse)
async def start_over(innings_id: str, over: OverStart, user: dict = Depends(get_current_user)):
    """Start a new over within an innings."""
    # Retrieve the innings to get the match_id
    innings_res = supabase.table("innings").select("*").eq("id", innings_id).single().execute()
    if not innings_res.data:
        raise HTTPException(status_code=404, detail="Innings not found")
    
    innings_data = innings_res.data
    match_id = innings_data["match_id"]
    
    # Block if innings is already completed
    if innings_data.get("status") == "completed":
        raise HTTPException(status_code=400, detail="Innings is already completed")

    # Check consecutive bowler restriction (only if over_number > 1)
    if over.over_number > 1:
        prev_over_res = (
            supabase.table("overs")
            .select("bowler_id")
            .eq("innings_id", innings_id)
            .eq("over_number", over.over_number - 1)
            .execute()
        )
        if prev_over_res.data and prev_over_res.data[0]["bowler_id"] == over.bowler_id:
            raise HTTPException(status_code=400, detail="A bowler cannot bowl consecutive overs")
    
    # Check if the over already exists
    existing_over = (
        supabase.table("overs")
        .select("*")
        .eq("innings_id", innings_id)
        .eq("over_number", over.over_number)
        .execute()
    )
    if existing_over.data:
        current_over = existing_over.data[0]
        if current_over["bowler_id"] != over.bowler_id:
            update_res = (
                supabase.table("overs")
                .update({"bowler_id": over.bowler_id})
                .eq("id", current_over["id"])
                .execute()
            )
            return APIResponse(message="Over updated with new bowler", data=update_res.data[0])
        return APIResponse(message="Over already started", data=current_over)

    payload = {
        "innings_id": innings_id,
        "match_id": match_id,
        "over_number": over.over_number,
        "bowler_id": over.bowler_id,
        "runs": 0,
        "wickets": 0,
        "is_completed": False
    }
    res = supabase.table("overs").insert(payload).execute()
    if not res.data:
        raise HTTPException(status_code=400, detail="Failed to start over")
    return APIResponse(message="Over started successfully", data=res.data[0])


# ─── Ball-by-Ball ─────────────────────────────────────────────────────────────

@router.post("/innings/{innings_id}/deliver", response_model=APIResponse)
async def record_delivery(innings_id: str, ball: DeliveryCreate, user: dict = Depends(get_current_user)):
    """Record a single delivery and broadcast scorecard update."""
    # 1. Fetch innings
    innings_res = supabase.table("innings").select("*").eq("id", innings_id).single().execute()
    if not innings_res.data:
        raise HTTPException(status_code=404, detail="Innings not found")
    innings = innings_res.data
    match_id = innings["match_id"]
    
    # Block if innings is already completed
    if innings.get("status") == "completed":
        raise HTTPException(status_code=400, detail="Innings is already completed")
    
    # 2. Fetch current active over
    overs_res = (
        supabase.table("overs")
        .select("*")
        .eq("innings_id", innings_id)
        .eq("is_completed", False)
        .execute()
    )
    if not overs_res.data:
        raise HTTPException(status_code=400, detail="No active over found. Please start an over first.")
    active_over = overs_res.data[0]
    over_id = active_over["id"]
    over_number = active_over["over_number"]
    
    # 3. Calculate ball numbers in current over
    prev_deliveries_res = (
        supabase.table("deliveries")
        .select("id, extra_type")
        .eq("over_id", over_id)
        .execute()
    )
    prev_deliveries = prev_deliveries_res.data or []
    raw_ball_number = len(prev_deliveries) + 1
    
    legal_balls_so_far = sum(
        1 for d in prev_deliveries 
        if d.get("extra_type") not in ["wide", "noball"]
    )
    
    is_legal = ball.extra_type not in ["wide", "noball"]
    ball_number = legal_balls_so_far + 1 if is_legal else legal_balls_so_far
    
    # 4. Insert delivery row
    total_runs = (innings.get("total_runs") or 0) + ball.runs_batsman + ball.runs_extras
    
    delivery_payload = {
        "match_id": match_id,
        "innings_id": innings_id,
        "over_id": over_id,
        "over_number": over_number,
        "ball_number": ball_number,
        "raw_ball_number": raw_ball_number,
        "batsman_id": ball.batsman_id,
        "non_striker_id": ball.non_striker_id,
        "bowler_id": ball.bowler_id,
        "runs_batsman": ball.runs_batsman,
        "runs_extras": ball.runs_extras,
        "extra_type": ball.extra_type,
        "is_wicket": ball.is_wicket,
        "wicket_type": ball.wicket_type,
        "dismissed_id": ball.dismissed_id,
    }
    
    delivery_res = supabase.table("deliveries").insert(delivery_payload).execute()
    if not delivery_res.data:
        raise HTTPException(status_code=400, detail="Failed to insert delivery record")
        
    # 5. Update overs table
    bowler_runs = ball.runs_batsman + (ball.runs_extras if ball.extra_type in ["wide", "noball"] else 0)
    bowler_wicket = 1 if (ball.is_wicket and ball.wicket_type in ["bowled", "caught", "lbw", "stumped", "hit_wicket"]) else 0
    
    legal_balls_in_over = legal_balls_so_far + (1 if is_legal else 0)
    is_over_completed = (legal_balls_in_over == 6)
    
    updated_over_runs = (active_over.get("runs") or 0) + bowler_runs
    updated_over_wickets = (active_over.get("wickets") or 0) + bowler_wicket
    
    supabase.table("overs").update({
        "runs": updated_over_runs,
        "wickets": updated_over_wickets,
        "is_completed": is_over_completed
    }).eq("id", over_id).execute()
    
    # 6. Update innings table
    updated_innings_runs = total_runs
    updated_innings_wickets = (innings.get("total_wickets") or 0) + (1 if ball.is_wicket else 0)
    
    extras_payload = {}
    if ball.extra_type == "wide":
        extras_payload["extras_wide"] = (innings.get("extras_wide") or 0) + ball.runs_extras
    elif ball.extra_type == "noball":
        extras_payload["extras_noball"] = (innings.get("extras_noball") or 0) + ball.runs_extras
    elif ball.extra_type == "bye":
        extras_payload["extras_bye"] = (innings.get("extras_bye") or 0) + ball.runs_extras
    elif ball.extra_type == "legbye":
        extras_payload["extras_legbye"] = (innings.get("extras_legbye") or 0) + ball.runs_extras
        
    # Calculate completed overs count
    completed_overs_count_res = (
        supabase.table("overs")
        .select("id", count=CountMethod.exact)
        .eq("innings_id", innings_id)
        .eq("is_completed", True)
        .execute()
    )
    completed_overs_count = completed_overs_count_res.count or 0
    
    if is_over_completed:
        overs_played = float(completed_overs_count)
    else:
        overs_played = completed_overs_count + (legal_balls_in_over / 10.0)
        
    innings_update_payload = {
        "total_runs": updated_innings_runs,
        "total_wickets": updated_innings_wickets,
        "overs_played": overs_played,
        **extras_payload
    }
    
    # Check for innings completion
    is_innings_completed = False
    is_match_completed = False
    
    # Get batting squad size
    batting_team_id = innings["batting_team_id"]
    squad_count_res = (
        supabase.table("match_squads")
        .select("id", count=CountMethod.exact)
        .eq("match_id", match_id)
        .eq("team_id", batting_team_id)
        .execute()
    )
    batting_squad_size = squad_count_res.count or 11
    max_wickets = max(batting_squad_size - 1, 1)
    
    # 1. Check all-out
    if updated_innings_wickets >= max_wickets:
        is_innings_completed = True
        
    # 2. Check overs limit
    match_res = supabase.table("matches").select("*").eq("id", match_id).single().execute()
    match_data = match_res.data
    total_overs = match_data.get("total_overs") or 20
    
    if overs_played >= total_overs:
        is_innings_completed = True
        
    # 3. For Innings 2, check if target chased
    if innings["innings_number"] == 2:
        # Fetch innings 1
        inn1_res = (
            supabase.table("innings")
            .select("total_runs")
            .eq("match_id", match_id)
            .eq("innings_number", 1)
            .execute()
        )
        if inn1_res.data:
            inn1_runs = inn1_res.data[0]["total_runs"] or 0
            if updated_innings_runs > inn1_runs:
                is_innings_completed = True
                is_match_completed = True
            elif is_innings_completed:
                # Innings 2 completed (all out or overs limit reached) and target not chased
                is_match_completed = True
    
    if is_innings_completed:
        innings_update_payload["status"] = "completed"
        
    supabase.table("innings").update(innings_update_payload).eq("id", innings_id).execute()
    
    if is_match_completed:
        supabase.table("matches").update({"status": "completed"}).eq("id", match_id).execute()
    elif is_innings_completed:
        supabase.table("matches").update({"status": "innings_break"}).eq("id", match_id).execute()
    
    # 7. Get full computed scorecard
    scorecard = await _build_scorecard(match_id)
    
    # Broadcast updated scorecard to all WebSocket viewers
    if scorecard:
        await manager.broadcast(match_id, scorecard)
        
    return APIResponse(message="Delivery recorded successfully", data=scorecard)


@router.post("/innings/{innings_id}/wicket", response_model=APIResponse)
async def record_wicket(innings_id: str, ball: DeliveryCreate, user: dict = Depends(get_current_user)):
    """Record a wicket (aliases /deliver)."""
    return await record_delivery(innings_id, ball, user)


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
