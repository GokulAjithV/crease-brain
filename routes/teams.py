from fastapi import APIRouter, Depends, HTTPException, status
from typing import List
from models.schemas import TeamCreate, TeamPlayerAdd, APIResponse
from services.db import supabase
from services.auth import get_current_user

router = APIRouter(prefix="/teams", tags=["teams"])

@router.post("", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def create_team(team: TeamCreate, user: dict = Depends(get_current_user)):
    user_id = user.get("sub")
    
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
        raise HTTPException(status_code=500, detail="Failed to create team")
        
    return APIResponse(message="Team created successfully", data=response.data[0])

@router.get("", response_model=APIResponse)
async def list_teams(user: dict = Depends(get_current_user)):
    # Get teams created by the user or where the user is a player
    # For now, just returning teams created by the user
    user_id = user.get("sub")
    response = supabase.table("teams").select("*").eq("created_by", user_id).execute()
    return APIResponse(message="Teams retrieved", data=response.data or [])

@router.post("/{team_id}/players", response_model=APIResponse, status_code=status.HTTP_201_CREATED)
async def add_player_to_team(team_id: str, player: TeamPlayerAdd, user: dict = Depends(get_current_user)):
    player_id = player.player_id
    
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
            raise HTTPException(status_code=500, detail="Failed to create guest user account")
            
        player_id = guest_res.data[0]["id"]
        
    # Check if team exists
    team_res = supabase.table("teams").select("id").eq("id", team_id).execute()
    if not team_res.data:
        raise HTTPException(status_code=404, detail="Team not found")
        
    # Check if player is already in the team
    existing = supabase.table("team_players").select("id").eq("team_id", team_id).eq("user_id", player_id).execute()
    if existing.data:
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
        raise HTTPException(status_code=500, detail="Failed to add player to team")
        
    return APIResponse(message="Player added to team successfully", data=tp_res.data[0])
