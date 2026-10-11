"""
Player profile + stats routes.

Handles player CRUD, profile retrieval, per-player career stats,
and match history lookups.
"""

from email import message
from pydantic.deprecated.copy_internals import _get_value
from fastapi.openapi.utils import status_code_ranges
import logging
import uuid
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from typing import cast
from botocore.exceptions import ClientError

from models.schemas import (
    PlayerCreate,
    PlayerUpdate,
    PlayerResponse,
    APIResponse,
    PlayerMatchStats,
)
from services.db import supabase
from services.auth import get_current_user
from services.storage import upload_file, delete_file, key_from_url
from services.image_processing import process_avatar, InvalidImageError

logger = logging.getLogger(__name__)

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


@router.get("/search", response_model=APIResponse)
async def search_by_phone(phone: str, user: dict = Depends(get_current_user)):
    """Search for a registered user by phone number."""
    logger.info("Searching user by phone: %s", phone)
    
    cleaned = "".join([c for c in phone if c.isdigit() or c == "+"])
    
    query = supabase.table("users").select("id, first_name, last_name, phone, role, avatar_color").eq("phone", cleaned).execute()
    
    if not query.data and len(cleaned) == 10 and cleaned.isdigit():
        query = supabase.table("users").select("id, first_name, last_name, phone, role, avatar_color").eq("phone", f"+91{cleaned}").execute()
        
    if not query.data and cleaned.startswith("+91") and len(cleaned) > 3:
        query = supabase.table("users").select("id, first_name, last_name, phone, role, avatar_color").eq("phone", cleaned[3:]).execute()
        
    if not query.data:
        return APIResponse(message="No player found with this phone number", data=None)
        
    return APIResponse(message="Player found", data=query.data[0])


MAX_AVATAR_BYTES = 5 * 1024 * 1024
ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp"}

def _delete_quietly(key: str) -> None:
    """best-effort s3 delete; a leftover file must never fail the request"""
    try:
        delete_file(key)
    except ClientError:
        logger.warning("Failed to delete s3 object %s", key, exc_info=True)

def _get_avatar_url(user_id: str) -> str | None:
    res = supabase.table("users").select("avatar_url").eq("id", user_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")
    row = cast(dict, res.data[0])
    return row.get("avatar_url")

# plain `def` (not async): Pillow, boto3 and supabase are blocking, so FastAPI
# runs this in a worker thread instead of freezing the event loop.

@router.post("/me/avatar", response_model=APIResponse)
def upload_my_avatar(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    """upload or replace the current user's profile photo"""
    user_id = user["sub"]

    # cheap early rejection; the real check is Pillow below (content_type can be faked)
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG or WebP images are allowed.")

    #Read one byte past the limit to detect oversize without loading huge files
    data = file.file.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Image must be 5MB or smaller")

    try:
        webp = process_avatar(data)
    except InvalidImageError as e:
        raise HTTPException(status_code=400, detail=str(e))

    old_url = _get_avatar_url(user_id)
    key = f"avatars/{user_id}/{uuid.uuid4().hex}.webp"
    
    try:
        new_url = upload_file(key, webp, "image/webp")
    except ClientError:
        logger.exception("S3 upload failed for user %s", user_id)
        raise HTTPException(status_code=502, detail="Could not store image. Please try again.")
    
    try: supabase.table("users").update({"avatar_url": new_url}).eq("id", user_id).execute()
    except Exception:
        logger.exception("DB update failed after avatar upload for user %s", user_id)
        _delete_quietly(key) # don't leave an orphaned file behind in s3
        raise HTTPException(status_code=500, detail="Could not save profile photo")

    old_key = key_from_url(old_url)
    if old_key:
        _delete_quietly(old_key)
    
    logger.info("Avatar updated for user %s", user_id)
    return APIResponse(message="Avatar updated", data={"avatar_url": new_url})


@router.delete("/me/avatar", response_model=APIResponse)
def delete_my_avatar(user: dict = Depends(get_current_user)):
    """Remove the current user's profile photo (falls back to initials)"""
    user_id = user["sub"]
    old_url = _get_avatar_url(user_id)
    
    supabase.table("users").update({"avatar_url": None}).eq("id", user_id).execute()

    old_key = key_from_url(old_url)
    if old_key:
        _delete_quietly(old_key)
    return APIResponse(message="Avatar removed", data={"avatar_url": None})



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
    """Get aggregated career stats dynamically for a player."""
    try:
        # 1. Fetch match squads to count matches played
        ms_res = supabase.table("match_squads").select("match_id").eq("user_id", player_id).execute()
        matches_played = len(ms_res.data or [])

        # 2. Fetch batting deliveries
        bat_res = supabase.table("deliveries").select("match_id, runs_batsman, extra_type, is_wicket, dismissed_id").eq("batsman_id", player_id).execute()
        bat_d = bat_res.data or []

        # 3. Fetch bowling deliveries
        bowl_res = supabase.table("deliveries").select("match_id, runs_batsman, runs_extras, extra_type, is_wicket, wicket_type, over_id").eq("bowler_id", player_id).execute()
        bowl_d = bowl_res.data or []

        # Calculate Batting stats
        batting_runs = sum(d.get("runs_batsman") or 0 for d in bat_d)
        balls_faced = sum(1 for d in bat_d if d.get("extra_type") != "wide")
        fours = sum(1 for d in bat_d if d.get("runs_batsman") == 4)
        sixes = sum(1 for d in bat_d if d.get("runs_batsman") == 6)
        dismissals = sum(1 for d in bat_d if d.get("is_wicket") and d.get("dismissed_id") == player_id)

        bat_matches = set(d["match_id"] for d in bat_d)
        batting_innings = len(bat_matches)

        # High score & milestones (50s, 100s)
        runs_per_match = {}
        for d in bat_d:
            mid = d["match_id"]
            runs_per_match[mid] = runs_per_match.get(mid, 0) + (d.get("runs_batsman") or 0)
        scores = list(runs_per_match.values())
        high_score = max(scores, default=0)
        fifties = sum(1 for s in scores if s >= 50 and s < 100)
        hundreds = sum(1 for s in scores if s >= 100)

        strike_rate = round((batting_runs / max(balls_faced, 1)) * 100, 1)
        batting_average = round(batting_runs / max(dismissals, 1), 1)

        # Calculate Bowling stats
        bowl_matches = set(d["match_id"] for d in bowl_d)
        bowling_innings = len(bowl_matches)

        wickets = sum(1 for d in bowl_d if d.get("is_wicket") and d.get("wicket_type") not in ["runout", "retired"])
        runs_conceded = sum((d.get("runs_batsman") or 0) + (d.get("runs_extras") or 0) for d in bowl_d if d.get("extra_type") in ["wide", "noball", None])
        balls_bowled = sum(1 for d in bowl_d if d.get("extra_type") not in ["wide", "noball"])

        bowling_avg = round(runs_conceded / max(wickets, 1), 1)
        economy = round((runs_conceded / max(balls_bowled, 1)) * 6, 2)

        # Best Bowling
        w_per_match = {}
        r_per_match = {}
        for d in bowl_d:
            mid = d["match_id"]
            is_w = d.get("is_wicket") and d.get("wicket_type") not in ["runout", "retired"]
            if is_w:
                w_per_match[mid] = w_per_match.get(mid, 0) + 1
            if d.get("extra_type") in ["wide", "noball", None]:
                r_per_match[mid] = r_per_match.get(mid, 0) + ((d.get("runs_batsman") or 0) + (d.get("runs_extras") or 0))

        best_w = 0
        best_r = 999
        for mid in bowl_matches:
            w = w_per_match.get(mid, 0)
            r = r_per_match.get(mid, 0)
            if w > best_w:
                best_w = w
                best_r = r
            elif w == best_w and r < best_r:
                best_r = r
        best_bowling = f"{best_w}/{best_r}" if bowl_matches else "0/0"

        # Maidens count
        over_runs = {}
        for d in bowl_d:
            oid = d["over_id"]
            if d.get("extra_type") in ["wide", "noball", None]:
                over_runs[oid] = over_runs.get(oid, 0) + ((d.get("runs_batsman") or 0) + (d.get("runs_extras") or 0))
        maidens = sum(1 for oid, runs in over_runs.items() if runs == 0)

        dot_balls = sum(1 for d in bowl_d if (d.get("runs_batsman") or 0) == 0 and d.get("extra_type") is None)
        wides = sum(1 for d in bowl_d if d.get("extra_type") == "wide")

        career = {
            "matches": matches_played,
            "runs": batting_runs,
            "balls_faced": balls_faced,
            "fours": fours,
            "sixes": sixes,
            "highest_score": str(high_score),
            "wickets": wickets,
            "catches": 0,
            "batting_average": batting_average,
            "strike_rate": strike_rate,
            "batting_innings": batting_innings,
            "fifties": fifties,
            "hundreds": hundreds,
            "bowling_innings": bowling_innings,
            "bowling_runs_conceded": runs_conceded,
            "balls_bowled": balls_bowled,
            "bowling_average": bowling_avg,
            "economy": economy,
            "best_bowling": best_bowling,
            "maidens": maidens,
            "dot_balls": dot_balls,
            "wides": wides,
        }
        return APIResponse(message="Player stats fetched", data=career)
    except Exception as e:
        logger.error("Error fetching player stats: %s", e)
        raise HTTPException(status_code=500, detail="Failed to calculate player stats")


@router.get("/{player_id}/matches", response_model=APIResponse)
async def get_player_match_history(player_id: str, limit: int = 20):
    """Get per-match stats history for a player dynamically."""
    try:
        # Fetch match squads
        ms_res = supabase.table("match_squads").select("match_id, team_id").eq("user_id", player_id).execute()
        squads = ms_res.data or []
        
        results = []
        for sq in squads[:limit]:
            mid = sq["match_id"]
            team_id = sq["team_id"]
            
            # Fetch match details
            m_res = supabase.table("matches").select("*, team_a:team_a_id(name), team_b:team_b_id(name)").eq("id", mid).execute()
            if not m_res.data:
                continue
            m = m_res.data[0]
            
            # Opponent
            is_team_a = m.get("team_a_id") == team_id
            my_team_name = m.get("team_a", {}).get("name") if is_team_a else m.get("team_b", {}).get("name")
            opp_team_name = m.get("team_b", {}).get("name") if is_team_a else m.get("team_a", {}).get("name")
            
            # Did we win?
            won = m.get("winner_id") == team_id
            
            # Batting stats in this match
            bat_res = supabase.table("deliveries").select("runs_batsman, extra_type").eq("match_id", mid).eq("batsman_id", player_id).execute()
            bat_d = bat_res.data or []
            runs = sum(d.get("runs_batsman") or 0 for d in bat_d)
            balls = sum(1 for d in bat_d if d.get("extra_type") != "wide")
            
            # Bowling stats in this match
            bowl_res = supabase.table("deliveries").select("runs_batsman, runs_extras, extra_type, is_wicket, wicket_type").eq("match_id", mid).eq("bowler_id", player_id).execute()
            bowl_d = bowl_res.data or []
            wkts = sum(1 for d in bowl_d if d.get("is_wicket") and d.get("wicket_type") not in ["runout", "retired"])
            runs_conc = sum((d.get("runs_batsman") or 0) + (d.get("runs_extras") or 0) for d in bowl_d if d.get("extra_type") in ["wide", "noball", None])
            
            results.append({
                "match_id": mid,
                "team_name": my_team_name,
                "opponent_name": opp_team_name,
                "date": m.get("created_at") or "",
                "format": m.get("match_type") or "T20",
                "won": won,
                "batting_runs": runs,
                "batting_balls": balls,
                "bowling_wickets": wkts,
                "bowling_runs_conceded": runs_conc,
                "is_mom": m.get("man_of_the_match_id") == player_id
            })
            
        return APIResponse(message="Match history fetched", data=results)
    except Exception as e:
        logger.error("Error fetching match history: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load match history")
