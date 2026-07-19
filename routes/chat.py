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
from ai.rag import CricketRAG, SemanticTextIndex, PredictEngine

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

class ScoutRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    team_a_id: Optional[str] = None
    team_b_id: Optional[str] = None
    venue: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None

class PredictRequest(BaseModel):
    team_a_id: Optional[str] = None
    team_b_id: Optional[str] = None
    venue: Optional[str] = None

class AddDocRequest(BaseModel):
    text: str = Field(..., min_length=1)
    metadata: Optional[Dict[str, Any]] = None


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
    
    # 1. Fetch structured RAG context from DB
    scouting_context = await CricketRAG.get_context(request.team_a_id, request.team_b_id, request.venue)
    
    # 2. Enrich context with similar documents using semantic text search
    try:
        sem_docs = await SemanticTextIndex.search(request.query, top_k=2)
        if sem_docs:
            scouting_context += "\n\n--- SEMANTIC INSIGHTS ---\n" + "\n".join(
                f"- [{d['metadata'].get('type', 'Note')}]: {d['text']}" for d in sem_docs
            )
    except Exception as e:
        logger.error("Failed to add semantic search insights to prompt context: %s", e)

    # 3. Format Gemini Chat Prompt History
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

    # 4. Make API request to Google Gemini
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
                    sources=[{"type": "rag", "source": "CricketRAG"}, {"type": "semantic", "source": "SemanticTextIndex"}]
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


@router.post("/predict", response_model=APIResponse)
async def predict_match_outcome(request: PredictRequest):
    """
    Generate match simulations, win probabilities, and expected player statistics.
    """
    logger.info("Predicting outcome for Match: Team A=%s, Team B=%s, Venue=%s", request.team_a_id, request.team_b_id, request.venue)
    prediction = await PredictEngine.predict_match(request.team_a_id, request.team_b_id, request.venue)
    if "error" in prediction:
        raise HTTPException(status_code=400, detail=prediction["error"])
    return APIResponse(message="Match prediction generated successfully", data=prediction)


@router.post("/document", response_model=APIResponse)
async def add_document(request: AddDocRequest):
    """
    Index and embed a match report or note for semantic retrieval during scouting/predictions.
    """
    logger.info("Indexing new document chunk to semantic search...")
    try:
        doc = await SemanticTextIndex.add_document(request.text, request.metadata)
        return APIResponse(message="Document added and indexed successfully", data=doc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to index document: {str(e)}")
