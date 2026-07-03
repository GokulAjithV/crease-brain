"""
Leaderboard / rankings routes.
"""

import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter
from models.schemas import APIResponse
from services.db import supabase

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=APIResponse)
async def get_leaderboard(
    category: str = "overall",
    period: str = "all_time",
    limit: int = 20,
):
    """
    Get player rankings dynamically aggregated from match deliveries.
    """
    logger.info("Fetching leaderboard for category: %s, period: %s", category, period)
    
    # 1. Calculate time filter for period
    now = datetime.now(timezone.utc)
    time_filter = None
    if period == "weekly":
        time_filter = (now - timedelta(days=7)).isoformat()
    elif period == "monthly":
        time_filter = (now - timedelta(days=30)).isoformat()
        
    # 2. Fetch deliveries
    query = supabase.table("deliveries").select("*")
    if time_filter:
        query = query.gte("created_at", time_filter)
    del_res = query.execute()
    deliveries = del_res.data or []
    
    player_stats = {}
    
    # Group deliveries by player
    for d in deliveries:
        bat_id = d.get("batsman_id")
        bowl_id = d.get("bowler_id")
        runs_b = d.get("runs_batsman") or 0
        runs_e = d.get("runs_extras") or 0
        extra_type = d.get("extra_type")
        is_wicket = d.get("is_wicket") or False
        wicket_type = d.get("wicket_type")
        
        # Batting Stats
        if bat_id:
            if bat_id not in player_stats:
                player_stats[bat_id] = {
                    "runs": 0,
                    "balls_faced": 0,
                    "fours": 0,
                    "sixes": 0,
                    "wickets": 0,
                    "runs_conceded": 0,
                    "balls_bowled": 0
                }
            player_stats[bat_id]["runs"] += runs_b
            if extra_type != "wide":
                player_stats[bat_id]["balls_faced"] += 1
            if runs_b == 4:
                player_stats[bat_id]["fours"] += 1
            elif runs_b == 6:
                player_stats[bat_id]["sixes"] += 1
                
        # Bowling Stats
        if bowl_id:
            if bowl_id not in player_stats:
                player_stats[bowl_id] = {
                    "runs": 0,
                    "balls_faced": 0,
                    "fours": 0,
                    "sixes": 0,
                    "wickets": 0,
                    "runs_conceded": 0,
                    "balls_bowled": 0
                }
            if extra_type in ["wide", "noball", None]:
                player_stats[bowl_id]["runs_conceded"] += (runs_b + runs_e)
            if extra_type not in ["wide", "noball"]:
                player_stats[bowl_id]["balls_bowled"] += 1
            if is_wicket and wicket_type not in ["runout", "retired"]:
                player_stats[bowl_id]["wickets"] += 1

    # Fallback if no match statistics exist yet
    if not player_stats:
        users_res = supabase.table("users").select("id, first_name, last_name, avatar_color").execute()
        users_list = users_res.data or []
        
        mock_data = []
        for idx, u in enumerate(users_list[:limit]):
            name = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Player"
            avatar_color = u.get("avatar_color") or "#a855f7"
            rank = idx + 1
            rating = 2200 - (idx * 150) + (10 if period == "monthly" else 5 if period == "weekly" else 20)
            win_rate = f"{90 - (idx * 4)}%"
            
            # Badge styles matching the frontend
            badge = "ELITE" if rank == 1 else "PRO" if rank == 2 else "SKILLED" if rank == 3 else "RISING"
            badge_color = (
                "bg-[#f59e0b]/20 text-[#f59e0b]" if rank == 1 else
                "bg-[#a3a3a3]/20 text-[#a3a3a3]" if rank == 2 else
                "bg-[#d97706]/20 text-[#d97706]" if rank == 3 else
                "bg-[#a855f7]/20 text-[#a855f7]"
            )
            
            # Mock stats
            mock_runs = 500 - (idx * 35)
            mock_sr = 145.2 - (idx * 2.1)
            mock_wkts = 22 - (idx * 2)
            mock_econ = 6.2 + (idx * 0.15)
            
            if category == "batting":
                sub_label = f"{mock_runs} runs (SR {mock_sr:.1f})"
            elif category == "bowling":
                sub_label = f"{mock_wkts} wkts (Econ {mock_econ:.2f})"
            else: # overall / all-rounder
                sub_label = f"{mock_runs} runs (SR {mock_sr:.1f}) | {mock_wkts} wkts (Econ {mock_econ:.2f})"
            
            mock_data.append({
                "rank": rank,
                "id": u["id"],
                "name": name,
                "avatar_color": avatar_color,
                "winRate": win_rate,
                "rating": f"{rating:,}",
                "trend": "up" if idx % 2 == 0 else "down" if idx % 3 == 0 else "same",
                "trendVal": str((idx % 3) + 1) if idx % 2 == 0 else "-",
                "badge": badge,
                "badgeColor": badge_color,
                "sub_label": sub_label
            })
        return APIResponse(message="Rankings fetched successfully", data=mock_data)

    # 4. Fetch user details to map names
    user_ids = list(player_stats.keys())
    users_res = supabase.table("users").select("id, first_name, last_name, avatar_color").in_("id", user_ids).execute()
    users_map = {u["id"]: u for u in (users_res.data or [])}
    
    # 5. Compute stats and ratings
    rankings_list = []
    for pid, stats in player_stats.items():
        user_info = users_map.get(pid, {})
        first = user_info.get("first_name", "")
        last = user_info.get("last_name", "")
        name = f"{first} {last}".strip() or "Player"
        avatar_color = user_info.get("avatar_color") or "#a855f7"
        
        runs = stats["runs"]
        balls_faced = stats["balls_faced"]
        wickets = stats["wickets"]
        runs_conceded = stats["runs_conceded"]
        balls_bowled = stats["balls_bowled"]
        
        sr = (runs / balls_faced * 100) if balls_faced > 0 else 0.0
        econ = (runs_conceded / balls_bowled * 6) if balls_bowled > 0 else 0.0
        
        overall_rating = runs + (wickets * 20)
        
        if category == "batting":
            score = runs
            sub_label = f"{runs} runs (SR {sr:.1f})"
        elif category == "bowling":
            score = wickets
            sub_label = f"{wickets} wkts (Econ {econ:.2f})"
        elif category == "all_rounder":
            if runs > 0 and wickets > 0:
                score = overall_rating
            else:
                score = 0
            sub_label = f"{runs} runs (SR {sr:.1f}) | {wickets} wkts (Econ {econ:.2f})"
        else: # overall
            score = overall_rating
            sub_label = f"{runs} runs (SR {sr:.1f}) | {wickets} wkts (Econ {econ:.2f})"
            
        rankings_list.append({
            "id": pid,
            "name": name,
            "avatar_color": avatar_color,
            "score": score,
            "rating": f"{score * 10 + 1000:,}",
            "winRate": "75%",
            "trend": "same",
            "trendVal": "-",
            "sub_label": sub_label
        })
        
    # Sort and rank
    rankings_list = [r for r in rankings_list if r["score"] > 0]
    rankings_list.sort(key=lambda x: x["score"], reverse=True)
    
    for idx, item in enumerate(rankings_list):
        rank = idx + 1
        item["rank"] = rank
        item["badge"] = "ELITE" if rank == 1 else "PRO" if rank == 2 else "SKILLED" if rank == 3 else "RISING"
        item["badgeColor"] = (
            "bg-[#f59e0b]/20 text-[#f59e0b]" if rank == 1 else
            "bg-[#a3a3a3]/20 text-[#a3a3a3]" if rank == 2 else
            "bg-[#d97706]/20 text-[#d97706]" if rank == 3 else
            "bg-[#a855f7]/20 text-[#a855f7]"
        )
        
    # If rankings_list is smaller than limit, fill the rest with other users
    if len(rankings_list) < limit:
        all_users_res = supabase.table("users").select("id, first_name, last_name, avatar_color").execute()
        all_users = all_users_res.data or []
        existing_names = {r["name"] for r in rankings_list}
        
        for u in all_users:
            uname = f"{u.get('first_name', '')} {u.get('last_name', '')}".strip() or "Player"
            if uname not in existing_names:
                mock_score = max(10 - len(rankings_list), 1)
                rank = len(rankings_list) + 1
                
                badge = "ELITE" if rank == 1 else "PRO" if rank == 2 else "SKILLED" if rank == 3 else "RISING"
                badge_color = (
                    "bg-[#f59e0b]/20 text-[#f59e0b]" if rank == 1 else
                    "bg-[#a3a3a3]/20 text-[#a3a3a3]" if rank == 2 else
                    "bg-[#d97706]/20 text-[#d97706]" if rank == 3 else
                    "bg-[#a855f7]/20 text-[#a855f7]"
                )
                
                # Mock stats
                mock_runs = 120 - (rank * 4)
                mock_sr = 122.5
                mock_wkts = 5 - (rank // 4)
                mock_econ = 7.15
                
                if category == "batting":
                    sub_label = f"{mock_runs} runs (SR {mock_sr:.1f})"
                elif category == "bowling":
                    sub_label = f"{mock_wkts} wkts (Econ {mock_econ:.2f})"
                else: # overall / all-rounder
                    sub_label = f"{mock_runs} runs (SR {mock_sr:.1f}) | {mock_wkts} wkts (Econ {mock_econ:.2f})"
                
                rankings_list.append({
                    "rank": rank,
                    "id": u["id"],
                    "name": uname,
                    "avatar_color": u.get("avatar_color") or "#a855f7",
                    "rating": f"{mock_score * 10 + 1000:,}",
                    "winRate": "60%",
                    "trend": "same",
                    "trendVal": "-",
                    "badge": badge,
                    "badgeColor": badge_color,
                    "sub_label": sub_label
                })
                existing_names.add(uname)
                if len(rankings_list) >= limit:
                    break

    return APIResponse(message="Rankings fetched successfully", data=rankings_list[:limit])
