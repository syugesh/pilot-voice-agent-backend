from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # App
    APP_NAME: str = "PILOT"
    SECRET_KEY: str = "6a1c65b237eb947bd5f03707c5720408259787b4939509fab1b73942f8378fb0"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15  # access token; refresh token is 7 days (see security.py)

    # DB
    DATABASE_URL: str = "sqlite+aiosqlite:///./pilot.db"

    # Providers — swap via .env
    ASR_PROVIDER: str = "whisper"
    TTS_PROVIDER: str = "edge_tts"
    DIAR_PROVIDER: str = "pyannote"
    EMBED_PROVIDER: str = "wespeaker"
    FRONT_LLM_PROVIDER: str = "ollama"
    BG_LLM_PROVIDER: str = "gemini"

    # Model settings
    WHISPER_MODEL: str = "distil-large-v3"
    OLLAMA_MODEL: str = "qwen3:8b"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    GEMINI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    SERPAPI_KEY: Optional[str] = None
    PEXELS_API_KEY: Optional[str] = None

    # MCP settings
    PILOT_MCP_COMMAND: str = "node"
    PILOT_MCP_ARGS: Optional[str] = None
    PILOT_MCP_TOOL: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None




    # Speaker identity
    COSINE_THRESHOLD: float = 0.75
    EMBEDDING_DIM: int = 256  # WeSpeaker ECAPA-TDNN outputs 256-dim

    # Queue sizes (back-pressure)
    RAW_AUDIO_Q_SIZE: int = 100
    TURN_Q_SIZE: int = 30
    LABELED_TURN_Q_SIZE: int = 30
    TRANSCRIPT_Q_SIZE: int = 50
    EVENT_Q_SIZE: int = 500
    RING_BUFFER_N: int = 50

    # Policy gate
    CONFIRM_TIMEOUT_S: int = 10
    DESTRUCTIVE_TOOLS: list = ["flight_book", "ticket_close"]

    # Google OAuth SSO
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/sso/google/callback"
    FRONTEND_URL: str = "http://localhost:5173"

    # Email (leave blank for dev console output)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASS: Optional[str] = None
    EMAIL_FROM: str = "pilot@localhost"

    class Config:
        env_file = ".env"

settings = Settings()
