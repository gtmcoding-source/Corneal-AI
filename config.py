import os
from datetime import timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

_remember_days_raw = (os.getenv("REMEMBER_ME_DAYS", "30") or "30").strip()
try:
    _remember_days = max(1, int(_remember_days_raw))
except ValueError:
    _remember_days = 30

_admin_log_tail_raw = (os.getenv("ADMIN_LOG_TAIL_LINES", "200") or "200").strip()
try:
    _admin_log_tail_lines = max(50, int(_admin_log_tail_raw))
except ValueError:
    _admin_log_tail_lines = 200

class Config:
    SECRET_KEY = os.getenv("APP_SECRET_KEY") or os.getenv("SECRET_KEY", "super-secret-key-change-me")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    PERMANENT_SESSION_LIFETIME = timedelta(days=_remember_days)
    ADMIN_LOG_FILE = os.getenv("ADMIN_LOG_FILE", "").strip()
    ADMIN_LOG_TAIL_LINES = _admin_log_tail_lines
    
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

    # OAuth Providers
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "").strip()
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "").strip()

    # Payment Platform Checkout URLs (optional)
    STRIPE_CHECKOUT_URL = os.getenv("STRIPE_CHECKOUT_URL", "").strip()
    PAYPAL_CHECKOUT_URL = os.getenv("PAYPAL_CHECKOUT_URL", "").strip()
    RAZORPAY_CHECKOUT_URL = os.getenv("RAZORPAY_CHECKOUT_URL", "").strip()
