import logging
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from pydantic import BaseModel
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

class TeamUpdate(BaseModel):
    name: str | None = None
    avatar_color: str | None = None
    city: str | None = None
    captain_can_edit: bool | None = None
    is_active: bool | None = None


@router.get("", response_model=APIResponse)
async def list_teams(scope: str | None = None, active_only: bool = False, user: dict = Depends(get_current_user)):
    """
    List teams.

    Args:
        scope: 'all' to return every team, otherwise only the current user's teams.
        active_only: if true, returns only active teams.
    """
    user_id = user.get("sub")
    logger.info("Fetching teams for user %s (scope=%s, active_only=%s)", user_id, scope, active_only)

    query = supabase.table("teams").select("*, team_players(count)")
    if scope != "all":
        query = query.eq("created_by", user_id)
        
    if active_only:
        query = query.eq("is_active", True)

    response = query.order("name").execute()

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

@router.post("/join/{token}", response_model=APIResponse)
async def join_via_token(token: str, user: dict = Depends(get_current_user)):
    """Join a team via an invite token."""
    user_id = user.get("sub")
    logger.info("User %s is attempting to join team via token", user_id)
    
    # Fetch team with this token
    res = supabase.table("teams").select("*").eq("invite_token", token).execute()
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invite link or token"
        )
        
    team = res.data[0]
    expires_at_str = team.get("invite_expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invite link has expired"
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to parse invite expiration: %s", e)
            
    # Check if user is already a member
    existing = supabase.table("team_players").select("id").eq("team_id", team["id"]).eq("user_id", user_id).execute()
    if existing.data:
        return APIResponse(
            message="You are already a member of this team",
            data={"team_id": team["id"], "team": team}
        )
        
    # Join the team as a player
    tp_payload = {
        "team_id": team["id"],
        "user_id": user_id,
        "role": "player",
        "batting_style": "right_hand",
        "is_captain": False,
        "is_vc": False
    }
    
    tp_res = supabase.table("team_players").insert(tp_payload).execute()
    if not tp_res.data:
        logger.error("Failed to add user %s to team %s via token", user_id, team["id"])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to join the team"
        )
        
    logger.info("User %s successfully joined team %s via invite token", user_id, team["id"])
    return APIResponse(
        message="Joined team successfully",
        data={"team_id": team["id"], "team": team}
    )

@router.post("/{team_id}/invite", response_model=APIResponse)
async def generate_invite(team_id: str, user: dict = Depends(get_current_user)):
    """Generate or retrieve a team invite token."""
    # Check if team exists
    res = supabase.table("teams").select("*").eq("id", team_id).execute()
    if not res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
        
    team = res.data[0]
    token = team.get("invite_token")
    expires_at_str = team.get("invite_expires_at")
    
    expires_at = None
    if token and expires_at_str:
        try:
            parsed_expires_at = datetime.fromisoformat(expires_at_str)
            if parsed_expires_at.tzinfo is None:
                parsed_expires_at = parsed_expires_at.replace(tzinfo=timezone.utc)
            if parsed_expires_at > datetime.now(timezone.utc):
                expires_at = parsed_expires_at
        except Exception:
            pass
            
    if expires_at is None:
        token = secrets.token_urlsafe(16)
        expires_at = datetime.now(timezone.utc) + timedelta(days=7)
        
        update_res = supabase.table("teams").update({
            "invite_token": token,
            "invite_expires_at": expires_at.isoformat()
        }).eq("id", team_id).execute()
        
        if not update_res.data:
            logger.error("Failed to save invite token for team %s", team_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save invite token"
            )
            
    return APIResponse(
        message="Invite token retrieved",
        data={
            "invite_token": token,
            "invite_expires_at": expires_at.isoformat()
        }
    )

@router.get("/{team_id}/players", response_model=APIResponse)
async def list_team_players(team_id: str, user: dict = Depends(get_current_user)):
    """List all players in a specific team."""
    logger.info("Fetching players for team %s", team_id)
    
    # Check if team exists
    team_res = supabase.table("teams").select("id, name").eq("id", team_id).execute()
    if not team_res.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )
        
    # Query team_players with user profile left join
    response = supabase.table("team_players").select(
        "role, batting_style, bowling_style, is_captain, is_vc, users(id, first_name, last_name, email, phone, avatar_color, is_guest)"
    ).eq("team_id", team_id).execute()
    
    # Format and sanitize response
    players = []
    for item in (response.data or []):
        user_data = item.get("users") or {}
        first_name = user_data.get("first_name", "")
        last_name = user_data.get("last_name", "")
        name = f"{first_name} {last_name}".strip()
        
        if not name:
            name = "Unknown Player"
            
        players.append({
            "id": user_data.get("id"),
            "first_name": first_name,
            "last_name": last_name,
            "name": name,
            "email": user_data.get("email"),
            "phone": user_data.get("phone"),
            "avatar_color": user_data.get("avatar_color") or "#7c3aed",
            "is_guest": user_data.get("is_guest") or False,
            "role": item.get("role"),
            "batting_style": item.get("batting_style"),
            "bowling_style": item.get("bowling_style"),
            "is_captain": item.get("is_captain"),
            "is_vc": item.get("is_vc")
        })
        
    return APIResponse(
        message="Team players retrieved",
        data={
            "team_name": team_res.data[0].get("name"),
            "players": players
        }
    )


@router.delete("/{team_id}/players/{player_id}", response_model=APIResponse)
async def remove_player_from_team(team_id: str, player_id: str, user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    logger.info("User %s is removing player %s from team %s", user_id, player_id, team_id)
    
    # 1. Fetch team details
    team_res = supabase.table("teams").select("created_by").eq("id", team_id).execute()
    if not team_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        
    # Check permissions
    team_creator = team_res.data[0].get("created_by")
    if team_creator != user_id and player_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to remove this player"
        )
        
    # 2. Delete player from team
    res = supabase.table("team_players").delete().eq("team_id", team_id).eq("user_id", player_id).execute()
    if not res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found in this team")
        
    logger.info("Successfully removed player %s from team %s", player_id, team_id)
    return APIResponse(message="Player removed from team successfully", data=res.data[0])


@router.put("/{team_id}", response_model=APIResponse)
async def update_team(team_id: str, team: TeamUpdate, user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    logger.info("User %s is updating team %s", user_id, team_id)
    
    # 1. Fetch team details
    team_res = supabase.table("teams").select("*").eq("id", team_id).execute()
    if not team_res.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        
    # Check permissions (must be team creator)
    if team_res.data[0].get("created_by") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the team creator can edit team details"
        )
        
    payload = {}
    if team.name is not None:
        payload["name"] = team.name
        # Update initials
        words = team.name.split()
        payload["initials"] = "".join([w[0].upper() for w in words[:2]])
    if team.avatar_color is not None:
        payload["avatar_color"] = team.avatar_color
    if team.city is not None:
        payload["city"] = team.city
    if team.captain_can_edit is not None:
        payload["captain_can_edit"] = team.captain_can_edit
    if team.is_active is not None:
        payload["is_active"] = team.is_active
        
    if not payload:
        return APIResponse(message="No updates provided", data=team_res.data[0])
        
    response = supabase.table("teams").update(payload).eq("id", team_id).execute()
    if not response.data:
        raise HTTPException(status_code=500, detail="Failed to update team")
        
    logger.info("Successfully updated team %s", team_id)
    return APIResponse(message="Team updated successfully", data=response.data[0])

