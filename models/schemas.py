"""
Pydantic request/response models for CREASE Brain API.

Aligned with the Supabase schema:
  users, teams, team_players, matches, innings, overs, balls, player_match_stats
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ─── Enums ────────────────────────────────────────────────────────────────────

class MatchFormat(str, Enum):
    LIMITED_OVERS = "limited_overs"
    BOX_TURF = "box_turf"
    PAIR_CRICKET = "pair_cricket"
    TEST = "test"
    THE_HUNDRED = "the_hundred"


class MatchStatus(str, Enum):
    SCHEDULED = "scheduled"
    TOSS = "toss"
    LIVE = "live"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class InningsStatus(str, Enum):
    NOT_STARTED = "not_started"
    ONGOING = "ongoing"
    COMPLETED = "completed"


class WicketType(str, Enum):
    BOWLED = "bowled"
    CAUGHT = "caught"
    LBW = "lbw"
    RUN_OUT = "run_out"
    STUMPED = "stumped"
    HIT_WICKET = "hit_wicket"
    RETIRED = "retired"
    OBSTRUCTING = "obstructing"


class ExtraType(str, Enum):
    WIDE = "wide"
    NO_BALL = "no_ball"
    BYE = "bye"
    LEG_BYE = "leg_bye"


class BattingStyle(str, Enum):
    RIGHT_HAND = "right_hand"
    LEFT_HAND = "left_hand"


class BowlingStyle(str, Enum):
    RIGHT_ARM_FAST = "right_arm_fast"
    RIGHT_ARM_MEDIUM = "right_arm_medium"
    LEFT_ARM_FAST = "left_arm_fast"
    LEFT_ARM_MEDIUM = "left_arm_medium"
    RIGHT_ARM_SPIN = "right_arm_spin"
    LEFT_ARM_SPIN = "left_arm_spin"
    RIGHT_ARM_OFFBREAK = "right_arm_offbreak"
    LEFT_ARM_ORTHODOX = "left_arm_orthodox"


class PlayerRole(str, Enum):
    BATSMAN = "batsman"
    BOWLER = "bowler"
    ALL_ROUNDER = "all_rounder"
    WICKET_KEEPER = "wicket_keeper"


# ─── Player / User Models ────────────────────────────────────────────────────

class UserRegister(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=50)
    last_name: str = Field(..., min_length=1, max_length=50)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=15)
    password: str = Field(..., min_length=6)
    role: Optional[str] = "player"

class UserLogin(BaseModel):
    email_or_phone: str
    password: str

class AuthUserResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    avatar_color: Optional[str] = None
    role: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserResponse


# ─── Player Profile Models ───────────────────────────────────────────────────

class PlayerCreate(BaseModel):
    """Register a new player profile."""
    name: str = Field(..., min_length=1, max_length=100)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    avatar_url: Optional[str] = None
    role: PlayerRole = PlayerRole.BATSMAN
    batting_style: BattingStyle = BattingStyle.RIGHT_HAND
    bowling_style: Optional[BowlingStyle] = None
    team_id: Optional[str] = None


class PlayerUpdate(BaseModel):
    """Partial update for a player profile."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    avatar_url: Optional[str] = None
    role: Optional[PlayerRole] = None
    batting_style: Optional[BattingStyle] = None
    bowling_style: Optional[BowlingStyle] = None


class PlayerResponse(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    role: Optional[str] = None
    batting_style: Optional[str] = None
    bowling_style: Optional[str] = None
    created_at: Optional[str] = None



# ─── Team Models ──────────────────────────────────────────────────────────────

class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    logo_url: Optional[str] = None


class TeamPlayerAdd(BaseModel):
    player_id: str
    is_captain: bool = False
    is_vice_captain: bool = False


# ─── Match Models ─────────────────────────────────────────────────────────────

class MatchCreate(BaseModel):
    """Create a new match."""
    team_a: str = Field(..., description="Team A identifier (team id or name)")
    team_b: str = Field(..., description="Team B identifier (team id or name)")
    format: MatchFormat = MatchFormat.LIMITED_OVERS
    overs: int = Field(20, ge=1, le=100, description="Total overs per side")
    overs_per_bowler: Optional[int] = Field(None, ge=1, description="Max overs per bowler")
    powerplay_overs: Optional[int] = Field(None, ge=0)
    city: Optional[str] = Field(None, max_length=100)
    ground: Optional[str] = Field(None, max_length=200)
    scheduled_at: Optional[datetime] = None

    @field_validator("overs_per_bowler")
    @classmethod
    def validate_overs_per_bowler(cls, v: Optional[int], info) -> Optional[int]:
        if v is not None and "overs" in info.data and v > info.data["overs"]:
            raise ValueError("overs_per_bowler cannot exceed total overs")
        return v


class MatchResponse(BaseModel):
    id: str
    team_a: str
    team_b: str
    format: str
    status: str
    overs: Optional[int] = None
    city: Optional[str] = None
    ground: Optional[str] = None
    toss_winner: Optional[str] = None
    toss_decision: Optional[str] = None
    winner: Optional[str] = None
    created_at: Optional[str] = None


# ─── Toss ─────────────────────────────────────────────────────────────────────

class TossRecord(BaseModel):
    toss_winner: str = Field(..., description="Team id that won the toss")
    toss_decision: str = Field(..., pattern="^(bat|bowl)$", description="Elected to bat or bowl")


# ─── Innings Models ──────────────────────────────────────────────────────────

class InningsCreate(BaseModel):
    """Start a new innings within a match."""
    batting_team: str = Field(..., description="Team id batting this innings")
    bowling_team: str = Field(..., description="Team id bowling this innings")
    innings_number: int = Field(..., ge=1, le=4)


class InningsResponse(BaseModel):
    id: str
    match_id: str
    batting_team: str
    bowling_team: Optional[str] = None
    innings_number: int
    status: str
    total_runs: Optional[int] = 0
    total_wickets: Optional[int] = 0
    total_overs: Optional[str] = None


# ─── Ball / Delivery Models ─────────────────────────────────────────────────

class BallEventCreate(BaseModel):
    """Record a single delivery."""
    over_number: int = Field(..., ge=0, description="Current over (0-indexed)")
    ball_number: int = Field(..., ge=1, le=10, description="Ball within the over (1-indexed)")
    batsman_id: str
    non_striker_id: str
    bowler_id: str
    runs: int = Field(0, ge=0, le=6, description="Runs scored off the bat")
    is_wicket: bool = False
    wicket_type: Optional[WicketType] = None
    dismissed_player_id: Optional[str] = None
    fielder_id: Optional[str] = None
    extras: int = Field(0, ge=0)
    extra_type: Optional[ExtraType] = None
    is_boundary: bool = False
    is_six: bool = False
    commentary: Optional[str] = None

    @field_validator("wicket_type")
    @classmethod
    def wicket_type_required_if_wicket(cls, v, info):
        if info.data.get("is_wicket") and v is None:
            raise ValueError("wicket_type is required when is_wicket is True")
        return v


class BallEventResponse(BaseModel):
    id: str
    innings_id: str
    over_number: int
    ball_number: int
    batsman_id: str
    bowler_id: str
    runs: int
    extras: int
    is_wicket: bool
    wicket_type: Optional[str] = None
    extra_type: Optional[str] = None


# ─── Scorecard Aggregations ──────────────────────────────────────────────────

class BatsmanStat(BaseModel):
    player_id: str
    name: Optional[str] = None
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    strike_rate: float = 0.0
    is_out: bool = False
    dismissal: Optional[str] = None


class BowlerStat(BaseModel):
    player_id: str
    name: Optional[str] = None
    overs: str = "0.0"
    maidens: int = 0
    runs: int = 0
    wickets: int = 0
    economy: float = 0.0


class InningsScorecard(BaseModel):
    innings_id: str
    batting_team: str
    total_runs: int
    total_wickets: int
    overs: str
    run_rate: float = 0.0
    batsmen: List[BatsmanStat] = []
    bowlers: List[BowlerStat] = []
    extras_total: int = 0


class FullScorecard(BaseModel):
    match_id: str
    innings: List[InningsScorecard] = []


# ─── Player Match Stats (computed cache) ─────────────────────────────────────

class PlayerMatchStats(BaseModel):
    player_id: str
    match_id: str
    innings_id: Optional[str] = None
    runs_scored: int = 0
    balls_faced: int = 0
    fours: int = 0
    sixes: int = 0
    strike_rate: float = 0.0
    overs_bowled: str = "0.0"
    runs_conceded: int = 0
    wickets_taken: int = 0
    economy: float = 0.0
    catches: int = 0
    run_outs: int = 0
    is_man_of_match: bool = False


# ─── Leaderboard ─────────────────────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    rank: int
    player_id: str
    name: str
    avatar_url: Optional[str] = None
    rating: float = 0.0
    matches: int = 0
    runs: int = 0
    wickets: int = 0
    tier: Optional[str] = None  # Elite, Challenger, Rising, Contender, Rookie
    rank_change: Optional[int] = None  # positive = up, negative = down


# ─── Chat / RAG ──────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    match_id: Optional[str] = None
    player_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]] = []


# ─── Generic API Wrappers ────────────────────────────────────────────────────

class APIResponse(BaseModel):
    message: str
    data: Optional[Any] = None


class APIError(BaseModel):
    detail: str
