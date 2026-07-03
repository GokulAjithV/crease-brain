"""
Authentication routes for register and login.
"""

import logging
import httpx
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status
from models.schemas import UserRegister, UserLogin, Token, AuthUserResponse
from services.db import supabase
from services.auth import get_password_hash, verify_password, create_access_token
import random

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

def generate_random_color() -> str:
    """Generate a random hex color code for avatar_color."""
    return "#{:06x}".format(random.randint(0, 0xFFFFFF))

@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(user: UserRegister):
    identifier = user.email or user.phone
    logger.info("Registration attempt for: %s", identifier)
    
    if not user.email and not user.phone:
        logger.warning("Registration failed: neither email nor phone provided.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either email or phone must be provided."
        )

    # Check if user already exists
    query = supabase.table("users").select("id").limit(1)
    if user.email:
        query = query.or_(f"email.eq.{user.email}")
    if user.phone:
        query = query.or_(f"phone.eq.{user.phone}")
        
    existing_user = query.execute()
    if existing_user.data:
        logger.warning("Registration failed: user with email/phone %s already exists.", identifier)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email or phone already exists."
        )

    # Hash the password
    hashed_password = get_password_hash(user.password)

    # Prepare payload
    payload = {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "password_hash": hashed_password,
        "role": user.role,
        "avatar_color": generate_random_color(),
    }
    
    if user.email:
        payload["email"] = user.email
    if user.phone:
        payload["phone"] = user.phone

    # Insert into Supabase
    response = supabase.table("users").insert(payload).execute()
    
    if not response.data:
        logger.error("Database insert failed during registration for %s", identifier)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register user."
        )

    new_user = response.data[0]
    if not isinstance(new_user, dict):
        logger.error("Unexpected database response format during registration")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected response format from database."
        )
    
    user_response = AuthUserResponse.model_validate(new_user)

    # Generate JWT
    access_token = create_access_token(data={"sub": new_user["id"], "role": new_user["role"]})

    logger.info("Successfully registered user %s", new_user["id"])
    return Token(access_token=access_token, user=user_response)

@router.post("/login", response_model=Token)
async def login(credentials: UserLogin):
    logger.info("Login attempt for: %s", credentials.email_or_phone)
    # Find user by email or phone
    # We will try both fields.
    query = supabase.table("users").select("*").or_(f"email.eq.{credentials.email_or_phone},phone.eq.{credentials.email_or_phone}").limit(1).execute()
    
    if not query.data:
        logger.warning("Login failed: User not found for %s", credentials.email_or_phone)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user_data = query.data[0]
    if not isinstance(user_data, dict):
        logger.error("Unexpected database response format during login")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected response format from database."
        )
    
    # Verify active
    if not user_data.get("is_active", True):
        logger.warning("Login failed: Account disabled for user %s", user_data.get("id"))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled."
        )
        
    # Verify password
    if not verify_password(credentials.password, str(user_data["password_hash"])):
        logger.warning("Login failed: Incorrect password for user %s", user_data.get("id"))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user_response = AuthUserResponse.model_validate(user_data)
    
    # Generate JWT
    access_token = create_access_token(data={"sub": user_data["id"], "role": user_data["role"]})

    logger.info("User %s logged in successfully", user_data["id"])
    return Token(access_token=access_token, user=user_response)


class GoogleLoginRequest(BaseModel):
    id_token: str

@router.post("/google", response_model=Token)
async def google_login(payload: GoogleLoginRequest):
    logger.info("Google login attempt started")
    
    # 1. Verify Google Token
    async with httpx.AsyncClient() as client:
        google_res = await client.get(
            f"https://oauth2.googleapis.com/tokeninfo?id_token={payload.id_token}"
        )
        if google_res.status_code != 200:
            logger.warning("Google login failed: Invalid ID token returned by Google info")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Google ID token."
            )
        google_data = google_res.json()
        
    email = google_data.get("email")
    if not email:
        logger.warning("Google login failed: Email not present in Google tokeninfo")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account must have a verified email address."
        )
        
    first_name = google_data.get("given_name", google_data.get("name", "Google")).strip()
    last_name = google_data.get("family_name", "").strip()
    
    # 2. Check if user already exists by email
    query = supabase.table("users").select("*").eq("email", email).limit(1).execute()
    
    if query.data:
        # User exists, proceed with login
        user_data = query.data[0]
        if not user_data.get("is_active", True):
            logger.warning("Google login failed: Account disabled for user %s", user_data.get("id"))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is disabled."
            )
    else:
        # User doesn't exist, auto-register them!
        logger.info("Auto-registering new user via Google SSO: %s", email)
        
        # Generate a random password hash since password is required by schema/table
        import uuid
        random_password = str(uuid.uuid4())
        hashed_password = get_password_hash(random_password)
        
        reg_payload = {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "password_hash": hashed_password,
            "role": "scorer", # default role
            "avatar_color": generate_random_color(),
            "is_active": True
        }
        
        insert_res = supabase.table("users").insert(reg_payload).execute()
        if not insert_res.data:
            logger.error("Failed to insert new Google SSO user in database")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to register user via Google SSO."
            )
        user_data = insert_res.data[0]
        
    user_response = AuthUserResponse.model_validate(user_data)
    
    # 3. Generate standard JWT
    access_token = create_access_token(data={"sub": user_data["id"], "role": user_data["role"]})
    
    logger.info("User %s logged in successfully via Google SSO", user_data["id"])
    return Token(access_token=access_token, user=user_response)
