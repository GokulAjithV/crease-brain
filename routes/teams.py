import logging
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.schemas import TeamCreate, TeamPlayerAdd, APIResponse
from services.db import supabase
from services.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])

@router.post("", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def create_team(team: TeamCreate, user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    logger.info("User %s is creating a new team: %s", user_id, team.name)
    
    # Generate initials (first letters of first two words)
    words = team.name.split()
    initials = "".join([w[0].upper() for w in words[:2]])
    
    payload = {
        "name": team.name,
        "initials": initials,
        "avatar_color": team.avatar_color or "#7c3aed",
        "city": team.city,
        "captain_can_edit": team.captain_can_edit,
        "created_by": user_id
    }
    
    response = supabase.table("teams").insert(payload).execute()
    if not response.data:
        logger.error("Database insert failed for team creation by user %s", user_id)
        raise HTTPException(status_code=500, detail="Failed to create team")
        
    logger.info("Successfully created team %s (ID: %s)", team.name, response.data[0].get("id"))
    return APIResponse(message="Team created successfully", data=response.data[0])

@router.get("", response_model=APIResponse)
async def list_teams(scope: str | None = None, user: dict = Depends(get_current_user)):
    """
    List teams.

    Args:
        scope: 'all' to return every team, otherwise only the current user's teams.
    """
    user_id = user.get("sub")
    logger.info("Fetching teams for user %s (scope=%s)", user_id, scope)

    if scope == "all":
        response = supabase.table("teams").select("*, team_players(count)").order("name").execute()
    else:
        response = supabase.table("teams").select("*, team_players(count)").eq("created_by", user_id).order("name").execute()

    # Flatten the player_count from the nested team_players aggregate
    teams = []
    for team in (response.data or []):
        tp = team.pop("team_players", [])
        team["player_count"] = tp[0]["count"] if tp else 0
        teams.append(team)

    return APIResponse(message="Teams retrieved", data=teams)

@router.post("/{team_id}/players", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def add_player_to_team(team_id: str, player: TeamPlayerAdd, user: dict = Depends(get_current_user)):
    player_id = player.player_id
    logger.info("Adding player %s (guest: %s) to team %s", player_id, player.guest_name, team_id)
    
    # Handle Guest Player Logic
    if not player_id:
        if not player.guest_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Must provide either player_id or guest_name"
            )
            
        # Parse guest name
        name_parts = player.guest_name.strip().split(maxsplit=1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        
        # Pre-create a stub user account
        guest_payload = {
            "first_name": first_name,
            "last_name": last_name,
            "phone": player.guest_phone,
            "is_guest": True,
            "role": "player",
            "avatar_color": "#a855f7" # Distinct color for guests maybe
        }
        
        guest_res = supabase.table("users").insert(guest_payload).execute()
        if not guest_res.data:
            logger.error("Failed to create guest user for name %s", player.guest_name)
            raise HTTPException(status_code=500, detail="Failed to create guest user account")
            
        player_id = guest_res.data[0]["id"]
        logger.info("Successfully created guest user with ID %s", player_id)
        
    # Check if team exists
    team_res = supabase.table("teams").select("id").eq("id", team_id).execute()
    if not team_res.data:
        logger.warning("Attempted to add player to non-existent team %s", team_id)
        raise HTTPException(status_code=404, detail="Team not found")
        
    # Check if player is already in the team
    existing = supabase.table("team_players").select("id").eq("team_id", team_id).eq("user_id", player_id).execute()
    if existing.data:
        logger.warning("Player %s is already in team %s", player_id, team_id)
        raise HTTPException(status_code=400, detail="Player is already in this team")
        
    # Add player to team
    tp_payload = {
        "team_id": team_id,
        "user_id": player_id,
        "role": player.role,
        "batting_style": player.batting_style,
        "bowling_style": player.bowling_style,
        "is_captain": player.is_captain,
        "is_vc": player.is_vice_captain
    }
    
    tp_res = supabase.table("team_players").insert(tp_payload).execute()
    if not tp_res.data:
        logger.error("Database insert failed when adding player %s to team %s", player_id, team_id)
        raise HTTPException(status_code=500, detail="Failed to add player to team")
        
    logger.info("Successfully added player %s to team %s", player_id, team_id)
    return APIResponse(message="Player added to team successfully", data=tp_res.data[0])
