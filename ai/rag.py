"""
RAG (Retrieval-Augmented Generation) and Prediction pipeline for Crease.
Implements structured SQL-based context aggregation, pure-Python semantic text indexing
via Gemini Embeddings, and AI-grounded match outcome predictions.
"""

import os
import json
import logging
import httpx
from typing import List, Dict, Any, Optional
from services.db import supabase

logger = logging.getLogger(__name__)

# File path to persist semantic documents and their embeddings
DOCS_STORE_PATH = os.path.join(os.path.dirname(__file__), "document_store.json")


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """Compute the cosine similarity between two vectors."""
    dot_product = sum(x * y for x, y in zip(v1, v2))
    norm_v1 = sum(x * x for x in v1) ** 0.5
    norm_v2 = sum(x * x for x in v2) ** 0.5
    if norm_v1 == 0.0 or norm_v2 == 0.0:
        return 0.0
    return dot_product / (norm_v1 * norm_v2)


async def get_gemini_embedding(text: str) -> List[float]:
    """Fetch text embedding from Google Gemini Embeddings API."""
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured in the backend environment.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={GEMINI_API_KEY}"
    payload = {
        "model": "models/text-embedding-004",
        "content": {
            "parts": [{"text": text}]
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, timeout=15.0)
        if response.status_code == 200:
            res_data = response.json()
            return res_data["embedding"]["values"]
        else:
            raise Exception(f"Gemini Embedding API returned status {response.status_code}: {response.text}")


class SemanticTextIndex:
    """
    Pure-Python vector search engine using Gemini text-embedding-004.
    Saves text documents and pre-computed embeddings to a local JSON file.
    """

    @staticmethod
    def _load_store() -> List[Dict[str, Any]]:
        if not os.path.exists(DOCS_STORE_PATH):
            return []
        try:
            with open(DOCS_STORE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load document store: %s", e)
            return []

    @staticmethod
    def _save_store(data: List[Dict[str, Any]]) -> None:
        try:
            with open(DOCS_STORE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save document store: %s", e)

    @classmethod
    async def add_document(cls, text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Embed and store a new document chunk."""
        logger.info("Adding document to SemanticTextIndex...")
        embedding = await get_gemini_embedding(text)
        
        store = cls._load_store()
        doc_id = len(store) + 1
        new_doc = {
            "id": doc_id,
            "text": text,
            "metadata": metadata or {},
            "embedding": embedding
        }
        store.append(new_doc)
        cls._save_store(store)
        
        return {"id": doc_id, "text": text, "metadata": metadata or {}}

    @classmethod
    async def search(cls, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Search the store for semantically similar documents."""
        store = cls._load_store()
        if not store:
            return []

        try:
            query_embedding = await get_gemini_embedding(query)
        except Exception as e:
            logger.error("Failed to embed query: %s", e)
            return []

        results = []
        for doc in store:
            sim = cosine_similarity(query_embedding, doc["embedding"])
            results.append({
                "id": doc["id"],
                "text": doc["text"],
                "metadata": doc["metadata"],
                "score": round(sim, 4)
            })

        # Sort descending by similarity score
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]


class CricketRAG:
    """
    Structured RAG aggregator compiling rosters, career statistics, H2H,
    and venue history from Supabase.
    """

    @classmethod
    async def get_context(cls, team_a_id: Optional[str], team_b_id: Optional[str], venue: Optional[str]) -> str:
        team_a_name = "Team A"
        team_b_name = "Team B"
        roster_details = []
        career_details = []
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
        if team_a_id:
            try:
                team_res = supabase.table("teams").select("name").eq("id", team_a_id).single().execute()
                if team_res.data:
                    team_a_name = team_res.data.get("name", "Team A")
                tp_res = supabase.table("team_players").select("user_id, role, batting_style, bowling_style").eq("team_id", team_a_id).execute()
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
        if team_b_id:
            try:
                team_res = supabase.table("teams").select("name").eq("id", team_b_id).single().execute()
                if team_res.data:
                    team_b_name = team_res.data.get("name", "Team B")
                tp_res = supabase.table("team_players").select("user_id, role, batting_style, bowling_style").eq("team_id", team_b_id).execute()
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
        if venue:
            venue_details.append(f"\n--- Venue performance history at: {venue} ---")
            try:
                matches_res = supabase.table("matches").select("id").ilike("venue", f"%{venue}%").execute()
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
                                f"- {pname} at {venue}: Batting: {b_stat['runs']} runs ({b_stat['balls']} balls) | Bowling: {w_stat['wickets']} wickets, Economy {econ}"
                            )
                else:
                    venue_details.append("No matches played at this venue yet in the database.")
            except Exception as e:
                logger.error("Error calculating venue-specific stats: %s", e)

        return "\n".join(roster_details + career_details + h2h_details + venue_details)


class PredictEngine:
    """AI Match Predictor using Gemini 2.5 Flash."""

    @classmethod
    async def predict_match(
        cls, team_a_id: Optional[str], team_b_id: Optional[str], venue: Optional[str]
    ) -> Dict[str, Any]:
        """Generate match probability, expected scores, and tactical matchups."""
        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
        if not GEMINI_API_KEY:
            return {"error": "Gemini API Key is not configured."}

        # 1. Fetch structured cricket context
        cricket_context = await CricketRAG.get_context(team_a_id, team_b_id, venue)

        # 2. Query Semantic Text Index for similar ground notes or team summaries
        sem_docs = []
        if venue:
            try:
                sem_docs = await SemanticTextIndex.search(venue, top_k=2)
            except Exception as e:
                logger.error("Semantic search failed: %s", e)

        sem_context = ""
        if sem_docs:
            sem_context = "\n--- Semantic Insights from Ground Notes/Reports ---\n" + "\n".join(
                f"- [{d['metadata'].get('type', 'Note')}]: {d['text']}" for d in sem_docs
            )

        full_context = cricket_context + sem_context

        # 3. Create prediction prompt
        system_instruction = (
            "You are Crease Predict, a predictive AI modeling agent specializing in professional cricket analytics. "
            "You have access to historical career logs, head-to-head match stats, and venue-specific ground reports. "
            "Formulate a structured predictions summary based ONLY on the provided context. "
            "Format the output strictly as a JSON object with the keys: "
            "'winner_probability' (object with team names as keys and percent integer probabilities as values, e.g. {\"Team A\": 65, \"Team B\": 35}), "
            "'predicted_outcome' (string summarizing the predicted match script), "
            "'top_batsmen' (list of objects with keys 'name', 'runs_range', e.g. [{\"name\": \"Player X\", \"runs_range\": \"40-55\"}]), "
            "'top_bowlers' (list of objects with keys 'name', 'wickets_range', e.g. [{\"name\": \"Player Y\", \"wickets_range\": \"2-3\"}]), "
            "'tactical_matchups' (list of strings highlighting critical head-to-head battles and advantages)."
        )

        prompt = (
            f"--- HISTORICAL DATA CONTEXT ---\n{full_context}\n\n"
            f"Run a predictive simulation for this match. Output a valid, clean JSON object matching the specification."
        )

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system_instruction}]
            },
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
                "maxOutputTokens": 1000
            }
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=25.0)
                if response.status_code == 200:
                    res_data = response.json()
                    text = res_data["candidates"][0]["content"]["parts"][0]["text"]
                    return json.loads(text.strip())
                else:
                    logger.error("Gemini API call failed for predict: %s, %s", response.status_code, response.text)
                    return {"error": f"Gemini API returned status {response.status_code}"}
        except Exception as e:
            logger.exception("Unexpected error in PredictEngine")
            return {"error": f"Failed to run match simulation: {str(e)}"}
