"""
RAG-based chat and strategic scouting endpoints.

Accepts natural-language queries about matches / players and returns
AI-generated answers grounded in match data, rosters, H2H player matchups,
and venue histories using Google Gemini-2.5-flash.
"""

import os
import logging
import httpx
from fastapi import APIRouter, HTTPException
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from models.schemas import ChatResponse, APIResponse
from services.db import supabase

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

class ScoutRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    team_a_id: Optional[str] = None
    team_b_id: Optional[str] = None
    venue: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None

@router.post("/", response_model=ChatResponse)
async def chat(request: ScoutRequest):
    """
    General RAG-based chat query about matches/players.
    If no match/player context is supplied, answers using general data.
    """
    return await chat_scout(request)

@router.post("/scout", response_model=ChatResponse)
async def chat_scout(request: ScoutRequest):
    """
    Scouting analysis endpoint. Aggregates roster information, dynamic player-vs-player
    H2H matchup metrics, and venue statistics before querying Google's Gemini-2.5-flash.
    """
    logger.info("Strategic scouting query: %s", request.query)
    
    # 1. Fetch team names and rosters
    team_a_name = "Team A"
    team_b_name = "Team B"
    roster_details = []
    h2h_details = []
    venue_details = []
    
    names_map = {}
    player_roles = {}
    player_batting = {}
    player_bowling = {}
    
    all_player_ids = []
    players_a = []
    players_b = []

    # Get players from team_a
    if request.team_a_id:
        try:
            team_res = supabase.table("teams").select("name").eq("id", request.team_a_id).single().execute()
            if team_res.data:
                team_a_name = team_res.data.get("name", "Team A")
            tp_res = supabase.table("team_players").select("user_id, role, batting_style, bowling_style").eq("team_id", request.team_a_id).execute()
            for p in (tp_res.data or []):
                uid = p["user_id"]
                players_a.append(uid)
                all_player_ids.append(uid)
                player_roles[uid] = p.get("role") or "All-Rounder"
                player_batting[uid] = p.get("batting_style") or "RHB"
                player_bowling[uid] = p.get("bowling_style") or "Right-Arm Medium"
        except Exception as e:
            logger.error("Error loading team_a roster: %s", e)

    # Get players from team_b
    if request.team_b_id:
        try:
            team_res = supabase.table("teams").select("name").eq("id", request.team_b_id).single().execute()
            if team_res.data:
                team_b_name = team_res.data.get("name", "Team B")
            tp_res = supabase.table("team_players").select("user_id, role, batting_style, bowling_style").eq("team_id", request.team_b_id).execute()
            for p in (tp_res.data or []):
                uid = p["user_id"]
                players_b.append(uid)
                all_player_ids.append(uid)
                player_roles[uid] = p.get("role") or "All-Rounder"
                player_batting[uid] = p.get("batting_style") or "RHB"
                player_bowling[uid] = p.get("bowling_style") or "Right-Arm Medium"
        except Exception as e:
            logger.error("Error loading team_b roster: %s", e)

    # Fetch user names
    if all_player_ids:
        try:
            users_res = supabase.table("users").select("id, first_name, last_name").in_("id", all_player_ids).execute()
            for u in (users_res.data or []):
                names_map[u["id"]] = f"{u.get('first_name','')} {u.get('last_name','')}".strip() or "Player"
        except Exception as e:
            logger.error("Error loading user names: %s", e)

    # Build roster details string
    if players_a:
        roster_details.append(f"--- Roster: {team_a_name} ---")
        for pid in players_a:
            pname = names_map.get(pid, f"Player {pid}")
            roster_details.append(f"- {pname} | Role: {player_roles.get(pid)} | Batting: {player_batting.get(pid)} | Bowling: {player_bowling.get(pid)}")
    
    if players_b:
        roster_details.append(f"\n--- Roster: {team_b_name} ---")
        for pid in players_b:
            pname = names_map.get(pid, f"Player {pid}")
            roster_details.append(f"- {pname} | Role: {player_roles.get(pid)} | Batting: {player_batting.get(pid)} | Bowling: {player_bowling.get(pid)}")

    # 2. Aggregated career stats for these players
    career_details = []
    if all_player_ids:
        career_details.append("\n--- Career Performance Stats ---")
        for pid in all_player_ids:
            try:
                pname = names_map.get(pid, f"Player {pid}")
                
                # Fetch Batting deliveries
                bat_res = supabase.table("deliveries").select("runs_batsman, extra_type, is_wicket, dismissed_id").eq("batsman_id", pid).execute()
                bat_d = bat_res.data or []
                bat_runs = sum(d.get("runs_batsman") or 0 for d in bat_d)
                balls_f = sum(1 for d in bat_d if d.get("extra_type") != "wide")
                dismissals = sum(1 for d in bat_d if d.get("is_wicket") and d.get("dismissed_id") == pid)
                sr = round((bat_runs / max(balls_f, 1)) * 100, 1)
                avg = round(bat_runs / max(dismissals, 1), 1)
                
                # Fetch Bowling deliveries
                bowl_res = supabase.table("deliveries").select("runs_batsman, runs_extras, extra_type, is_wicket, wicket_type").eq("bowler_id", pid).execute()
                bowl_d = bowl_res.data or []
                wickets = sum(1 for d in bowl_d if d.get("is_wicket") and d.get("wicket_type") not in ["runout", "retired"])
                runs_c = sum((d.get("runs_batsman") or 0) + (d.get("runs_extras") or 0) for d in bowl_d if d.get("extra_type") in ["wide", "noball", None])
                balls_b = sum(1 for d in bowl_d if d.get("extra_type") not in ["wide", "noball"])
                econ = round((runs_c / max(balls_b, 1)) * 6, 2)
                
                career_details.append(
                    f"- {pname}: Batting: {bat_runs} runs ({balls_f} balls, SR {sr}, Avg {avg}) | Bowling: {wickets} wickets, Economy {econ}"
                )
            except Exception as e:
                logger.error("Error aggregating career stats for player %s: %s", pid, e)

    # 3. Head-to-Head (H2H) matchups
    if players_a and players_b:
        try:
            h2h_res1 = supabase.table("deliveries").select("batsman_id, bowler_id, runs_batsman, extra_type, is_wicket").in_("batsman_id", players_a).in_("bowler_id", players_b).execute()
            h2h_res2 = supabase.table("deliveries").select("batsman_id, bowler_id, runs_batsman, extra_type, is_wicket").in_("batsman_id", players_b).in_("bowler_id", players_a).execute()
            h2h_data = (h2h_res1.data or []) + (h2h_res2.data or [])
            
            if h2h_data:
                h2h_details.append("\n--- Head-to-Head Player Matchups ---")
                h2h_stats = {}
                for d in h2h_data:
                    bat = d["batsman_id"]
                    bowl = d["bowler_id"]
                    runs = d.get("runs_batsman") or 0
                    extra = d.get("extra_type")
                    is_w = d.get("is_wicket") or False
                    
                    key = (bat, bowl)
                    if key not in h2h_stats:
                        h2h_stats[key] = {"runs": 0, "balls": 0, "wickets": 0}
                    h2h_stats[key]["runs"] += runs
                    if extra != "wide":
                        h2h_stats[key]["balls"] += 1
                    if is_w:
                        h2h_stats[key]["wickets"] += 1
                
                for (bat, bowl), stat in h2h_stats.items():
                    bat_name = names_map.get(bat, f"Player {bat}")
                    bowl_name = names_map.get(bowl, f"Player {bowl}")
                    sr = round((stat["runs"] / max(stat["balls"], 1)) * 100, 1)
                    h2h_details.append(
                        f"- {bat_name} vs {bowl_name}: Faced {stat['balls']} balls, scored {stat['runs']} runs, dismissed {stat['wickets']} times (SR {sr})"
                    )
        except Exception as e:
            logger.error("Error calculating head-to-head matchups: %s", e)

    # 4. Venue-Specific stats
    if request.venue:
        venue_details.append(f"\n--- Venue performance history at: {request.venue} ---")
        try:
            matches_res = supabase.table("matches").select("id").ilike("venue", f"%{request.venue}%").execute()
            match_ids = [m["id"] for m in (matches_res.data or [])]
            if match_ids:
                del_res = supabase.table("deliveries").select("batsman_id, bowler_id, runs_batsman, runs_extras, extra_type, is_wicket").in_("match_id", match_ids).execute()
                dels = del_res.data or []
                
                venue_batting = {}
                venue_bowling = {}
                for d in dels:
                    bat = d["batsman_id"]
                    bowl = d["bowler_id"]
                    runs_b = d.get("runs_batsman") or 0
                    runs_e = d.get("runs_extras") or 0
                    extra = d.get("extra_type")
                    is_w = d.get("is_wicket") or False
                    
                    if bat and bat in all_player_ids:
                        if bat not in venue_batting:
                            venue_batting[bat] = {"runs": 0, "balls": 0}
                        venue_batting[bat]["runs"] += runs_b
                        if extra != "wide":
                            venue_batting[bat]["balls"] += 1
                    if bowl and bowl in all_player_ids:
                        if bowl not in venue_bowling:
                            venue_bowling[bowl] = {"runs": 0, "balls": 0, "wickets": 0}
                        if extra in ["wide", "noball", None]:
                            venue_bowling[bowl]["runs"] += (runs_b + runs_e)
                        if extra not in ["wide", "noball"]:
                            venue_bowling[bowl]["balls"] += 1
                        if is_w:
                            venue_bowling[bowl]["wickets"] += 1
                
                for pid in all_player_ids:
                    pname = names_map.get(pid, f"Player {pid}")
                    b_stat = venue_batting.get(pid, {"runs": 0, "balls": 0})
                    w_stat = venue_bowling.get(pid, {"runs": 0, "balls": 0, "wickets": 0})
                    if b_stat["balls"] > 0 or w_stat["balls"] > 0:
                        econ = round((w_stat["runs"] / max(w_stat["balls"], 1)) * 6, 2)
                        venue_details.append(
                            f"- {pname} at {request.venue}: Batting: {b_stat['runs']} runs ({b_stat['balls']} balls) | Bowling: {w_stat['wickets']} wickets, Economy {econ}"
                        )
            else:
                venue_details.append("No matches played at this venue yet in the database.")
        except Exception as e:
            logger.error("Error calculating venue-specific stats: %s", e)

    # 5. Build full context text
    scouting_context = "\n".join(roster_details + career_details + h2h_details + venue_details)

    # 6. Format Gemini Chat Prompt History
    contents = []
    
    # System Instruction
    system_instruction = (
        "You are Crease AI, a brilliant cricket scouting analyst and coach. "
        "You have access to historical career data, head-to-head (H2H) batsman-vs-bowler matchups, "
        "and venue-specific performance analytics for the players on both rosters. "
        "Use the provided context to answer the coach or captain's scouting query. "
        "Provide specific, data-backed, tactical suggestions (e.g. recommend a specific bowler to target a batsman, "
        "highlight a batsman's weakness against a bowler type, or describe pitch conditions based on historical stats). "
        "Be extremely accurate and structure your answer with clear headers, lists, or tables for readability."
    )
    
    # Add history
    if request.history:
        for msg in request.history:
            contents.append({
                "role": "user" if msg["role"] == "user" else "model",
                "parts": [{"text": msg["content"]}]
            })
            
    # Append final question with context
    final_prompt = f"--- SCOUTING CONTEXT ---\n{scouting_context}\n\n--- SCOUTING QUERY ---\n{request.query}"
    contents.append({
        "role": "user",
        "parts": [{"text": final_prompt}]
    })

    # 7. Make API request to Google Gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logger.error("Missing GEMINI_API_KEY environment variable")
        return ChatResponse(
            answer="Error: Gemini API Key is not configured in the backend environment. Please configure GEMINI_API_KEY.",
            sources=[]
        )
        
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [
                {"text": system_instruction}
            ]
        },
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 2048
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code == 200:
                res_data = response.json()
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return ChatResponse(
                    answer=text,
                    sources=[{"type": "roster", "count": len(all_player_ids)}, {"type": "h2h", "count": len(h2h_details)}]
                )
            else:
                logger.error("Gemini API call failed with status: %s, response: %s", response.status_code, response.text)
                return ChatResponse(
                    answer=f"Error: Gemini API responded with status {response.status_code}.",
                    sources=[]
                )
    except Exception as e:
        logger.exception("Unexpected error calling Gemini API")
        return ChatResponse(
            answer=f"Error: Exception occurred during analysis: {str(e)}",
            sources=[]
        )
