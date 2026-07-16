import os
from typing import Optional

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings

# Resolve .env relative to this file's directory (backend/), not the CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV_FILE = os.path.join(_HERE, "..", ".env")  # backend/.env
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    # App
    APP_NAME: str = "PILOT"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if not v or v in ("change-me-32-random-chars-minimum", "your_jwt_secret_key_here", ""):
            raise ValueError("SECRET_KEY must be a strong, non-default random string.")
        return v
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    # DB
    DATABASE_URL: str = os.getenv("DATABASE_URL") or "sqlite+aiosqlite:///./data/pilot.db"

    # Providers — swap via .env
    ASR_PROVIDER: str = os.getenv("ASR_PROVIDER") or "whisper"
    TTS_PROVIDER: str = os.getenv("TTS_PROVIDER") or "edge_tts"
    DIAR_PROVIDER: str = os.getenv("DIAR_PROVIDER") or "pyannote"
    EMBED_PROVIDER: str = os.getenv("EMBED_PROVIDER") or "wespeaker"
    FRONT_LLM_PROVIDER: str = os.getenv("FRONT_LLM_PROVIDER") or "ollama"
    BG_LLM_PROVIDER: str = os.getenv("BG_LLM_PROVIDER") or "ollama"

    # Hardware acceleration preferences — toggle between 'cpu' and 'mps' (for Apple Silicon CoreML)
    PREFERRED_DEVICE: str = "mps"

    # Model settings
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL") or "distil-large-v3"
    WHISPER_LANGUAGE: Optional[str] = os.getenv("WHISPER_LANGUAGE") or "en"
    # Primary model. If it isn't actually pulled in Ollama yet (checked once at
    # startup in FrontLLMProvider.load()), OLLAMA_MODEL is transparently
    # swapped to OLLAMA_FALLBACK_MODEL for the rest of the process — every call
    # site reads settings.OLLAMA_MODEL, so they all pick up the fallback
    # automatically with no per-call-site changes needed.
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL") or "qwen3.5:4b"
    OLLAMA_FALLBACK_MODEL: str = os.getenv("OLLAMA_FALLBACK_MODEL") or "qwen2.5:7b"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    # Hard ceiling on a single classify() call to Ollama. Without this, a slow
    # or memory-starved Ollama instance blocks the whole voice pipeline for as
    # long as it takes (observed: 40s+ under system memory pressure) instead
    # of degrading to the deterministic keyword fallback.
    OLLAMA_TIMEOUT_S: float = 6.0
    # Separate, longer ceiling for tools that generate a full answer (general_qa's
    # 1024-token code/explanation replies) rather than a quick classify() decision —
    # 6s is too tight for those and would abort genuinely-in-progress generations.
    OLLAMA_QA_TIMEOUT_S: float = 45.0
    GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
    GROQ_API_KEY: Optional[str] = os.getenv("GROQ_API_KEY")
    SERPAPI_KEY: Optional[str] = None

    # MCP settings
    PILOT_MCP_COMMAND: str = os.getenv("PILOT_MCP_COMMAND") or "node"
    PILOT_MCP_ARGS: Optional[str] = os.getenv("PILOT_MCP_ARGS")
    PILOT_MCP_TOOL: Optional[str] = os.getenv("PILOT_MCP_TOOL")
    TAVILY_API_KEY: Optional[str] = os.getenv("TAVILY_API_KEY")
    GOOGLE_CLIENT_ID: Optional[str] = os.getenv("GOOGLE_CLIENT_ID")

    # Google SSO login — server-side OAuth2 authorization-code flow
    # (GET /auth/sso/google -> Google consent -> GET /auth/sso/google/callback).
    # Replaces the earlier GIS "Sign in with Google" button/ID-token flow;
    # needs the client secret since it exchanges the auth code server-side.
    GOOGLE_REDIRECT_URI: str = (
        os.getenv("GOOGLE_REDIRECT_URI") or "http://localhost:8000/api/v1/auth/sso/google/callback"
    )

    # Google Calendar — separate OAuth2 authorization-code flow, same OAuth
    # client as the login flow above but its own redirect URI/scope since it
    # requests calendar API access rather than just identity.
    GOOGLE_CLIENT_SECRET: Optional[str] = os.getenv("GOOGLE_CLIENT_SECRET")
    GOOGLE_CALENDAR_REDIRECT_URI: str = (
        os.getenv("GOOGLE_CALENDAR_REDIRECT_URI") or "http://localhost:8000/api/v1/calendar/callback"
    )
    GOOGLE_CALENDAR_SCOPE: str = "https://www.googleapis.com/auth/calendar.events"
    # Where to send the browser after the OAuth consent redirect completes.
    FRONTEND_URL: str = os.getenv("FRONTEND_URL") or "http://localhost:5173"

    # Twilio Voice — real two-party calling for Customer Resolution (rep in
    # browser via Twilio Voice JS SDK <-> real PSTN call to the customer).
    # ACCOUNT_SID/AUTH_TOKEN are the account-level REST credentials; API_KEY_*
    # are a separate credential pair Twilio requires specifically for signing
    # client-side Voice access tokens (Account SID/Auth Token alone can't do
    # this). TWIML_APP_SID routes the browser client's outbound call attempt
    # to our /telephony/voice webhook.
    TWILIO_ACCOUNT_SID: Optional[str] = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_API_KEY_SID: Optional[str] = os.getenv("TWILIO_API_KEY_SID")
    TWILIO_API_KEY_SECRET: Optional[str] = os.getenv("TWILIO_API_KEY_SECRET")
    TWILIO_TWIML_APP_SID: Optional[str] = os.getenv("TWILIO_TWIML_APP_SID")
    TWILIO_PHONE_NUMBER: Optional[str] = os.getenv("TWILIO_PHONE_NUMBER")
    # Publicly reachable URL for this backend (ngrok in dev, real domain in
    # prod) — Twilio calls webhooks and streams call audio to this over the
    # open internet, it can never be localhost.
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL") or "http://localhost:8000"

    # Travel MCP API keys
    GOOGLE_MAPS_API_KEY: Optional[str] = os.getenv("GOOGLE_MAPS_API_KEY")
    AMADEUS_CLIENT_ID: Optional[str] = os.getenv("AMADEUS_CLIENT_ID")
    AMADEUS_CLIENT_SECRET: Optional[str] = os.getenv("AMADEUS_CLIENT_SECRET")

    # Speaker identity
    COSINE_THRESHOLD: float = 0.50
    COSINE_MARGIN: float = 0.05
    EMBEDDING_DIM: int = 512

    # Queue sizes (back-pressure)
    RAW_AUDIO_Q_SIZE: int = 100
    TURN_Q_SIZE: int = 30
    LABELED_TURN_Q_SIZE: int = 30
    TRANSCRIPT_Q_SIZE: int = 50
    EVENT_Q_SIZE: int = 500
    RING_BUFFER_N: int = 50

    # Policy gate
    CONFIRM_TIMEOUT_S: int = 10
    # DESTRUCTIVE_TOOLS: list = ["flight_book", "ticket_close"]
    DESTRUCTIVE_TOOLS: list = ["flight_book", "ppt_delete_slide"]

    # Email (leave blank for dev console output)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = os.getenv("SMTP_USER")
    SMTP_PASS: Optional[str] = os.getenv("SMTP_PASS")
    EMAIL_FROM: str = os.getenv("SMTP_USER") or "pilot@localhost"

    DUFFEL_PILOT_OS_TOKEN:str=os.getenv('DUFFEL_PILOT_OS_TOKEN')
    DUFFEL_API_KEY: Optional[str] = os.getenv("DUFFEL_API_KEY") or os.getenv("DUFFEL_PILOT_OS_TOKEN")
    HF_TOKEN: Optional[str] = os.getenv("HF_TOKEN")

    # OnlyOffice Document Server (optional fallback)
    ONLYOFFICE_URL: Optional[str] = os.getenv("ONLYOFFICE_URL")
    ONLYOFFICE_PUBLIC_URL: Optional[str] = os.getenv("ONLYOFFICE_PUBLIC_URL")
    ONLYOFFICE_JWT_SECRET: Optional[str] = os.getenv("ONLYOFFICE_JWT_SECRET")
    APP_URL: Optional[str] = os.getenv("APP_URL") or "http://host.docker.internal:8000"

    class Config:
        env_file = _ENV_FILE  # always load backend/.env regardless of CWD
        extra = "ignore"


settings = Settings()



 
