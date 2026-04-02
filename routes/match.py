from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, List, Dict
import json
from services.db import supabase

router = APIRouter(tags=["match"])

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, match_id: str):
        await websocket.accept()
        if match_id not in self.active_connections:
            self.active_connections[match_id] = []
        self.active_connections[match_id].append(websocket)

    def disconnect(self, websocket: WebSocket, match_id: str):
        if match_id in self.active_connections:
            self.active_connections[match_id].remove(websocket)
            if not self.active_connections[match_id]:
                del self.active_connections[match_id]

    async def broadcast(self, match_id: str, message: dict):
        if match_id in self.active_connections:
            for connection in self.active_connections[match_id]:
                await connection.send_json(message)

manager = ConnectionManager()

async def get_scorecard_data(match_id: str):
    # Fetch all innings for this match
    innings_response = supabase.table("innings").select("*").eq("match_id", match_id).execute()
    
    if hasattr(innings_response, 'error') and innings_response.error:
        return None
    
    scorecard = []
    
    for inn in innings_response.data:
        innings_id = inn["id"]
        # Fetch all deliveries for this innings
        deliveries_response = supabase.table("deliveries").select("*").eq("innings_id", innings_id).execute()
        
        if hasattr(deliveries_response, 'error') and deliveries_response.error:
            continue
            
        deliveries = deliveries_response.data
        total_runs = sum(d["runs"] + d["extras"] for d in deliveries)
        total_wickets = sum(1 for d in deliveries if d["is_wicket"])
        
        # Basic over calculation (not accounting for wide/no-balls correctly in over count here for simplicity)
        legal_balls = sum(1 for d in deliveries if d["extra_type"] not in ["wide", "no_ball"])
        overs = f"{legal_balls // 6}.{legal_balls % 6}"
        
        # Batsman stats
        batsmen_stats = {}
        for d in deliveries:
            bid = d["batsman_id"]
            if bid not in batsmen_stats:
                batsmen_stats[bid] = {"runs": 0, "balls": 0}
            batsmen_stats[bid]["runs"] += d["runs"]
            if d["extra_type"] not in ["wide"]: # Wides don't count as balls faced
                batsmen_stats[bid]["balls"] += 1
                
        # Bowler stats
        bowler_stats = {}
        for d in deliveries:
            brid = d["bowler_id"]
            if brid not in bowler_stats:
                bowler_stats[brid] = {"runs": 0, "wickets": 0, "balls": 0}
            bowler_stats[brid]["runs"] += (d["runs"] + d["extras"])
            if d["is_wicket"]:
                bowler_stats[brid]["wickets"] += 1
            if d["extra_type"] not in ["wide", "no_ball"]:
                bowler_stats[brid]["balls"] += 1

        scorecard.append({
            "innings_id": innings_id,
            "batting_team": inn["batting_team"],
            "total_runs": total_runs,
            "total_wickets": total_wickets,
            "overs": overs,
            "batsmen": batsmen_stats,
            "bowlers": bowler_stats
        })
    
    return {"match_id": match_id, "scorecard": scorecard}

class MatchCreate(BaseModel):
    team_a: str
    team_b: str
    format: str  # e.g., T20, ODI

class InningsStart(BaseModel):
    batting_team: str
    innings_number: int

class BallEvent(BaseModel):
    batsman_id: str
    bowler_id: str
    runs: int
    is_wicket: bool = False
    wicket_type: Optional[str] = None
    extras: int = 0
    extra_type: Optional[str] = None # e.g., wide, no_ball, bye, leg_bye

@router.post("/match/create")
async def create_match(match: MatchCreate):
    response = supabase.table("matches").insert({
        "team_a": match.team_a,
        "team_b": match.team_b,
        "format": match.format,
        "status": "scheduled"
    }).execute()
    
    if hasattr(response, 'error') and response.error:
        raise HTTPException(status_code=400, detail=str(response.error))
    
    return {"message": "Match created", "data": response.data}

@router.post("/match/{match_id}/innings/start")
async def start_innings(match_id: str, innings: InningsStart):
    response = supabase.table("innings").insert({
        "match_id": match_id,
        "batting_team": innings.batting_team,
        "innings_number": innings.innings_number,
        "status": "ongoing"
    }).execute()
    
    if hasattr(response, 'error') and response.error:
        raise HTTPException(status_code=400, detail=str(response.error))
    
    return {"message": "Innings started", "data": response.data}

@router.post("/innings/{innings_id}/ball")
async def record_ball(innings_id: str, ball: BallEvent):
    response = supabase.table("deliveries").insert({
        "innings_id": innings_id,
        "batsman_id": ball.batsman_id,
        "bowler_id": ball.bowler_id,
        "runs": ball.runs,
        "is_wicket": ball.is_wicket,
        "wicket_type": ball.wicket_type,
        "extras": ball.extras,
        "extra_type": ball.extra_type
    }).execute()
    
    if hasattr(response, 'error') and response.error:
        raise HTTPException(status_code=400, detail=str(response.error))
    
    # Fetch match_id to broadcast
    innings_res = supabase.table("innings").select("match_id").eq("id", innings_id).single().execute()
    if innings_res.data:
        match_id = innings_res.data["match_id"]
        scorecard = await get_scorecard_data(match_id)
        if scorecard:
            await manager.broadcast(match_id, scorecard)
    
    return {"message": "Ball recorded", "data": response.data}

@router.get("/match/{match_id}/scorecard")
async def get_scorecard(match_id: str):
    scorecard = await get_scorecard_data(match_id)
    if scorecard is None:
        raise HTTPException(status_code=400, detail="Error fetching scorecard")
    return scorecard

@router.websocket("/ws/match/{match_id}")
async def websocket_endpoint(websocket: WebSocket, match_id: str):
    await manager.connect(websocket, match_id)
    try:
        # Send initial scorecard
        scorecard = await get_scorecard_data(match_id)
        if scorecard:
            await websocket.send_json(scorecard)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, match_id)
