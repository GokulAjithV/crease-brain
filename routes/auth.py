"""
Authentication routes for register and login.
"""

from fastapi import APIRouter, HTTPException, status
from models.schemas import UserRegister, UserLogin, Token, AuthUserResponse
from services.db import supabase
from services.auth import get_password_hash, verify_password, create_access_token
import random

router = APIRouter(prefix="/auth", tags=["auth"])

def generate_random_color() -> str:
    """Generate a random hex color code for avatar_color."""
    return "#{:06x}".format(random.randint(0, 0xFFFFFF))

@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
async def register(user: UserRegister):
    if not user.email and not user.phone:
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A user with this email or phone already exists."
        )

    # Hash the password
    hashed_password = get_password_hash(user.password)

    # Prepare payload
    payload = {
        "full_name": user.full_name,
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register user."
        )

    new_user = response.data[0]
    if not isinstance(new_user, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected response format from database."
        )
    
    user_response = AuthUserResponse.model_validate(new_user)

    # Generate JWT
    access_token = create_access_token(data={"sub": new_user["id"], "role": new_user["role"]})

    return Token(access_token=access_token, user=user_response)

@router.post("/login", response_model=Token)
async def login(credentials: UserLogin):
    # Find user by email or phone
    # We will try both fields.
    query = supabase.table("users").select("*").or_(f"email.eq.{credentials.email_or_phone},phone.eq.{credentials.email_or_phone}").limit(1).execute()
    
    if not query.data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user_data = query.data[0]
    if not isinstance(user_data, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected response format from database."
        )
    
    # Verify active
    if not user_data.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled."
        )
        
    # Verify password
    if not verify_password(credentials.password, str(user_data["password_hash"])):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    user_response = AuthUserResponse.model_validate(user_data)
    
    # Generate JWT
    access_token = create_access_token(data={"sub": user_data["id"], "role": user_data["role"]})

    return Token(access_token=access_token, user=user_response)
