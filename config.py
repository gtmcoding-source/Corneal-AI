import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    SECRET_KEY = os.getenv("APP_SECRET_KEY") or os.getenv("SECRET_KEY", "super-secret-key-change-me")
    
    # Database Config
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///database.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # AI Config (Groq)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    GROQ_BASE_URL = "https://api.groq.com/openai/v1"
    AI_MODEL = "llama-3.1-8b-instant"

    # Auth0 Config
    AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "").strip()
    AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "").strip()
    AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET", "").strip()
