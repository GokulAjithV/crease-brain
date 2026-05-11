"""
RAG-based chat endpoint.

Accepts natural-language queries about matches / players and returns
AI-generated answers grounded in match data.
"""

from fastapi import APIRouter, HTTPException

from models.schemas import ChatRequest, ChatResponse, APIResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Ask CREASE AI a question about matches or players.

    Uses RAG (Retrieval-Augmented Generation) to ground Gemini responses
    in actual match data stored in ChromaDB.

    TODO: Wire up ai/rag.py once ChromaDB embeddings are populated.
    """
    # Placeholder — will be replaced with RAG pipeline
    return ChatResponse(
        answer="CREASE AI is not yet connected. This endpoint will use Gemini + ChromaDB RAG.",
        sources=[],
    )
