"""
Supabase client singleton.

Reads SUPABASE_URL and SUPABASE_KEY from a .env file and exposes
a single `supabase` client instance to be imported across the project.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from supabase import create_client, Client

# Load .env from project root (crease-brain/.env)
load_dotenv()

_url: str | None = os.getenv("SUPABASE_URL")
_key: str | None = os.getenv("SUPABASE_KEY")

if not _url or not _key:
    raise ValueError(
        "Missing Supabase credentials. "
        "Set SUPABASE_URL and SUPABASE_KEY in your .env file."
    )


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Return a cached Supabase client (singleton)."""
    return create_client(_url, _key)


# Convenience export — import this directly:
#   from services.db import supabase
supabase: Client = get_supabase_client()
