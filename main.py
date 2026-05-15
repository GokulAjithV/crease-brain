"""
CREASE Brain — FastAPI backend for the CREASE cricket scoring platform.

Initialises the app, configures CORS for the crease-lens frontend,
and registers all route modules.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.matches import router as matches_router
from routes.players import router as players_router
from routes.chat import router as chat_router
from routes.auth import router as auth_router
from routes.teams import router as teams_router

# ─── App Initialisation ──────────────────────────────────────────────────────

app = FastAPI(
    title="CREASE Brain",
    description="Backend API for CREASE — grassroots cricket scoring with AI insights.",
    version="0.1.0",
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
# Allow the crease-lens Vercel frontend (and localhost for dev)

origins = [
    "https://crease-lens.vercel.app",
    "http://localhost:5173",   # Vite dev server
    "http://localhost:3000",   # Next.js dev server (fallback)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Router Registration ─────────────────────────────────────────────────────

app.include_router(auth_router, prefix="/api")
app.include_router(matches_router, prefix="/api")
app.include_router(players_router, prefix="/api")
app.include_router(teams_router, prefix="/api")
app.include_router(chat_router, prefix="/api")


# ─── Health Check ─────────────────────────────────────────────────────────────

@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "service": "crease-brain"}


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "healthy"}
