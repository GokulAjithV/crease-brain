"""
Gemini-powered match summary and RAG analysis generation.

Takes raw match telemetry and answers user queries about specific matches
using Google's Gemini-2.5-flash model.
"""

import logging
import os
import httpx
from services.db import supabase

logger = logging.getLogger(__name__)

async def generate_ai_analysis(match_id: str, question: str, history: list) -> str:
    """Fetch complete match telemetry context and generate a response using Gemini."""
    logger.info("Generating AI Analysis for match: %s", match_id)
    
    # 1. Fetch match and team details
    match_res = (
        supabase.table("matches")
        .select("*, team_a:team_a_id(*), team_b:team_b_id(*)")
        .eq("id", match_id)
        .single()
        .execute()
    )
    if not match_res.data:
        return "Error: Match not found in database."
    
    m = match_res.data
    team_a_name = m.get("team_a", {}).get("name", "Team A")
    team_b_name = m.get("team_b", {}).get("name", "Team B")
    
    winner_name = None
    if m.get("winner_id"):
        if m["winner_id"] == m.get("team_a_id"):
            winner_name = team_a_name
        elif m["winner_id"] == m.get("team_b_id"):
            winner_name = team_b_name
            
    # 2. Fetch innings summaries
    innings_res = (
        supabase.table("innings")
        .select("*")
        .eq("match_id", match_id)
        .order("innings_number")
        .execute()
    )
    innings_data = innings_res.data or []
    
    # 3. Fetch player squads to map names
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

    # 4. Fetch all deliveries sequentially
    deliveries_log = []
    for inn in innings_data:
        inn_num = inn["innings_number"]
        batting_team = team_a_name if inn["batting_team_id"] == m.get("team_a_id") else team_b_name
        
        # Get deliveries
        del_res = (
            supabase.table("deliveries")
            .select("*")
            .eq("innings_id", inn["id"])
            .order("created_at")
            .execute()
        )
        dels = del_res.data or []
        
        deliveries_log.append(f"\n--- Innings {inn_num} ({batting_team} batting) ---")
        
        legal_ball_count = 0
        for d in dels:
            batsman = squad_map.get(d.get("batsman_id"), "Unknown Batter")
            bowler = squad_map.get(d.get("bowler_id"), "Unknown Bowler")
            runs_b = d.get("runs_batsman") or 0
            runs_e = d.get("runs_extras") or 0
            total_runs = runs_b + runs_e
            extra_type = d.get("extra_type")
            is_wicket = d.get("is_wicket")
            wicket_type = d.get("wicket_type")
            
            # Calculate over index representation (e.g. 0.1, 0.2)
            over_num = legal_ball_count // 6
            ball_num = (legal_ball_count % 6) + 1
            over_ball = f"{over_num}.{ball_num}"
            
            # Wicket detail
            wicket_str = ""
            if is_wicket:
                wicket_str = f" | WICKET! Dismissal Type: {wicket_type}"
                
            # Extras detail
            extras_str = ""
            if extra_type:
                extras_str = f" ({extra_type} - {runs_e} extra runs)"
                
            deliveries_log.append(
                f"Over {over_ball}: {bowler} to {batsman} -> {total_runs} runs{extras_str}{wicket_str}"
            )
            
            if extra_type not in ["wide", "noball"]:
                legal_ball_count += 1
                
    # 5. Build full context text
    context_parts = [
        f"Match: {team_a_name} vs {team_b_name}",
        f"Venue: {m.get('venue', 'Unknown')}, City: {m.get('city', 'Unknown')}",
        f"Scheduled: {m.get('scheduled_at', 'Unknown')}",
        f"Overs Limit: {m.get('total_overs', 20)} overs per innings",
        f"Match Status: {m.get('status')}",
    ]
    
    if m.get("status") == "completed" and winner_name:
        context_parts.append(f"Result: {winner_name} won by {m.get('win_margin')} {m.get('win_type')}.")
    elif m.get("status") == "completed":
        context_parts.append("Result: Match ended in a tie.")
        
    for inn in innings_data:
        batting_team = team_a_name if inn["batting_team_id"] == m.get("team_a_id") else team_b_name
        context_parts.append(
            f"Innings {inn['innings_number']} Summary ({batting_team}): {inn.get('total_runs', 0)}/{inn.get('total_wickets', 0)} in {inn.get('overs_played', '0.0')} overs"
        )
        
    context_parts.append("\n--- Ball-by-Ball Event Log ---")
    context_parts.extend(deliveries_log)
    
    match_context = "\n".join(context_parts)
    
    # 6. Format Prompt and History
    contents = []
    
    # System Instruction
    system_instruction = (
        "You are Crease AI, a brilliant cricket analyst. You have access to the complete ball-by-ball telemetry, "
        "scorecard, and match metadata of this game. "
        "Use the provided match context to answer the user's questions in a clear, narrative, and engaging way. "
        "Be extremely accurate and support your answers with specific runs, over counts, batsman/bowler details, "
        "and wickets from the context. If a question cannot be answered from the provided data, state that clearly."
    )
    
    # Add history
    for msg in history:
        contents.append({
            "role": "user" if msg["role"] == "user" else "model",
            "parts": [{"text": msg["content"]}]
        })
        
    # Append final question with context
    final_prompt = f"--- MATCH CONTEXT ---\n{match_context}\n\n--- QUESTION ---\n{question}"
    contents.append({
        "role": "user",
        "parts": [{"text": final_prompt}]
    })

    # 7. Make API request to Google Gemini
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logger.error("Missing GEMINI_API_KEY environment variable")
        return "Error: Gemini API Key is not configured."
        
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
            "maxOutputTokens": 1000
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, timeout=30.0)
            if response.status_code == 200:
                res_data = response.json()
                text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                return text
            else:
                logger.error("Gemini API call failed with status: %s, response: %s", response.status_code, response.text)
                return f"Error: Gemini API responded with status {response.status_code}."
    except Exception as e:
        logger.exception("Unexpected error calling Gemini API")
        return f"Error: Exception occurred during analysis: {str(e)}"
